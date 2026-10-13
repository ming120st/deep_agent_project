from deepagents.middleware.skills import (
    SkillsMiddleware,
)
from deepagents.backends.filesystem import (
    FilesystemBackend,
)

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
    get_meta_analysis_targets,
    sync_meta_campaign_daily_performance,
    get_meta_campaign_performance,
    get_meta_campaign_skill,
    get_meta_campaigns,
)
from tools.integrations.meta_ads_mcp.tools.adset import (
    get_meta_campaign_adsets,
)
from tools.integrations.meta_ads_mcp.tools.ad import (
    get_meta_campaign_ads,
    get_meta_ad_preview,
    get_meta_campaign_ad_performance,
)
from tools.integrations.meta_ads_mcp.tools.creative import (
    get_meta_creatives_by_ids,
    get_meta_ad_images,
    get_meta_ad_videos,
)
from tools.integrations.meta_ads_mcp.tools.campaign_analysis_snapshot import (
    save_campaign_analysis_snapshot,
    get_campaign_analysis_snapshot,
)
from tools.integrations.meta_ads_mcp.dashboard.tool import (
    create_meta_ads_dashboard,
)


async def register_meta_ads_agent() -> None:
    system_prompt = await prompt_service.get(
        "deep_agent.subagent.meta_ads.system"
    )

    # Redis / MongoDB의 최신 Skill을 확인하고
    # 필요한 경우 Runtime SKILL.md 갱신
    skill_sources = await (
        prompt_service.prepare_skills(
            agent="meta_ads",
        )
    )

    # .runtime 파일을 읽는 Backend
    skill_backend = FilesystemBackend(
        root_dir="."
    )

    register_agent(
        name="meta_ads",
        description=(
            "Meta Ads 전용 Agent. "
            "Meta Ads 캠페인, 광고세트, 광고, 소재/크리에이티브, "
            "CTR, CPC, CPM, CPA, ROAS 등 광고 성과 분석과 "
            "소재 문제 진단 및 교체 여부 판단을 처리한다. "
            "분석 결과의 엑셀 파일 저장, 엑셀 대시보드 생성까지 "
            "이 서브에이전트 하나가 끝까지 처리한다."
        ),

        system_prompt=system_prompt,

        # 항상 노출되는 Tool
        tools=[
            get_meta_campaigns,
            sync_meta_campaign_daily_performance,
            get_meta_ad_preview,
            get_meta_creatives_by_ids,
            get_meta_ad_images,
            get_meta_ad_videos,
            get_campaign_analysis_snapshot,
        ],

        middleware=[
            SkillsMiddleware(
                backend=skill_backend,
                sources=skill_sources,

                # Skill을 읽은 후 노출할 Tool
                tools=[
                    get_meta_analysis_targets,
                    get_meta_campaign_performance,
                    get_meta_campaign_skill,
                    get_meta_campaign_adsets,
                    get_meta_campaign_ads,
                    get_meta_campaign_ad_performance,
                    save_campaign_analysis_snapshot,
                    create_meta_ads_dashboard,
                ],
            ),

            ToolLoggingMiddleware(),
        ],

        sandbox=False,
    )