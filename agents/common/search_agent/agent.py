from core.prompt.prompt_service import (
    prompt_service,
)

from registry.agent_registry import (
    register_agent,
)

from tools.integrations.search.tool import (
    search_web,
)
from tools.common.article_image.tool import (
    upload_article_image,
)
from tools.integrations.search.providers.youtube import (
    search_youtube_videos,
)
SEARCH_PROMPT_KEY = (
    "deep_agent.subagent.web_search.system"
)
async def register_search_agent() -> None:
    system_prompt = await prompt_service.render(
       SEARCH_PROMPT_KEY
    )

    register_agent(
        name="web_search",
        description=(
            "공개 웹에서 최신 정보, 뉴스, 공식 자료, 시장 동향, 외부 근거를 "
            "검색하고 출처 URL과 함께 정리해야 할 때 사용하는 범용 검색 Agent."
        ),
        system_prompt=system_prompt,
        prompt_key=SEARCH_PROMPT_KEY,
        tools=[
            search_web,
            search_youtube_videos,            
        ],
    )
