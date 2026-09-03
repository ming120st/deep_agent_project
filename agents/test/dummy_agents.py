from core.llm.model_factory import LLM_LIGHT
from registry.agent_registry import register_agent


def register_dummy_agent(
    *,
    name: str,
    description: str,
    message: str,
) -> None:
    register_agent(
        name=name,
        description=description,
        system_prompt=(
            f"""
너는 테스트용 Dummy SubAgent다.

사용자의 요청이 이 Agent의 업무에 해당하면
다음 내용을 응답한다.

응답:
{message}
""".strip()
        ),
        model=LLM_LIGHT,
        tools=[],
    )


# =========================================================
# WiFi Dummy Agents
# =========================================================

register_dummy_agent(
    name="wifi_status",
    description=(
        "와이파이 단말기의 현재 회선 상태, "
        "연결 상태와 사용 국가를 조회하는 네트워크 업무 Agent"
    ),
    message="WiFi 상태 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_plan_change",
    description=(
        "와이파이 단말기의 데이터 요금제와 "
        "통신 플랜을 변경하는 네트워크 업무 Agent"
    ),
    message="WiFi 요금제 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_country_change",
    description=(
        "와이파이 단말기의 사용 국가 또는 "
        "로밍 대상 국가를 변경하는 네트워크 업무 Agent"
    ),
    message="WiFi 국가 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_expiry_change",
    description=(
        "와이파이 회선 또는 SIM 서비스의 "
        "사용 만료일과 이용 기간을 변경하는 네트워크 업무 Agent"
    ),
    message="WiFi 만료일 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_sim_info",
    description=(
        "와이파이 단말기에 장착된 SIM의 IMSI, "
        "SIM 유형과 회선 정보를 조회하는 네트워크 업무 Agent"
    ),
    message="WiFi SIM 정보 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_device_info",
    description=(
        "와이파이 단말기의 IMEI, 단말 번호, "
        "모델명과 장비 정보를 조회하는 네트워크 업무 Agent"
    ),
    message="WiFi 단말 정보 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_line_suspend",
    description=(
        "와이파이 단말기의 통신 회선을 "
        "일시 정지하는 네트워크 업무 Agent"
    ),
    message="WiFi 회선 정지 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_line_resume",
    description=(
        "정지된 와이파이 단말기의 통신 회선을 "
        "다시 활성화하는 네트워크 업무 Agent"
    ),
    message="WiFi 회선 재개 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_roaming_status",
    description=(
        "와이파이 단말기의 해외 로밍 상태와 "
        "현재 사용 국가를 조회하는 네트워크 업무 Agent"
    ),
    message="WiFi 로밍 상태 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="wifi_usage_check",
    description=(
        "와이파이 단말기의 데이터 사용량, "
        "잔여 데이터와 이용량을 조회하는 네트워크 업무 Agent"
    ),
    message="WiFi 데이터 사용량 조회 테스트 Agent입니다.",
)


# =========================================================
# USIM Dummy Agents
# =========================================================

register_dummy_agent(
    name="usim_status",
    description=(
        "USIM의 현재 개통 상태, 사용 상태와 "
        "회선 연결 상태를 조회하는 네트워크 업무 Agent"
    ),
    message="USIM 상태 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_activation",
    description=(
        "새로운 USIM을 개통하고 "
        "통신 회선을 활성화하는 네트워크 업무 Agent"
    ),
    message="USIM 개통 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_deactivation",
    description=(
        "사용 중인 USIM의 통신 서비스를 "
        "해지하거나 비활성화하는 네트워크 업무 Agent"
    ),
    message="USIM 해지 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_country_change",
    description=(
        "USIM에 적용된 사용 국가 또는 "
        "로밍 대상 국가를 변경하는 네트워크 업무 Agent"
    ),
    message="USIM 국가 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_plan_change",
    description=(
        "USIM 회선에 적용된 데이터 요금제와 "
        "통신 플랜을 변경하는 네트워크 업무 Agent"
    ),
    message="USIM 요금제 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_imsi_lookup",
    description=(
        "USIM의 IMSI 번호와 "
        "가입자 식별 정보를 조회하는 네트워크 업무 Agent"
    ),
    message="USIM IMSI 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_iccid_lookup",
    description=(
        "USIM의 ICCID와 SIM 카드 "
        "식별 정보를 조회하는 네트워크 업무 Agent"
    ),
    message="USIM ICCID 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_expiry_change",
    description=(
        "USIM 서비스의 사용 만료일과 "
        "이용 기간을 변경하는 네트워크 업무 Agent"
    ),
    message="USIM 만료일 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_replacement",
    description=(
        "기존 USIM을 새로운 USIM으로 "
        "교체하고 회선 정보를 이전하는 네트워크 업무 Agent"
    ),
    message="USIM 교체 테스트 Agent입니다.",
)


register_dummy_agent(
    name="usim_usage_check",
    description=(
        "USIM 회선의 데이터 사용량과 "
        "잔여 데이터 사용 가능량을 조회하는 네트워크 업무 Agent"
    ),
    message="USIM 데이터 사용량 조회 테스트 Agent입니다.",
)


# =========================================================
# eSIM Dummy Agents
# =========================================================

register_dummy_agent(
    name="esim_status",
    description=(
        "eSIM 프로파일의 설치 상태, "
        "활성화 상태와 회선 상태를 조회하는 네트워크 업무 Agent"
    ),
    message="eSIM 상태 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_activation",
    description=(
        "설치된 eSIM 프로파일을 활성화하고 "
        "통신 회선을 개통하는 네트워크 업무 Agent"
    ),
    message="eSIM 활성화 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_deactivation",
    description=(
        "사용 중인 eSIM 프로파일 또는 "
        "통신 회선을 비활성화하는 네트워크 업무 Agent"
    ),
    message="eSIM 비활성화 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_profile_download",
    description=(
        "단말기에 새로운 eSIM 프로파일을 "
        "다운로드하고 설치하는 네트워크 업무 Agent"
    ),
    message="eSIM 프로파일 다운로드 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_profile_delete",
    description=(
        "단말기에 설치된 eSIM 프로파일을 "
        "삭제하는 네트워크 업무 Agent"
    ),
    message="eSIM 프로파일 삭제 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_country_change",
    description=(
        "eSIM 회선의 사용 국가 또는 "
        "로밍 대상 국가를 변경하는 네트워크 업무 Agent"
    ),
    message="eSIM 국가 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_plan_change",
    description=(
        "eSIM 회선에 적용된 데이터 요금제와 "
        "통신 플랜을 변경하는 네트워크 업무 Agent"
    ),
    message="eSIM 요금제 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_expiry_change",
    description=(
        "eSIM 서비스의 사용 만료일과 "
        "이용 기간을 변경하는 네트워크 업무 Agent"
    ),
    message="eSIM 만료일 변경 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_eid_lookup",
    description=(
        "eSIM을 지원하는 단말기의 EID와 "
        "eSIM 식별 정보를 조회하는 네트워크 업무 Agent"
    ),
    message="eSIM EID 조회 테스트 Agent입니다.",
)


register_dummy_agent(
    name="esim_profile_switch",
    description=(
        "단말기에 설치된 여러 eSIM 프로파일 중 "
        "사용할 프로파일을 다른 프로파일로 전환하는 네트워크 업무 Agent"
    ),
    message="eSIM 프로파일 전환 테스트 Agent입니다.",
)