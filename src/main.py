import os
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv
load_dotenv()
from src.orchestrator import orchestrate_chat_event
from src.core.mongo_client import checkpointer
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph
from starlette.middleware.sessions import SessionMiddleware
from src.dashboard.auth import router as auth_router
from src.dashboard.task_router import router as dashboard_router
import time
import asyncio
from src.dashboard.knowledge_router import router as knowledge_router

app = FastAPI()

app.include_router(knowledge_router)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "SEI8209H4VNJSQW3DMW490G3NMIWKRENDV8204")
)

app.include_router(auth_router)
app.include_router(dashboard_router)

@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()

    if event.get("chat").get("removedFromSpacePayload") or event.get("chat").get("addedToSpacePayload"):
        return {}

    event_time = event.get("chat", {}).get("eventTime", {})
    user_name = event.get("chat", {}).get("user", {}).get("displayName")
    user_id = event.get("chat", {}).get("user", {}).get("email")
    message = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("text") or ""
    space_name = event.get("chat", {}).get("messagePayload", {}).get("space", {}).get("name") or ""
    thread_name = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("thread", {}).get("name")
    quoted_message = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {}).get("text")
    quoted_message_sender = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {}).get("sender", {})

    # ── 체크포인터에서 기존 state 로드 ──────────────────────────────
    config = {"configurable": {"thread_id": user_id}}
    existing = await asyncio.to_thread(main_graph.get_state, config)

    if existing.values:
        state = GraphState(**existing.values)
        # 타임아웃 체크
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
        "user_id": user_id,
        "user_name": user_name,
        "space_name": space_name,
        "thread_name": thread_name,
        "quoted_message": quoted_message,
        "quoted_message_sender": quoted_message_sender,
        "event_time": event_time
    }
    state.add_user_message(message)

    background_tasks.add_task(orchestrate_chat_event, state, config)

    return {}

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)