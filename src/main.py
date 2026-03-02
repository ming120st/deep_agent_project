import os
import json
from fastapi import FastAPI, Request, BackgroundTasks
import uvicorn
from dotenv import load_dotenv
load_dotenv()
from src.orchestrator import orchestrate_chat_event
from src.core.session_manager import get_or_create_state
from starlette.middleware.sessions import SessionMiddleware
from src.dashboard.auth import router as auth_router, get_current_user, require_login
# HTML 렌더링을 위한 템플릿 엔진
from src.dashboard.router import router as dashboard_router

app = FastAPI()

# ── 세션 미들웨어 (OAuth에 필수) ──────────────────────────────
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "SEI8209H4VNJSQW3DMW490G3NMIWKRENDV8204")
)

app.include_router(auth_router)
app.include_router(dashboard_router)

@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    # print(f"Received event: {json.dumps(event, ensure_ascii=False)}")

    # space 추가/삭제 이벤트는 무시하고 빈 응답 반환
    if event.get("chat").get("removedFromSpacePayload") or event.get("chat").get("addedToSpacePayload"):
        return {}

    # 주요 데이터 추출
    event_time = event.get("chat", {}).get("eventTime", {})
    user_name = event.get("chat", {}).get("user", {}).get("displayName")
    user_id = event.get("chat", {}).get("user", {}).get("email")
    message = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("text") or ""
    space_name = event.get("chat", {}).get("messagePayload", {}).get("space", {}).get("name") or ""
    thread_name = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("thread", {}).get("name")
    quoted_message = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {}).get("text")
    quoted_message_sender = event.get("chat", {}).get("messagePayload", {}).get("message", {}).get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {}).get("sender", {})

    state = await get_or_create_state(user_id)
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

    background_tasks.add_task(orchestrate_chat_event, state)

    return {}

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)