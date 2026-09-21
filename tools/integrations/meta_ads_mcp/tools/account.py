from __future__ import annotations

from langchain_core.runnables import (
    RunnableConfig,
)
from langchain_core.tools import tool

from tools.integrations.meta_ads_mcp.tools.common import (
    invoke_meta_mcp_tool,
)


@tool
async def get_meta_ad_accounts(
    config: RunnableConfig,
) -> dict:
    """
    접근 가능한 Meta 광고 계정 목록을 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_accounts",
        payload={},
        config=config,
    )