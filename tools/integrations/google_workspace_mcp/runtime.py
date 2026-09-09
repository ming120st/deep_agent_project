from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.tools import load_mcp_tools
from pydantic import BaseModel, create_model

from tools.integrations.google_workspace_mcp.client import (
    create_google_workspace_mcp_client,
)

# 서버 코드가 직접 넣을 파라미터 목록
SYSTEM_INJECTED_PARAMS = {
    "user_google_email",
}


class GoogleWorkspaceMCPRuntime:
    def __init__(self):
        self.client = None
        self.session = None
        self.tools = []
        self.tool_map = {}
        self._exit_stack = AsyncExitStack()

    async def start(self):
        self.client = (
            create_google_workspace_mcp_client()
        )

        self.session = await (
            self._exit_stack.enter_async_context(
                self.client.session(
                    "google_workspace"
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

    async def stop(self):
        await self._exit_stack.aclose()

        self.session = None
        self.client = None
        self.tools = []
        self.tool_map = {}

    def get_tool(self, name: str):
        try:
            return self.tool_map[name]
        except KeyError:
            raise ValueError(
                f"MCP Tool을 찾을 수 없습니다: {name}"
            ) from None

    async def invoke_tool(
        self,
        name: str,
        args: dict[str, Any],
        runtime: ToolRuntime,
    ):
        tool = self.get_tool(name)

        invoke_args = dict(args)

        if (
            "user_google_email"
            in tool.args
        ):
            user_email = (
                runtime.state.get(
                    "user_email"
                )
            )

            if not user_email:
                raise RuntimeError(
                    "사용자 이메일을 확인할 수 없습니다."                    
                )

            invoke_args[
                "user_google_email"
            ] = user_email

        return await tool.ainvoke(
            invoke_args
        )
    
    # MCP Tool의 입력 파라미터를 Agent용 schema로 변환
    def _build_agent_args_schema(
        self,
        mcp_tool,
    ) -> type[BaseModel]:

        # 원본 스키마를 읽어서
        original_schema = (
            mcp_tool.args_schema
            or {}
        )
        # 파라미터 목록을 가져와서
        properties = (
            original_schema.get(
                "properties",
                {},
            )
        )
        # 필수 파라미터 확인 후
        required = set(
            original_schema.get(
                "required",
                [],
            )
        )

        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        fields = {}

        for (
            field_name,
            field_schema,
        ) in properties.items():

            if (
                field_name
                in SYSTEM_INJECTED_PARAMS
            ):
                continue

            python_type = type_map.get(
                field_schema.get("type"),
                Any,
            )

            default = (
                ...
                if field_name in required
                else field_schema.get(
                    "default",
                    None,
                )
            )

            fields[field_name] = (
                python_type,
                default,
            )

        return create_model(
            f"{mcp_tool.name}AgentArgs",
            **fields,
        )

    # Deep Agent 생성시 호출
    # 현재는 MCP Tool 목록을 전체 돌면서 가져오지만, 목록화하여 agent 한테 제공 예정
    def build_agent_tools(self):
        agent_tools = []

        for mcp_tool in self.tools:
            tool_name = mcp_tool.name

            args_schema = (
                self._build_agent_args_schema(
                    mcp_tool
                )
            )

            def make_invoke(
                current_tool_name: str,
            ):
                async def invoke(
                    runtime: ToolRuntime,
                    **kwargs: Any,
                ):
                    return await self.invoke_tool(
                        name=current_tool_name,
                        args=kwargs,
                        runtime=runtime,
                    )

                return invoke

            agent_tools.append(
                StructuredTool.from_function(
                    name=tool_name,
                    description=(
                        mcp_tool.description
                    ),
                    coroutine=make_invoke(
                        tool_name
                    ),
                    args_schema=args_schema,
                )
            )

        return agent_tools


google_workspace_runtime = (
    GoogleWorkspaceMCPRuntime()
)