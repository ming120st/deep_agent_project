"""
[자동 등록 로직]
  space_type == "DM"        → boa_members upsert  (개인)
  space_type == "ROOM"      → boa_spaces  upsert  (단체)
  SCHEDULE_REGISTRY, TRIGGER_REGISTRY 기반 에이전트 앱 시작 시 스케줄러에 자동 등록 (MongoDB enabled 상태 반영)

[부서 등록 로직]
  순수 Chat API 앱 (gcp-sa-chat) 기반.
  동기 응답(return {"cardsV2": ...})으로 카드 즉시 표시 가능.
  버튼 클릭은 type == "CARD_CLICKED", action.actionMethodName 으로 수신.
  formInputs는 common.formInputs 에서 파싱.

  실제 이벤트 구조 (확인됨):
    event.type                    → "MESSAGE" | "CARD_CLICKED" | "ADDED_TO_SPACE" 등
    event.user.email              → 유저 ID
    event.user.displayName        → 유저 이름
    event.space.name              → space ID
    event.space.type              → "DM" | "ROOM" | "GROUP_CHAT" 등
    event.message.text            → 메시지 텍스트
    event.message.thread.name     → 스레드 이름
    event.action.actionMethodName → 버튼 클릭 시 function 이름
    event.common.formInputs       → 카드 폼 입력값
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
from src.dashboard.device_job_router import router as device_job_router
from src.dashboard.members_router import router as members_router
from src.dashboard.spaces_router import router as spaces_router
from src.core.members_manager import upsert_member, update_member_department
from src.core.spaces_manager import upsert_space
from src.core.google_chat import send_message
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.agent.sync_registry import sync_registry_to_mongo
from src.dashboard.agents_router import router as agents_router
from src.agent.schedule.bq_analysis_schedule.schedule_manager import (
    register_bq_schedule,
    restore_bq_schedules,
    delete_bq_schedule,
    list_user_schedules,
    bq_schedules_collection,
    INTERVAL_LABELS,
)
from src.agent.schedule.bq_analysis_schedule.cards import (
    build_bq_schedule_card,
    build_schedule_confirm_message,
)


# ── 스케줄러 ──────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


@asynccontextmanager
async def lifespan(app: FastAPI):

    # 1. registry → MongoDB sync (앱 시작 시 1회)
    await sync_registry_to_mongo()

    # 2. RAG 엔진 초기화 (Blob → 없으면 MongoDB 전체 빌드)
    from src.core.rag_engine import init_rag_engine
    await init_rag_engine()

    # 2. 앱 시작 시 스케줄 에이전트 등록
    for agent_id, meta in SCHEDULE_REGISTRY.items():
        scheduler.add_job(
            meta["func"],
            trigger="cron",
            **meta["cron"],
            id=agent_id,
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
        )
        print(f"[Scheduler] 스케줄 등록: {agent_id}")

    # 3. 앱 시작 시 트리거 에이전트 등록
    for agent_id, meta in TRIGGER_REGISTRY.items():
        scheduler.add_job(
            meta["func"],
            trigger="interval",
            **meta["interval"],
            id=agent_id,
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        print(f"[Scheduler] 트리거 등록: {agent_id}")

    # 4. MongoDB에 저장된 BQ 분석 스케줄 복원 (서버 재시작 시 유지)
    await restore_bq_schedules(scheduler)

    # 5. MongoDB enabled=False인 job은 시작 시 바로 pause
    from src.core.mongo_client import boa_agents_collection
    cursor = boa_agents_collection.find({"enabled": False, "type": {"$in": ["schedule", "trigger"]}})
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
    secret_key=os.getenv("SESSION_SECRET_KEY", "")
)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(agents_router)
app.include_router(members_router)
app.include_router(spaces_router)
app.include_router(knowledge_router)
app.include_router(device_job_router)


# ── Google Chat Webhook ───────────────────────────────────────────────────────
@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    print(f"[DEBUG FULL EVENT] {json.dumps(event, ensure_ascii=False)}")

    event_type = event.get("type")

    # ── 스페이스 추가/제거 이벤트 무시 ────────────────────────────────────────
    if event_type in ("ADDED_TO_SPACE", "REMOVED_FROM_SPACE"):
        return {}

    # ── 공통 필드 파싱 (확인된 실제 구조 기반) ─────────────────────────────────
    user       = event.get("user", {})
    user_id    = user.get("email", "")
    user_name  = user.get("displayName", "")
    space      = event.get("space", {})
    space_name = space.get("name", "")
    space_type = space.get("type", "")
    display_name = space.get("displayName", "")

    # ── 카드 인터랙션 처리 ────────────────────────────────────────────────────
    if event_type == "CARD_CLICKED":

        # 개별 버튼 
        action_method = event.get("action", {}).get("actionMethodName", "")

        # BQ 분석 스케줄 등록
        # BQ 스케줄 등록
        if action_method == "submitBqSchedule":
            form_inputs = event.get("common", {}).get("formInputs", {})

            agent_name = form_inputs.get("bq_agent_name", {}).get("stringInputs", {}).get("value", [""])[0].strip()
            query      = form_inputs.get("bq_query",      {}).get("stringInputs", {}).get("value", [""])[0].strip()
            interval   = form_inputs.get("bq_interval",   {}).get("stringInputs", {}).get("value", ["daily"])[0]
            hour       = int(form_inputs.get("bq_hour",   {}).get("stringInputs", {}).get("value", ["8"])[0])
            minute     = int(form_inputs.get("bq_minute", {}).get("stringInputs", {}).get("value", ["0"])[0])

            if not agent_name:
                return {"text": "⚠️ 에이전트 이름을 입력해주세요."}
            if not query:
                return {"text": "⚠️ 분석 요청 내용을 입력해주세요."}

            try:
                schedule_id = await register_bq_schedule(
                    scheduler=scheduler,
                    user_id=user_id,
                    space_name=space_name,
                    agent_name=agent_name,
                    query=query,
                    hour=hour,
                    minute=minute,
                    interval=interval,
                )
            except ValueError as e:
                return {"text": f"⚠️ {e}\n다른 이름을 사용해주세요."}

            return {"text": build_schedule_confirm_message(agent_name, query, interval, hour, minute, schedule_id)}

        # BQ 분석 스케줄 삭제
        if action_method == "deleteBqSchedule":
            form_inputs = event.get("common", {}).get("formInputs", {})
            schedule_id = form_inputs.get("schedule_id", {}).get("stringInputs", {}).get("value", [""])[0]
            if schedule_id:
                deleted = await delete_bq_schedule(scheduler, schedule_id)
                msg = (f"🗑️ 스케줄 `{schedule_id[-8:]}` 이 삭제되었습니다."
                       if deleted else "⚠️ 스케줄을 찾을 수 없습니다.")
                return {"text": msg}
            return {}

        # 다른 카드 클릭 이벤트는 여기서 추가 처리
        return {}

    # ── 일반 메시지 처리 ───────────────────────────────────────────────────────
    if event_type != "MESSAGE":
        return {}

    message_obj    = event.get("message", {})
    message        = message_obj.get("text", "")
    thread_name    = message_obj.get("thread", {}).get("name")
    event_time     = event.get("eventTime", {})
    quoted_message = (
        message_obj.get("quotedMessageMetadata", {})
                   .get("quotedMessageSnapshot", {})
                   .get("text")
    )
    quoted_message_sender = (
        message_obj.get("quotedMessageMetadata", {})
                   .get("quotedMessageSnapshot", {})
                   .get("sender", {})
    )

    # ── DM/ROOM 정보 자동 등록 ─────────────────────────────────────────────────
    if space_type == "DM":
        await upsert_member(user_id, user_name, space_name)

    elif space_type in ("ROOM", "GROUP_CHAT", "SPACE"):
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

    # ── 슬래시 커맨드 처리 (LLM 없이 즉시 카드/텍스트 반환) ─────────────────────
    # 추가로 슬래시 커맨드로 처리할 기능이 있으면 여기에 if문 추가
    if message.strip() == "/데이터분석스케줄":
        return build_bq_schedule_card()

    if message.strip().startswith("/분석스케줄취소"):
        parts = message.strip().split()
        if len(parts) >= 2:
            short_id = parts[1]
            doc = await bq_schedules_collection.find_one(
                {"user_id": user_id, "schedule_id": {"$regex": short_id + "$"}}
            )
            if doc:
                await delete_bq_schedule(scheduler, doc["schedule_id"])
                return {"text": f"🗑️ 스케줄 `{short_id}` 이 삭제되었습니다."}
            return {"text": "⚠️ 해당 스케줄 ID를 찾을 수 없습니다."}
        return {"text": "사용법: `/분석스케줄취소 {스케줄ID}`"}

    if message.strip() == "/내스케줄":
        schedules = await list_user_schedules(user_id)
        if not schedules:
            return {"text": "등록된 분석 스케줄이 없습니다."}
        lines = ["📋 *등록된 데이터 분석 스케줄*\n"]
        for s in schedules:
            label    = INTERVAL_LABELS.get(s["interval"], s["interval"])
            time_str = f"{s['hour']:02d}:{s['minute']:02d}" if s["interval"] != "hourly" else "매시간"
            lines.append(f"• `{s['schedule_id'][-8:]}` | {label} {time_str}\n  _{s['query'][:40]}_")
        return {"text": "\n".join(lines)}

    background_tasks.add_task(orchestrate_chat_event, state, config)
    return {}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)