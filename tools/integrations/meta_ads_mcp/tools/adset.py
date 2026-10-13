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
async def get_meta_campaign_adsets(
    campaign_id: str,
    config: RunnableConfig,
) -> Any:
    """
    특정 Meta 캠페인에 속한 광고 세트 목록을 조회한다.

    캠페인 분석에 필요한 광고 세트의
    최적화 목표, 전환 설정, 타겟팅, 예산,
    입찰 전략 및 운영 상태를 함께 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload={
            "level": "adset",
            "fields": [
                "optimization_goal",
                "promoted_object",
                "targeting",
                "daily_budget",
                "lifetime_budget",
                "bid_strategy",
                "billing_event",
                "status",
                "effective_status",
                "start_time",
                "end_time",
            ],
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