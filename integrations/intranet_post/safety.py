"""Safety helpers shared by the schema and the renderer.

``UNSAFE_BODY`` is intentionally identical to the regex used by the device_agent
handler (``post_intranet_board.py``), so the renderer output can be checked with
the very same rule before it is ever sent to the intranet.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# Keep in sync with device_agent/src/jobs/post_intranet_board/post_intranet_board.py
UNSAFE_BODY = re.compile(
    r"<\s*(?:script|style|iframe|frame|object|embed|form|input|button|link|meta|base)\b"
    r"|<[^>]*\son[a-z0-9_-]+\s*="
    r"|(?:href|src|action)\s*=\s*[\"']?\s*(?:javascript|vbscript):",
    re.IGNORECASE,
)

MAX_URL_LENGTH = 2000
_URL_FORBIDDEN = re.compile(r"[\s\x00-\x1f\x7f\"<>`\\]")


def is_safe_html(html: str) -> bool:
    """True when the HTML contains none of the constructs the intranet must never receive."""
    return not UNSAFE_BODY.search(html)


def safe_url(value: str) -> str | None:
    """Return the URL unchanged if it is a plain http(s) link, otherwise ``None``.

    Rejects: other schemes (javascript:, data:, ...), credentials in the URL
    (``user:pw@host``), whitespace/control chars, quotes/angle brackets/backslashes,
    and over-long values.
    """
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL_LENGTH or _URL_FORBIDDEN.search(url):
        return None
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        _ = parts.port  # raises ValueError on an invalid port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return url
