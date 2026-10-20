from __future__ import annotations

import os
from typing import Any

import httpx
from langchain_core.tools import tool

from core.tracing.logger import get_logger

logger = get_logger("search.youtube")


class YouTubeSearchProvider:
    """
    YouTube Data API v3 Provider.

    search.list API를 사용하여
    YouTube 영상을 검색한다.
    """

    BASE_URL = (
        "https://www.googleapis.com/youtube/v3"
    )

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "youtube"

    def _get_api_key(
        self,
    ) -> str:
        api_key = os.getenv(
            "YOUTUBE_API_KEY"
        )

        if not api_key:
            raise RuntimeError(
                "YOUTUBE_API_KEY "
                "환경변수가 설정되어 있지 않습니다."
            )

        return api_key

    def _get_client(
        self,
    ) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=10.0,
            )

        return self._client

    async def search(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """
        YouTube search.list API를 사용하여
        관련 영상을 검색한다.
        """

        clean_query = query.strip()

        if not clean_query:
            raise ValueError(
                "query는 비어 있을 수 없습니다."
            )

        api_key = self._get_api_key()
        client = self._get_client()

        params = {
            "part": "snippet",
            "q": clean_query,
            "type": "video",
            "maxResults": min(
                max_results,
                50,
            ),
            "order": "relevance",
            "key": api_key,
        }

        logger.info(
            "YouTube search start "
            "query=%r",
            clean_query,
        )

        try:
            response = await client.get(
                f"{self.BASE_URL}/search",
                params=params,
            )

            response.raise_for_status()

            payload = response.json()

        except Exception:
            logger.exception(
                "YouTube search failed "
                "query=%r",
                clean_query,
            )
            raise

        items = payload.get(
            "items",
            [],
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
                    item
                )
            )

            if normalized is not None:
                results.append(
                    normalized
                )

        logger.info(
            "YouTube search success "
            "query=%r results=%d",
            clean_query,
            len(results),
        )

        return results

    def _normalize_item(
        self,
        item: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        YouTube search.list 응답을
        Agent가 사용하기 쉬운 형태로 변환한다.
        """

        video_id = (
            item
            .get("id", {})
            .get("videoId")
        )

        if not video_id:
            return None

        snippet = item.get(
            "snippet",
            {},
        )

        return {
            "video_id": video_id,
            "title": snippet.get(
                "title",
                "",
            ),
            "description": snippet.get(
                "description",
                "",
            ),
            "channel_title": snippet.get(
                "channelTitle",
                "",
            ),
            "published_at": snippet.get(
                "publishedAt",
            ),
            "thumbnail_url": (
                snippet
                .get("thumbnails", {})
                .get("high", {})
                .get("url")
            ),
            "url": (
                "https://www.youtube.com/"
                f"watch?v={video_id}"
            ),
            "_provider": self.name,
        }


_youtube_provider = (
    YouTubeSearchProvider()
)


@tool
async def search_youtube_videos(
    query: str,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """
    YouTube에서 관련 영상을 검색한다.

    사용자가 한국어로 요청한 경우 한국어 검색어를 우선 사용한다.
    """

    return await (
        _youtube_provider.search(
            query=query,
            max_results=max_results,
        )
    )