from typing import Literal
from langgraph.graph import StateGraph, START, END
from src.workflow.graph_infra.state import GraphState
from src.agent.chat.esim_resend.node import (
    ask_order_id,     # 주문번호 요청
    fetch_order,      # 주문 조회 (자동 진행)
    show_product_list,# 상품 목록 안내 + 재발송 대상 선택 요청
    ask_send_target,  # 이메일/전화번호 요청
    cancel_inquiry,   # 업무 취소
    resend_esim,      # 재발송 API 호출
)

# --------------------------------------------------
# 이심 재발송 에이전트 (esim_resend)
#
# 흐름도:
#   START ──[router]──► ask_order_id ──► END
#                    ──► fetch_order ──[route_after_fetch]──► show_product_list ──► END
#                                                         ──► END (조회 실패 retry)
#                    ──► ask_send_target ──► END
#                    ──► cancel_inquiry ──► END
#                    ──► resend_esim ──► END
# --------------------------------------------------

AGENT_NAME = "esim_resend"


# ── 진입점 라우터 ─────────────────────────────────────────────────────────────

def route_esim_resend(state: GraphState) -> Literal[
    "ask_order_id",
    "fetch_order",
    "show_product_list",
    "ask_send_target",
    "cancel_inquiry",
    "resend_esim",
    "end",
]:
    """
    매 턴 START에서 호출되는 단일 진입 라우터.
    collected_parameters["esim_resend"] 상태를 보고 다음 노드를 결정.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})

    if state.error_message:
        return "end"

    # 업무 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 1단계: 주문번호 수집
    if not params.get("order_id"):
        return "ask_order_id"

    # 2단계: 주문 조회 (orders_detail 미설정 = 아직 조회 안 됨)
    if "orders_detail" not in params:
        return "fetch_order"

    # 3단계: 재발송 대상 선택 (selected_order_detail_ids 미설정)
    if not params.get("selected_order_detail_ids"):
        return "show_product_list"

    # 4단계: 수신처 확인 (email/mobile 둘 다 params에 없으면 아직 안 받은 것)
    if "email" not in params and "mobile" not in params:
        return "ask_send_target"

    # 5단계: 재발송 API 호출 (resend_success 미설정 = 아직 처리 안 됨)
    if "resend_success" not in params:
        return "resend_esim"

    return "end"


# ── fetch_order 이후 라우터 ───────────────────────────────────────────────────

def route_after_fetch(state: GraphState) -> Literal[
    "show_product_list",
    "end",
]:
    """
    fetch_order 완료 후 분기.
    조회 실패 시 order_id가 초기화되므로 orders_detail 키가 없음 → END.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "show_product_list" if "orders_detail" in params else "end"


# ── 그래프 구성 ───────────────────────────────────────────────────────────────

_NODE_MAP = {
    "ask_order_id":      "ask_order_id",
    "fetch_order":       "fetch_order",
    "show_product_list": "show_product_list",
    "ask_send_target":   "ask_send_target",
    "cancel_inquiry":    "cancel_inquiry",
    "resend_esim":       "resend_esim",
    "end":               END,
}

workflow = StateGraph(GraphState)

workflow.add_node("ask_order_id",      ask_order_id)
workflow.add_node("fetch_order",       fetch_order)
workflow.add_node("show_product_list", show_product_list)
workflow.add_node("ask_send_target",   ask_send_target)
workflow.add_node("cancel_inquiry",    cancel_inquiry)
workflow.add_node("resend_esim",       resend_esim)

# 진입점: 단일 라우터
workflow.add_conditional_edges(START, route_esim_resend, _NODE_MAP)

# 사용자 입력 대기 노드 → END
workflow.add_edge("ask_order_id",      END)
workflow.add_edge("show_product_list", END)
workflow.add_edge("ask_send_target",   END)

# fetch_order: 자동 진행 → 결과에 따라 분기
workflow.add_conditional_edges(
    "fetch_order",
    route_after_fetch,
    {
        "show_product_list": "show_product_list",
        "end":               END,
    },
)

# 업무 종료 노드 → END
workflow.add_edge("cancel_inquiry", END)
workflow.add_edge("resend_esim",    END)

esim_resend_graph = workflow.compile()