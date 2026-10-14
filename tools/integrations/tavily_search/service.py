from __future__ import annotations

import asyncio
import math
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tavily import AsyncTavilyClient

from core.tracing.logger import get_logger
from tools.integrations.tavily_search.models import (
    QualityReport,
    SearchPlan,
    SearchProfile,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from tools.integrations.tavily_search.planner import LLMSearchPlanner, SearchPlanner

logger = get_logger("tavily_search")

_TRACKING_PARAMS = {"fbclid", "gclid", "ref", "referrer"}
_TOKEN_PATTERN = re.compile(r"[0-9a-zA-Z가-힣]+")


class SearchService:
    """Plans, executes, deduplicates and evaluates reusable web searches."""

    def __init__(
        self,
        profile: SearchProfile | None = None,
        *,
        planner: SearchPlanner | None = None,
        client: Any | None = None,
    ) -> None:
        self.profile = profile or SearchProfile()
        self.planner = planner or LLMSearchPlanner()
        self._client = client

    async def search(self, request: str) -> SearchResponse:
        request = request.strip()
        if not request:
            raise ValueError("검색 요청은 비어 있을 수 없습니다.")

        plan = await self.planner.create_plan(request, self.profile)
        raw_results, errors = await self._execute_queries(plan, plan.queries)
        results = self._deduplicate_and_rank(request, plan, raw_results)
        quality = self._evaluate_quality(plan, results, raw_results)

        if (
            quality.needs_more_search
            and self.profile.allow_additional_search
            and len(plan.queries) < self.profile.max_queries
        ):
            follow_up = await self.planner.create_follow_up(
                request, plan, results, quality
            )
            existing = {item.query.casefold() for item in plan.queries}
            if follow_up.query.strip() and follow_up.query.casefold() not in existing:
                extra_results, extra_errors = await self._execute_queries(
                    plan, [follow_up]
                )
                errors.extend(extra_errors)
                plan = plan.model_copy(update={"queries": [*plan.queries, follow_up]})
                raw_results = [*raw_results, *extra_results]
                results = self._deduplicate_and_rank(
                    request, plan, raw_results
                )
                quality = self._evaluate_quality(plan, results, raw_results)

        return SearchResponse(
            request=request,
            plan=plan,
            results=results,
            quality=quality,
            errors=errors,
        )

    async def _execute_queries(
        self,
        plan: SearchPlan,
        queries: list[SearchQuery],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        outcomes = await asyncio.gather(
            *(self._search_one(plan, query) for query in queries),
            return_exceptions=True,
        )
        collected: list[dict[str, Any]] = []
        errors: list[str] = []
        for query, outcome in zip(queries, outcomes):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                logger.warning("Tavily query failed query=%r error=%s", query.query, outcome)
                errors.append(f"검색 실패 ({query.query}): {outcome}")
                continue
            collected.extend(outcome)
        return collected, errors

    async def _search_one(
        self,
        plan: SearchPlan,
        search_query: SearchQuery,
    ) -> list[dict[str, Any]]:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "query": search_query.query,
            "search_depth": self.profile.search_depth,
            "topic": plan.topic,
            "max_results": self.profile.max_results_per_query,
            "include_answer": False,
        }
        if plan.include_domains:
            kwargs["include_domains"] = plan.include_domains
            kwargs["include_domains_mode"] = "prefer"
        if plan.exclude_domains:
            kwargs["exclude_domains"] = plan.exclude_domains
        freshness_days = self.profile.freshness_days or (
            30 if plan.freshness_important else None
        )
        if freshness_days:
            if plan.topic == "news":
                kwargs["days"] = freshness_days
            else:
                kwargs["time_range"] = _time_range(freshness_days)

        response = await client.search(**kwargs)
        return [
            {
                **item,
                "_query": search_query.query,
                "_category": search_query.category,
            }
            for item in response.get("results", [])
            if isinstance(item, dict)
        ]

    def _get_client(self) -> Any:
        if self._client is None:
            api_key = os.getenv("TAVILY_API_KEY")
            if not api_key:
                raise RuntimeError("TAVILY_API_KEY 환경변수가 설정되어 있지 않습니다.")
            self._client = AsyncTavilyClient(api_key=api_key)
        return self._client

    def _deduplicate_and_rank(
        self,
        request: str,
        plan: SearchPlan,
        raw_results: list[dict[str, Any]],
    ) -> list[SearchResult]:
        ranked: list[SearchResult] = []
        for raw in raw_results:
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            domain = _domain(url)
            if _matches_domain(domain, plan.exclude_domains):
                continue
            ranked.append(
                SearchResult(
                    title=str(raw.get("title") or "").strip(),
                    url=_canonical_url(url),
                    content=str(raw.get("content") or raw.get("raw_content") or "").strip(),
                    published_date=raw.get("published_date") or raw.get("publishedDate"),
                    source_domain=domain,
                    query=str(raw.get("_query") or ""),
                    category=str(raw.get("_category") or "general"),
                    score=self._score_result(request, plan, raw, domain),
                )
            )

        ranked.sort(key=lambda item: item.score, reverse=True)
        unique: list[SearchResult] = []
        seen_urls: set[str] = set()
        for item in ranked:
            url_key = _url_key(item.url)
            if url_key in seen_urls:
                continue
            title_tokens = _tokens(item.title)
            if any(_jaccard(title_tokens, _tokens(other.title)) >= 0.82 for other in unique):
                continue
            seen_urls.add(url_key)
            unique.append(item)
        return unique

    def _score_result(
        self,
        request: str,
        plan: SearchPlan,
        raw: dict[str, Any],
        domain: str,
    ) -> float:
        tavily_score = _clamp(_safe_float(raw.get("score")))
        request_tokens = _tokens(request)
        text_tokens = _tokens(f"{raw.get('title', '')} {raw.get('content', '')}")
        overlap = len(request_tokens & text_tokens) / max(1, len(request_tokens))
        relevance = _clamp(tavily_score * 0.8 + overlap * 0.2)

        official = _is_official(domain, plan.include_domains)
        trust = 1.0 if official else 0.55 + tavily_score * 0.3
        recency = _recency_score(raw.get("published_date") or raw.get("publishedDate"))
        if plan.freshness_important:
            score = relevance * 0.5 + trust * 0.2 + recency * 0.2 + official * 0.1
        else:
            score = relevance * 0.65 + trust * 0.25 + official * 0.1
        return round(_clamp(score), 4)

    def _evaluate_quality(
        self,
        plan: SearchPlan,
        results: list[SearchResult],
        raw_results: list[dict[str, Any]],
    ) -> QualityReport:
        covered = {
            str(item.get("_query"))
            for item in raw_results
            if item.get("_query")
        }
        coverage = len(covered) / max(1, len(plan.queries))
        top_scores = [item.score for item in results[:5]]
        overall = sum(top_scores) / len(top_scores) if top_scores else 0.0
        reasons: list[str] = []
        if len(results) < self.profile.min_results:
            reasons.append("충분한 수의 고유 결과를 확보하지 못했습니다.")
        if overall < self.profile.min_quality_score:
            reasons.append("상위 결과의 관련성/신뢰도 점수가 낮습니다.")
        if coverage < 1:
            reasons.append("일부 검색 Query에서 유효한 결과를 얻지 못했습니다.")
        return QualityReport(
            overall_score=round(overall, 4),
            result_count=len(results),
            query_coverage=round(coverage, 4),
            needs_more_search=bool(reasons),
            reasons=reasons,
        )


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        sorted(
            [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in _TRACKING_PARAMS
            ]
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _url_key(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc.lower()}{parts.path.rstrip('/')}/{parts.query}".casefold()


def _matches_domain(domain: str, candidates: list[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in candidates)


def _is_official(domain: str, preferred: list[str]) -> bool:
    return _matches_domain(domain, preferred) or domain.endswith(
        (".gov", ".gov.kr", ".go.kr", ".ac.kr", ".edu")
    )


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_PATTERN.findall(value) if len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _recency_score(value: Any) -> float:
    if not value:
        return 0.5
    try:
        published = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - published).total_seconds() / 86400)
        return _clamp(math.exp(-age_days / 180))
    except (TypeError, ValueError):
        return 0.5


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _time_range(days: int) -> str:
    if days <= 1:
        return "day"
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    return "year"
