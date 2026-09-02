from registry.agent_registry import (
    register_agent,
)

from langchain_core.runnables import RunnableLambda


def create_dummy_agent(
    message: str,
    name: str,
):
    async def run(
        input_data,
        config=None,
    ):
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": message,
                }
            ]
        }

    return RunnableLambda(
        run,
        name=name,
    )


# =========================================================
# WiFi Dummy Agents
# =========================================================

wifi_status_agent = create_dummy_agent(
    message="WiFi 상태 조회 테스트 Agent입니다.",
    name="wifi_status",
)

wifi_status_agent = register_agent(
    name="wifi_status",
    description=(
        "와이파이 단말기의 현재 회선 상태, "
        "연결 상태와 사용 국가를 조회하는 네트워크 업무 Agent"
    ),
)(wifi_status_agent)


wifi_plan_change_agent = create_dummy_agent(
    message="WiFi 요금제 변경 테스트 Agent입니다.",
    name="wifi_plan_change",
)

wifi_plan_change_agent = register_agent(
    name="wifi_plan_change",
    description=(
        "와이파이 단말기의 데이터 요금제와 "
        "통신 플랜을 변경하는 네트워크 업무 Agent"
    ),
)(wifi_plan_change_agent)


wifi_country_change_agent = create_dummy_agent(
    message="WiFi 국가 변경 테스트 Agent입니다.",
    name="wifi_country_change",
)

wifi_country_change_agent = register_agent(
    name="wifi_country_change",
    description=(
        "와이파이 단말기의 사용 국가 또는 "
        "로밍 대상 국가를 변경하는 네트워크 업무 Agent"
    ),
)(wifi_country_change_agent)


wifi_expiry_change_agent = create_dummy_agent(
    message="WiFi 만료일 변경 테스트 Agent입니다.",
    name="wifi_expiry_change",
)

wifi_expiry_change_agent = register_agent(
    name="wifi_expiry_change",
    description=(
        "와이파이 회선 또는 SIM 서비스의 "
        "사용 만료일과 이용 기간을 변경하는 네트워크 업무 Agent"
    ),
)(wifi_expiry_change_agent)


wifi_sim_info_agent = create_dummy_agent(
    message="WiFi SIM 정보 조회 테스트 Agent입니다.",
    name="wifi_sim_info",
)

wifi_sim_info_agent = register_agent(
    name="wifi_sim_info",
    description=(
        "와이파이 단말기에 장착된 SIM의 IMSI, "
        "SIM 유형과 회선 정보를 조회하는 네트워크 업무 Agent"
    ),
)(wifi_sim_info_agent)


wifi_device_info_agent = create_dummy_agent(
    message="WiFi 단말 정보 조회 테스트 Agent입니다.",
    name="wifi_device_info",
)

wifi_device_info_agent = register_agent(
    name="wifi_device_info",
    description=(
        "와이파이 단말기의 IMEI, 단말 번호, "
        "모델명과 장비 정보를 조회하는 네트워크 업무 Agent"
    ),
)(wifi_device_info_agent)


wifi_line_suspend_agent = create_dummy_agent(
    message="WiFi 회선 정지 테스트 Agent입니다.",
    name="wifi_line_suspend",
)

wifi_line_suspend_agent = register_agent(
    name="wifi_line_suspend",
    description=(
        "와이파이 단말기의 통신 회선을 "
        "일시 정지하는 네트워크 업무 Agent"
    ),
)(wifi_line_suspend_agent)


wifi_line_resume_agent = create_dummy_agent(
    message="WiFi 회선 재개 테스트 Agent입니다.",
    name="wifi_line_resume",
)

wifi_line_resume_agent = register_agent(
    name="wifi_line_resume",
    description=(
        "정지된 와이파이 단말기의 통신 회선을 "
        "다시 활성화하는 네트워크 업무 Agent"
    ),
)(wifi_line_resume_agent)


wifi_roaming_status_agent = create_dummy_agent(
    message="WiFi 로밍 상태 조회 테스트 Agent입니다.",
    name="wifi_roaming_status",
)

wifi_roaming_status_agent = register_agent(
    name="wifi_roaming_status",
    description=(
        "와이파이 단말기의 해외 로밍 상태와 "
        "현재 사용 국가를 조회하는 네트워크 업무 Agent"
    ),
)(wifi_roaming_status_agent)


