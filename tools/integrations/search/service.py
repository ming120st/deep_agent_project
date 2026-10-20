
from __future__ import annotations

import asyncio
from typing import Any

from core.tracing.logger import get_logger

from models.search_model import (
    SearchPlan,
    SearchProfile,
    SearchQuery,
    SearchResponse,
)
from tools.integrations.search.processor import (
    SearchResultProcessor,
)
from tools.integrations.search.providers import (
    NaverSearchProvider,
    SearchProvider,
    TavilySearchProvider,
)

logger = get_logger("search")


class SearchService:
    def __init__(
        self,
        profile: SearchProfile | None = None,
        *,
        providers: dict[str, SearchProvider] | None = None,
    ) -> None:
        self.profile = profile or SearchProfile()

        self.providers = (
            providers
            if providers is not None
            else {
                "tavily": TavilySearchProvider(),
                "naver": NaverSearchProvider(),
            }
        )

        self.processor = SearchResultProcessor(
            self.profile
        )

    async def search(
        self,
        request: str,
        plan: SearchPlan,
    ) -> SearchResponse:
        """
        검색 계획에 따라 검색한다.

        1. 최초 검색
        2. 결과 정제 및 품질 평가
        3. 품질 미달 시 최대 1회 보완 검색
        4. 최종 결과 반환
        """
        request = request.strip()

        if not request:
            raise ValueError(
                "검색 요청은 비어 있을 수 없습니다."
            )

        raw_results, errors = await self._execute_queries(
            plan,
            plan.queries,
        )

        results = self.processor.process(
            request,
            plan,
            raw_results,
        )

        quality = self.processor.evaluate_quality(
            plan,
            results,
            raw_results,
        )

        if (
            self.profile.allow_additional_search
            and quality.needs_more_search
        ):
            followup_queries = self._build_followup_queries(
                plan
            )

            if followup_queries:
                logger.info(
                    "Additional search started queries=%s",
                    [
                        query.query
                        for query in followup_queries
                    ],
                )

                extra_results, extra_errors = (
                    await self._execute_queries(
                        plan,
                        followup_queries,
                    )
                )

                raw_results.extend(extra_results)
                errors.extend(extra_errors)

                results = self.processor.process(
                    request,
                    plan,
                    raw_results,
                )

                quality = self.processor.evaluate_quality(
                    plan,
                    results,
                    raw_results,
                )

        return SearchResponse(
            request=request,
            plan=plan,
            results=results,
            quality=quality,
            errors=errors,
        )

    def _build_followup_queries(
        self,
        plan: SearchPlan,
    ) -> list[SearchQuery]:
        """
        기존 검색어와 중복되지 않는
        보완 검색어를 최대 1개 생성한다.
        """
        existing_queries = {
            query.query.strip().casefold()
            for query in plan.queries
        }

        keywords = " ".join(
            plan.keywords
        ).strip()

        for perspective in plan.perspectives:
            followup_query = (
                f"{keywords} {perspective.strip()}"
            ).strip()

            if (
                followup_query
                and followup_query.casefold()
                not in existing_queries
            ):
                return [
                    SearchQuery(
                        query=followup_query,
                        category="follow_up",
                    )
                ]

        if (
            keywords
            and keywords.casefold()
            not in existing_queries
        ):
            return [
                SearchQuery(
                    query=keywords,
                    category="follow_up",
                )
            ]

        return []

    async def _execute_queries(
        self,
        plan: SearchPlan,
        queries: list[SearchQuery],
    ) -> tuple[
        list[dict[str, Any]],
        list[str],
    ]:
        """
        여러 검색어를 병렬 실행하고
        결과와 오류를 수집한다.
        """
        outcomes = await asyncio.gather(
            *(
                self._search_one(
                    plan,
                    query,
                )
                for query in queries
            ),
            return_exceptions=True,
        )

        results: list[dict[str, Any]] = []
        errors: list[str] = []

        for query, outcome in zip(
            queries,
            outcomes,
        ):
            if isinstance(
                outcome,
                asyncio.CancelledError,
            ):
                raise outcome

            if isinstance(outcome, Exception):
                errors.append(
                    f"검색 실패 ({query.query}): "
                    f"{outcome}"
                )
                continue

            query_results, query_errors = outcome

            results.extend(query_results)
            errors.extend(query_errors)

        return results, errors

    async def _search_one(
        self,
        plan: SearchPlan,
        query: SearchQuery,
    ) -> tuple[
        list[dict[str, Any]],
        list[str],
    ]:
        """
        하나의 검색어를 Provider별 병렬 검색한다.

        일부 Provider 실패 시 정상 결과를 유지하고,
        실패 정보는 errors에 기록한다.
        """
        providers = self._get_providers(
            plan
        )

        logger.info(
            "Search providers selected "
            "scope=%s requested=%s "
            "enabled=%s selected=%s query=%r",
            plan.scope,
            plan.providers,
            self.profile.enabled_providers,
            [
                provider.name
                for provider in providers
            ],
            query.query,
        )

        if not providers:
            raise RuntimeError(
                "사용 가능한 검색 Provider가 없습니다."
            )

        outcomes = await asyncio.gather(
            *(
                provider.search(
                    plan,
                    query,
                    self.profile,
                )
                for provider in providers
            ),
            return_exceptions=True,
        )

        results: list[dict[str, Any]] = []
        errors: list[str] = []

        for provider, outcome in zip(
            providers,
            outcomes,
        ):
            if isinstance(
                outcome,
                asyncio.CancelledError,
            ):
                raise outcome

            if isinstance(outcome, Exception):
                errors.append(
                    f"검색 Provider 실패 "
                    f"(query={query.query}, "
                    f"provider={provider.name}): "
                    f"{outcome}"
                )

                logger.warning(
                    "Search provider failed "
                    "provider=%s query=%r error=%r",
                    provider.name,
                    query.query,
                    outcome,
                )
                continue

            results.extend(outcome)

        if len(errors) == len(providers):
            raise RuntimeError(
                "; ".join(errors)
            )

        return results, errors

    def _get_providers(
        self,
        plan: SearchPlan,
    ) -> list[SearchProvider]:
        """
        검색 계획과 Profile에서
        허용된 Provider만 반환한다.
        """
        selected: list[SearchProvider] = []

        for provider_name in plan.providers:
            if (
                provider_name
                not in self.profile.enabled_providers
            ):
                continue

            provider = self.providers.get(
                provider_name
            )

            if provider is not None:
                selected.append(
                    provider
                )

        return selected
