from __future__ import annotations

import asyncio
import re
from datetime import (
    datetime,
    timezone,
)
from typing import Any
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
from core.deep_agent.reload_manager import (
    agent_reload_manager,
)

router = APIRouter()
logger = get_logger(
    "google_chat"
)


async def _process_google_chat(
    deep_agent: Any,
    payload: dict,
) -> None:
    """
    Google Chat 요청을 실제 DeepAgent 작업으로 처리한다.

    HTTP Request 객체와 분리하여
    background task에서 독립적으로 실행한다.
    """

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

    logger.debug(
        "[GOOGLE_CHAT_PARSED] "
        "user_id=%r email=%r name=%r",
        chat_message.user_id,
        chat_message.user_email,
        chat_message.user_name,
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

        # =====================================================
        # 사용자 메시지 저장
        # =====================================================
        await session_repository.add_message(
            thread_id,
            {
                "role": "user",
                "content": (
                    chat_message.text
                ),
                "created_at": (
                    datetime.now(
                        timezone.utc
                    )
                ),
            },
        )

        # =====================================================
        # DeepAgent 실행
        # =====================================================
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
                "user_id": (
                    chat_message.user_email
                    or chat_message.user_id
                ),
                "user_email": (
                    chat_message.user_email
                ),
                "session_id": (
                    thread_id
                ),
                "task_id": (
                    task_id
                ),
            },
            config={
                "configurable": {
                    "thread_id": (
                        thread_id
                    ),
                    "user_id": (
                        chat_message.user_email
                        or chat_message.user_id
                    ),
                    "user_email": (
                        chat_message.user_email
                    ),
                    "google_user_id": (
                        chat_message.user_id
                    ),
                    "task_id": (
                        task_id
                    ),
                    "space_id": (
                        chat_message.space_id
                    ),
                    "google_thread_name": (
                        google_thread_name
                    ),
                }
            },
        )

        # =====================================================
        # Agent 응답 추출
        # =====================================================
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

        # =====================================================
        # Assistant 메시지 저장
        # =====================================================
        await session_repository.add_message(
            thread_id,
            {
                "role": "assistant",
                "content": (
                    response_text
                ),
                "created_at": (
                    datetime.now(
                        timezone.utc
                    )
                ),
            },
        )

        # =====================================================
        # Google Chat 전송
        # =====================================================
        await send_message(
            space_name=(
                chat_message.space_id
            ),
            message=response_text,
            thread_name=(
                google_thread_name
            ),
        )

    except asyncio.CancelledError:
        current_task = (
            asyncio.current_task()
        )

        task_name = (
            current_task.get_name()
            if current_task is not None
            else "unknown"
        )

        logger.warning(
            "[AGENT_CANCELLED] "
            "thread_id=%s "
            "task=%s",
            thread_id,
            task_name,
        )

        # CancelledError는 삼키지 않고
        # 상위 lifecycle로 다시 전달한다.
        raise

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
    messages = result[
        "messages"
    ]

    if not messages:
        return (
            "처리 결과가 없습니다."
        )

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
    deep_agent: Any,
    thread_id: str,
) -> str:
    snapshot = await deep_agent.aget_state(
        {
            "configurable": {
                "thread_id": (
                    thread_id
                ),
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
        or str(
            uuid4()
        )
    )


def _on_background_task_done(
    task: asyncio.Task,
) -> None:
    """
    Background Task 완료 후 예외를 회수한다.

    Cancel된 Task는 정상적인 cancellation 흐름으로 처리하고,
    예상하지 못한 예외만 로그로 남긴다.
    """

    if task.cancelled():
        return

    try:
        exception = task.exception()

    except asyncio.CancelledError:
        return

    if exception is not None:
        logger.error(
            "[BACKGROUND_TASK_ERROR] "
            "task=%s",
            task.get_name(),
            exc_info=(
                type(exception),
                exception,
                exception.__traceback__,
            ),
        )


@router.post(
    "/chat"
)
async def google_chat(
    request: Request,
):
    payload = (
        await request.json()
    )

    # SubAgent Prompt version 변경 여부 확인
    await agent_reload_manager.refresh_if_needed(
        request.app
    )

    # 변경이 있었다면 최신 DeepAgent 객체를 가져온다.
    # 이번 요청에서는 이 객체를 끝까지 사용한다.
    deep_agent = (
        request.app.state.deep_agent
    )

    background_tasks = (
        request.app.state.background_tasks
    )

    task = asyncio.create_task(
        _process_google_chat(
            deep_agent=deep_agent,
            payload=payload,
        ),
        name=(
            "google-chat-"
            f"{uuid4()}"
        ),
    )

    # Task에 대한 strong reference 유지
    background_tasks.add(
        task
    )

    def cleanup(
        completed_task: asyncio.Task,
    ) -> None:
        background_tasks.discard(
            completed_task
        )

        _on_background_task_done(
            completed_task
        )

    task.add_done_callback(
        cleanup
    )

    # Google Chat webhook에는 즉시 응답
    return {}