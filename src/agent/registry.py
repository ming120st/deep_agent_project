"""
src/agent/registry.py

[설계 원칙]
  - 코드: 에이전트 메타데이터 + 실행 로직 참조만 정의
  - MongoDB(boa_agent_registry): enabled 상태 영구 저장
  - 앱 시작 시 registry → MongoDB sync (enabled는 덮어쓰지 않음)

[타입]
  chat     → Google Chat 메시지 트리거 (AGENT_REGISTRY)
  schedule → cron 기반 주기 실행     (SCHEDULE_REGISTRY)
  trigger  → 조건 기반 이벤트 실행   (TRIGGER_REGISTRY)
"""

from typing import Dict, Any

from src.agent.wifi.subagents.chat.wifi_usage_inquiry.graph import wifi_usage_inquiry_graph
from src.agent.esim.subagents.chat.esim_order_cancellation.graph import esim_order_cancellation_graph
from src.agent.analytics.subagents.chat.bigquery_agent.agent import bigquery_agent_node
from src.agent.esim.subagents.chat.esim_resend.graph import esim_resend_graph
from src.agent.wifi.subagents.chat.wifi_line_change.graph import wifi_line_change_graph
from src.agent.esim.subagents.chat.esim_fault_replacement.graph import esim_fault_replacement_graph
# from src.agent.xxx.graph import xxx_graph  ← graph 쓰는 에이전트 추가할 때마다 import


from src.core.rag_engine import reload_rag_index_if_updated
# from src.triggers.xxx import xxx  ← 트리거 추가할 때마다 import

from src.core.mongo_client import boa_agent_registry_collection
import src.agent.usage_guide as usage_guide  # 에이전트별 사용 가이드 텍스트 (src/agent/usage_guide.py에서 정의)

