from typing import Literal
from langgraph.graph import StateGraph, START, END
from src.workflow.graph_infra.state import GraphState
from src.agent.wifi.subagents.chat.wifi_usage_inquiry.node import (
    cancel_inquiry, # 조회 업무 취소
    request_imei_list, # IMEI 리스트 요청
    request_date_range, # 기간 정보 요청
    fetch_usage, # 사용량 조회
    confirm_email_sending, # 이메일 전송 희망 여부 확인
    request_email_address, # 이메일 주소 요청
    send_usage_email, # 이메일 전송
    send_usage_chat, # 채팅창에 사용량 결과 전송
)

def route_wifi_usage_inquiry(state: GraphState) -> Literal[
    "cancel_inquiry",
    "request_imei_list",
    "request_date_range",
    "fetch_usage",
    "confirm_email_sending",
    "request_email_address",
    "send_usage_email",
    "send_usage_chat",
    "end"
]:

    # 현재 에이전트의 파라미터만 가져오도록 수정
    all_parameters = getattr(state, "collected_parameters", {}) or {}
    parameters = all_parameters.get("wifi_usage_inquiry", {})
    response = getattr(state, "response", {}) or {}
    agent_response = response.get("wifi_usage_inquiry", {})
    
    # 0. 에러 발생 시 즉시 종료 (이미 에러 메시지 보냄)
    if state.error_message:
        return "end"

    if parameters.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 2. IMEI 리스트 확인
    if not parameters.get("imei_list"):
        return "request_imei_list"

    # 3. 조회 기간 확인
    if not parameters.get("start_date") or not parameters.get("end_date"):
        return "request_date_range"

    # 4. 사용량 조회
    if "usage_data" not in agent_response:
        return "fetch_usage"

    # 5. 이메일 발송 여부 확인
    if parameters.get("email_sending_confirmed") is None:
        return "confirm_email_sending"

    # 6. 이메일 발송 주소 확인 및 발송
    if parameters.get("email_sending_confirmed") is True:
        if not parameters.get("email_address"):
            return "request_email_address"
        if not parameters.get("email_sent"):  # 이중 발송 방지
            return "send_usage_email"
    elif parameters.get("email_sending_confirmed") is False:
        if not parameters.get("chat_sent"):
            return "send_usage_chat"

    return "end"

workflow = StateGraph(GraphState)

# 노드 등록
workflow.add_node("cancel_inquiry", cancel_inquiry)
workflow.add_node("request_imei_list", request_imei_list)
workflow.add_node("request_date_range", request_date_range)
workflow.add_node("fetch_usage", fetch_usage)
workflow.add_node("confirm_email_sending", confirm_email_sending)
workflow.add_node("request_email_address", request_email_address)
workflow.add_node("send_usage_email", send_usage_email)
workflow.add_node("send_usage_chat", send_usage_chat)

# 조건부 진입점
workflow.add_conditional_edges(
    START,
    route_wifi_usage_inquiry, 
    {
        "cancel_inquiry": "cancel_inquiry",
        "request_imei_list": "request_imei_list",
        "request_date_range": "request_date_range",
        "fetch_usage": "fetch_usage",
        "confirm_email_sending": "confirm_email_sending",
        "request_email_address": "request_email_address",
        "send_usage_email": "send_usage_email",
        "send_usage_chat": "send_usage_chat",
        "end": END
    }
)

# 노드 종료 후 처리 (fetch_usage는 자동 진행, 나머지는 사용자 입력 대기)
workflow.add_edge("cancel_inquiry", END)
workflow.add_edge("request_imei_list", END)
workflow.add_edge("request_date_range", END)

workflow.add_conditional_edges(
    "fetch_usage",
    route_wifi_usage_inquiry,
    {
        "cancel_inquiry": "cancel_inquiry",
        "request_imei_list": "request_imei_list",
        "request_date_range": "request_date_range",
        "fetch_usage": "fetch_usage",
        "confirm_email_sending": "confirm_email_sending", # Expected path
        "request_email_address": "request_email_address",
        "send_usage_email": "send_usage_email",
        "send_usage_chat": "send_usage_chat",
        "end": END
    }
)

workflow.add_edge("confirm_email_sending", END)
workflow.add_edge("request_email_address", END)
workflow.add_edge("send_usage_email", END)
workflow.add_edge("send_usage_chat", END)

wifi_usage_inquiry_graph = workflow.compile()
