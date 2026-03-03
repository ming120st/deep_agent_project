"""
메인 대화 그래프 (Main Conversation Graph)

Google Chat 메시지 수신부터 응답 전송까지의 전체 대화 흐름을
하나의 LangGraph StateGraph로 관리합니다.

────────────────────────────────────────────────────────────────
  플로우 다이어그램

  START
    │
    ▼ (항상 의도 분류 먼저)
  [classify_agent_action]  ←── LLM 의도 분류
    │
    ▼ [route_by_action] — actionResult에 따라 라우팅
  ┌──────────┬───────────┼──────────────┬──────────────┐
  │new_agent │ parameter │ rag          │light_response│
  ▼          ▼           ▼              ▼              
execute_  update_    execute_rag   execute_light_
<agent>   parameters               response
  │          │           │              │              
  │          ▼           │              │
  │     [route_after_    │              │
  │      update_params]  │              │
  │          │           │              │
  └──────────┴───────────┴──────────────┴──────────────┘
                              │
                        [send_response]
                              │
                            END
────────────────────────────────────────────────────────────────
"""
from typing import Literal

from langgraph.graph import StateGraph, START, END

from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.nodes.action_classifier import classify_agent_action
from src.workflow.main_graph.nodes.response_sender import send_response
from src.agent.agent_config import AGENT_REGISTRY
from src.core.task_manager import create_task, TaskStatus
from src.core.mongo_client import boa_sops_collection # SOP 조회를 위해 추가
from src.core.mongo_client import checkpointer

# ── 임시 노드 (틀) ─────────────────────────────────────────────────────────────

def execute_rag(state: GraphState):
    # TODO: RAG 로직 구현
    state.add_ai_message("RAG 검색 결과를 바탕으로 답변드립니다. (구현 예정)")
    return state

def execute_light_response(state: GraphState):
    # TODO: Light Response 로직 구현
    state.add_ai_message("가벼운 응답입니다. (구현 예정)")
    return state

def handle_unclear(state: GraphState):
    state.add_ai_message("죄송합니다. 말씀하신 내용을 정확히 이해하지 못했습니다. 다시 한 번 말씀해 주시겠어요?")
    return state

def handle_error(state: GraphState):
    state.add_ai_message("시스템 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.")
    return state

# ── 파라미터 업데이트 노드 ─────────────────────────────────────────────────────────────
async def update_parameters(state: GraphState):
    action_data = getattr(state, "action", {})
    
    updates = {}
    
    agent_name = action_data.get("agent_name")
    current_agent = state.current_agent
    if agent_name:
        current_agent = agent_name
        updates["current_agent"] = current_agent
    
    if current_agent and isinstance(action_data, dict):
        collected_parameters = dict(state.collected_parameters)
        
        if current_agent not in collected_parameters:
            collected_parameters[current_agent] = {}
        else:
            collected_parameters[current_agent] = dict(collected_parameters[current_agent])
            
        if action_data.get("parameters"):
            collected_parameters[current_agent].update(action_data["parameters"])

        if action_data.get("action") == "new_agent":
            collected_parameters[current_agent].setdefault("intent_confirmed", True)

        updates["collected_parameters"] = collected_parameters

    # ── new_task True이면 task 생성 ──────────────────────────────
    if action_data.get("new_task") is True:
        agent_info = AGENT_REGISTRY.get(current_agent, {})
        task_id = await create_task(
            user_id=state.chat_context.get("user_id", ""),
            user_name=state.chat_context.get("user_name", ""),
            agent_name=current_agent,
            agent_display_name=agent_info.get("display_name", current_agent),
            parameters=collected_parameters.get(current_agent, {}),
        )
        updates["task_id"] = task_id
        print(f"[Task] 새 task 생성: {task_id} ({current_agent})")

    return updates

# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

def route_by_action(state: GraphState):
    action_data = getattr(state, "action", {})
    if not isinstance(action_data, dict):
        return "handle_unclear"

    action_type = action_data.get("action")

    if action_type == "rag":
        return "execute_rag"
    elif action_type == "light_response":
        return "execute_light_response"
    elif action_type in ("new_agent", "continue_task"):
        return "extract_parameters"  # ← 파라미터 추출 노드로

    return "handle_unclear"

def route_after_update_parameters(state: GraphState):
    agent_name = state.current_agent
    # 다음을 위해 반드시 에이전트 이름과 노드 이름이 일치해야 함
    if agent_name in AGENT_REGISTRY:
        return agent_name
    return "handle_error"

# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_main_graph():
    from langgraph.graph import StateGraph, START, END
    from src.workflow.graph_infra.state import GraphState
    from src.workflow.main_graph.nodes.action_classifier import classify_agent_action
    from src.workflow.main_graph.nodes.parameter_extractor import extract_parameters
    from src.workflow.main_graph.nodes.response_sender import send_response
    from src.agent.agent_config import AGENT_REGISTRY

    workflow = StateGraph(GraphState)

    # ── 고정 노드 등록 ──────────────────────────────────────────────
    workflow.add_node("classify_agent_action", classify_agent_action)
    workflow.add_node("extract_parameters", extract_parameters)       # ← 추가
    workflow.add_node("execute_rag", execute_rag)
    workflow.add_node("execute_light_response", execute_light_response)
    workflow.add_node("update_parameters", update_parameters)
    workflow.add_node("handle_unclear", handle_unclear)
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("send_response", send_response)

    # ── 에이전트 노드 자동 등록 ─────────────────────────────────────
    for agent_name, agent_info in AGENT_REGISTRY.items():
        workflow.add_node(agent_name, agent_info["graph"])
        workflow.add_edge(agent_name, "send_response")

    # ── 엣지 ──────────────────────────────────────────────────────
    workflow.add_edge(START, "classify_agent_action")

    workflow.add_conditional_edges(
        "classify_agent_action",
        route_by_action,
        {
            "extract_parameters": "extract_parameters",  # ← new_agent, continue_task 둘 다
            "execute_rag": "execute_rag",
            "execute_light_response": "execute_light_response",
            "handle_unclear": "handle_unclear",
        },
    )

    # extract_parameters → update_parameters (항상)
    workflow.add_edge("extract_parameters", "update_parameters")

    # update_parameters → 에이전트 (자동 라우팅)
    agent_routing = {name: name for name in AGENT_REGISTRY}
    agent_routing["handle_error"] = "handle_error"
    workflow.add_conditional_edges("update_parameters", route_after_update_parameters, agent_routing)

    for node_name in ["execute_rag", "execute_light_response", "handle_unclear", "handle_error"]:
        workflow.add_edge(node_name, "send_response")

    workflow.add_edge("send_response", END)

    return workflow.compile(checkpointer=checkpointer)


# ── 싱글톤 그래프 인스턴스 ────────────────────────────────────────────────────
main_graph = build_main_graph()