# ── 대화형 에이전트 ────────────────────────────────────────────────────────────
AGENT_REGISTRY: dict = {
    "wifi_usage_inquiry": {
        "type":         "chat",
        "display_name": "와이파이 사용량 조회",
        "description":  "와이파이도시락 데이터 사용량을 조회하여 알려주는 에이전트입니다. 사용자가 단말 IMEI와 조회 기간을 입력하면, 해당 기간 동안의 데이터 사용량을 조회하여 알려줍니다.",
        "description_short": "와이파이도시락 데이터 사용량 조회(당일데이터 조회불가)",
        "visible":      True,  # 도움말 카드 노출 여부
        "usage_guide": usage_guide.wifi_usage_inquiry, # 도움말 카드 상세설명
        "enabled":      True,
        "graph":        wifi_usage_inquiry_graph,
        "parameters": {
            "intent_confirmed": {
                "type": "bool",
                "description": "사용자의 와이파이도시락 사용량 조회 업무 진행 의사 여부. 사용자가 업무 진행 취소 의사를 표현하면 false로 설정.",
                "required": True,
            },
            "imei_list": {
                "type": "list[str]",
                "description": "조회할 단말의 IMEI 리스트",
                "required": True,
            },
            "start_date": {
                "type": "str",
                "description": "조회 시작 날짜 (YYYYMMDD 형식)",
                "required": True,
            },
            "end_date": {
                "type": "str",
                "description": "조회 종료 날짜 (YYYYMMDD 형식)",
                "required": True,
            },
            "email_sending_confirmed": {
                "type": "bool",
                "description": "조회 결과를 이메일로 받을지 여부. 사용자가 긍정하면 true, 부정하면 false",
                "required": True,
            },
            "email_address": {
                "type": "str",
                "description": "조회 결과를 받을 이메일 주소",
                "required": False,
            },
        },
    },
    "esim_order_cancellation": {
        "type":         "chat",
        "display_name": "이심 주문취소",
        "description":  "eSIM 주문 취소 신청을 처리하는 에이전트. "
                        "이심 인트라넷 주문번호를 제공하면 취소 가능 여부를 확인 후 취소신청을 접수합니다.",
        "description_short": "eSIM 주문 취소 처리 (부분취소 불가, 전체 취소만 가능)",
        "visible":      True,  # 도움말 카드 노출 여부
        "usage_guide": usage_guide.esim_order_cancellation, # 도움말 카드 상세설명
        "enabled":      True,
        "graph":        esim_order_cancellation_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "이심 주문취소 업무 진행 의사 여부. 사용자가 업무 취소 의사를 표현하면 false로 설정.",
                "required":    True,
            },
            "order_id": {
                "type":        "str",
                "description": "이심 인트라넷 주문번호 (예: E202501010001)",
                "required":    True,
            },
            "cancel_confirmed": {
                "type":        "bool",
                "description": "구매 상품 전체 취소 진행 의사. 사용자가 전체 취소에 동의하면 true, 원하지 않으면 false.",
                "required":    True,
            },
        },
    },
    "bigquery_agent": {
        "type": "chat",
        "display_name": "데이터 분석 에이전트(판매/매출)",
        "description": "판매 실적(판매량/매출) 데이터 분석."
                       "와이파이,유심,이심 서비스 대상으로 채널/국가/상품 등 세부조건별 판매량/매출 데이터 분석이 필요한 모든 요청에 사용합니다.",
        "description_short": "와이파이/유심/이심 판매 데이터분석 및 리포트 생성",
        "visible": True,
        "usage_guide": usage_guide.sales_data_analysis, # 도움말 카드 상세설명
        "graph": bigquery_agent_node,  # DeepAgent 노드 함수 (LangGraph subgraph 아님)
        "enabled": True,
    },
    "esim_resend": {
        "type":         "chat",
        "display_name": "이심 QR 재발송",
        "description":  "기존 구매한 eSIM QR코드를 재발송합니다. 상품 변경이나 교체 없이 동일 QR 재전송이 필요할 때 사용합니다. 전화번호,이메일은 변경 가능합니다."
                        "이심 재발급 에이전트와 재발송/재발급 단어를 기준으로 명확히 구분합니다.",
        "visible":      True,
        "usage_guide": usage_guide.esim_resend, # 도움말 카드 상세설명
        "description_short": "고객의 eSIM QR코드 재발송 처리(재발급과는 다름)",
        "enabled":      True,
        "graph":        esim_resend_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "이심 재발송 업무 진행 의사. 취소 의사 시 false.",
                "required":    True,
            },
            "order_id": {
                "type":        "str",
                "description": "E로 시작하는 이심 인트라넷 주문번호 (예: E23061200272)",
                "required":    True,
            },
            "selected_order_detail_ids": {
                "type":        "list[str]",
                "description": "재발송할 orderDetailId 리스트. '전체' 요청 시 ordersDetail 전체 항목의 orderDetailId를 포함.",
                "required":    True,
            },
            "email": {
                "type":        "str",
                "description": "재발송 수신 이메일 주소. '그대로' 응답 시 order_contact.email 값을 그대로 사용. 미사용 시 빈 문자열.",
                "required":    False,
            },
            "mobile": {
                "type":        "str",
                "description": "재발송 수신 전화번호 (숫자만, 예: 01012345678). '그대로' 응답 시 order_contact.mobile 값을 그대로 사용. 미사용 시 빈 문자열.",
                "required":    False,
            },
        },
    },
    "esim_fault_replacement": {
        "type":         "chat",
        "display_name": "이심 재발급",
        "description":  "고객이 구매한 이심 사용 중 문제 발생 시 대체 이심을 발송하는 에이전트. "
                        "주문번호, 재발급 사용일수, 수량을 입력하면 대체 상품을 선정하여 재발급 처리 및 상담 등록을 진행합니다. ",
        "visible":      True,
        "usage_guide": usage_guide.esim_fault_replacement, # 도움말 카드 상세설명
        "description_short": "이심 재발급. 사용 중 문제 발생 시 대체 이심 발송 처리",
        "enabled":      True,
        "graph":        esim_fault_replacement_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "이심 재발급 업무 진행 의사 여부. 사용자가 업무 취소 의사를 표현하면 false로 설정.",
                "required":    True,
            },
            "order_id": {
                "type":        "str",
                "description": "이심 인트라넷 주문번호 (E로 시작, 예: E26031101886). 링크 제공 시 주문번호만 추출하여 사용.",
                "required":    True,
            },
            "request_days": {
                "type":        "int",
                "description": "재발급할 이심의 사용일수 (예: 30)",
                "required":    True,
            },
            "country_name": {
                "type":        "str",
                "description": "이심 사용 국가명. 대체 상품 검색 시 국가 기반 조회에 사용. (예: 일본, 대만, 미국)",
                "required":    False,
            },
        },
    },
    "wifi_line_change": {
        "type":         "chat",
        "display_name": "와이파이 회선변경",
        "description": "와이파이도시락 단말기의 회선(SIM)을 교체합니다. "
                    "사용자는 단말기 번호(IMEI)와 사용 국가, 만료일을 입력합니다."
                    "와이파이 회선변경, SIM 교체요청에 사용합니다.",
        "visible":      True,
        "usage_guide": usage_guide.wifi_line_change, # 도움말 카드 상세설명
        "description_short": "와이파이 회선 변경 에이전트",
        "enabled":      True,
        "graph":        wifi_line_change_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "회선변경 업무 진행 의사. 취소 의사 시 false.",
                "required":    True,
            },
            "device_number": {
                "type":        "str",
                "description": "단말기 번호 (IMEI. 숫자만 입력, 예: 123456789012345)",
                "required":    True,
            },
            "country_code": {
                "type":        "str",
                "description": "사용 국가. ISO 3166-1 alpha-2의 2자리 코드로 입력 (예: JP, US, TH)",
                "required":    True,
            },
            "expiry_date": {
                "type":        "str",
                "description": "회선 만료일. YYYYMMDD 형식으로 입력 (예: 20231231)",
                "required":    True,
            }
        },
    },
}

