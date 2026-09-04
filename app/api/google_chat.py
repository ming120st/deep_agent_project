from typing import Any

from fastapi import APIRouter, Request

from integrations.google_chat.event_parser import (
    parse_google_chat_event,
)
from core.tracing.logger import get_logger
from core.state.session_manager import (
    session_manager,
)

router = APIRouter()
logger = get_logger("google_chat")

def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
        )

    return str(content)


def _extract_response_text(
    result: dict,
) -> str:
    messages = result.get(
        "messages",
        [],
    )

    if not messages:
        return "처리 결과가 없습니다."

    last_message = messages[-1]

    if hasattr(last_message, "content"):
        content = last_message.content

    elif isinstance(last_message, dict):
        content = last_message.get(
            "content",
            "",
        )

    else:
        content = str(last_message)

    return _extract_text(content)


@router.post("/chat")
async def google_chat(
    request: Request,
):
    deep_agent = request.app.state.deep_agent

    payload = await request.json()

    google_thread_name = (
        payload.get("message", {})
        .get("thread", {})
        .get("name")
    )

    chat_message = parse_google_chat_event(
        payload
    )
    thread_id = session_manager.get_or_create(
        user_id=chat_message.user_id,
        space_id=chat_message.space_id,
    )
    logger.info(
        "[CHAT] google_thread=%s internal_thread=%s user_id=%s message=%r",
        google_thread_name,
        thread_id,
        chat_message.user_id,
        chat_message.text,
    )

    result = await deep_agent.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": chat_message.text,
                }
            ],
        },
        config={
            "configurable": {
                "thread_id": thread_id,
            }
        },
    )

    response_text = _extract_response_text(
        result
    )

    return {
        "text": response_text,
    }