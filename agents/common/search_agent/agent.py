<<<<<<< Updated upstream
from core.prompt.prompt_service import (
    prompt_service,
)

from registry.agent_registry import (
    register_agent,
)
=======
from registry.agent_registry import register_agent
from tools.integrations.search.tool import search_web
from tools.integrations.search.providers.youtube import search_youtube_videos
>>>>>>> Stashed changes

from tools.integrations.tavily_search.tool import (
    search_web,
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
<<<<<<< Updated upstream
        system_prompt=system_prompt,
        prompt_key=SEARCH_PROMPT_KEY,
        tools=[
            search_web,
        ],
    )
=======
        system_prompt=(
            "너는 BOA의 범용 웹 검색 담당 Agent다. 사용자의 검색 목적과 범위를 "
            "빠뜨리지 말고 search_web 도구에 전달한다. 도구가 반환한 구조화 결과를 "
            "근거로 답하고, 핵심 주장에는 반드시 출처 URL을 함께 제시한다. 검색 오류가 "
            "일부 있으면 성공한 결과를 우선 사용하되 한계를 명시한다."
        ),
        tools=[search_web, search_youtube_videos],
    )
>>>>>>> Stashed changes
