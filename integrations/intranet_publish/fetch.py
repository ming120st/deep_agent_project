"""Download an image from a URL into memory (never to disk) for the image upload tool.

Guards (the URL comes from an agent, so it is untrusted):
- http/https on the default ports only, no credentials in the URL
- every host (and every redirect target) must resolve only to public addresses
- at most 3 redirects, 15 s timeout, 10 MB limit while streaming
Residual risk: DNS can change between the check and the connection (rebinding).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .config import MAX_IMAGE_BYTES
from .validation import PublishError

MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 15.0


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    return ip.is_global and not ip.is_multicast


async def check_url(url: str) -> None:
    """Raise PublishError(INVALID_IMAGE_URL) unless the URL is a public http(s) address."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise PublishError("INVALID_IMAGE_URL", "이미지 주소를 해석할 수 없습니다.") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PublishError("INVALID_IMAGE_URL", "이미지 주소는 http(s)여야 합니다.")
    if parts.username or parts.password:
        raise PublishError("INVALID_IMAGE_URL", "주소에 인증 정보를 넣을 수 없습니다.")
    if port not in (None, 80, 443):
        raise PublishError("INVALID_IMAGE_URL", "80/443 이외의 포트는 사용할 수 없습니다.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise PublishError("IMAGE_DOWNLOAD_FAILED", "이미지 주소의 호스트를 찾을 수 없습니다.") from None
    if not infos or not all(_is_public(info[4][0]) for info in infos):
        raise PublishError("INVALID_IMAGE_URL", "내부 또는 비공개 주소는 사용할 수 없습니다.")


async def fetch_image(url: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """Return the response body. Raises PublishError (INVALID_IMAGE_URL / IMAGE_DOWNLOAD_FAILED / INVALID_IMAGE)."""
    owned = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(current)
            try:
                async with client.stream("GET", current, headers={"Accept": "image/*"}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise PublishError("IMAGE_DOWNLOAD_FAILED", "리다이렉트 주소가 없습니다.")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise PublishError("IMAGE_DOWNLOAD_FAILED", f"이미지를 받지 못했습니다 (HTTP {response.status_code}).")
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                        raise PublishError("INVALID_IMAGE", "이미지가 10MB를 넘습니다.")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_IMAGE_BYTES:
                            raise PublishError("INVALID_IMAGE", "이미지가 10MB를 넘습니다.")
                    return bytes(data)
            except httpx.HTTPError as exc:
                raise PublishError("IMAGE_DOWNLOAD_FAILED", f"이미지를 받지 못했습니다 ({type(exc).__name__}).") from None
        raise PublishError("IMAGE_DOWNLOAD_FAILED", "리다이렉트가 너무 많습니다.")
    finally:
        if owned:
            await client.aclose()
