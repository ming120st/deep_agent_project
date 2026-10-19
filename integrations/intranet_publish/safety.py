"""Body safety rule shared with the device_agent.

``UNSAFE_BODY`` is identical to the regex in the device_agent handler
(``post_intranet_board.py``). Checking it here means a body the device_agent would refuse
(``BODY_REJECTED``) is stopped in BOA before any upload or request.
"""
from __future__ import annotations

import re

# Keep in sync with device_agent/src/jobs/post_intranet_board/post_intranet_board.py
UNSAFE_BODY = re.compile(
    r"<\s*(?:script|style|iframe|frame|object|embed|form|input|button|link|meta|base)\b"
    r"|<[^>]*\son[a-z0-9_-]+\s*="
    r"|(?:href|src|action)\s*=\s*[\"']?\s*(?:javascript|vbscript):",
    re.IGNORECASE,
)


def is_safe_html(html: str) -> bool:
    """True when the HTML contains none of the constructs the intranet must never receive."""
    return not UNSAFE_BODY.search(html)
