"""
메인 대화 그래프 (Main Conversation Graph)

Google Chat 메시지 수신부터 응답 전송까지의 전체 대화 흐름을
하나의 LangGraph StateGraph로 관리합니다.

────────────────────────────────────────────────────────────────
  플로우 다이어그램

  START
    │
    ▼ (항상 의도 분류 먼저)
  [classify_intent]  ←── LLM 의도 분류
    │
    ▼ [route_by_intent] — intent + current_agent 조합으로 판단
  ┌──────────────────────────────────────────────────────────────┐
  │  활성 에이전트(current_agent) 있음                            │
  │  + intent가 명확한 전환 신호가 아님(rag/out_of_scope 아님)    │
  │  → 에이전트 계속 진행                                         │
  └──────────────────────┬───────────────────────────────────────┘
                         │ (명확한 의도 전환이거나 활성 에이전트 없음)
  ┌──────────┬───────────┼──────────────┬──────────────┐
  │wifi      │ chitchat  │ rag_search   │unclear /     │
  │_inquiry  │           │              │out_of_scope  │
  │          │           │              │/ error       │
  ▼          ▼           ▼              ▼              ▼
execute_  execute_   execute_rag   handle_*        handle_
wifi      chitchat   _search       nodes           error
  │          │           │              │              │
  └──────────┴───────────┴──────────────┴──────────────┘
                              │
                        [send_response]
                              │
                            END
────────────────────────────────────────────────────────────────

예외 처리:
  - 의도 분류 실패 (JSON 파싱 오류) → intent="unclear" → handle_unclear
  - LLM 호출 오류 → intent="error" → handle_error → send_response
  - 서브그래프 실행 오류 → 노드 내부에서 에러 메시지 추가 → send_response
  - 응답 전송 실패 → 로그만 기록, 상태는 정상 반환
  - 전체 그래프 오류 → orchestrator에서 최종 catch
"""
from typing import Literal

from langgraph.graph import StateGraph, START, END

from src.workflow.graph_infra.state import GraphState
from workflow.main_graph.nodes.action_classifier import classify_agent_action
from src.workflow.main_graph.nodes.executor import (
    execute_wifi_inquiry,
    execute_chitchat,
    execute_rag_search,
    handle_unclear,
    handle_out_of_scope,
    handle_error,
)
from src.workflow.main_graph.nodes.response_sender import send_response


# ── 라우팅 함수 ───────────────────────────────────────────────────────────────

# 활성 에이전트에서 명확한 의도 전환으로 판단할 intent 목록
# (이 경우에만 현재 에이전트를 중단하고 새 의도로 전환)
_INTENT_FORCE_SWITCH = {"rag_search", "out_of_scope"}


def route_by_intent(
    state: GraphState,
) -> Literal[
    "execute_wifi_inquiry",
    "execute_chitchat",
    "execute_rag_search",
    "handle_unclear",
    "handle_out_of_scope",
    "handle_error",
]:
    """
    classify_intent 결과와 current_agent 상태를 함께 고려하여 실행 노드를 결정합니다.

    판단 로직:
      1. 오류 발생 시 → handle_error
      2. 활성 에이전트가 있고 intent가 명확한 전환 신호(rag_search, out_of_scope)가
         아닌 경우 → 현재 에이전트 계속 진행
         (chitchat/unclear = 에이전트 질문에 답하는 중으로 간주)
      3. 활성 에이전트 없음 또는 명확한 의도 전환 → intent 기반 표준 라우팅

    ※ 새 에이전트 추가 시 _INTENT_FORCE_SWITCH 및 routing_map에 항목을 추가하세요.
    """
    # 1. 오류 처리
    if state.intent == "error" or (state.error_message and not state.intent):
        return "handle_error"

    intent = state.intent or "unclear"

    # 2. 활성 에이전트 유지 여부 판단
    if state.current_agent == "wifi_usage_inquiry":
        if intent not in _INTENT_FORCE_SWITCH:
            # chitchat / unclear / wifi_usage_inquiry → 에이전트 내 응답으로 간주
            return "execute_wifi_inquiry"
        # rag_search / out_of_scope → 명확한 주제 전환, 에이전트 중단
        # (current_agent 초기화는 execute_wifi_inquiry 바깥이므로,
        #  다음 메시지부터 classify_intent가 다시 판단)

    # TODO: 다른 에이전트 추가 시 위와 같은 패턴으로 elif 블록 추가

    # 3. 표준 intent 라우팅
    routing_map = {
        "wifi_usage_inquiry": "execute_wifi_inquiry",
        "chitchat":           "execute_chitchat",
        "rag_search":         "execute_rag_search",
        "out_of_scope":       "handle_out_of_scope",
        "unclear":            "handle_unclear",
    }
    return routing_map.get(intent, "handle_unclear")


# ── 그래프 빌더 ───────────────────────────────────────────────────────────────

def build_main_graph():
    """메인 대화 그래프를 구성하고 컴파일하여 반환합니다."""
    workflow = StateGraph(GraphState)

    # ── 노드 등록 ──────────────────────────────────────────────────────────
    workflow.add_node("classify_agent_action", classify_agent_action)
    workflow.add_node("execute_wifi_inquiry",  execute_wifi_inquiry)
    workflow.add_node("execute_chitchat",      execute_chitchat)
    workflow.add_node("execute_rag_search",    execute_rag_search)
    workflow.add_node("handle_unclear",        handle_unclear)
    workflow.add_node("handle_out_of_scope",   handle_out_of_scope)
    workflow.add_node("handle_error",          handle_error)
    workflow.add_node("send_response",         send_response)

    # ── 엣지: START → 의도 분류 (항상) ────────────────────────────────────
    workflow.add_edge(START, "classify_agent_action")

    # ── 엣지: 의도 분류 → 실행 노드 ───────────────────────────────────────
    workflow.add_conditional_edges(
        "classify_agent_action",
        route_by_intent,
        {
            "execute_wifi_inquiry": "execute_wifi_inquiry",
            "execute_chitchat":     "execute_chitchat",
            "execute_rag_search":   "execute_rag_search",
            "handle_unclear":       "handle_unclear",
            "handle_out_of_scope":  "handle_out_of_scope",
            "handle_error":         "handle_error",
        },
    )

    # ── 엣지: 모든 실행/처리 노드 → 응답 전송 ────────────────────────────
    for node_name in [
        "execute_wifi_inquiry",
        "execute_chitchat",
        "execute_rag_search",
        "handle_unclear",
        "handle_out_of_scope",
        "handle_error",
    ]:
        workflow.add_edge(node_name, "send_response")

    # ── 엣지: 응답 전송 → END ─────────────────────────────────────────────
    workflow.add_edge("send_response", END)

    return workflow.compile()


# ── 싱글톤 그래프 인스턴스 ────────────────────────────────────────────────────
main_graph = build_main_graph()