wifi_usage_check_agent = create_dummy_agent(
    message="WiFi 데이터 사용량 조회 테스트 Agent입니다.",
    name="wifi_usage_check",
)

wifi_usage_check_agent = register_agent(
    name="wifi_usage_check",
    description=(
        "와이파이 단말기의 데이터 사용량, "
        "잔여 데이터와 이용량을 조회하는 네트워크 업무 Agent"
    ),
)(wifi_usage_check_agent)


# =========================================================
# USIM Dummy Agents
# =========================================================

usim_status_agent = create_dummy_agent(
    message="USIM 상태 조회 테스트 Agent입니다.",
    name="usim_status",
)

usim_status_agent = register_agent(
    name="usim_status",
    description=(
        "USIM의 현재 개통 상태, 사용 상태와 "
        "회선 연결 상태를 조회하는 네트워크 업무 Agent"
    ),
)(usim_status_agent)


usim_activation_agent = create_dummy_agent(
    message="USIM 개통 테스트 Agent입니다.",
    name="usim_activation",
)

usim_activation_agent = register_agent(
    name="usim_activation",
    description=(
        "새로운 USIM을 개통하고 "
        "통신 회선을 활성화하는 네트워크 업무 Agent"
    ),
)(usim_activation_agent)


usim_deactivation_agent = create_dummy_agent(
    message="USIM 해지 테스트 Agent입니다.",
    name="usim_deactivation",
)

usim_deactivation_agent = register_agent(
    name="usim_deactivation",
    description=(
        "사용 중인 USIM의 통신 서비스를 "
        "해지하거나 비활성화하는 네트워크 업무 Agent"
    ),
)(usim_deactivation_agent)


usim_country_change_agent = create_dummy_agent(
    message="USIM 국가 변경 테스트 Agent입니다.",
    name="usim_country_change",
)

usim_country_change_agent = register_agent(
    name="usim_country_change",
    description=(
        "USIM에 적용된 사용 국가 또는 "
        "로밍 대상 국가를 변경하는 네트워크 업무 Agent"
    ),
)(usim_country_change_agent)


usim_plan_change_agent = create_dummy_agent(
    message="USIM 요금제 변경 테스트 Agent입니다.",
    name="usim_plan_change",
)

usim_plan_change_agent = register_agent(
    name="usim_plan_change",
    description=(
        "USIM 회선에 적용된 데이터 요금제와 "
        "통신 플랜을 변경하는 네트워크 업무 Agent"
    ),
)(usim_plan_change_agent)


usim_imsi_lookup_agent = create_dummy_agent(
    message="USIM IMSI 조회 테스트 Agent입니다.",
    name="usim_imsi_lookup",
)

usim_imsi_lookup_agent = register_agent(
    name="usim_imsi_lookup",
    description=(
        "USIM의 IMSI 번호와 "
        "가입자 식별 정보를 조회하는 네트워크 업무 Agent"
    ),
)(usim_imsi_lookup_agent)


usim_iccid_lookup_agent = create_dummy_agent(
    message="USIM ICCID 조회 테스트 Agent입니다.",
    name="usim_iccid_lookup",
)

usim_iccid_lookup_agent = register_agent(
    name="usim_iccid_lookup",
    description=(
        "USIM의 ICCID와 SIM 카드 "
        "식별 정보를 조회하는 네트워크 업무 Agent"
    ),
)(usim_iccid_lookup_agent)


usim_expiry_change_agent = create_dummy_agent(
    message="USIM 만료일 변경 테스트 Agent입니다.",
    name="usim_expiry_change",
)

usim_expiry_change_agent = register_agent(
    name="usim_expiry_change",
    description=(
        "USIM 서비스의 사용 만료일과 "
        "이용 기간을 변경하는 네트워크 업무 Agent"
    ),
)(usim_expiry_change_agent)


usim_replacement_agent = create_dummy_agent(
    message="USIM 교체 테스트 Agent입니다.",
    name="usim_replacement",
)

usim_replacement_agent = register_agent(
    name="usim_replacement",
    description=(
        "기존 USIM을 새로운 USIM으로 "
        "교체하고 회선 정보를 이전하는 네트워크 업무 Agent"
    ),
)(usim_replacement_agent)


usim_usage_check_agent = create_dummy_agent(
    message="USIM 데이터 사용량 조회 테스트 Agent입니다.",
    name="usim_usage_check",
)

