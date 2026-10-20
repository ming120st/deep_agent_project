
from __future__ import annotations

from langchain_core.tools import StructuredTool

from core.state.search_repository import (
    search_repository,
)
from core.tracing.logger import get_logger

from models.search_model import (
    SearchPlan,
    SearchProfile,
)

from tools.integrations.search.service import (
    SearchService,
)


logger = get_logger("search")


def create_search_tool(
    profile: SearchProfile | None = None,
    *,
    name: str = "search_web",
) -> StructuredTool:
    """
    LLM이 생성한 SearchPlan에 따라 웹 검색을 수행하는 Tool.

    - SearchService를 통한 검색 실행
    - 최종 SearchResponse 생성
    - MongoDB 검색 결과 저장
    - search_id 및 검색 결과 반환
    """

    service = SearchService(
        profile=profile
    )

    async def run_search(
        request: str,
        plan: SearchPlan,
    ) -> dict:
        """
        LLM이 생성한 검색 계획으로 검색을 수행한다.

        1. SearchService 검색 실행
        2. SearchResponse 생성
        3. MongoDB 저장
        4. search_id와 검색 결과 반환
        """

        # 1. 기존 검색 실행
        response = await service.search(
            request=request,
            plan=plan,
        )

        # 2. 검색 결과 MongoDB 저장
        search_id = (
            await search_repository.save_search_response(
                search_response=response
            )
        )

        logger.info(
            "Search result saved "
            "search_id=%s result_count=%s",
            search_id,
            len(response.results),
        )

        # 3. 검색 결과 반환
        return {
            "search_id": search_id,
            "status": "completed",
            "result_count": len(response.results),
            "quality": response.quality.model_dump(
                mode="json"
            ),
        }

    return StructuredTool.from_function(
        coroutine=run_search,
        name=name,
        description=(
            "범용 웹 검색 도구. "
            "사용자의 검색 요청을 분석하여 "
            "검색 계획(SearchPlan)을 직접 생성한 뒤 "
            "request와 plan을 함께 전달한다. "
            "검색 목적에 따라 Naver 또는 Tavily를 선택하고, "
            "필요한 경우 여러 검색어로 분리한다. "
            "검색 범위(scope), Provider, Topic, "
            "최신성 및 도메인 조건을 결정한다. "
            "검색 결과를 통합·중복 제거·품질 평가하여 "
            "MongoDB에 저장한다. "
            "저장된 검색 결과의 search_id와 "
            "출처 URL을 포함한 구조화 검색 결과를 반환한다."
        ),
    )


search_web = create_search_tool()