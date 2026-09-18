from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    register_agent,
)
from core.tracing.logging_middleware import (
    ToolLoggingMiddleware,
)
from tools.integrations.meta_ads_mcp.tools.campaign import (
    get_meta_campaigns,
    get_meta_campaign_performance,
)
from tools.integrations.meta_ads_mcp.tools.adset import (
    get_meta_campaign_adsets,
)
from tools.integrations.meta_ads_mcp.tools.ad import (
    get_meta_campaign_ads,
    get_meta_ad_preview,
)
from tools.integrations.meta_ads_mcp.tools.creative import (
    get_meta_creatives,
    get_meta_creative_ads,
    get_meta_ad_images,
    get_meta_ad_videos,
)
from tools.integrations.meta_ads_mcp.tools.insights import (
    get_meta_performance_trend,
    get_meta_anomaly_signal,
)
from tools.integrations.meta_ads_mcp.tools.campaign_analysis_snapshot import (
    save_campaign_analysis_snapshot,
)
from tools.integrations.meta_ads_mcp.dashboard.tool import (
    create_meta_ads_dashboard,
)


async def register_meta_ads_agent() -> None:
    system_prompt = await prompt_service.get(
        "deep_agent.subagent.meta_ads.system"
    )

    skills = await prompt_service.prepare_skills(
        "meta_ads"
    )

    tools = [
        # 캠페인 조회
        get_meta_campaigns,

        # 특정 캠페인 성과 조회
        get_meta_campaign_performance,

        # 특정 캠페인의 광고 세트 조회
        get_meta_campaign_adsets,

        # 특정 캠페인의 광고 조회
        get_meta_campaign_ads,

        # 실제 광고 Preview 조회
        get_meta_ad_preview,

        # Creative 상세 조회
        get_meta_creatives,

        # Creative가 사용된 광고 역조회
        get_meta_creative_ads,

        # 이미지 소재 조회
        get_meta_ad_images,

        # 영상 소재 조회
        get_meta_ad_videos,

        # 성과 추이 조회
        get_meta_performance_trend,

        # 성과 이상 징후 조회
        get_meta_anomaly_signal,

        # 분석 Snapshot 저장
        save_campaign_analysis_snapshot,

        # Excel Dashboard 생성
        create_meta_ads_dashboard,
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