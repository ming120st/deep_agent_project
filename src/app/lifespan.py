from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import FastAPI

from src.agent.analytics.subagents.schedule.bq_analysis_schedule.schedule_manager import (
    restore_bq_schedules,
)
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.agent.sync_registry import sync_registry_to_mongo

# .env 파일을 로드해 런타임에서 필요한 환경변수(시크릿/설정값)를 사용할 수 있게 한다.
load_dotenv()

# 스케줄/트리거 에이전트는 앱 시작 시 이 scheduler에 등록된다.
# 전역 스케줄러 단일 인스턴스: 앱 전체에서 동일 객체를 공유한다.
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

# 앱 런타임 준비
async def _initialize_app_runtime() -> None:
    # 코드에 정의된 에이전트 레지스트리를 DB와 동기화한다.
    # (서버 재시작 시 레지스트리 불일치 방지)
    await sync_registry_to_mongo()

    # 지연 import: 초기 import 비용/순환 의존 리스크를 줄이기 위해 함수 내부에서 로드한다.
    from src.core.rag_engine import init_rag_engine

    # RAG 엔진(검색/생성 관련 리소스) 초기화
    await init_rag_engine()

    # 지연 import: startup 시점에만 필요한 인덱스 보장 로직을 불러온다.
    from src.dashboard.knowledge_v2_router import ensure_kb_v2_indexes

    # 지식베이스 v2 인덱스를 보장한다.
    # (없으면 생성, 있으면 유지)
    await ensure_kb_v2_indexes()


async def _bootstrap_scheduler_jobs() -> None:
    # 코드 레지스트리 기반 정적 스케줄(cron) 등록
    _register_registry_schedule_jobs()
    # 코드 레지스트리 기반 트리거(interval) 등록
    _register_registry_trigger_jobs()
    # DB에 저장된 사용자/운영 스케줄을 런타임 scheduler로 복원
    await restore_bq_schedules(scheduler)
    # DB에서 비활성 상태인 에이전트 잡은 실행되지 않도록 pause 유지
    await _pause_disabled_registry_jobs()


def _register_registry_schedule_jobs() -> None:
    # SCHEDULE_REGISTRY의 각 항목(agent_id, meta)을 순회하며 cron 잡으로 등록
    for agent_id, meta in SCHEDULE_REGISTRY.items():
        scheduler.add_job(
            # 실제 실행할 함수
            meta["func"],
            # cron 스케줄 타입
            trigger="cron",
            # cron 파라미터(minute/hour/day_of_week 등) 전달
            **meta["cron"],
            # 잡 고유 식별자
            id=agent_id,
            # 동일 id 잡이 이미 있으면 교체
            replace_existing=True,
            # 스케줄 미스 발생 시 10분까지는 실행 허용
            misfire_grace_time=600,
            # 누적 트리거를 하나로 합쳐 폭주를 방지
            coalesce=True,
            # 동일 잡 동시 실행 방지
            max_instances=1,
        )
        # 운영 로그: 어떤 스케줄이 등록됐는지 추적
        print(f"[Scheduler] 스케줄 등록: {agent_id}")


def _register_registry_trigger_jobs() -> None:
    # TRIGGER_REGISTRY의 각 항목(agent_id, meta)을 순회하며 interval 잡으로 등록
    for agent_id, meta in TRIGGER_REGISTRY.items():
        scheduler.add_job(
            # 실제 실행할 함수
            meta["func"],
            # interval 스케줄 타입
            trigger="interval",
            # interval 파라미터(seconds/minutes 등) 전달
            **meta["interval"],
            # 잡 고유 식별자
            id=agent_id,
            # 동일 id 잡이 이미 있으면 교체
            replace_existing=True,
            # 스케줄 미스 발생 시 10분까지는 실행 허용
            misfire_grace_time=600,
            # 누적 트리거를 하나로 합쳐 폭주를 방지
            coalesce=True,
            # 동일 잡 동시 실행 방지
            max_instances=1,
        )
        # 운영 로그: 어떤 트리거가 등록됐는지 추적
        print(f"[Scheduler] 트리거 등록: {agent_id}")


async def _pause_disabled_registry_jobs() -> None:
    # 에이전트 on/off 상태가 저장된 컬렉션
    from src.core.mongo_client import boa_agent_registry_collection

    # 비활성(enabled=False)이고 schedule/trigger 타입인 에이전트만 조회
    cursor = boa_agent_registry_collection.find(
        {"enabled": False, "agent_type": {"$in": ["schedule", "trigger"]}}
    )
    # 전체 결과를 리스트로 가져온다.
    disabled_agents = await cursor.to_list(length=None)

    # 조회된 비활성 에이전트를 순회하며 현재 scheduler 잡을 pause 처리
    for agent in disabled_agents:
        # 등록된 job id는 agent_id와 동일하다.
        job = scheduler.get_job(agent["agent_id"])
        # 잡이 실제로 존재할 때만 pause
        if job:
            job.pause()
            # 운영 로그: 비활성 상태 유지 확인
            print(f"[Scheduler] 비활성 상태 유지: {agent['agent_id']}")


# FastAPI lifespan 훅: startup/yield/shutdown 구간을 정의
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 전 공통 런타임 준비
    await _initialize_app_runtime()
    # 앱 시작 전 스케줄러 잡 등록/복원/비활성 반영
    await _bootstrap_scheduler_jobs()
    # scheduler를 실제 동작 상태로 시작
    scheduler.start()

    # yield 위 부분은 앱 시작 시 실행, yield 아래 부분은 앱 종료 시 실행된다.
    yield

    # 앱 종료 시 scheduler 리소스를 정상 정리
    scheduler.shutdown()


def get_scheduler() -> AsyncIOScheduler:
    # 다른 모듈(예: 라우터)에서 동일 scheduler 인스턴스를 참조할 수 있게 제공
    return scheduler