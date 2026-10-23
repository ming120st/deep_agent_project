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

BROWSER_TOOLS = {
    "start_browser", "browser_action", "get_browser_state", "close_browser",
    "analyze_browser_screen", "execute_browser_task",
}


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
        sensitive = tool_name in BROWSER_TOOLS
        logged_args = {"redacted": True} if sensitive else args

        session_id = (
            request.state.get(
                "session_id"
            )
            if request.state
            else None
        )
        if not session_id:
            runtime = getattr(request, "runtime", None)
            cfg = (getattr(runtime, "config", None) or {}).get("configurable", {})
            session_id = cfg.get("session_id") or cfg.get("thread_id")

        logger.info(
            "[TOOL_CALL] id=%s name=%s args=%s",
            tool_call_id,
            tool_name,
            _truncate_value(logged_args),
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
                        "args": logged_args,
                        "status": "success",
                        "result": "[browser result redacted]" if sensitive else _truncate_value(result),
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

            logger.error(
                "[TOOL_ERROR] id=%s name=%s elapsed=%.2fs",
                tool_call_id,
                tool_name,
                elapsed,
                exc_info=not sensitive,
            )

            if session_id:
                await session_repository.add_event(
                    session_id,
                    {
                        "type": "tool",
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "args": logged_args,
                        "status": "error",
                        "error": type(exc).__name__ if sensitive else str(exc),
                        "elapsed": elapsed,
                        "started_at": started_at,
                        "finished_at": datetime.now(
                            timezone.utc
                        ),
                    },
                )

            raise
