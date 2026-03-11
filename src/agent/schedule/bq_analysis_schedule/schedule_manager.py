"""
src/agent/schedule/bq_analysis_schedule/schedule_manager.py

BQ 분석 스케줄 CRUD + APScheduler 동적 등록/해제.

[컬렉션 — boa_bq_schedules]
  schedule_id : str      (고유 ID, APScheduler job ID와 동일)
  agent_name  : str      (사용자 입력 에이전트명, 중복 불가)
  user_id     : str      (Google Chat 유저 이메일)
  space_name  : str      (결과 전송 대상 space)
  query       : str      (분석 요청 자연어 — 대시보드 설명 컬럼에 표시)
  hour        : int      (수행 시간, 0~23)
  minute      : int      (수행 분, 0 또는 30)
  interval    : str      ("daily" | "weekly" | "hourly")
  enabled     : bool
  created_at  : datetime

[복원 전략]
  앱 재시작 시 restore_bq_schedules() 호출 → enabled=True 항목 전부 APScheduler 재등록
"""
import uuid
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.mongo_client import db
from src.agent.schedule.bq_analysis_schedule.runner import run_scheduled_bq_analysis

logger = logging.getLogger(__name__)

bq_schedules_collection = db["boa_bq_schedules"]

INTERVAL_LABELS = {
    "daily":  "매일",
    "weekly": "매주 월요일",
    "hourly": "매시간",
}


# ── ID 생성 ────────────────────────────────────────────────────────────────────

def _make_schedule_id(user_id: str) -> str:
    short = uuid.uuid4().hex[:8]
    prefix = user_id.split("@")[0][:10]
    return f"bq_{prefix}_{short}"


# ── 이름 중복 체크 ─────────────────────────────────────────────────────────────

async def check_agent_name_duplicate(agent_name: str) -> bool:
    """
    에이전트 이름 중복 여부 반환.
    True = 중복 존재, False = 사용 가능
    """
    existing = await bq_schedules_collection.find_one({"agent_name": agent_name})
    return existing is not None


# ── APScheduler 등록 (내부) ───────────────────────────────────────────────────

def _add_to_scheduler(
    scheduler: AsyncIOScheduler,
    schedule_id: str,
    query: str,
    space_name: str,
    hour: int,
    minute: int,
    interval: str,
) -> None:
    """APScheduler에 job 추가. interval 타입별 트리거 분기."""
    kwargs = {"query": query, "space_name": space_name, "schedule_id": schedule_id}
    common = dict(
        id=schedule_id,
        replace_existing=True,
        misfire_grace_time=600,
        coalesce=True,
        kwargs=kwargs,
    )

    if interval == "hourly":
        scheduler.add_job(run_scheduled_bq_analysis, trigger="interval", hours=1, **common)
    elif interval == "weekly":
        scheduler.add_job(
            run_scheduled_bq_analysis, trigger="cron",
            day_of_week="mon", hour=hour, minute=minute, **common,
        )
    else:  # daily (기본값)
        scheduler.add_job(
            run_scheduled_bq_analysis, trigger="cron",
            hour=hour, minute=minute, **common,
        )


# ── Public API ─────────────────────────────────────────────────────────────────

async def register_bq_schedule(
    scheduler: AsyncIOScheduler,
    user_id: str,
    space_name: str,
    agent_name: str,
    query: str,
    hour: int,
    minute: int,
    interval: str,
) -> str:
    """
    스케줄 등록 (MongoDB 저장 + APScheduler 등록).

    Returns:
        schedule_id: 등록된 스케줄 ID

    Raises:
        ValueError: agent_name 중복 시
    """
    if await check_agent_name_duplicate(agent_name):
        raise ValueError(f"이미 사용 중인 에이전트 이름입니다: '{agent_name}'")

    schedule_id = _make_schedule_id(user_id)

    await bq_schedules_collection.insert_one({
        "schedule_id": schedule_id,
        "agent_name":  agent_name,
        "user_id":     user_id,
        "space_name":  space_name,
        "query":       query,
        "hour":        hour,
        "minute":      minute,
        "interval":    interval,
        "enabled":     True,
        "created_at":  datetime.utcnow(),
    })

    _add_to_scheduler(scheduler, schedule_id, query, space_name, hour, minute, interval)
    logger.info(f"[BqScheduleManager] 등록: {schedule_id} ({agent_name}) / {interval} {hour:02d}:{minute:02d}")
    return schedule_id


async def restore_bq_schedules(scheduler: AsyncIOScheduler) -> None:
    """앱 재시작 시 MongoDB → APScheduler 복원."""
    cursor = bq_schedules_collection.find({"enabled": True})
    docs = await cursor.to_list(length=None)

    for doc in docs:
        _add_to_scheduler(
            scheduler,
            doc["schedule_id"],
            doc["query"],
            doc["space_name"],
            doc.get("hour", 8),
            doc.get("minute", 0),
            doc.get("interval", "daily"),
        )
        logger.info(f"[BqScheduleManager] 복원: {doc['schedule_id']} ({doc.get('agent_name', '')})")

    logger.info(f"[BqScheduleManager] 총 {len(docs)}개 스케줄 복원 완료")


async def toggle_bq_schedule(
    scheduler: AsyncIOScheduler,
    schedule_id: str,
    enabled: bool,
) -> bool:
    """
    활성화/비활성화 토글 (대시보드 연동).

    Returns:
        True = 성공, False = schedule_id 없음
    """
    result = await bq_schedules_collection.update_one(
        {"schedule_id": schedule_id},
        {"$set": {"enabled": enabled, "updated_at": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        return False

    job = scheduler.get_job(schedule_id)
    if job:
        job.resume() if enabled else job.pause()

    logger.info(f"[BqScheduleManager] {'활성화' if enabled else '비활성화'}: {schedule_id}")
    return True


async def delete_bq_schedule(scheduler: AsyncIOScheduler, schedule_id: str) -> bool:
    """스케줄 삭제 (MongoDB + APScheduler)."""
    result = await bq_schedules_collection.delete_one({"schedule_id": schedule_id})
    job = scheduler.get_job(schedule_id)
    if job:
        job.remove()
    deleted = result.deleted_count > 0
    logger.info(f"[BqScheduleManager] 삭제: {schedule_id} / 결과: {deleted}")
    return deleted


async def list_user_schedules(user_id: str) -> list:
    """사용자의 스케줄 목록 반환."""
    cursor = bq_schedules_collection.find({"user_id": user_id}, {"_id": 0})
    return await cursor.to_list(length=None)