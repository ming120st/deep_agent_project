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
  ┌──────────┬───────────────┼──────────────┬──────────────┐
  │new_agent │ continue_task │     rag      │light_response│
       ▼           ▼                ▼              ▼       │
     execute_   update_        execute_rag  execute_light_ │
     <agent>  parameters                       response    │
  │          │               │              │              │
  │                ▼         │              │              │              
  │           [route_after_  │              │              │
  │           update_params] │              │              │
  │          │               │              │              │
  └──────────┴───────────────┴──────────────┴──────────────┘
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
from src.agent.registry import AGENT_REGISTRY
from src.core.task_manager import create_task, TaskStatus
from src.core.mongo_client import checkpointer
from src.core.mongo_client import boa_agents_collection

# ── 임시 노드 (틀) ─────────────────────────────────────────────────────────────

def execute_rag(state: GraphState):
    # TODO: RAG 로직 구현
    state.add_ai_message("RAG 검색 결과를 바탕으로 답변드립니다. (구현 예정)")
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

    collected_parameters = dict(state.collected_parameters)  # 기존 파라미터 복사
    
    agent_name = action_data.get("agent_name")
    current_agent = state.current_agent
    if agent_name:
        current_agent = agent_name
        updates["current_agent"] = current_agent
    
    if current_agent and isinstance(action_data, dict):        
        if current_agent not in collected_parameters:
            collected_parameters[current_agent] = {}
        else:
            collected_parameters[current_agent] = dict(collected_parameters[current_agent])
            
        if action_data.get("parameters"):
            collected_parameters[current_agent].update(action_data["parameters"])

        if action_data.get("action") == "new_agent":
            collected_parameters[current_agent].setdefault("intent_confirmed", True)

        updates["collected_parameters"] = collected_parameters

    if action_data.get("action") == "new_agent":

        agent_doc = await boa_agents_collection.find_one({"agent_id": current_agent})
        if agent_doc and not agent_doc.get("enabled", True):
            msg = "죄송합니다. 현재 해당 에이전트는 사용이 중지되어 있습니다. AI팀에 문의해 주세요."
            state.add_ai_message(msg)  # state.messages 뮤테이션
            return {
                "messages": state.messages,  # 뮤테이션된 messages 전체를 반환에 포함
                "current_agent": None,       # 라우팅 차단
            }

    # ── new_task True이면 task 생성 ──────────────────────────────
    if action_data.get("new_task") is True:
        updates["error_message"] = None # 새로운 업무 시작이므로 기존 오류 메시지 초기화. 안하면 그래프 시작과 동시에 에러로 라우팅 됨
        updates["task_status"] = None
        updates["response"] = {}

        # 새 태스크니까 이전 플래그(chat_sent, email_sent 등) 초기화
        collected_parameters[current_agent] = {}
        if action_data.get("parameters"):
            collected_parameters[current_agent].update(action_data["parameters"])
        updates["collected_parameters"] = collected_parameters

        agent_info = AGENT_REGISTRY.get(current_agent, {})
        task_id = await create_task(
            user_id=state.chat_context.get("user_id", ""),
            user_name=state.chat_context.get("user_name", ""),
            agent_name=current_agent,
            agent_display_name=agent_info.get("display_name", current_agent),
            parameters={},
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

# llm이 의도 분류 후 반환하는 에이전트 이름이 AGENT_REGISTRY에 등록된 이름과 일치해야 extract_parameters → update_parameters → 세부 에이전트 노드로 라우팅됨
def route_after_update_parameters(state: GraphState):
    agent_name = state.current_agent
    # 다음을 위해 반드시 세부 에이전트 이름과 노드 이름이 일치해야 함
    if agent_name in AGENT_REGISTRY:
        return agent_name
    return "handle_error"

# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_main_graph():
    # 순환참조 방지 위해 그래프 빌더 내부에서 임포트
    from src.workflow.main_graph.nodes.parameter_extractor import extract_parameters
    from src.workflow.main_graph.nodes.light_response import execute_light_response
    from src.agent.registry import AGENT_REGISTRY

    # 그래프 인스턴스 생성
    workflow = StateGraph(GraphState)

    # ── 고정 노드 등록 ──────────────────────────────────────────────
    workflow.add_node("classify_agent_action", classify_agent_action)
    workflow.add_node("extract_parameters", extract_parameters)
    workflow.add_node("execute_rag", execute_rag)
    workflow.add_node("execute_light_response", execute_light_response)
    workflow.add_node("update_parameters", update_parameters)
    workflow.add_node("handle_unclear", handle_unclear)
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("send_response", send_response)

    # ── 에이전트 노드/엣지 자동 등록 ─────────────────────────────────────
    for agent_name, agent_info in AGENT_REGISTRY.items():
        workflow.add_node(agent_name, agent_info["graph"]) # 각 세부 에이전트의 이름으로 StateGraph를 노드에 등록
        workflow.add_edge(agent_name, "send_response") # 세부 에이전트 실행 후 응답 노드로 연결

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
    agent_routing = {name: name for name in AGENT_REGISTRY} # AGENT_REGISTRY에 등록된 에이전트 이름을 노드 이름으로 라우팅
    agent_routing["handle_error"] = "handle_error"
    workflow.add_conditional_edges("update_parameters", route_after_update_parameters, agent_routing)
    # 위 3줄은 그 위의 add_conditional_edges와 사실 같은 방식으로 라우팅 하고 있는건데, 에이전트 레지스트리 기반으로 자동 라우팅하기 위해 표현이 달라진 것뿐임.

    for node_name in ["execute_rag", "execute_light_response", "handle_unclear", "handle_error"]:
        workflow.add_edge(node_name, "send_response")

    workflow.add_edge("send_response", END)

    return workflow.compile(checkpointer=checkpointer)


# ── 싱글톤 그래프 인스턴스 ────────────────────────────────────────────────────
main_graph = build_main_graph()