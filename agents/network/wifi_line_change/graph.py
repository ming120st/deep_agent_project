from __future__ import annotations

from typing import Literal

from langgraph.graph import (
    END,
    START,
    StateGraph,
)

from agents.network.wifi_line_change.nodes import (
    apply_user_input,
    ask_missing_parameter,
    request_device_job,
    wait_for_device_job,
    handle_result,
)
from agents.network.wifi_line_change.state import (
    WifiLineChangeState,
)
from core.state.checkpointer import checkpointer


REQUIRED_PARAMETERS = (
    "device_number",
    "country_code",
    "expiry_date",
)


def route_wifi_line_change(
    state: WifiLineChangeState,
) -> Literal[
    "ask_missing_parameter",
    "request_device_job",
    "wait_for_device_job",
    "end",
]:
    """
    현재 State를 보고 다음 단계만 결정한다.
    """

    if state.get("error_message"):
        return "end"

    params = state.get(
        "parameters",
        {},
    )

    # 필수 파라미터 확인
    for parameter in REQUIRED_PARAMETERS:
        if not params.get(parameter):
            return "ask_missing_parameter"

    # 이미 완료된 작업
    if params.get("job_done"):
        return "end"

    # Device Job이 아직 생성되지 않음
    if not params.get("_job_id"):
        return "request_device_job"

    # Device Job 생성 후 결과 대기
    return "wait_for_device_job"


def route_after_device_job_request(
    state: WifiLineChangeState,
) -> Literal[
    "wait_for_device_job",
    "end",
]:
    if state.get("error_message"):
        return "end"

    return "wait_for_device_job"


def build_wifi_line_change_graph():
    """
    Wifi Line Change LangGraph 생성.
    """

    workflow = StateGraph(
        WifiLineChangeState
    )

    # ─────────────────────────────────────────
    # Nodes
    # ─────────────────────────────────────────

    workflow.add_node(
        "apply_user_input",
        apply_user_input,
    )

    workflow.add_node(
        "ask_missing_parameter",
        ask_missing_parameter,
    )

    workflow.add_node(
        "request_device_job",
        request_device_job,
    )

    workflow.add_node(
        "wait_for_device_job",
        wait_for_device_job,
    )

    workflow.add_node(
        "handle_result",
        handle_result,
    )

    # ─────────────────────────────────────────
    # Entry
    # ─────────────────────────────────────────

    workflow.add_edge(
        START,
        "apply_user_input",
    )

    # ─────────────────────────────────────────
    # Routing
    # ─────────────────────────────────────────

    workflow.add_conditional_edges(
        "apply_user_input",
        route_wifi_line_change,
        {
            "ask_missing_parameter":
                "ask_missing_parameter",

            "request_device_job":
                "request_device_job",

            "wait_for_device_job":
                "wait_for_device_job",

            "end":
                END,
        },
    )

    # 사용자 추가 입력 대기
    workflow.add_edge(
        "ask_missing_parameter",
        END,
    )

    # Device Job 제출
    workflow.add_conditional_edges(
        "request_device_job",
        route_after_device_job_request,
        {
            "wait_for_device_job":
                "wait_for_device_job",

            "end":
                END,
        },
    )

    # callback 결과 처리
    workflow.add_edge(
        "wait_for_device_job",
        "handle_result",
    )

    workflow.add_edge(
        "handle_result",
        END,
    )

    return workflow.compile(
        checkpointer=checkpointer,
    )