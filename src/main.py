"""
[자동 등록 로직]
  space_type == "DM"        → boa_members upsert  (개인)
  space_type == "ROOM"      → boa_spaces  upsert  (단체)
  SCHEDULE_REGISTRY, TRIGGER_REGISTRY 기반 에이전트 앱 시작 시 스케줄러에 자동 등록 (MongoDB enabled 상태 반영)

[부서 등록 로직]
  이 앱은 Google Workspace Events(Pub/Sub) 방식으로 이벤트를 수신합니다.
  Google Chat은 웹훅에 대해 HTTP 200만 기대하므로, 모든 메시지는
  Chat REST API를 통해 비동기로 전송합니다. (return {} 후 background_tasks로 처리)

  DM 수신 시 upsert_member → department 미설정이면 send_department_card() 비동기 발송
  카드 등록 버튼 클릭 → commonEventObject.invokedFunction == "submitDepartment"
                      → formInputs에서 선택된 부서 파싱 → MongoDB 저장 → 완료 메시지
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
from src.agent.schedule.daily_sales_report.report_runner import run_daily_report
from src.core.members_manager import upsert_member, update_member_department
from src.core.spaces_manager import upsert_space
from src.core.google_chat import send_message, send_department_card
from src.agent.trigger.check_overdue_tasks import check_overdue_tasks
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.agent.sync_registry import sync_registry_to_mongo
from src.dashboard.agents_router import router as agents_router


# ── 스케줄러 ──────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


@asynccontextmanager
async def lifespan(app: FastAPI):

    # 1. registry → MongoDB sync (앱 시작 시 1회)
    await sync_registry_to_mongo()

    # 2. 앱 시작 시 스케줄 에이전트 등록 (SCHEDULE_REGISTRY 기반으로 동적 등록)
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

    # 3. 앱 시작 시 트리거 에이전트 등록 (TRIGGER_REGISTRY 기반으로 동적 등록)
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

    # 4. MongoDB enabled=False인 job은 시작 시 바로 pause
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


# ── Google Chat Webhook ───────────────────────────────────────────────────────
@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    print(f"[DEBUG FULL EVENT] {json.dumps(event, ensure_ascii=False)}")

    chat_event = event.get("chat")
    if not chat_event or chat_event.get("removedFromSpacePayload") or chat_event.get("addedToSpacePayload"):
        return {}

    # ── 카드 버튼 클릭 처리 (부서 등록 폼 제출) ────────────────────────────────
    # Pub/Sub 방식: 버튼 클릭 이벤트도 commonEventObject.invokedFunction으로 들어옴
    common           = event.get("commonEventObject", {})
    invoked_function = common.get("invokedFunction", "")

    if invoked_function == "submitDepartment":
        # selectionInput(RADIO_BUTTON) 값은 formInputs["department"]["stringInputs"]["value"][0]
        form_inputs = common.get("formInputs", {})
        dept = (
            form_inputs.get("department", {})
                       .get("stringInputs", {})
                       .get("value", [""])[0]
        )
        user_id    = chat_event.get("user", {}).get("email")
        space_name = chat_event.get("messagePayload", {}).get("space", {}).get("name") or ""

        print(f"[Dept] 부서 선택: user={user_id}, dept={dept}")

        if user_id and dept:
            await update_member_department(user_id, dept)
            if space_name:
                background_tasks.add_task(
                    send_message, space_name,
                    f"✅ *{dept}* 으로 등록되었습니다. BOA를 이용해 주세요!"
                )
        return {}

    # ── 일반 메시지 처리 ───────────────────────────────────────────────────────
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

    # ── DM/ROOM 정보 자동 등록 ─────────────────────────────────────────────────
    if space_type == "DM":
        needs_dept = await upsert_member(user_id, user_name, space_name)
        if needs_dept:
            print(f"[Dept] department 미설정 → 카드 발송: {user_id}")
            # Pub/Sub 방식: return {} 후 Chat REST API로 비동기 발송
            background_tasks.add_task(send_department_card, space_name)
            return {}

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

    background_tasks.add_task(orchestrate_chat_event, state, config)
    return {}


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)
