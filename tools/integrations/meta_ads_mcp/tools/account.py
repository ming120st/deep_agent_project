# tools/integrations/meta_ads/tools/account.py

from langchain_core.tools import tool

from tools.integrations.meta_ads_mcp.runtime  import (
    meta_ads_runtime,
)


@tool
async def get_meta_ad_accounts() -> dict:
    """
    사용자가 접근 가능한 Meta 광고 계정 목록을 조회한다.
    """

    mcp_tool = meta_ads_runtime.get_tool(
        "ads_get_ad_accounts"
    )

    return await mcp_tool.ainvoke({})


@tool
async def get_meta_account_activity_logs(
    ad_account_id: str,
) -> dict:
    """
    특정 Meta 광고 계정의 활동 로그를 조회한다.
    """

    mcp_tool = meta_ads_runtime.get_tool(
        "ads_account_get_activity_logs"
    )

    return await mcp_tool.ainvoke(
        {
            "ad_account_id": ad_account_id,
        }
    )


META_ADS_ACCOUNT_TOOLS = [
    get_meta_ad_accounts,
    get_meta_account_activity_logs,
]