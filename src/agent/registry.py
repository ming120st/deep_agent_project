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
from src.agent.chat.bigquery_agent.agent import bigquery_agent_node
# from src.agent.xxx.graph import xxx_graph  ← graph 쓰는 에이전트 추가할 때마다 import

from src.agent.schedule.daily_sales_report.report_runner import run_daily_report
from src.agent.trigger.check_overdue_tasks import check_overdue_tasks
from src.agent.trigger.check_stale_docs import check_stale_docs
from src.core.rag_engine import reload_rag_index_if_updated
# from src.triggers.xxx import xxx  ← 트리거 추가할 때마다 import

from src.core.mongo_client import boa_agents_collection

# ── 대화형 에이전트 ────────────────────────────────────────────────────────────
AGENT_REGISTRY: dict = {
    "wifi_usage_inquiry": {
        "type":         "chat",
        "display_name": "와이파이 사용량 조회",
        "description":  "와이파이도시락 데이터 사용량을 조회하여 알려주는 에이전트입니다.",
        "enabled":      True,   # 초기값 (MongoDB에 없을 때만 사용)
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
    "bigquery_agent": {
        "type": "chat",
        "display_name": "실적 분석 에이전트",
        "description": "BigQuery 데이터 조회 및 매출 분석 리포트 생성. "
                       "'지난달 매출 분석', '이번 주 판매 현황', '상품별 매출 비교' 등 "
                       "데이터 분석이 필요한 모든 요청에 사용합니다.",
        "graph": bigquery_agent_node,  # DeepAgent 노드 함수 (LangGraph subgraph 아님)
        "enabled": True,
    },
}

# ── 스케줄 에이전트 ────────────────────────────────────────────────────────────
SCHEDULE_REGISTRY: dict = {
    "daily_report": {
        "type":         "schedule",
        "display_name": "일일 리포트",
        "description":  "매일 오전 8시 영업 리포트를 구성원에게 발송합니다.",
        "enabled":      True,
        "cron":         {"hour": 8, "minute": 0},   # APScheduler cron 파라미터
        "func":         run_daily_report,
    },
}

# ── 트리거 에이전트 ────────────────────────────────────────────────────────────
TRIGGER_REGISTRY: dict = {
    "overdue_tasks": {
        "type":         "trigger",
        "display_name": "기한 초과 알림",
        "description":  "기한이 지난 미완료 태스크를 10분마다 확인하여 담당자에게 알립니다.",
        "enabled":      True,
        "interval":     {"minutes": 10},
        "func":         check_overdue_tasks,
    },
    "stale_docs": {
        "type":         "trigger",
        "display_name": "지식베이스 검토 요청",
        "description":  "검토 기한이 지난 지식베이스 문서를 매일 오전 9시에 확인하여 담당자에게 검토를 요청합니다.",
        "enabled":      True,
        "interval":     {"hours": 24},
        "func":         check_stale_docs,
    },
    "rag_index_reload": {
        "type":         "trigger",
        "display_name": "RAG 인덱스 동기화",
        "description":  "10분마다 Azure Blob Storage의 FAISS 인덱스 버전을 체크하여 변경 시 reload합니다.",
        "enabled":      True,
        "interval":     {"minutes": 10},
        "func":         reload_rag_index_if_updated,
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