"""
src/agent/chat/esim_fault_replacement/graph.py

이심 재발급 에이전트 그래프.

흐름도:
  START ──[router]──► request_parameters   ──► END  (파라미터 입력 대기)
                   ──► fetch_order         ──[route_after_fetch]──► select_iccid
                                                                 ──► END (실패)
                   ──► select_iccid        ──[route_after_select_iccid]──► select_product (자동 선택)
                                                                        ──► END (목록 표시 후 대기)
                   ──► validate_iccid      ──[route_after_validate_iccid]──► select_product (검증 성공)
                                                                          ──► select_iccid  (검증 실패, 재선택)
                   ──► select_product      ──[route_after_select]──► fetch_product_details
                                                                  ──► END (국가 입력 대기 / 실패)
                   ──► fetch_product_details ──[route_after_details]──► confirm_payload
                                                                     ──► END (실패)
                   ──► confirm_payload     ──► END  (컨펌 입력 대기)
                   ──► process_confirmation ──[route_after_confirmation]──► send_esim
                                                                        ──► confirm_payload
                   ──► send_esim           ──[route_after_send]──► register_consultation (성공)
                                                                ──► END (실패, 프로세스 종료)
                   ──► register_consultation ──► END  (device_agent 콜백 대기)
                   ──► cancel_inquiry      ──► END
"""
from typing import Literal

from langgraph.graph import END, START, StateGraph

from src.workflow.graph_infra.state import GraphState
from src.agent.chat.esim_fault_replacement.node import (
    cancel_inquiry,
    confirm_payload,
    fetch_order,
    fetch_product_details,
    process_confirmation,
    register_consultation,
    request_parameters,
    select_iccid,
    select_product,
    send_esim,
    validate_iccid,
)

AGENT_NAME = "esim_fault_replacement"


# ── 진입점 라우터 ─────────────────────────────────────────────────────────────

def route_esim_fault_replacement(state: GraphState) -> Literal[
    "request_parameters",
    "fetch_order",
    "select_iccid",
    "validate_iccid",
    "select_product",
    "fetch_product_details",
    "confirm_payload",
    "process_confirmation",
    "send_esim",
    "register_consultation",
    "cancel_inquiry",
    "end",
]:
    """
    매 턴 START에서 호출되는 단일 진입 라우터.
    collected_parameters["esim_fault_replacement"] 상태를 보고 다음 노드를 결정.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})

    if state.error_message:
        return "end"

    # 업무 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 3.1: 기본 파라미터 수집 (order_id, request_days)
    if not (params.get("order_id") and params.get("request_days")):
        return "request_parameters"

    # 3.2: 주문 조회 (order_products 미설정 = 아직 조회 안 됨)
    if "order_products" not in params:
        return "fetch_order"

    # 3.2.1: iccid 목록 표시 후 사용자 응답 대기 중 → validate_iccid
    if params.get("iccid_list_shown") and "selected_iccids" not in params:
        return "validate_iccid"

    # 3.2.1: iccid 미선택 (안전 폴백) → select_iccid
    if "selected_iccids" not in params:
        return "select_iccid"

    # 3.3: 상품 선정 (raw_product_list 미설정 = 아직 선정 안 됨)
    if "raw_product_list" not in params:
        return "select_product"

    # 3.4: 상품 상세 조회 (product_list 미설정 / 안전 폴백)
    if "product_list" not in params:
        return "fetch_product_details"

    # 3.5.2: payload 표시 후 사용자 응답 대기 중 → process_confirmation
    if params.get("payload_shown") and not params.get("payload_confirmed"):
        return "process_confirmation"

    # 3.5.1+3.5.2: payload 미표시 (최초 또는 modify/change 후) → confirm_payload
    if not params.get("payload_confirmed"):
        return "confirm_payload"

    # 3.6: 이심 발송 (send_success 미설정 / 안전 폴백)
    if "send_success" not in params:
        return "send_esim"

    # 3.7: 상담 등록 (job_done 미설정 = 아직 처리 안 됨)
    if params.get("send_success") and "job_done" not in params:
        return "register_consultation"

    return "end"


# ── 중간 라우터들 ─────────────────────────────────────────────────────────────

def route_after_fetch(state: GraphState) -> Literal["select_iccid", "end"]:
    """fetch_order 완료 후 분기. 성공 시 order_products 설정됨."""
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "select_iccid" if "order_products" in params else "end"


def route_after_select_iccid(
    state: GraphState,
) -> Literal["select_product", "end"]:
    """
    select_iccid 완료 후 분기.
    자동 선택 완료(selected_iccids 설정됨) → select_product 자동 진행.
    목록 표시 후 대기 → END.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "select_product" if "selected_iccids" in params else "end"


