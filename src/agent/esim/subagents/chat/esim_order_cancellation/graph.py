from typing import Literal
from langgraph.graph import StateGraph, START, END
from src.workflow.graph_infra.state import GraphState
from src.agent.esim.subagents.chat.esim_order_cancellation.node import (
    ask_order_id,       # 주문번호 요청
    fetch_order,        # 주문 조회 (자동 진행)
    confirm_cancelable, # 취소 가능 안내 + 진행 의사 확인
    handle_non_cancelable, # 취소 불가 안내
    cancel_inquiry,     # 취소 중단 안내
    cancel_order,       # 취소 API 호출
)

# --------------------------------------------------
# 이심 주문취소 에이전트 (esim_order_cancellation)
#
# 흐름도:
#   START ──[route_esim]──► ask_order_id ──► END (주문번호 대기)
#                        ──► fetch_order ──[route_after_fetch]──► confirm_cancelable ──► END (의사 대기)
#                                                              ──► handle_non_cancelable ──► END
#                                                              ──► END (조회 실패, 다음 턴 retry)
#                        ──► cancel_inquiry ──► END
#                        ──► cancel_order ──► END
# --------------------------------------------------

AGENT_NAME = "esim_order_cancellation"


# ── 진입점 라우터 ─────────────────────────────────────────────────────────────

def route_esim_order_cancellation(state: GraphState) -> Literal[
    "ask_order_id",
    "fetch_order",
    "confirm_cancelable",
    "handle_non_cancelable",
    "cancel_inquiry",
    "cancel_order",
    "end",
]:
    """
    START에서 호출되는 단일 진입 라우터.
    collected_parameters["esim_order_cancellation"] 상태를 보고 다음 노드 결정.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})

    # 시스템 에러 시 즉시 종료
    if state.error_message:
        return "end"

    # 업무 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 1단계: 주문번호 수집
    if not params.get("order_id"):
        return "ask_order_id"

    # 2단계: 주문 조회 (cancelable 미설정 = 아직 조회 안 됨)
    if "cancelable" not in params:
        return "fetch_order"

    # 3단계: 취소 불가
    if not params.get("cancelable"):
        return "handle_non_cancelable"

    # 4단계: 취소 진행 의사 확인 (cancel_confirmed 미설정 = 아직 답변 없음)
    if params.get("cancel_confirmed") is None:
        return "confirm_cancelable"

    # 취소 포기
    if not params.get("cancel_confirmed"):
        return "cancel_inquiry"

    # 5단계: 취소 API 호출 (cancel_success 미설정 = 아직 처리 안 됨)
    if "cancel_success" not in params:
        return "cancel_order"

    return "end"


# ── fetch_order 이후 라우터 ───────────────────────────────────────────────────

def route_after_fetch(state: GraphState) -> Literal[
    "confirm_cancelable",
    "handle_non_cancelable",
    "end",
]:
    """
    fetch_order 완료 후 조회 결과에 따라 분기.
    조회 실패 시 order_id가 초기화되므로 cancelable 키가 없음 → END.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})

    if "cancelable" not in params:
        # 조회 실패: 에러 메시지 이미 전송됨, 다음 턴 진입 시 ask_order_id로 재라우팅
        return "end"

    return "confirm_cancelable" if params.get("cancelable") else "handle_non_cancelable"


# ── 그래프 구성 ───────────────────────────────────────────────────────────────

_NODE_MAP = {
    "ask_order_id":         "ask_order_id",
    "fetch_order":          "fetch_order",
    "confirm_cancelable":   "confirm_cancelable",
    "handle_non_cancelable":"handle_non_cancelable",
    "cancel_inquiry":       "cancel_inquiry",
    "cancel_order":         "cancel_order",
    "end":                  END,
}

workflow = StateGraph(GraphState)

# 노드 등록
workflow.add_node("ask_order_id",          ask_order_id)
workflow.add_node("fetch_order",           fetch_order)
workflow.add_node("confirm_cancelable",    confirm_cancelable)
workflow.add_node("handle_non_cancelable", handle_non_cancelable)
workflow.add_node("cancel_inquiry",        cancel_inquiry)
workflow.add_node("cancel_order",          cancel_order)

# 진입점: 단일 라우터
workflow.add_conditional_edges(START, route_esim_order_cancellation, _NODE_MAP)

# 사용자 입력 대기 노드 → END
workflow.add_edge("ask_order_id",        END)
workflow.add_edge("confirm_cancelable",  END)

# fetch_order: 사용자 입력 없이 자동 진행 → 조회 결과에 따라 분기
workflow.add_conditional_edges(
    "fetch_order",
    route_after_fetch,
    {
        "confirm_cancelable":    "confirm_cancelable",
        "handle_non_cancelable": "handle_non_cancelable",
        "end":                   END,
    },
)

# 업무 종료 노드 → END
workflow.add_edge("handle_non_cancelable", END)
workflow.add_edge("cancel_inquiry",        END)
workflow.add_edge("cancel_order",          END)

esim_order_cancellation_graph = workflow.compile()
