from __future__ import annotations

import html
import os
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from core.tracing.logger import get_logger
from models.search_model import (
    SearchPlan,
    SearchProfile,
    SearchQuery,
)


logger = get_logger("search.naver")

_HTML_TAG_PATTERN = re.compile(
    r"<[^>]+>"
)


class NaverSearchProvider:
    """
    Naver Search Open API Provider.

    국내 웹문서 및 뉴스 검색을 담당한다.
    """

    BASE_URL = (
        "https://naverapihub.apigw.ntruss.com/search/v1"
    )

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "naver"

    def _get_credentials(
        self,
    ) -> tuple[str, str]:
        """
        Naver API 인증 정보를 환경변수에서 조회한다.
        """

        client_id = os.getenv(
            "NAVER_CLIENT_ID"
        )

        client_secret = os.getenv(
            "NAVER_CLIENT_SECRET"
        )

        if not client_id:
            raise RuntimeError(
                "NAVER_CLIENT_ID "
                "환경변수가 설정되어 있지 않습니다."
            )

        if not client_secret:
            raise RuntimeError(
                "NAVER_CLIENT_SECRET "
                "환경변수가 설정되어 있지 않습니다."
            )

        return (
            client_id,
            client_secret,
        )

    def _get_client(
        self,
    ) -> httpx.AsyncClient:
        """
        HTTP Client를 lazy initialization 한다.
        """

        if self._client is None:
            self._client = (
                httpx.AsyncClient(
                    timeout=10.0,
                )
            )

        return self._client

    async def search(
        self,
        plan: SearchPlan,
        query: SearchQuery,
        profile: SearchProfile,
    ) -> list[dict[str, Any]]:

        logger.info(
            "Naver provider entered query=%r",
            query.query,
        )

        try:
            client_id, client_secret = (
                self._get_credentials()
            )

            client = self._get_client()

            endpoint = self._resolve_endpoint(
                plan
            )

            url = (
                f"{self.BASE_URL}"
                f"{endpoint}"
            )

            headers = {
                "X-NCP-APIGW-API-KEY-ID": client_id,
                "X-NCP-APIGW-API-KEY": client_secret,
            }

            params = {
                "query": query.query,
                "display": profile.max_results_per_query,
                "start": 1,
                "sort": (
                    "date"
                    if plan.freshness_important
                    else "sim"
                ),
                "format": "json",
            }

            logger.info(
                "Naver search start "
                "query=%r endpoint=%s",
                query.query,
                endpoint,
            )

            response = await client.get(
                url,
                headers=headers,
                params=params,
            )

            response.raise_for_status()

            payload = response.json()

        except Exception:
            logger.exception(
                "Naver search failed "
                "query=%r",
                query.query,
            )
            raise

        items = payload.get(
            "items",
            [],
        )

        logger.info(
            "Naver search success "
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

            normalized = (
                self._normalize_item(
                    item=item,
                    query=query,
                )
            )

            if normalized:
                results.append(
                    normalized
                )

        return results
    
    @staticmethod
    def _resolve_endpoint(
        plan: SearchPlan,
    ) -> str:
        """
        SearchPlan의 topic에 따라
        Naver API HUB Endpoint를 선택한다.
        """

        if plan.topic == "news":
            return "/news"

        return "/webkr"

    def _normalize_item(
        self,
        item: dict[str, Any],
        query: SearchQuery,
    ) -> dict[str, Any] | None:
        """
        Naver 응답을 공통 Raw Result 형식으로 변환한다.
        """

        url = str(
            item.get("originallink")
            or item.get("link")
            or ""
        ).strip()

        if not url:
            return None

        title = _clean_html(
            str(
                item.get("title")
                or ""
            )
        )

        content = _clean_html(
            str(
                item.get("description")
                or ""
            )
        )

        published_date = (
            _normalize_published_date(
                item.get("pubDate")
            )
        )

        return {
            "title": title,
            "url": url,
            "content": content,
            "published_date": (
                published_date
            ),
            "_query": query.query,
            "_category": (
                query.category
            ),
            "_provider": self.name,
        }


def _clean_html(
    value: str,
) -> str:
    """
    Naver 검색 결과의 HTML 태그와
    HTML Entity를 제거한다.
    """

    without_tags = (
        _HTML_TAG_PATTERN.sub(
            "",
            value,
        )
    )

    return html.unescape(
        without_tags
    ).strip()


def _normalize_published_date(
    value: Any,
) -> str | None:
    """
    Naver 뉴스 API의 RFC 2822 날짜를
    ISO-8601 문자열로 변환한다.
    """

    if not value:
        return None

    try:
        parsed = (
            parsedate_to_datetime(
                str(value)
            )
        )

        if parsed.tzinfo is None:
            parsed = (
                parsed.replace(
                    tzinfo=timezone.utc
                )
            )

        return parsed.isoformat()

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return None