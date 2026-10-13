from __future__ import annotations

from langchain_core.tools import StructuredTool

from tools.integrations.tavily_search.models import SearchProfile
from tools.integrations.tavily_search.service import SearchService


def create_search_tool(
    profile: SearchProfile | None = None,
    *,
    name: str = "search_web",
) -> StructuredTool:
    """Create a generic search Tool with an agent-specific policy bound to it."""

    service = SearchService(profile=profile)

    async def run_search(request: str) -> dict:
        """Search the public web and return planned, deduplicated, quality-ranked results."""
        response = await service.search(request)
        return response.model_dump(mode="json")

    return StructuredTool.from_function(
        coroutine=run_search,
        name=name,
        description=(
            "범용 웹 검색 도구. 요청을 분석해 검색 계획과 여러 Query를 만들고, "
            "Tavily 병렬 검색·중복 제거·품질 평가·필요 시 추가 검색을 수행한 뒤 "
            "출처 URL이 포함된 구조화 결과를 반환한다. 검색할 내용을 request에 "
            "구체적인 자연어로 전달한다."
        ),
    )


search_web = create_search_tool()
