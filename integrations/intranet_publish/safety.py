"""device_agent와 공유하는 본문 안전성 검사 규칙.

``UNSAFE_BODY`` 정규식은 device_agent 핸들러(``post_intranet_board.py``)와 동일합니다.
BOA에서 미리 검사함으로써 device_agent가 거부할(``BODY_REJECTED``) 본문을 업로드나 작업 요청 전에 차단합니다.
"""
from __future__ import annotations

import re

# device_agent/src/jobs/post_intranet_board/post_intranet_board.py 와 동기화 유지
UNSAFE_BODY = re.compile(
    r"<\s*(?:script|style|frame|object|embed|form|input|button|link|meta|base)\b"
    r"|<[^>]*\son[a-z0-9_-]+\s*="
    r"|(?:href|src|action)\s*=\s*[\"']?\s*(?:javascript|vbscript):",
    re.IGNORECASE,
)


def is_safe_html(html: str) -> bool:
    """인트라넷에 전달되어서는 안 되는 구문이 HTML에 포함되어 있지 않으면 True를 반환합니다."""
    return not UNSAFE_BODY.search(html)
