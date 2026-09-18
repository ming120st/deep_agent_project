from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import (
    APIRouter,
    Request,
)

from core.state.session_manager import (
    session_manager,
)
from core.state.session_repository import (
    session_repository,
)
from core.tracing.logger import (
    get_logger,
)
from integrations.google_chat.client import (
    send_message,
)
from integrations.google_chat.event_parser import (
    parse_google_chat_event,
)


router = APIRouter()
logger = get_logger("google_chat")


async def _process_google_chat(
    request: Request,
    payload: dict,
):
    deep_agent = (
        request.app.state.deep_agent
    )

    google_thread_name = (
        payload.get(
            "message",
            {},
        )
        .get(
            "thread",
            {},
        )
        .get(
            "name"
        )
    )

    chat_message = (
        parse_google_chat_event(
            payload
        )
    )

    thread_id = (
        session_manager
        .get_or_create(
            user_id=chat_message.user_id,
            space_id=chat_message.space_id,
        )
    )

    try:
        task_id = await _resolve_task_id(
            deep_agent=deep_agent,
            thread_id=thread_id,
        )

        await session_repository.add_message(
            thread_id,
            {
                "role": "user",
                "content": chat_message.text,
                "created_at": datetime.now(
                    timezone.utc
                ),
            },
        )

        result = await deep_agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": chat_message.text,
                    }
                ],
                "user_email": chat_message.user_email,
                "session_id": thread_id,
                "task_id": task_id,
            },
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "task_id": task_id,
                }
            },
        )

        response_text = _extract_response_text(
            result
        )

        response_text = _to_google_chat_format(
            response_text
        )

        await session_repository.add_message(
            thread_id,
            {
                "role": "assistant",
                "content": response_text,
                "created_at": datetime.now(
                    timezone.utc
                ),
            },
        )

        await send_message(
            space_name=chat_message.space_id,
            message=response_text,
            thread_name=google_thread_name,
        )

    except Exception:
        logger.exception(
            "[AGENT_ERROR] "
            "thread_id=%s",
            thread_id,
        )


def _extract_text(
    content: str | list,
) -> str:
    if isinstance(
        content,
        str,
    ):
        return content

    return "".join(
        block.get(
            "text",
            "",
        )
        for block in content
        if isinstance(
            block,
            dict,
        )
    )


def _extract_response_text(
    result: dict,
) -> str:
    messages = result["messages"]

    if not messages:
        return "처리 결과가 없습니다."

    return _extract_text(
        messages[-1].content
    )


def _to_google_chat_format(
    text: str,
) -> str:
    """
    LLM이 반환한 일반 Markdown을
    Google Chat에서 보이기 좋은 형식으로 변환한다.
    """

    text = re.sub(
        r"\*\*(.+?)\*\*",
        r"*\1*",
        text,
    )

    return text


async def _resolve_task_id(
    deep_agent,
    thread_id: str,
) -> str:
    snapshot = await deep_agent.aget_state(
        {
            "configurable": {
                "thread_id": thread_id,
            }
        }
    )

    existing_task_id = (
        snapshot
        .values
        .get(
            "task_id"
        )
    )

    return (
        existing_task_id
        or str(uuid4())
    )


@router.post("/chat")
async def google_chat(
    request: Request,
):
    payload = await request.json()

    asyncio.create_task(
        _process_google_chat(
            request,
            payload,
        )
    )

    return {}