"""
Google Workspace MCP 클라이언트 연결과 세션, 로딩된 Tool을 애플리케이션 생명주기 동안 유지하는 Runtime Manager
main.py 에서 MCP 연결
"""

from __future__ import annotations
from contextlib import AsyncExitStack
from langchain_mcp_adapters.tools import (
    load_mcp_tools,
)
from tools.integrations.google_workspace_mcp.client import (
    create_google_workspace_mcp_client,
)


class GoogleWorkspaceMCPRuntime:
    def __init__(self):
        self.client = None
        self.session = None
        self.tools = []
        self._exit_stack = AsyncExitStack()

    async def start(self):
        self.client = create_google_workspace_mcp_client()
    
        self.session = await self._exit_stack.enter_async_context(
            self.client.session("google_workspace")
        )
    
        raw_tools = await self.session.list_tools()
    
        print(
            "RAW MCP TOOLS:",
            [
                tool.name
                for tool in raw_tools.tools
            ],
        )
    
        self.tools = await load_mcp_tools(
            self.session
        )
    
        print(
            "LANGCHAIN MCP TOOLS:",
            [
                tool.name
                for tool in self.tools
            ],
        )
    async def stop(self):
        await self._exit_stack.aclose()

    def get_tool(
        self,
        name: str,
    ):
        return next(
            tool
            for tool in self.tools
            if tool.name == name
        )


google_workspace_runtime = GoogleWorkspaceMCPRuntime()
