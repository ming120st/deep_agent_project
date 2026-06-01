"""
src/agent/chat/wifi_line_change/graph.py

와이파이 회선변경 에이전트 그래프.

흐름도:
  START ──[router]──► ask_device_number  ──► END  (입력 대기)
                   ──► ask_country_code  ──► END  (입력 대기)
                   ──► cancel_inquiry    ──► END
                   ──► request_device_job ──► handle_result ──► END
                   ──► end
"""
from typing import Literal
from langgraph.graph import StateGraph, START, END

from src.workflow.graph_infra.state import GraphState
from src.agent.wifi.subagents.chat.wifi_line_change.node import (
    ask_device_number,
    ask_country_code,
    ask_expiry_date,
    cancel_inquiry,
    request_device_job,
    wait_for_device_job,
    handle_result,
)

AGENT_NAME = "wifi_line_change"


# ── 진입점 라우터 ─────────────────────────────────────────────────────────────

def route_wifi_line_change(state: GraphState) -> Literal[
    "ask_device_number",
    "ask_country_code",
    "ask_expiry_date",
    "cancel_inquiry",
    "request_device_job",
    "wait_for_device_job",
    "end",
]:
    params = state.collected_parameters.get(AGENT_NAME, {})
    print(f"[라우터] 현재 수집된 파라미터: {params}")

    if state.error_message:
        return "end"

    # 업무 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 1단계: 단말기 번호 수집
    if not params.get("device_number"):
        return "ask_device_number"

    # 2단계: 국가코드 수집
    if not params.get("country_code"):
        return "ask_country_code"
    
    # 2-1단계: 회선 만료일 수집
    if not params.get("expiry_date"):
        return "ask_expiry_date"

    # 3단계: DeviceJob 실행 (아직 안 했으면)
    if "job_done" not in params:
        if not params.get("_job_id"):
            return "request_device_job"
        return "wait_for_device_job"

    return "end"


# ── 그래프 구성 ───────────────────────────────────────────────────────────────

_NODE_MAP = {
    "ask_device_number":   "ask_device_number",
    "ask_country_code":    "ask_country_code",
    "ask_expiry_date":     "ask_expiry_date",
    "cancel_inquiry":      "cancel_inquiry",
    "request_device_job":  "request_device_job",
    "wait_for_device_job": "wait_for_device_job",
    "end":                 END,
}

workflow = StateGraph(GraphState)

workflow.add_node("ask_device_number",  ask_device_number)
workflow.add_node("ask_country_code",   ask_country_code)
workflow.add_node("ask_expiry_date",    ask_expiry_date)
workflow.add_node("cancel_inquiry",     cancel_inquiry)
workflow.add_node("request_device_job", request_device_job)
workflow.add_node("wait_for_device_job", wait_for_device_job)
workflow.add_node("handle_result",      handle_result)

# 진입점: 단일 라우터
workflow.add_conditional_edges(START, route_wifi_line_change, _NODE_MAP)

# 사용자 입력 대기 노드 → END
workflow.add_edge("ask_device_number", END)
workflow.add_edge("ask_country_code",  END)
workflow.add_edge("ask_expiry_date",    END)

workflow.add_edge("cancel_inquiry",    END)

# DeviceJob → handle_result → END
workflow.add_edge("request_device_job", "wait_for_device_job")
workflow.add_edge("wait_for_device_job", "handle_result")
workflow.add_edge("handle_result",      END)

wifi_line_change_graph = workflow.compile()
