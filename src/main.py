"""
main.py — APScheduler + 구성원/스페이스 자동 등록 통합 버전

[자동 등록 로직]
  space_type == "DM"                    → boa_members upsert  (개인)
  space_type in ("GROUP_CHAT", "SPACE") → boa_spaces  upsert  (단체)
"""
import os
import json
import time
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, BackgroundTasks
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from src.orchestrator import orchestrate_chat_event
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph
from src.dashboard.auth import router as auth_router
from src.dashboard.task_router import router as dashboard_router
from src.dashboard.knowledge_router import router as knowledge_router
from src.dashboard.members_router import router as members_router
from src.dashboard.spaces_router import router as spaces_router
from src.proactive.report_runner import run_daily_report, upsert_member
from src.proactive.spaces_manager import upsert_space
from src.trigger.check_overdue_tasks import check_overdue_tasks
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.agent.sync import sync_registry_to_mongo
from src.dashboard.agents_router import router as agents_router


# ── 스케줄러 ──────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


@asynccontextmanager
async def lifespan(app: FastAPI):

    # 1. registry → MongoDB sync (앱 시작 시 1회)
    await sync_registry_to_mongo()

    # 2. 스케줄 에이전트 등록 (SCHEDULE_REGISTRY 기반으로 동적 등록)
    for agent_id, meta in SCHEDULE_REGISTRY.items():
        scheduler.add_job(
            meta["func"],
            trigger="cron",
            **meta["cron"],
            id=agent_id,            # ★ agent_id를 job id로 사용 (토글 시 참조)
            replace_existing=True,
            misfire_grace_time=600, # 스케줄 놓쳐도 최대 10분까지는 실행 (서버 재시작 등 상황 대비),
            coalesce=True, # 이전 스케줄이 아직 실행 중이면 다음 스케줄은 건너뛰고 바로 다음 스케줄로 (중복 실행 방지)
        )
        print(f"[Scheduler] 스케줄 등록: {agent_id}")

    # 3. 트리거 에이전트 등록 (TRIGGER_REGISTRY 기반으로 동적 등록)
    for agent_id, meta in TRIGGER_REGISTRY.items():
        scheduler.add_job(
            meta["func"],
            trigger="interval",
            **meta["interval"],
            id=agent_id,            # ★ agent_id를 job id로 사용 (토글 시 참조)
            replace_existing=True,
            misfire_grace_time=600, # 스케줄 놓쳐도 최대 10분까지는 실행 (서버 재시작 등 상황 대비),
            coalesce=True, # 이전 스케줄이 아직 실행 중이면 다음 스케줄은 건너뛰고 바로 다음 스케줄로 (중복 실행 방지)
            max_instances=1, # 트리거는 중복 실행 방지 위해 최대 1개 인스턴스만 허용
        )
        print(f"[Scheduler] 트리거 등록: {agent_id}")

    # 4. MongoDB enabled=False인 job은 시작 시 바로 pause
    from src.core.mongo_client import db
    cursor = db["boa_agents"].find({"enabled": False, "type": {"$in": ["schedule", "trigger"]}})
    disabled = await cursor.to_list(length=None)
    for agent in disabled:
        job = scheduler.get_job(agent["agent_id"])
        if job:
            job.pause()
            print(f"[Scheduler] 비활성 상태 유지: {agent['agent_id']}")

    scheduler.start()
    yield
    scheduler.shutdown()



# ── FastAPI 앱 ────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "SEI8209H4VNJSQW3DMW490G3NMIWKRENDV8204")
)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(knowledge_router)
app.include_router(members_router)
app.include_router(spaces_router)
app.include_router(agents_router)


    # ── Google Chat Webhook ───────────────────────────────────────────────────────
@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()

    chat_event = event.get("chat")
    if not chat_event or chat_event.get("removedFromSpacePayload") or chat_event.get("addedToSpacePayload"):
        return {}

    event_time    = chat_event.get("eventTime", {})
    user_name     = chat_event.get("user", {}).get("displayName")
    user_id       = chat_event.get("user", {}).get("email")
    message       = chat_event.get("messagePayload", {}).get("message", {}).get("text") or ""
    space         = chat_event.get("messagePayload", {}).get("space", {})
    space_name    = space.get("name") or ""
    space_type    = space.get("type") or ""
    display_name  = space.get("displayName") or ""
    thread_name   = chat_event.get("messagePayload", {}).get("message", {}).get("thread", {}).get("name")
    quoted_message = chat_event.get("messagePayload", {}).get("message", {}).get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {}).get("text")
    quoted_message_sender = chat_event.get("messagePayload", {}).get("message", {}).get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {}).get("sender", {})

    # ── 공간 유형별 자동 등록 ─────────────────────────────────────────────────
    if space_type == "DM":
        # 개인 → boa_members
        background_tasks.add_task(upsert_member, user_id, user_name, space_name)
    elif space_type in ("GROUP_CHAT", "SPACE"):
        # 단체방/스페이스 → boa_spaces
        background_tasks.add_task(upsert_space, space_name, display_name, space_type)

    # ── 세션 처리 ─────────────────────────────────────────────────────────────
    config   = {"configurable": {"thread_id": user_id}}
    existing = await asyncio.to_thread(main_graph.get_state, config)

    if existing.values:
        state        = GraphState(**existing.values)
        current_time = int(time.time())
        if state.last_activity_time and current_time - state.last_activity_time > 3600:
            print("세션 타임아웃 → 초기화")
            state = GraphState()
        else:
            print("기존 세션 유지")
    else:
        state = GraphState()
        print("새로운 세션 생성")

    state.last_activity_time = int(time.time())
    state.chat_context = {
        "user_id":               user_id,
        "user_name":             user_name,
        "space_name":            space_name,
        "space_type":            space_type,
        "thread_name":           thread_name,
        "quoted_message":        quoted_message,
        "quoted_message_sender": quoted_message_sender,
        "event_time":            event_time,
    }
    state.add_user_message(message)

    background_tasks.add_task(orchestrate_chat_event, state, config)
    return {}


# ── 리포트 수동 실행 (테스트용) ───────────────────────────────────────────────
@app.post("/report/trigger")
async def trigger_report(request: Request, background_tasks: BackgroundTasks):
    """
    Request body (선택): { "date": "2026-03-01" }
    생략 시 어제 날짜로 실행
    """
    body        = await request.json() if request.headers.get("content-type") == "application/json" else {}
    target_date = body.get("date")
    background_tasks.add_task(run_daily_report, target_date)
    return {"ok": True, "message": f"리포트 실행 시작 (date={target_date or '어제'})"}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)