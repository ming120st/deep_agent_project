from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlsplit,
    urlunsplit,
)

from models.search_model import (
    QualityReport,
    SearchPlan,
    SearchProfile,
    SearchResult,
)


_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "ref",
    "referrer",
}

_TOKEN_PATTERN = re.compile(
    r"[0-9a-zA-Z가-힣]+"
)


class SearchResultProcessor:
    """
    검색 Provider에서 수집한 Raw Result를 정제하고,
    중복 제거, 점수 계산, 품질 평가를 수행한다.
    """

    def __init__(
        self,
        profile: SearchProfile,
    ) -> None:
        self.profile = profile

    def process(
        self,
        request: str,
        plan: SearchPlan,
        raw_results: list[
            dict[str, Any]
        ],
    ) -> list[SearchResult]:
        """
        Raw Result를 SearchResult로 변환하고
        중복 제거 후 점수순으로 정렬한다.
        """

        ranked: list[
            SearchResult
        ] = []

        for raw in raw_results:
            url = str(
                raw.get("url")
                or ""
            ).strip()

            if not url:
                continue

            domain = _domain(
                url
            )

            if _matches_domain(
                domain,
                plan.exclude_domains,
            ):
                continue

            provider = str(
                raw.get("_provider")
                or ""
            )

            if provider not in (
                "tavily",
                "naver",
            ):
                continue

            ranked.append(
                SearchResult(
                    title=str(
                        raw.get("title")
                        or ""
                    ).strip(),
                    url=_canonical_url(
                        url
                    ),
                    content=str(
                        raw.get("content")
                        or raw.get("raw_content")
                        or ""
                    ).strip(),
                    published_date=(
                        raw.get(
                            "published_date"
                        )
                        or raw.get(
                            "publishedDate"
                        )
                    ),
                    source_domain=domain,
                    query=str(
                        raw.get("_query")
                        or ""
                    ),
                    category=str(
                        raw.get("_category")
                        or "general"
                    ),
                    provider=provider,
                    score=self._score_result(
                        request,
                        plan,
                        raw,
                        domain,
                    ),
                )
            )

        ranked.sort(
            key=lambda item: item.score,
            reverse=True,
        )

        unique: list[
            SearchResult
        ] = []

        seen_urls: set[str] = set()

        for item in ranked:
            url_key = _url_key(
                item.url
            )

            if url_key in seen_urls:
                continue

            title_tokens = _tokens(
                item.title
            )

            is_similar = any(
                _jaccard(
                    title_tokens,
                    _tokens(
                        other.title
                    ),
                )
                >= 0.82
                for other in unique
            )

            if is_similar:
                continue

            seen_urls.add(
                url_key
            )

            unique.append(
                item
            )

        return unique

    def evaluate_quality(
        self,
        plan: SearchPlan,
        results: list[
            SearchResult
        ],
        raw_results: list[
            dict[str, Any]
        ],
    ) -> QualityReport:
        """
        최종 검색 결과의 품질과
        Query 커버리지를 평가한다.
        """

        # 최초 검색 계획에 포함된 쿼리
        planned_queries = {
            query.query.strip().casefold()
            for query in plan.queries
        }

        covered_queries = {
            item.query.strip().casefold()
            for item in results
            if item.query.strip().casefold()
            in planned_queries
        }

        coverage = (
            len(covered_queries) / len(planned_queries)
            if planned_queries
            else 0.0
        )

        top_scores = [
            item.score
            for item in results[:5]
        ]

        overall_score = (
            sum(
                top_scores
            )
            / len(
                top_scores
            )
            if top_scores
            else 0.0
        )

        reasons: list[str] = []

        if (
            len(results)
            < self.profile.min_results
        ):
            reasons.append(
                "충분한 수의 고유 결과를 "
                "확보하지 못했습니다."
            )

        if (
            overall_score
            < self.profile.min_quality_score
        ):
            reasons.append(
                "상위 결과의 관련성/"
                "신뢰도 점수가 낮습니다."
            )

        if coverage < 1:
            reasons.append(
                "일부 검색 Query에서 "
                "유효한 결과를 얻지 못했습니다."
            )

        return QualityReport(
            overall_score=round(
                overall_score,
                4,
            ),
            result_count=len(
                results
            ),
            query_coverage=round(
                coverage,
                4,
            ),
            needs_more_search=bool(
                reasons
            ),
            reasons=reasons,
        )

    def _score_result(
        self,
        request: str,
        plan: SearchPlan,
        raw: dict[str, Any],
        domain: str,
    ) -> float:
        """
        Provider별 특성을 고려해
        공통 품질 점수를 계산한다.
        """

        request_tokens = _tokens(
            request
        )

        text_tokens = _tokens(
            f"{raw.get('title', '')} "
            f"{raw.get('content', '')}"
        )

        overlap = (
            len(
                request_tokens
                & text_tokens
            )
            / max(
                1,
                len(
                    request_tokens
                ),
            )
        )

        raw_score = raw.get(
            "score"
        )

        if raw_score is not None:
            provider_score = _clamp(
                _safe_float(
                    raw_score
                )
            )

            relevance = _clamp(
                provider_score * 0.8
                + overlap * 0.2
            )
        else:
            relevance = _clamp(
                overlap
            )

        official = _is_official(
            domain,
            plan.include_domains,
        )

        trust = (
            1.0
            if official
            else (
                0.55
                + relevance * 0.3
            )
        )

        recency = _recency_score(
            raw.get(
                "published_date"
            )
            or raw.get(
                "publishedDate"
            )
        )

        if plan.freshness_important:
            score = (
                relevance * 0.5
                + trust * 0.2
                + recency * 0.2
                + official * 0.1
            )
        else:
            score = (
                relevance * 0.65
                + trust * 0.25
                + official * 0.1
            )

        return round(
            _clamp(
                score
            ),
            4,
        )


