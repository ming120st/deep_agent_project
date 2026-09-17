from __future__ import annotations
from uuid import uuid4
import re
from typing import Any

from fastapi import (
    APIRouter,
    Request,
)

from core.state.session_manager import (
    session_manager,
)
from core.tracing.logger import (
    get_logger,
)
from integrations.google_chat.event_parser import (
    parse_google_chat_event,
)


router = APIRouter()
logger = get_logger("google_chat")


def _extract_text(
    content: Any,
) -> str:
    if isinstance(
        content,
        str,
    ):
        return content

    if isinstance(
        content,
        list,
    ):
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

    if hasattr(
        last_message,
        "content",
    ):
        content = last_message.content

    elif isinstance(
        last_message,
        dict,
    ):
        content = last_message.get(
            "content",
            "",
        )

    else:
        content = str(
            last_message
        )

    return _extract_text(
        content
    )

def _to_google_chat_format(
    text: str,
) -> str:
    """
    LLM이 반환한 일반 Markdown을
    Google Chat에서 보이기 좋은 형식으로 변환한다.
    """

    # Markdown bold
    # **text** -> *text*
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

    state = (
        snapshot.values
        if snapshot
        else {}
    )

    existing_task_id = state.get(
        "task_id"
    )

    if existing_task_id:
        return existing_task_id

    return str(uuid4())

@router.post("/chat")
async def google_chat(
    request: Request,
):
    deep_agent = (
        request.app.state.deep_agent
    )

    payload = await request.json()

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
            user_id=(
                chat_message.user_id
            ),
            space_id=(
                chat_message.space_id
            ),
        )
    )

    logger.info(
        (
            "[CHAT] "
            "google_thread=%s "
            "internal_thread=%s "
            "user_id=%s "
            "message=%r"
        ),
        google_thread_name,
        thread_id,
        chat_message.user_id,
        chat_message.text,
    )

    try:
        task_id = await _resolve_task_id(
            deep_agent=deep_agent,
            thread_id=thread_id,
        )
        result = await deep_agent.ainvoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            chat_message.text
                        ),
                    }
                ],
                "user_email": (
                    chat_message.user_email
                ),
                "session_id": (
                    thread_id
                ),
                "task_id": task_id,
            },
            config={
                "configurable": {
                    "thread_id": (
                        thread_id
                    ),
                    "task_id": task_id,
                }
            },
        )

        response_text = (
            _extract_response_text(
                result
            )
        )

        response_text = (
            _to_google_chat_format(
                response_text
            )
        )

        logger.info(
            (
                "[AGENT_RESPONSE] "
                "thread_id=%s "
                "response=%r"
            ),
            thread_id,
            response_text,
        )

        return {
            "text": response_text,
        }

    except Exception:
        logger.exception(
            (
                "[AGENT_ERROR] "
                "thread_id=%s"
            ),
            thread_id,
        )

        return {
            "text": (
                "요청 처리 중 오류가 발생했습니다."
            ),
        }