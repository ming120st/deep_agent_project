import time
from typing import Any
from langchain.agents.middleware.types import (
    AgentMiddleware,
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

        logger.info(
            "[TOOL_CALL] id=%s name=%s args=%s",
            tool_call_id,
            tool_name,
            _truncate_value(args),
        )

        start = time.monotonic()

        try:
            result = await handler(request)

            logger.info(
                "[TOOL_RESULT] id=%s name=%s elapsed=%.2fs",
                tool_call_id,
                tool_name,
                time.monotonic() - start,
            )

            return result

        except Exception:
            logger.exception(
                "[TOOL_ERROR] id=%s name=%s elapsed=%.2fs",
                tool_call_id,
                tool_name,
                time.monotonic() - start,
            )
            raise