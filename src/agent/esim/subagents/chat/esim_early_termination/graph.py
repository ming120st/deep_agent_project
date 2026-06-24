"""
src/agent/esim/subagents/chat/esim_early_termination/graph.py

eSIM 중도해지 에이전트 그래프.

흐름도:
  START ──[router]──► request_orderid     ──► END  (주문번호 입력 대기)
                  ──► fetch_order          ──[route_after_fetch]──► select_iccid
                                                                ──► END (실패)
                  ──► select_iccid         ──[route_after_select_iccid]──► validate_iccid  (2개 이상, 목록 표시 후 대기)
                                                                       ──► request_termination (1개 자동 선택)
                                                                       ──► END
                  ──► validate_iccid       ──[route_after_validate_iccid]──► request_termination (검증 성공)
                                                                         ──► select_iccid       (검증 실패, 재선택)
                  ──► request_termination  ──► END  (처리 완료/실패)
                  ──► cancel_inquiry       ──► END
"""
from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.workflow.graph_infra.state import GraphState
from src.agent.esim.subagents.chat.esim_early_termination.node import (
    cancel_inquiry,
    fetch_order,
    request_orderid,
    request_termination,
    select_iccid,
    validate_iccid,
)

AGENT_NAME = "esim_early_termination"


# ── 진입점 라우터 ─────────────────────────────────────────────────────────────

def route_esim_early_termination(state: GraphState) -> Literal[
    "request_orderid",
    "fetch_order",
    "select_iccid",
    "validate_iccid",
    "request_termination",
    "cancel_inquiry",
    "end",
]:
    """
    매 턴 START에서 호출되는 단일 진입 라우터.
    collected_parameters["esim_early_termination"] 상태를 보고 다음 노드를 결정.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})

    if state.error_message:
        return "end"

    # 업무 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 1단계: 주문번호 수집
    if not params.get("order_id"):
        return "request_orderid"

    # 2단계: 주문 조회 (order_products 미설정 = 아직 조회 안 됨)
    if "order_products" not in params:
        return "fetch_order"

    # 2.1단계: iccid 목록 표시 후 사용자 응답 대기 중 → validate_iccid
    if params.get("iccid_list_shown") and "selected_sims" not in params:
        return "validate_iccid"

    # 2.2단계: iccid 미선택 (안전 폴백) → select_iccid
    if "selected_sims" not in params:
        return "select_iccid"

    # 3단계: 중도해지 처리 (termination_completed 미설정 = 아직 처리 안 됨)
    if "termination_completed" not in params:
        return "request_termination"

    return "end"


# ── 중간 라우터들 ─────────────────────────────────────────────────────────────

def route_after_fetch(state: GraphState) -> Literal["select_iccid", "end"]:
    """fetch_order 완료 후 분기. 성공 시 order_products 설정됨."""
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "select_iccid" if "order_products" in params else "end"


def route_after_select_iccid(
    state: GraphState,
) -> Literal["validate_iccid", "request_termination", "end"]:
    """
    select_iccid 완료 후 분기.
    - 상품 1개 자동 선택 완료(selected_sims 설정됨) → request_termination
    - 상품 2개 이상, 목록 표시 후 대기(iccid_list_shown=True) → END (다음 턴에 validate_iccid 진입)
    """
    params = state.collected_parameters.get(AGENT_NAME, {})

    # 1개 상품 자동 선택 완료 → 바로 처리
    if "selected_sims" in params:
        return "request_termination"

    # 목록 표시 후 대기
    if params.get("iccid_list_shown"):
        return "end"

    return "end"


def route_after_validate_iccid(
    state: GraphState,
) -> Literal["request_termination", "select_iccid"]:
    """
    validate_iccid 완료 후 분기.
    - 검증 성공(selected_sims 설정됨) → request_termination
    - 검증 실패(iccid_list_shown=False, selected_sims 없음) → select_iccid 재실행
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "request_termination" if "selected_sims" in params else "select_iccid"


# ── 그래프 구성 ───────────────────────────────────────────────────────────────

_NODE_MAP = {
    "request_orderid":      "request_orderid",
    "fetch_order":          "fetch_order",
    "select_iccid":         "select_iccid",
    "validate_iccid":       "validate_iccid",
    "request_termination":  "request_termination",
    "cancel_inquiry":       "cancel_inquiry",
    "end":                  END,
}

workflow = StateGraph(GraphState)

workflow.add_node("request_orderid",     request_orderid)
workflow.add_node("fetch_order",         fetch_order)
workflow.add_node("select_iccid",        select_iccid)
workflow.add_node("validate_iccid",      validate_iccid)
workflow.add_node("request_termination", request_termination)
workflow.add_node("cancel_inquiry",      cancel_inquiry)

# 진입점: 단일 라우터
workflow.add_conditional_edges(START, route_esim_early_termination, _NODE_MAP)

# 사용자 입력 대기 노드 → END
workflow.add_edge("request_orderid", END)

# 자동 진행 체인 (중간 라우터로 분기)
workflow.add_conditional_edges(
    "fetch_order",
    route_after_fetch,
    {"select_iccid": "select_iccid", "end": END},
)
workflow.add_conditional_edges(
    "select_iccid",
    route_after_select_iccid,
    {
        "validate_iccid":     "validate_iccid",
        "request_termination": "request_termination",
        "end": END,
    },
)
workflow.add_conditional_edges(
    "validate_iccid",
    route_after_validate_iccid,
    {"request_termination": "request_termination", "select_iccid": "select_iccid"},
)

# 중도해지 처리 / 취소 → END
workflow.add_edge("request_termination", END)
workflow.add_edge("cancel_inquiry", END)

esim_early_termination_graph = workflow.compile()