def _canonical_url(
    url: str,
) -> str:
    """
    UTM 등 추적 파라미터를 제거하고
    URL을 정규화한다.
    """

    parts = urlsplit(
        url
    )

    query = urlencode(
        sorted(
            [
                (
                    key,
                    value,
                )
                for (
                    key,
                    value,
                )
                in parse_qsl(
                    parts.query,
                    keep_blank_values=True,
                )
                if not key.lower().startswith(
                    "utm_"
                )
                and key.lower()
                not in _TRACKING_PARAMS
            ]
        )
    )

    path = (
        parts.path.rstrip("/")
        or "/"
    )

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            query,
            "",
        )
    )


def _domain(
    url: str,
) -> str:
    """
    URL에서 hostname을 추출한다.
    """

    return (
        urlsplit(
            url
        ).hostname
        or ""
    ).lower().removeprefix(
        "www."
    )


def _url_key(
    url: str,
) -> str:
    """
    URL 중복 비교용 Key를 생성한다.
    """

    parts = urlsplit(
        url
    )

    return (
        f"{parts.netloc.lower()}"
        f"{parts.path.rstrip('/')}/"
        f"{parts.query}"
    ).casefold()


def _matches_domain(
    domain: str,
    candidates: list[str],
) -> bool:
    """
    현재 도메인이 지정 도메인 또는
    하위 도메인인지 확인한다.
    """

    return any(
        domain == item
        or domain.endswith(
            "." + item
        )
        for item in candidates
    )


def _is_official(
    domain: str,
    preferred: list[str],
) -> bool:
    """
    우선 도메인 또는 공공/교육기관
    도메인인지 판별한다.
    """

    return (
        _matches_domain(
            domain,
            preferred,
        )
        or domain.endswith(
            (
                ".gov",
                ".gov.kr",
                ".go.kr",
                ".ac.kr",
                ".edu",
            )
        )
    )


def _tokens(
    value: str,
) -> set[str]:
    """
    검색어 비교용 토큰 집합을 생성한다.
    """

    return {
        token.casefold()
        for token
        in _TOKEN_PATTERN.findall(
            value
        )
        if len(token) > 1
    }


def _jaccard(
    left: set[str],
    right: set[str],
) -> float:
    """
    두 토큰 집합의 Jaccard 유사도를 계산한다.
    """

    if not left or not right:
        return 0.0

    return (
        len(
            left & right
        )
        / len(
            left | right
        )
    )


def _recency_score(
    value: Any,
) -> float:
    """
    게시일을 기준으로 최신성 점수를 계산한다.
    """

    if not value:
        return 0.5

    try:
        published = (
            datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00",
                )
            )
        )

        if published.tzinfo is None:
            published = (
                published.replace(
                    tzinfo=timezone.utc
                )
            )

        age_days = max(
            0.0,
            (
                datetime.now(
                    timezone.utc
                )
                - published
            ).total_seconds()
            / 86400,
        )

        return _clamp(
            math.exp(
                -age_days / 180
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0.5


def _clamp(
    value: float,
) -> float:
    """
    값을 0~1 범위로 제한한다.
    """

    return max(
        0.0,
        min(
            1.0,
            value,
        ),
    )


def _safe_float(
    value: Any,
) -> float:
    """
    안전하게 float로 변환한다.
    """

    try:
        return float(
            value or 0
        )

    except (
        TypeError,
        ValueError,
    ):
        return 0.0