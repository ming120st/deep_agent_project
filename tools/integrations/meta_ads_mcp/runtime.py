from __future__ import annotations

from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import (
    load_mcp_tools,
)

from tools.integrations.meta_ads_mcp.client import (
    create_meta_ads_mcp_client,
)


class MetaAdsMCPRuntime:
    def __init__(self):
        self.client = None
        self.session = None

        self.tools: list[BaseTool] = []
        self.tool_map: dict[str, BaseTool] = {}

        self._exit_stack = AsyncExitStack()

        self.started = False

    # MCP 연결 + Tool 로드
    async def start(self):
        if self.started:
            return

        # stop() 이후 다시 start() 되는 경우를 대비
        self._exit_stack = AsyncExitStack()

        self.client = (
            create_meta_ads_mcp_client()
        )

        self.session = await (
            self._exit_stack.enter_async_context(
                self.client.session(
                    "meta_ads"
                )
            )
        )

        self.tools = await load_mcp_tools(
            self.session
        )

        self.tool_map = {
            tool.name: tool
            for tool in self.tools
        }

        self.started = True

        print(
            "[META ADS MCP] "
            f"loaded {len(self.tools)} tools"
        )

        for tool in self.tools:
            print(
                "[META ADS MCP TOOL]",
                tool.name,
            )
    
    # MCP 연결 종료
    async def stop(self):
        if not self.started:
            return

        await self._exit_stack.aclose()

        self.client = None
        self.session = None

        self.tools = []
        self.tool_map = {}

        self.started = False

        print(
            "[META ADS MCP] stopped"
        )

    def get_tool(
        self,
        tool_name: str,
    ) -> BaseTool:
        if not self.started:
            raise RuntimeError(
                "Meta Ads MCP Runtime이 "
                "시작되지 않았습니다."
            )

        tool = self.tool_map.get(
            tool_name
        )

        if tool is None:
            available_tools = ", ".join(
                sorted(
                    self.tool_map.keys()
                )
            )

            raise KeyError(
                f"Meta Ads MCP Tool을 찾을 수 없습니다: "
                f"{tool_name}. "
                f"사용 가능한 Tool: "
                f"{available_tools}"
            )

        return tool

    def get_tools(
        self,
    ) -> list[BaseTool]:
        if not self.started:
            raise RuntimeError(
                "Meta Ads MCP Runtime이 "
                "시작되지 않았습니다."
            )

        return self.tools

    def build_agent_tools(
        self,
    ) -> list[BaseTool]:

        if not self.started:
            raise RuntimeError(
                "Meta Ads MCP Runtime이 시작되지 않았습니다."
            )

        return list(self.tools)


meta_ads_runtime = MetaAdsMCPRuntime()