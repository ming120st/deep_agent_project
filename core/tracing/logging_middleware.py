import time
from datetime import datetime, timezone
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
)

from core.state.session_repository import (
    session_repository,
)
from core.tracing.logger import (
    get_logger,
)


logger = get_logger("tool_logging")


def _truncate_value(
    value: Any,
    max_length: int = 1000,
) -> str:
    text = str(value)

    if len(text) <= max_length:
        return text

    return text[:max_length] + "..."


class ToolLoggingMiddleware(
    AgentMiddleware
):
    async def awrap_tool_call(
        self,
        request,
        handler,
    ):
        tool_call = request.tool_call

        tool_name = tool_call["name"]
        tool_call_id = tool_call.get("id")
        args = tool_call.get("args", {})

        session_id = (
            request.state.get(
                "session_id"
            )
            if request.state
            else None
        )

        logger.info(
            "[TOOL_CALL] id=%s name=%s args=%s",
            tool_call_id,
            tool_name,
            _truncate_value(args),
        )

        started_at = datetime.now(
            timezone.utc
        )

        start = time.monotonic()

        try:
            result = await handler(request)

            elapsed = (
                time.monotonic()
                - start
            )

            logger.info(
                "[TOOL_RESULT] id=%s name=%s elapsed=%.2fs",
                tool_call_id,
                tool_name,
                elapsed,
            )

            if session_id:
                await session_repository.add_event(
                    session_id,
                    {
                        "type": "tool",
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "args": args,
                        "status": "success",
                        "result": _truncate_value(
                            result
                        ),
                        "elapsed": elapsed,
                        "started_at": started_at,
                        "finished_at": datetime.now(
                            timezone.utc
                        ),
                    },
                )

            return result

        except Exception as exc:
            elapsed = (
                time.monotonic()
                - start
            )

            logger.exception(
                "[TOOL_ERROR] id=%s name=%s elapsed=%.2fs",
                tool_call_id,
                tool_name,
                elapsed,
            )

            if session_id:
                await session_repository.add_event(
                    session_id,
                    {
                        "type": "tool",
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "args": args,
                        "status": "error",
                        "error": str(exc),
                        "elapsed": elapsed,
                        "started_at": started_at,
                        "finished_at": datetime.now(
                            timezone.utc
                        ),
                    },
                )

            raise