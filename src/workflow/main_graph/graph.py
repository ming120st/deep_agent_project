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
def update_parameters(state: GraphState):
    action_data = getattr(state, "action", {})
    
    updates = {}
    
    # agent_name 업데이트
    agent_name = action_data.get("agent_name")
    current_agent = state.current_agent
    if agent_name:
        current_agent = agent_name
        updates["current_agent"] = current_agent
    
    # 현재 에이전트가 설정되어 있고 파라미터가 있다면 업데이트
    if current_agent and isinstance(action_data, dict) and action_data.get("parameters"):
        # 기존 파라미터 복사 (LangGraph 상태 업데이트를 위해)
        collected_parameters = dict(state.collected_parameters)
        
        # 해당 에이전트의 파라미터 딕셔너리가 없으면 초기화
        if current_agent not in collected_parameters:
            collected_parameters[current_agent] = {}
        else:
            # 내부 딕셔너리도 복사하여 안전하게 업데이트
            collected_parameters[current_agent] = dict(collected_parameters[current_agent])
            
        # 파라미터 병합
        collected_parameters[current_agent].update(action_data["parameters"])
        updates["collected_parameters"] = collected_parameters
    
    # 상태 업데이트를 위해 딕셔너리 반환
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
    elif action_type == "new_agent":
        agent_name = action_data.get("agent_name")
        if agent_name:
            # new_agent인 경우, 해당 에이전트를 현재 에이전트로 설정하고 파라미터 업데이트 노드로 보냄
            return "update_parameters"
        return "handle_error"
    elif action_type == "parameter":
        return "update_parameters"
        
    return "handle_unclear"

def route_after_update_parameters(state: GraphState):
    agent_name = state.current_agent
    # 다음을 위해 반드시 에이전트 이름과 노드 이름이 일치해야 함
    if agent_name in AGENT_REGISTRY:
        return agent_name
    return "handle_error"

# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_main_graph():
    workflow = StateGraph(GraphState)

    # ── 고정 노드 등록 ──────────────────────────────────────────────
    workflow.add_node("classify_agent_action", classify_agent_action)
    workflow.add_node("execute_rag", execute_rag)
    workflow.add_node("execute_light_response", execute_light_response)
    workflow.add_node("update_parameters", update_parameters)
    workflow.add_node("handle_unclear", handle_unclear)
    workflow.add_node("handle_error", handle_error)
    workflow.add_node("send_response", send_response)

    # ── 에이전트 노드 자동 등록 (AGENT_REGISTRY 기반) ────────────────
    for agent_name, agent_info in AGENT_REGISTRY.items():
        workflow.add_node(agent_name, agent_info["graph"])  # AGENT_REGISTRY에 graph 필드 추가 필요
        workflow.add_edge(agent_name, "send_response")      # 엣지도 자동 등록

    # ── 엣지: START → 의도 분류 ────────────────────────────────────
    workflow.add_edge(START, "classify_agent_action")

    # ── 엣지: 의도 분류 → 실행 노드 ───────────────────────────────
    workflow.add_conditional_edges(
        "classify_agent_action",
        route_by_action,
        {
            "execute_rag": "execute_rag",
            "execute_light_response": "execute_light_response",
            "update_parameters": "update_parameters",
            "handle_unclear": "handle_unclear",
            "handle_error": "handle_error",
        },
    )

    # ── 엣지: 파라미터 업데이트 → 에이전트 실행 (자동) ────────────
    agent_routing = {name: name for name in AGENT_REGISTRY}
    agent_routing["handle_error"] = "handle_error"
    workflow.add_conditional_edges("update_parameters", route_after_update_parameters, agent_routing)

    # ── 고정 엣지 ──────────────────────────────────────────────────
    for node_name in ["execute_rag", "execute_light_response", "handle_unclear", "handle_error"]:
        workflow.add_edge(node_name, "send_response")

    workflow.add_edge("send_response", END)

    return workflow.compile()


# ── 싱글톤 그래프 인스턴스 ────────────────────────────────────────────────────
main_graph = build_main_graph()