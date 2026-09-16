from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    register_agent,
)
from langchain.agents.middleware import (
    LLMToolSelectorMiddleware,
)
from core.tracing.logging_middleware import ToolLoggingMiddleware
from tools.integrations.meta_ads_mcp.runtime import (
    meta_ads_runtime,
)

META_ADS_ANALYSIS_TOOL_NAMES = {
    "ads_get_ad_accounts",
    "ads_get_ad_entities",
    "ads_get_ad_preview",
    "ads_get_creatives",
    "ads_get_creative_ads",
    "ads_get_ad_images",
    "ads_get_ad_videos",
    "ads_get_field_context",
    "ads_get_errors",
    "ads_get_opportunity_score",
    "ads_insights_advertiser_context",
    "ads_insights_anomaly_signal",
    "ads_insights_auction_ranking_benchmarks",
    "ads_insights_industry_benchmark",
    "ads_insights_performance_trend",
    "ads_get_dataset_details",
    "ads_get_dataset_quality",
    "ads_get_dataset_stats",
    "ads_get_datasets",
    "ads_get_ad_account_custom_audiences",
}


async def register_meta_ads_agent() -> None:
    system_prompt = await prompt_service.get(
        "deep_agent.subagent.meta_ads.system" 
    )

    skills = await prompt_service.prepare_skills(
        "meta_ads"
    )

    all_tools = meta_ads_runtime.get_tools()

    tools = [
        tool
        for tool in all_tools
        if tool.name
        in META_ADS_ANALYSIS_TOOL_NAMES
    ]

    register_agent(
        name="meta_ads",
        description=(
            "Meta Ads 전용 Agent. "
            "Meta Ads 캠페인, 광고세트, 광고, 소재/크리에이티브, "
            "CTR, CPC, CPM, CPA, ROAS 등 광고 성과 분석과 "
            "소재 문제 진단 및 교체 여부 판단을 처리한다."
        ),
        system_prompt=system_prompt,
        middleware=[
            ToolLoggingMiddleware(),
        ],
        tools=tools,
        skills=skills,
    )