usim_usage_check_agent = register_agent(
    name="usim_usage_check",
    description=(
        "USIM 회선의 데이터 사용량과 "
        "잔여 데이터 사용 가능량을 조회하는 네트워크 업무 Agent"
    ),
)(usim_usage_check_agent)


# =========================================================
# eSIM Dummy Agents
# =========================================================

esim_status_agent = create_dummy_agent(
    message="eSIM 상태 조회 테스트 Agent입니다.",
    name="esim_status",
)

esim_status_agent = register_agent(
    name="esim_status",
    description=(
        "eSIM 프로파일의 설치 상태, "
        "활성화 상태와 회선 상태를 조회하는 네트워크 업무 Agent"
    ),
)(esim_status_agent)


esim_activation_agent = create_dummy_agent(
    message="eSIM 활성화 테스트 Agent입니다.",
    name="esim_activation",
)

esim_activation_agent = register_agent(
    name="esim_activation",
    description=(
        "설치된 eSIM 프로파일을 활성화하고 "
        "통신 회선을 개통하는 네트워크 업무 Agent"
    ),
)(esim_activation_agent)


esim_deactivation_agent = create_dummy_agent(
    message="eSIM 비활성화 테스트 Agent입니다.",
    name="esim_deactivation",
)

esim_deactivation_agent = register_agent(
    name="esim_deactivation",
    description=(
        "사용 중인 eSIM 프로파일 또는 "
        "통신 회선을 비활성화하는 네트워크 업무 Agent"
    ),
)(esim_deactivation_agent)


esim_profile_download_agent = create_dummy_agent(
    message="eSIM 프로파일 다운로드 테스트 Agent입니다.",
    name="esim_profile_download",
)

esim_profile_download_agent = register_agent(
    name="esim_profile_download",
    description=(
        "단말기에 새로운 eSIM 프로파일을 "
        "다운로드하고 설치하는 네트워크 업무 Agent"
    ),
)(esim_profile_download_agent)


esim_profile_delete_agent = create_dummy_agent(
    message="eSIM 프로파일 삭제 테스트 Agent입니다.",
    name="esim_profile_delete",
)

esim_profile_delete_agent = register_agent(
    name="esim_profile_delete",
    description=(
        "단말기에 설치된 eSIM 프로파일을 "
        "삭제하는 네트워크 업무 Agent"
    ),
)(esim_profile_delete_agent)


esim_country_change_agent = create_dummy_agent(
    message="eSIM 국가 변경 테스트 Agent입니다.",
    name="esim_country_change",
)

esim_country_change_agent = register_agent(
    name="esim_country_change",
    description=(
        "eSIM 회선의 사용 국가 또는 "
        "로밍 대상 국가를 변경하는 네트워크 업무 Agent"
    ),
)(esim_country_change_agent)


esim_plan_change_agent = create_dummy_agent(
    message="eSIM 요금제 변경 테스트 Agent입니다.",
    name="esim_plan_change",
)

esim_plan_change_agent = register_agent(
    name="esim_plan_change",
    description=(
        "eSIM 회선에 적용된 데이터 요금제와 "
        "통신 플랜을 변경하는 네트워크 업무 Agent"
    ),
)(esim_plan_change_agent)


esim_expiry_change_agent = create_dummy_agent(
    message="eSIM 만료일 변경 테스트 Agent입니다.",
    name="esim_expiry_change",
)

esim_expiry_change_agent = register_agent(
    name="esim_expiry_change",
    description=(
        "eSIM 서비스의 사용 만료일과 "
        "이용 기간을 변경하는 네트워크 업무 Agent"
    ),
)(esim_expiry_change_agent)


esim_eid_lookup_agent = create_dummy_agent(
    message="eSIM EID 조회 테스트 Agent입니다.",
    name="esim_eid_lookup",
)

esim_eid_lookup_agent = register_agent(
    name="esim_eid_lookup",
    description=(
        "eSIM을 지원하는 단말기의 EID와 "
        "eSIM 식별 정보를 조회하는 네트워크 업무 Agent"
    ),
)(esim_eid_lookup_agent)


esim_profile_switch_agent = create_dummy_agent(
    message="eSIM 프로파일 전환 테스트 Agent입니다.",
    name="esim_profile_switch",
)

esim_profile_switch_agent = register_agent(
    name="esim_profile_switch",
    description=(
        "단말기에 설치된 여러 eSIM 프로파일 중 "
        "사용할 프로파일을 다른 프로파일로 전환하는 네트워크 업무 Agent"
    ),
)(esim_profile_switch_agent)