def route_after_validate_iccid(
    state: GraphState,
) -> Literal["select_product", "select_iccid"]:
    """
    validate_iccid 완료 후 분기.
    검증 성공(selected_iccids 설정됨) → select_product 자동 진행.
    검증 실패(iccid_list_shown=False) → select_iccid 재실행.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "select_product" if "selected_iccids" in params else "select_iccid"


def route_after_select(
    state: GraphState,
) -> Literal["fetch_product_details", "end"]:
    """
    select_product 완료 후 분기.
    raw_product_list 설정됨 → fetch_product_details 자동 진행.
    미설정 (국가 입력 대기 / 오류) → END.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "fetch_product_details" if "raw_product_list" in params else "end"


def route_after_details(
    state: GraphState,
) -> Literal["confirm_payload", "end"]:
    """fetch_product_details 완료 후 분기. 성공 시 product_list 설정됨."""
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "confirm_payload" if "product_list" in params else "end"


def route_after_confirmation(
    state: GraphState,
) -> Literal["send_esim", "confirm_payload"]:
    """
    process_confirmation 완료 후 분기.
    confirm → send_esim 자동 진행.
    modify / change_product → confirm_payload 재표시.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "send_esim" if params.get("payload_confirmed") else "confirm_payload"


def route_after_send(
    state: GraphState,
) -> Literal["register_consultation", "end"]:
    """
    send_esim 완료 후 분기.
    전체 성공(send_success=True) → register_consultation 자동 진행.
    실패(프로세스 종료) → END.
    """
    params = state.collected_parameters.get(AGENT_NAME, {})
    return "register_consultation" if params.get("send_success") is True else "end"


# ── 그래프 구성 ───────────────────────────────────────────────────────────────

_NODE_MAP = {
    "request_parameters":    "request_parameters",
    "fetch_order":           "fetch_order",
    "select_iccid":          "select_iccid",
    "validate_iccid":        "validate_iccid",
    "select_product":        "select_product",
    "fetch_product_details": "fetch_product_details",
    "confirm_payload":       "confirm_payload",
    "process_confirmation":  "process_confirmation",
    "send_esim":             "send_esim",
    "register_consultation": "register_consultation",
    "cancel_inquiry":        "cancel_inquiry",
    "end":                   END,
}

workflow = StateGraph(GraphState)

workflow.add_node("request_parameters",    request_parameters)
workflow.add_node("fetch_order",           fetch_order)
workflow.add_node("select_iccid",          select_iccid)
workflow.add_node("validate_iccid",        validate_iccid)
workflow.add_node("select_product",        select_product)
workflow.add_node("fetch_product_details", fetch_product_details)
workflow.add_node("confirm_payload",       confirm_payload)
workflow.add_node("process_confirmation",  process_confirmation)
workflow.add_node("send_esim",             send_esim)
workflow.add_node("register_consultation", register_consultation)
workflow.add_node("cancel_inquiry",        cancel_inquiry)

# 진입점: 단일 라우터
workflow.add_conditional_edges(START, route_esim_fault_replacement, _NODE_MAP)

# 사용자 입력 대기 노드 → END
workflow.add_edge("request_parameters", END)
workflow.add_edge("confirm_payload",    END)

# 자동 진행 체인 (중간 라우터로 분기)
workflow.add_conditional_edges(
    "fetch_order",
    route_after_fetch,
    {"select_iccid": "select_iccid", "end": END},
)
workflow.add_conditional_edges(
    "select_iccid",
    route_after_select_iccid,
    {"select_product": "select_product", "end": END},
)
workflow.add_conditional_edges(
    "validate_iccid",
    route_after_validate_iccid,
    {"select_product": "select_product", "select_iccid": "select_iccid"},
)
workflow.add_conditional_edges(
    "select_product",
    route_after_select,
    {"fetch_product_details": "fetch_product_details", "end": END},
)
workflow.add_conditional_edges(
    "fetch_product_details",
    route_after_details,
    {"confirm_payload": "confirm_payload", "end": END},
)

# 컨펌 루프
workflow.add_conditional_edges(
    "process_confirmation",
    route_after_confirmation,
    {"send_esim": "send_esim", "confirm_payload": "confirm_payload"},
)

# 발송 후 분기 (실패 시 프로세스 종료)
workflow.add_conditional_edges(
    "send_esim",
    route_after_send,
    {"register_consultation": "register_consultation", "end": END},
)

# 상담 등록 / 취소 → END
workflow.add_edge("register_consultation", END)
workflow.add_edge("cancel_inquiry",        END)

esim_fault_replacement_graph = workflow.compile()
