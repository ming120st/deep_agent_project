# tools/integrations/meta_ads/tools/campaign.py

from langchain_core.tools import tool

from tools.integrations.meta_ads_mcp.runtime  import (
    meta_ads_runtime,
)


@tool
async def get_meta_ad_entities(
    ad_account_id: str,
    entity_type: str,
) -> dict:
    """
    Meta 광고 계정의 광고 엔티티를 조회한다.

    entity_type 예시:
    - campaign
    - adset
    - ad
    """

    mcp_tool = meta_ads_runtime.get_tool(
        "ads_get_ad_entities"
    )

    return await mcp_tool.ainvoke(
        {
            "ad_account_id": ad_account_id,
            "entity_type": entity_type,
        }
    )


@tool
async def activate_meta_ad_entity(
    entity_id: str,
) -> dict:
    """
    Meta 광고 엔티티를 활성화한다.

    대상:
    - campaign
    - ad set
    - ad
    """

    mcp_tool = meta_ads_runtime.get_tool(
        "ads_activate_entity"
    )

    return await mcp_tool.ainvoke(
        {
            "entity_id": entity_id,
        }
    )


META_ADS_CAMPAIGN_TOOLS = [
    get_meta_ad_entities,
    activate_meta_ad_entity,
]