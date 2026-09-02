from typing import Any
from fastapi import APIRouter, Request
from integrations.google_chat.event_parser import (
    parse_google_chat_event,
)
from core.tracing.logger import get_logger

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
    messages = result.get("messages", [])

    if not messages:
        return "처리 결과가 없습니다."

    last_message = messages[-1]

    content = (
        last_message.content
        if hasattr(last_message, "content")
        else last_message
    )

    return _extract_text(content)

@router.post("/chat")
async def google_chat(request: Request):
    deep_agent = request.app.state.deep_agent

    chat_message = parse_google_chat_event(
        await request.json()
    )

    session_id = chat_message.thread_id

    logger.info(
        "[CHAT] session_id=%s user_id=%s message=%r",
        session_id,
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
            "session_id": session_id,
            "user_id": chat_message.user_id,
            "current_input": chat_message.text,
            "context": {},
        },
        config={
            "configurable": {
                "thread_id": session_id, 
            }
        },
    )

    response_text = _extract_response_text(
        result
    )

    logger.info(
        "[CHAT_RESPONSE] session_id=%s response=%r",
        session_id,
        response_text,
    )

    return {
        "text": response_text
    }
