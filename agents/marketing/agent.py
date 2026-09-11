from langchain.agents.middleware import (
    LLMToolSelectorMiddleware,
)

from core.llm.model_service import model_service
from core.prompt.prompt_service import prompt_service
from registry.agent_registry import register_agent
from tools.integrations.meta_ads_mcp.runtime import (
    meta_ads_runtime,
)


async def register_meta_ads_agent() -> None:

    # 기본 시스템 프롬프트 조회
    system_prompt = await prompt_service.render(
        "deep_agent.subagent.meta_ads.system",
    )

    # Meta Ads Agent 등록
    register_agent(
        name="meta_ads",
        description=(
            "Meta Ads 광고 업무 전용 Agent. "
            "Meta/Facebook/Instagram 광고 계정, "
            "캠페인, 광고 세트, 광고, 광고 성과, "
            "광고 소재 분석, 소재 피로도, 소재 교체 시점, "
            "경쟁사 Meta 광고 분석 요청에 사용한다."
        ),
        system_prompt=system_prompt,
        model=model_service.LLM_LIGHT,
        # Meta MCP Tool 전체 등록
        tools=(
            meta_ads_runtime
            .build_agent_tools()
        ),
        # 모델 호출 전에 관련 Tool만 자동 선택
        middleware=[
            LLMToolSelectorMiddleware(
                model=model_service.LLM_LIGHT,
                max_tools=5,
            ),
        ],
    )