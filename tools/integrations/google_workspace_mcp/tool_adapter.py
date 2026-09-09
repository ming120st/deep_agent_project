from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from langchain_core.runnables import RunnableConfig


class GoogleWorkspaceToolAdapter:
    def __init__(
        self,
        tool: BaseTool,
    ) -> None:
        self.tool = tool

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def description(self) -> str:
        return self.tool.description

    async def ainvoke(
        self,
        args: dict[str, Any],
        *,
        user_email: str,
        config: RunnableConfig | None = None,
    ) -> Any:
        invoke_args = dict(args)

        invoke_args[
            "user_google_email"
        ] = user_email

        return await self.tool.ainvoke(
            invoke_args,
            config=config,
        )

    