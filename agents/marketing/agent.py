from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    register_agent,
)
from core.tracing.logging_middleware import (
    ToolLoggingMiddleware,
)

from tools.integrations.meta_ads_mcp.tools.account import (
    get_meta_ad_accounts,
)
from tools.integrations.meta_ads_mcp.tools.campaign import (
    get_meta_ad_entities,
)
# from tools.integrations.meta_ads_mcp.tools.creative import (
#     get_meta_ad_preview,
#     get_meta_creatives,
# )
# from tools.integrations.meta_ads_mcp.tools.insights import (
#     get_meta_performance_trend,
#     get_meta_anomaly_signal,
# )


async def register_meta_ads_agent() -> None:
    system_prompt = await prompt_service.get(
        "deep_agent.subagent.meta_ads.system"
    )

    skills = await prompt_service.prepare_skills(
        "meta_ads"
    )

    tools = [
        get_meta_ad_accounts,
        get_meta_ad_entities,
        # get_meta_ad_preview,
        # get_meta_creatives,
        # get_meta_performance_trend,
        # get_meta_anomaly_signal,
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