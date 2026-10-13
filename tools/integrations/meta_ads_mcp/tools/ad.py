from __future__ import annotations
import json
from typing import Any
from langchain_core.runnables import (
    RunnableConfig,
)
from langchain_core.tools import tool
from tools.integrations.meta_ads_mcp.tools.common import (
    invoke_meta_mcp_tool,
)
DEFAULT_AD_PERFORMANCE_FIELDS = [
    "id",
    "name",
    "adset_id",
    "amount_spent",
    "impressions",
    "link_click",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "app_custom_event_fb_mobile_purchase",
    "omni_purchase",
    "omni_purchase_values",
    "cost_per_omni_purchase",
    "purchase_roas",
]


@tool
async def get_meta_campaign_ads(
    campaign_id: str,
    config: RunnableConfig,
) -> Any:
    """
    특정 Meta 캠페인에 속한 광고를 조회한다.

    각 광고의 소속 광고 세트와 Creative 연결 정보를 함께 조회한다.
    Creative 분석이 필요한 경우 반환된 creative_id를 사용한다.
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
            "fields": [
                "id",
                "name",
                "adset_id",
                "creative",
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

@tool
async def get_meta_campaign_details(
    campaign_id: str,
    config: RunnableConfig,
) -> Any:
    """
    특정 Meta 캠페인의 기본 정보와 설정을 조회한다.

    캠페인 상태, 목적, 최적화 설정, 예산,
    어트리뷰션 설정 등을 확인할 때 사용한다.

    성과 지표 조회는 get_meta_campaign_performance를 사용한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload={
            "level": "campaign",
            "fields": [
                "id",
                "name",
                "effective_status",
                "objective",
                "optimization_goal",
                "attribution_setting",
                "daily_budget",
            ],
            "object_ids": [
                campaign_id,
            ],
        },
        config=config,
    )

@tool
async def get_meta_campaign_ad_performance(
    campaign_id: str,
    config: RunnableConfig,
    date_preset: str | None = None,
    time_range: dict[str, str] | None = None,
    time_increment: str | None = None,
) -> Any:
    """
    특정 Meta 캠페인에 속한 광고별 성과를 조회한다.

    광고별 광고비, 구매, CPA, 구매가치, ROAS, CTR, CPC, CPM 등의 성과 비교에 사용한다.

    광고 세트별 성과가 필요한 경우 반환된 adset_id를 기준으로 광고 성과를 집계할 수 있다.
    """

    payload = {
        "level": "ad",
        "fields": DEFAULT_AD_PERFORMANCE_FIELDS,
        "filtering": [
            {
                "field": "campaign.id",
                "operator": "IN", 
                "value": [campaign_id],
            }
        ],
        "date_preset": date_preset,
        "time_range": (
            json.dumps(time_range)
            if time_range
            else None
        ),
        "time_increment": time_increment,
    }

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload=payload,
        config=config,
    )