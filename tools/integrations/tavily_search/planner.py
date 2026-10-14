from __future__ import annotations

import json
from datetime import date
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel

from core.tracing.logger import get_logger
from tools.integrations.tavily_search.models import (
    QualityReport,
    SearchPlan,
    SearchProfile,
    SearchQuery,
    SearchResult,
)

logger = get_logger("tavily_search_planner")


class FollowUpQuery(BaseModel):
    query: str
    category: str = "follow_up"


class SearchPlanner(Protocol):
    async def create_plan(self, request: str, profile: SearchProfile) -> SearchPlan: ...

    async def create_follow_up(
        self,
        request: str,
        plan: SearchPlan,
        results: list[SearchResult],
        quality: QualityReport,
    ) -> SearchQuery: ...


class LLMSearchPlanner:
    """Creates constrained search plans with BOA's configured lightweight model."""

    def __init__(self, model_key: str = "llm.light") -> None:
        self.model_key = model_key

    async def create_plan(self, request: str, profile: SearchProfile) -> SearchPlan:
        try:
            from core.llm.model_service import model_service

            model = await model_service.get_model(self.model_key)
            structured_model = model.with_structured_output(SearchPlan)
            plan = await structured_model.ainvoke(
                self._plan_prompt(request, profile)
            )
            if not isinstance(plan, SearchPlan):
                plan = SearchPlan.model_validate(plan)
            return self._apply_profile(plan, request, profile)
        except Exception:
            logger.exception("Search planning failed; using the heuristic fallback")
            return HeuristicSearchPlanner().build_plan(request, profile)

    async def create_follow_up(
        self,
        request: str,
        plan: SearchPlan,
        results: list[SearchResult],
        quality: QualityReport,
    ) -> SearchQuery:
        try:
            from core.llm.model_service import model_service

            model = await model_service.get_model(self.model_key)
            structured_model = model.with_structured_output(FollowUpQuery)
            summary = [
                {
                    "title": item.title,
                    "domain": item.source_domain,
                    "category": item.category,
                }
                for item in results[:8]
            ]
            follow_up = await structured_model.ainvoke(
                "Create exactly one new web query that fills the most important "
                "gap in these search results. Do not repeat an existing query.\n"
                f"User request: {request}\n"
                f"Existing queries: {[item.query for item in plan.queries]}\n"
                f"Quality issues: {quality.reasons}\n"
                f"Current results: {json.dumps(summary, ensure_ascii=False)}"
            )
            if not isinstance(follow_up, FollowUpQuery):
                follow_up = FollowUpQuery.model_validate(follow_up)
            return SearchQuery(**follow_up.model_dump())
        except Exception:
            logger.exception("Follow-up planning failed; using the heuristic fallback")
            return HeuristicSearchPlanner().build_follow_up(request, plan)

    @staticmethod
    def _plan_prompt(request: str, profile: SearchProfile) -> str:
        return (
            "You plan web searches. Analyze the request and return a concise search plan. "
            "Use one query for a simple factual lookup; otherwise create 2-5 distinct queries "
            "covering necessary perspectives. Queries must be directly usable by a search engine. "
            "Use include_domains only when specific authoritative domains are genuinely useful. "
            "Never include a domain listed in excluded_domains. Use news for current events, "
            "finance for market/financial data, otherwise general.\n"
            f"Today: {date.today().isoformat()}\n"
            f"User request: {request}\n"
            f"Search policy: {profile.model_dump_json()}"
        )

    @staticmethod
    def _apply_profile(
        plan: SearchPlan,
        request: str,
        profile: SearchProfile,
    ) -> SearchPlan:
        excluded = _unique(
            [_normalize_domain(item) for item in [*profile.excluded_domains, *plan.exclude_domains]]
        )
        included = [
            domain
            for domain in _unique(
                [
                    _normalize_domain(item)
                    for item in [*profile.preferred_domains, *plan.include_domains]
                ]
            )
            if domain not in excluded
        ]
        queries = [item for item in plan.queries if item.query.strip()]
        if not queries:
            queries = [SearchQuery(query=request)]
        return plan.model_copy(
            update={
                "queries": queries[: profile.max_queries],
                "include_domains": included,
                "exclude_domains": excluded,
                "official_sources_first": (
                    profile.official_sources_first or plan.official_sources_first
                ),
            }
        )


class HeuristicSearchPlanner:
    """Keeps search available if the planning model is unavailable."""

    async def create_plan(self, request: str, profile: SearchProfile) -> SearchPlan:
        return self.build_plan(request, profile)

    async def create_follow_up(
        self,
        request: str,
        plan: SearchPlan,
        results: list[SearchResult],
        quality: QualityReport,
    ) -> SearchQuery:
        return self.build_follow_up(request, plan)

    @staticmethod
    def build_plan(request: str, profile: SearchProfile) -> SearchPlan:
        lowered = request.lower()
        freshness = any(
            word in lowered
            for word in ("최신", "최근", "오늘", "동향", "뉴스", "latest", "recent")
        )
        official = profile.official_sources_first or any(
            word in lowered for word in ("공식", "정부", "원문", "official")
        )
        topic = "news" if freshness else "general"
        queries = [SearchQuery(query=request, category="primary")]
        complex_request = any(
            word in lowered for word in ("비교", "분석", "동향", "장단점", "compare", "trend")
        )
        if complex_request and profile.max_queries > 1:
            suffix = " 공식 자료" if official else " 주요 분석"
            queries.append(SearchQuery(query=request + suffix, category="evidence"))
        return SearchPlan(
            purpose=request,
            keywords=request.split()[:8],
            freshness_important=freshness,
            official_sources_first=official,
            scope="both",
            perspectives=["primary", "evidence"] if complex_request else ["primary"],
            queries=queries[: profile.max_queries],
            include_domains=profile.preferred_domains,
            exclude_domains=profile.excluded_domains,
            topic=topic,
        )

    @staticmethod
    def build_follow_up(request: str, plan: SearchPlan) -> SearchQuery:
        suffix = " 공식 최신 자료" if plan.official_sources_first else " 신뢰할 수 있는 출처"
        return SearchQuery(query=request + suffix, category="follow_up")


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _normalize_domain(value: str) -> str:
    candidate = value.strip().lower()
    if "://" not in candidate:
        candidate = "//" + candidate
    return (urlsplit(candidate).hostname or "").removeprefix("www.")
