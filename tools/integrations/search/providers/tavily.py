from __future__ import annotations
import os
from typing import Any
from tavily import AsyncTavilyClient

from core.tracing.logger import get_logger
from models.search_model import (
    SearchPlan,
    SearchProfile,
    SearchQuery,
)

logger = get_logger("search.tavily")


class TavilySearchProvider:
    """
    Tavily Search API Provider.
    """

    def __init__(
        self,
        client: Any | None = None,
    ) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "tavily"

    def _get_client(self) -> Any:
        """
        Tavily Client를 lazy initialization 한다.
        """

        if self._client is None:
            api_key = os.getenv(
                "TAVILY_API_KEY"
            )

            if not api_key:
                raise RuntimeError(
                    "TAVILY_API_KEY "
                    "환경변수가 설정되어 있지 않습니다."
                )

            self._client = AsyncTavilyClient(
                api_key=api_key
            )

        return self._client

    async def search(
        self,
        plan: SearchPlan,
        query: SearchQuery,
        profile: SearchProfile,
    ) -> list[dict[str, Any]]:
        """
        Tavily Search API를 사용하여
        하나의 Query를 검색한다.
        """

        client = self._get_client()

        kwargs: dict[str, Any] = {
            "query": query.query,
            "search_depth": (
                profile.search_depth
            ),
            "topic": "news",
            "max_results": (
                profile.max_results_per_query
            ),
            "include_answer": False,
        }

        if plan.include_domains:
            kwargs[
                "include_domains"
            ] = plan.include_domains

            kwargs[
                "include_domains_mode"
            ] = "restrict"

        if plan.exclude_domains:
            kwargs[
                "exclude_domains"
            ] = plan.exclude_domains

        freshness_days = (
            profile.freshness_days
            or (
                30
                if plan.freshness_important
                else None
            )
        )

        if freshness_days:
            if plan.topic == "news":
                kwargs["time_range"] = _time_range(
                    freshness_days
                )
            else:
                kwargs[
                    "time_range"
                ] = _time_range(
                    freshness_days
                )

        logger.info(
            "Tavily search start "
            "query=%r topic=%s",
            query.query,
            plan.topic,
        )

        try:
            response = await client.search(
                **kwargs
            )

        except Exception:
            logger.exception(
                "Tavily search failed "
                "query=%r topic=%s",
                query.query,
                plan.topic,
            )
            raise

        items = response.get(
            "results",
            [],
        )

        logger.info(
            "Tavily search success "
            "query=%r results=%d",
            query.query,
            len(items),
        )

        results: list[
            dict[str, Any]
        ] = []

        for item in items:
            if not isinstance(
                item,
                dict,
            ):
                continue

            results.append(
                {
                    **item,
                    "_query": (
                        query.query
                    ),
                    "_category": (
                        query.category
                    ),
                    "_provider": (
                        self.name
                    ),
                }
            )

        return results


def _time_range(
    days: int,
) -> str:
    """
    일수를 Tavily time_range 값으로 변환한다.
    """

    if days <= 1:
        return "day"

    if days <= 7:
        return "week"

    if days <= 31:
        return "month"

    return "year"