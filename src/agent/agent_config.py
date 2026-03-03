# ─────────────────────────────────────────────────────────────────
# 에이전트 레지스트리
# 각 에이전트의 이름, 설명, 사용자로부터 수집하는 파라미터 정보를 담고 있습니다.
# 새로운 에이전트를 추가할 때 이 레지스트리에 등록해야 합니다.
# 다양한 곳에서 에이전트 정보를 참조할 때 이 레지스트리만 사용합니다.
# ─────────────────────────────────────────────────────────────────

from typing import Dict, Any
from src.agent.wifi_usage_inquiry.graph import wifi_usage_inquiry_graph

AGENT_REGISTRY = {
    "wifi_usage_inquiry": {
        "display_name": "와이파이 사용량 조회",
        "description": "와이파이도시락 데이터 사용량을 조회하여 알려주는 에이전트입니다.",
        "graph": wifi_usage_inquiry_graph,
        "parameters": {
            "intent_confirmed": {
                "type": "bool",
                "description": "사용자가 와이파이도시락 사용량 조회 업무를 요청한 것이 맞는지 확인 여부 (긍정이면 true, 부정이면 false)",
                "display_name": "와이파이 사용량 조회 의도 확인 여부",
                "required": True,
            },
            "imei_list": {
                "type": "list[str]",
                "description": "조회할 단말의 IMEI 리스트",
                "display_name": "IMEI 번호 리스트",
                "required": True,
            },
            "start_date": {
                "type": "str",
                "description": "조회 시작 날짜 (YYYYMMDD 형식)",
                "display_name": "조회 시작 날짜",
                "required": True,
            },
            "end_date": {
                "type": "str",
                "description": "조회 종료 날짜 (YYYYMMDD 형식)",
                "display_name": "조회 종료 날짜",
                "required": True,
            },
            "email_sending_confirmed": {
                "type": "bool",
                "description": "조회 결과를 이메일로 받을지 여부 (긍정이면 true, 부정이면 false)",
                "display_name": "이메일 발송 의사 확인",
                "required": True,
            },
            "email_address": {
                "type": "str",
                "description": "조회 결과를 받을 이메일 주소",
                "display_name": "이메일 주소",
                "required": False,
            },
        },
    },
}

# ─────────────────────────────────────────────────────────────────
# 헬퍼 함수 및 공통 설정
# 필요한 공통 함수나 설정이 있다면 이곳에 추가합니다.
# ─────────────────────────────────────────────────────────────────

# 에이전트 이름 리스트
AGENT_NAMES = list(AGENT_REGISTRY.keys())

# 에이전트 이름+설명 딕셔너리
AGENT_DESCRIPTIONS = {name: info["description"] for name, info in AGENT_REGISTRY.items()}

def get_current_agent_spec(agent_name: str) -> Dict[str, Any]:
    """현재 에이전트의 상세 스펙을 반환하는 헬퍼 함수입니다. (graph 객체 제외)"""
    spec = AGENT_REGISTRY.get(agent_name, {})
    return {k: v for k, v in spec.items() if k != "graph"}