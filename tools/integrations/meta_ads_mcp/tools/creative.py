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
async def get_meta_creatives_by_ids(
    creative_ids: list[str],
    config: RunnableConfig,
) -> Any:
    """
    실제 Meta Creative ID 목록으로 Creative 상세 정보를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_creatives",
        payload={
            "fields": [
                "id",
                "name",
                "thumbnail_url",
            ],
            "filtering": [
                {
                    "field": "id",
                    "operator": "IN",
                    "value": creative_ids,
                }
            ],
        },
        config=config,
    )

@tool
async def get_meta_creatives(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    Meta 광고 소재(Creative)를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_creatives",
        payload=parameters,
        config=config,
    )

@tool
async def get_meta_creative_ads(
    creative_id: str,
    config: RunnableConfig,
) -> Any:
    """
    특정 Creative가 어떤 광고에서 사용되고 있는지 역조회한다.

    캠페인의 Creative 정보를 조회하기 위한 기본 Tool이 아니다.

    creative_id는 반드시 다른 Meta 조회 결과에서 확인된
    실제 Creative ID를 사용한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_creative_ads",
        payload={
            "creative_id": creative_id,
        },
        config=config,
    )

@tool
async def get_meta_ad_images(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    Meta 광고 이미지 정보를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_images",
        payload=parameters,
        config=config,
    )


@tool
async def get_meta_ad_videos(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    Meta 광고 영상 정보를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_videos",
        payload=parameters,
        config=config,
    )