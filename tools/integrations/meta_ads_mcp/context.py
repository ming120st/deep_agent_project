from __future__ import annotations

import secrets
import string

from langchain_core.runnables import (
    RunnableConfig,
)

from core.state.session_repository import (
    session_repository,
)


"""
Meta Ads MCP 호출에서 사용하는 세션 컨텍스트를 관리한다.

Deep Agent의 thread/session 단위로
Meta MCP의 client_conversation_id를 생성하고 MongoDB에 저장한다.

같은 사용자 대화 세션에서는 기존 conversation_id를 재사용하여
Meta MCP 호출 간 동일한 conversation context를 유지한다.
"""


def _generate_client_conversation_id() -> str:
    """
    Meta MCP에 전달할 client_conversation_id를 생성한다.
    """

    alphabet = (
        string.ascii_letters
        + string.digits
    )

    return "".join(
        secrets.choice(alphabet)
        for _ in range(20)
    )


def get_session_id(
    config: RunnableConfig,
) -> str:
    """
    LangGraph RunnableConfig에서
    현재 Deep Agent의 session/thread ID를 조회한다.
    """

    configurable = config.get(
        "configurable",
        {},
    )

    session_id = (
        configurable.get("thread_id")
        or configurable.get("session_id")
    )

    if not session_id:
        raise RuntimeError(
            "RunnableConfig에서 "
            "session_id를 찾을 수 없습니다."
        )

    return session_id


async def get_meta_client_conversation_id(
    config: RunnableConfig,
) -> str:
    """
    현재 Deep Agent 세션에 연결된
    Meta MCP client_conversation_id를 반환한다.

    기존 ID가 있으면 재사용하고,
    없으면 새로 생성한 뒤 session_repository에 저장한다.
    """

    session_id = get_session_id(
        config
    )

    session = await session_repository.get(
        session_id
    )

    if session:
        conversation_id = (
            session
            .get(
                "integration_context",
                {},
            )
            .get(
                "meta_ads",
                {},
            )
            .get(
                "client_conversation_id"
            )
        )

        if conversation_id:
            return conversation_id

    conversation_id = (
        _generate_client_conversation_id()
    )

    await session_repository.update(
        session_id,
        {
            "integration_context.meta_ads."
            "client_conversation_id": (
                conversation_id
            ),
        },
    )

    return conversation_id