from src.workflow.graph_infra.state import GraphState # 상태 관리 모델
from src.tools.mos_client import mos_client # MOS 클라이언트
from src.tools.smtp_mail import send_mail # 이메일 전송 함수
import httpx

################################################################################
#                        와이파이 사용량 조회 업무
#
# 특정 기간 동안의 와이파이도시락 사용량을 조회하고,
# 그 결과를 고객에게 이메일로 전송하는 업무를 수행합니다.
################################################################################

# 와이파이도시락 사용량 조회 업무가 맞는지 확인
async def confirm_intent(state: GraphState) -> GraphState:
    ai_response = "와이파이도시락 사용량을 조회 업무를 요청하신게 맞나요?"
    state.add_ai_message(ai_response)
    return state

# 업무 취소
async def cancel_inquiry(state: GraphState) -> GraphState:
    ai_response = "네, 알겠습니다. 와이파이도시락 사용량 조회 업무를 취소합니다. 다른 도움이 필요하시면 말씀해주세요."
    state.add_ai_message(ai_response)
    
    # 현재 에이전트 초기화
    state.current_agent = None
    return state

# IMEI 번호 리스트 요청
async def request_imei_list(state: GraphState) -> GraphState:
    ai_response = "사용량을 조회할 imei를 알려주세요"
    state.add_ai_message(ai_response)
    return state

# 기간 정보 요청
async def request_date_range(state: GraphState) -> GraphState:
    ai_response = "사용량을 조회할 기간(시작일과 종료일)을 알려주세요. (예: 20251201부터 20251231까지)"
    state.add_ai_message(ai_response)
    return state

# 사용량 조회
async def fetch_usage(state: GraphState) -> GraphState:
    params = state.collected_parameters.get("wifi_usage_inquiry", {})
    usage_data = mos_client.get_usage_by_date_range(
        imeis=params.get("imei_list", []),
        start_yyyymmdd=params.get("start_date", ""),
        end_yyyymmdd=params.get("end_date", ""),
    )
    state.response["usage_data"] = usage_data
    return state

# 이메일 전송 희망 여부 확인
async def confirm_email_sending(state: GraphState) -> GraphState:
    ai_response = "조회된 사용량을 이메일로 발송할까요?"
    state.add_ai_message(ai_response)
    return state

# 이메일 주소 요청
async def request_email_address(state: GraphState) -> GraphState:
    ai_response = "조회 결과를 받을 이메일 주소를 알려주세요."
    state.add_ai_message(ai_response)
    return state

# 이메일 전송
async def send_usage_email(state: GraphState) -> GraphState:
    params = state.collected_parameters.get("wifi_usage_inquiry", {})
    to_email = params.get("email_address")
    usage_data = state.response.get("usage_data", "")
    subject = "와이파이도시락 사용량 조회 결과"
    message = f"안녕하세요, 고객님.\n아래는 요청하신 와이파이도시락 사용량 조회 결과입니다. \n\n{usage_data}"
    send_mail(to_list=[to_email], message=message, subject=subject)
    
    if "wifi_usage_inquiry" not in state.collected_parameters:
        state.collected_parameters["wifi_usage_inquiry"] = {}
    state.collected_parameters["wifi_usage_inquiry"]["email_sent"] = True  # 이중 발송 방지
    
    ai_response = "사용량 조회 결과를 이메일로 전송했습니다."
    state.add_ai_message(ai_response)
    return state