"""뉴스 기사 HTML에서 대표 이미지(og:image 우선) URL을 추출한다."""
from __future__ import annotations

from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

# 초기 지원 매체: 필요할 때 명시적으로 추가한다.
ARTICLE_HOSTS = frozenset({
    "techcrunch.com", "www.techcrunch.com",
    "venturebeat.com", "www.venturebeat.com",
    "aitimes.com", "www.aitimes.com",
    "zdnet.co.kr", "www.zdnet.co.kr",
    "etnews.com", "www.etnews.com",
})
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 3


class ArticleImageExtractionError(ValueError):
    """기사 대표 이미지 추출 실패."""


def _approved_article_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.hostname.lower().rstrip(".") in ARTICLE_HOSTS
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


def _meta_url(soup: BeautifulSoup, page_url: str) -> str | None:
    for selector in (
        'meta[property="og:image"]',
        'meta[property="og:image:secure_url"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ):
        tag = soup.select_one(selector)
        value = tag.get("content") if tag else None
        if not isinstance(value, str) or not value.strip():
            continue
        image_url = urljoin(page_url, value.strip())
        parsed = urlsplit(image_url)
        if parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password:
            return image_url
    return None


async def extract_article_og_image(article_url: str) -> str | None:
    """허용된 기사에서 대표 이미지 URL 추출. 없으면 None, 실패하면 예외.

    리다이렉트 대상도 언론사 허용 목록으로 제한한다.
    """
    if not _approved_article_url(article_url):
        raise ArticleImageExtractionError("지원하지 않거나 유효하지 않은 기사 URL입니다.")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        follow_redirects=False,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; DeepAgentArticleImage/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        },
    ) as client:
        current_url = article_url
        for _ in range(MAX_REDIRECTS + 1):
            if not _approved_article_url(current_url):
                raise ArticleImageExtractionError("허용되지 않은 리다이렉트 대상입니다.")
            async with client.stream("GET", current_url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ArticleImageExtractionError("리다이렉트 Location이 없습니다.")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                mime = response.headers.get("content-type", "").lower()
                if "text/html" not in mime and "application/xhtml+xml" not in mime:
                    raise ArticleImageExtractionError("기사 응답이 HTML이 아닙니다.")
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_HTML_BYTES:
                        raise ArticleImageExtractionError("기사 HTML 크기 제한 초과")
                    chunks.append(chunk)
                soup = BeautifulSoup(b"".join(chunks), "html.parser")
                return _meta_url(soup, str(response.url))
        raise ArticleImageExtractionError("기사 리다이렉트 횟수 초과")