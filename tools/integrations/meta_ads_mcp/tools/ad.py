from __future__ import annotations

from typing import Any

from langchain_core.runnables import (
    RunnableConfig,
)
from langchain_core.tools import tool

from tools.integrations.meta_ads_mcp.tools.common import (
    invoke_meta_mcp_tool,
)


@tool
async def get_meta_campaign_ads(
    campaign_id: str,
    config: RunnableConfig,
) -> Any:
    """
    특정 Meta 캠페인에 속한 광고를 조회한다.

    캠페인 성과 분석 시 계정 전체 광고를 조회하지 않고,
    전달받은 campaign_id에 속한 광고만 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload={
            "level": "ad",
            "filtering": [
                {
                    "field": "campaign.id",
                    "operator": "IN",
                    "value": [
                        campaign_id,
                    ],
                }
            ],
        },
        config=config,
    )


@tool
async def get_meta_ad_preview(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    실제 Meta 광고 Preview를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_preview",
        payload=parameters,
        config=config,
    )