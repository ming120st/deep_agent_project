"""
src/agents/registry.py

[설계 원칙]
  - 코드: 에이전트 메타데이터 + 실행 로직 참조만 정의
  - MongoDB(boa_agents): enabled 상태 영구 저장
  - 앱 시작 시 registry → MongoDB sync (enabled는 덮어쓰지 않음)

[타입]
  chat     → Google Chat 메시지 트리거 (AGENT_REGISTRY)
  schedule → cron 기반 주기 실행     (SCHEDULE_REGISTRY)
  trigger  → 조건 기반 이벤트 실행   (TRIGGER_REGISTRY)
"""

from src.agent.wifi_usage_inquiry.graph import wifi_usage_inquiry_graph
# from src.workflow.agents.xxx.graph import xxx_graph  ← 추가할 때마다 import

from src.proactive.report_runner import run_daily_report
from src.trigger.check_overdue_tasks import check_overdue_tasks
# from src.triggers.xxx import xxx  ← 추가할 때마다 import


# ── 대화형 에이전트 ────────────────────────────────────────────────────────────
AGENT_REGISTRY: dict = {
    "wifi_usage_inquiry": {
        "type":         "chat",
        "display_name": "와이파이 사용량 조회",
        "description":  "와이파이도시락 데이터 사용량을 조회하여 알려주는 에이전트입니다.",
        "enabled":      True,   # 초기값 (MongoDB에 없을 때만 사용)
        "graph":        wifi_usage_inquiry_graph,
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
        "interval":     {"minutes": 10},            # APScheduler interval 파라미터
        "func":         check_overdue_tasks,
    },
}

# ── 전체 통합 (대시보드/sync용) ────────────────────────────────────────────────
ALL_REGISTRIES: dict = {
    **AGENT_REGISTRY,
    **SCHEDULE_REGISTRY,
    **TRIGGER_REGISTRY,
}