# ── 스케줄 에이전트 ────────────────────────────────────────────────────────────
"""
아직 구현된 스케줄 에이전트가 없어서 예시로 daily_report 하나만 주석 처리해둔 상태입니다. 스케줄 에이전트 추가할 때마다 registry에 정의하고 sync_registry.py에도 import + sync 코드 추가해주세요.
"daily_report": {
    "type":         "schedule",
    "display_name": "일일 리포트",
    "description":  "매일 오전 8시 영업 리포트를 구성원에게 발송합니다.",
    "description_short": "매일 오전 8시 영업 리포트 발송",
    "visible":      True,
    "usage_guide": usage_guide.daily_report, # 도움말 카드 상세설명
    "enabled":      True,
    "cron":         {"hour": 8, "minute": 0},   # APScheduler cron 파라미터
    "func":         run_daily_report,
    "target":       "구성원 DM (report_daily 설정)",  # 발송 대상 설명
},
"""

SCHEDULE_REGISTRY: dict = {
}

# ── 트리거 에이전트 ────────────────────────────────────────────────────────────
TRIGGER_REGISTRY: dict = {
    "rag_index_reload": {
        "type":         "trigger",
        "display_name": "RAG 인덱스 동기화",
        "description":  "10분마다 Azure Blob Storage의 FAISS 인덱스 버전을 체크하여 변경 시 reload합니다.",
        "visible":      False,
        "usage_guide": usage_guide.rag_index_reload, # 도움말 카드 상세설명
        "enabled":      True,
        "interval":     {"minutes": 10},
        "func":         reload_rag_index_if_updated,
        "target":       "사용자 인터랙션 없음",  # 메시지 발송 없음
    },
}

# ── 전체 통합 (대시보드/sync용) ────────────────────────────────────────────────
ALL_REGISTRIES: dict = {
    **AGENT_REGISTRY,
    **SCHEDULE_REGISTRY,
    **TRIGGER_REGISTRY,
}

# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

# 에이전트 이름 리스트
AGENT_NAMES: list = list(AGENT_REGISTRY.keys())

# 에이전트 이름 + 설명 딕셔너리 (action_classifier 프롬프트용). MongoDB에서 enabled=True인 chat 에이전트만 포함하도록 get_enabled_agent_descriptions() 함수로 대체중임.
AGENT_DESCRIPTIONS: dict = {
    name: info["description"] for name, info in AGENT_REGISTRY.items()
}

def get_current_agent_spec(agent_name: str) -> Dict[str, Any]:
    """에이전트 상세 스펙 반환 (graph 객체 제외). parameter_extractor 프롬프트용."""
    spec = AGENT_REGISTRY.get(agent_name, {})
    return {k: v for k, v in spec.items() if k != "graph"}

# MongoDB에서 enabled=True인 chat 에이전트의 이름과 설명 반환하는 함수 (action_classifier 프롬프트용)
async def get_enabled_agent_descriptions() -> dict:
    enabled = await boa_agent_registry_collection.find(
        {"agent_type": "chat", "enabled": True},
        {"_id": 0, "agent_id": 1}
    ).to_list(None)
    enabled_names = {doc["agent_id"] for doc in enabled}
    return {
        name: info["description"]
        for name, info in AGENT_REGISTRY.items()
        if name in enabled_names
    }
