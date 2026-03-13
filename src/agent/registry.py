"""
src/agent/registry.py

[설계 원칙]
  - 코드: 에이전트 메타데이터 + 실행 로직 참조만 정의
  - MongoDB(boa_agents): enabled 상태 영구 저장
  - 앱 시작 시 registry → MongoDB sync (enabled는 덮어쓰지 않음)

[타입]
  chat     → Google Chat 메시지 트리거 (AGENT_REGISTRY)
  schedule → cron 기반 주기 실행     (SCHEDULE_REGISTRY)
  trigger  → 조건 기반 이벤트 실행   (TRIGGER_REGISTRY)
"""

from typing import Dict, Any

from src.agent.chat.wifi_usage_inquiry.graph import wifi_usage_inquiry_graph
from src.agent.chat.esim_order_cancellation.graph import esim_order_cancellation_graph
from src.agent.chat.bigquery_agent.agent import bigquery_agent_node
from src.agent.chat.esim_resend.graph import esim_resend_graph
from src.agent.chat.esim_resend.graph import esim_resend_graph
# from src.agent.xxx.graph import xxx_graph  ← graph 쓰는 에이전트 추가할 때마다 import


from src.agent.trigger.check_stale_docs import check_stale_docs
from src.core.rag_engine import reload_rag_index_if_updated
# from src.triggers.xxx import xxx  ← 트리거 추가할 때마다 import

from src.core.mongo_client import boa_agents_collection

# ── 대화형 에이전트 ────────────────────────────────────────────────────────────
AGENT_REGISTRY: dict = {
    "wifi_usage_inquiry": {
        "type":         "chat",
        "display_name": "와이파이 사용량 조회",
        "description":  "와이파이도시락 데이터 사용량을 조회하여 알려주는 에이전트입니다. 사용자가 단말 IMEI와 조회 기간을 입력하면, 해당 기간 동안의 데이터 사용량을 조회하여 알려줍니다. 또한, 결과를 이메일로 발송할 수 있습니다.",
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
                        "이심 인트라넷 주문번호를 제공하면 취소 가능 여부를 확인 후 취소신청을 접수합니다. "
                        "이심 취소, 이심 주문 취소, eSIM 취소 등 이심 주문 취소 관련 요청에 사용합니다.",
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
        "display_name": "데이터 분석 에이전트",
        "description": "BigQuery 데이터 조회 및 매출 분석 리포트 생성."
                       "와이파이,유심,이심 대상으로 간단한 분석부터 복잡한 분석까지 데이터 분석이 필요한 모든 요청에 사용합니다.",
        "graph": bigquery_agent_node,  # DeepAgent 노드 함수 (LangGraph subgraph 아님)
        "enabled": True,
    },
    "esim_resend": {
        "type":         "chat",
        "display_name": "이심 재발송 에이전트",
        "description":  "고객의 eSIM QR코드를 재발송하는 에이전트. "
                        "담당자가 주문번호와 고객 이름을 입력하면 재발송 가능 여부를 확인 후 재발송을 처리합니다. "
                        "이심 재발송, QR코드 재발송, eSIM 재전송 등 이심 재발송 관련 요청에 사용합니다.",
        "enabled":      True,
        "graph":        esim_resend_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "이심 재발송 업무 진행 의사 여부. 담당자가 업무 취소 의사를 표현하면 false.",
                "required":    True,
            },
            "order_id": {
                "type":        "str",
                "description": "E로 시작하는 이심 인트라넷 주문번호 (예: E202501010001)",
                "required":    True,
            },
            "customer_name": {
                "type":        "str",
                "description": "재발송 대상 고객 이름 (주문자 이름)",
                "required":    True,
            },
            "resend_confirmed": {
                "type":        "bool",
                "description": "재발송 진행 의사. 담당자가 재발송에 동의하면 true, 취소하면 false.",
                "required":    True,
            },
        },
    },
    "esim_resend": {
        "type":         "chat",
        "display_name": "이심 재발송",
        "description":  "고객의 eSIM QR코드를 재발송하는 에이전트. "
                        "담당자가 주문번호를 입력하면 해당 주문의 이심 상품 목록을 조회하고, "
                        "재발송할 항목과 수신처(이메일/전화번호)를 확인하여 재발송을 처리합니다. "
                        "이심 재발송, QR 재전송, eSIM 재발송 등 재발송 관련 요청에 사용합니다.",
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
}

# ── 스케줄 에이전트 ────────────────────────────────────────────────────────────
"""
아직 구현된 스케줄 에이전트가 없어서 예시로 daily_report 하나만 주석 처리해둔 상태입니다. 스케줄 에이전트 추가할 때마다 registry에 정의하고 sync_registry.py에도 import + sync 코드 추가해주세요.
"daily_report": {
    "type":         "schedule",
    "display_name": "일일 리포트",
    "description":  "매일 오전 8시 영업 리포트를 구성원에게 발송합니다.",
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
    "stale_docs": {
        "type":         "trigger",
        "display_name": "지식베이스 검토 요청",
        "description":  "검토 기한이 지난 지식베이스 문서를 매일 오전 9시에 확인하여 담당자에게 검토를 요청합니다.",
        "enabled":      True,
        "interval":     {"hours": 24},
        "func":         check_stale_docs,
        "target":       "문서 담당자 DM",
    },
    "rag_index_reload": {
        "type":         "trigger",
        "display_name": "RAG 인덱스 동기화",
        "description":  "10분마다 Azure Blob Storage의 FAISS 인덱스 버전을 체크하여 변경 시 reload합니다.",
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
    enabled = await boa_agents_collection.find(
        {"type": "chat", "enabled": True},
        {"_id": 0, "agent_id": 1}
    ).to_list(None)
    enabled_names = {doc["agent_id"] for doc in enabled}
    return {
        name: info["description"]
        for name, info in AGENT_REGISTRY.items()
        if name in enabled_names
    }