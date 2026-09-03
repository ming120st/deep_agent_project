"""Middleware 실습: Tool 호출 전후 로깅.

AgentMiddleware.wrap_tool_call 훅으로 모든 Tool 실행을 가로채서
호출 전/후를 터미널에 출력한다. Deep Agents가 제공하는 표준 확장 지점이며,
Tool 자체를 수정하지 않고도 모든 Tool 호출에 공통 동작(로깅, 재시도, 모니터링 등)을
끼워 넣을 수 있음을 보여준다.
"""

import time
from bson.json_util import _truncate
from langchain.agents.middleware.types import AgentMiddleware

from core.tracing.logger import get_logger

logger = get_logger("tool_logging")
class ToolLoggingMiddleware(AgentMiddleware):

    async def awrap_tool_call(
        self,
        request,
        handler,
    ):
        tool_name = request.tool_call["name"]
        args = request.tool_call.get("args", {})

        logger.info(
            "[TOOL_CALL] name=%s args=%s",
            tool_name,
            _truncate(args),
        )

        start = time.monotonic()

        try:
            result = await handler(request)

            logger.info(
                "[TOOL_RESULT] name=%s elapsed=%.2fs",
                tool_name,
                time.monotonic() - start,
            )

            return result

        except Exception:
            logger.exception(
                "[TOOL_ERROR] name=%s elapsed=%.2fs",
                tool_name,
                time.monotonic() - start,
            )
            raise
