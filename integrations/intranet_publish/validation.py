"""Checks done in BOA before anything is uploaded or sent (pure functions)."""
from __future__ import annotations

import re
from typing import Any

from .config import JOB_ID_PREFIX, MAX_IMAGE_BYTES, MAX_RECIPIENT_CHARS, MAX_RECIPIENTS, BoardSpec


class PublishError(Exception):
    """Raised by publish_post. ``code`` is stable and meant for callers; ``message`` is Korean text.

    Messages never contain SAS URLs, connection strings, keys or recipient names.
    """

    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out


_FORBIDDEN_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f;<>\"']")

_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}


def make_job_id(board: str, post_key: str) -> str:
    """Deterministic: retries of the same post reuse it (device_agent's ledger is keyed by it)."""
    return f"{JOB_ID_PREFIX}{board}-{post_key}"


def check_board(board: str, boards: dict[str, BoardSpec]) -> BoardSpec:
    spec = boards.get(board)
    if spec is None:
        raise PublishError("INVALID_BOARD", f"허용되지 않은 게시판입니다. 사용 가능: {', '.join(sorted(boards))}")
    return spec


def normalize_recipients(recipients: Any, board: BoardSpec) -> list[str]:
    """Same rules as the device_agent adapter. Errors refer to positions, never to names."""
    if recipients is None:
        return []
    if not isinstance(recipients, (list, tuple)):
        raise PublishError("INVALID_RECIPIENTS", "recipients는 이름 문자열 목록이어야 합니다.")
    if recipients and not board.allows_recipients:
        raise PublishError("INVALID_RECIPIENTS", f"{board.label}({board.name})에는 수신자를 지정할 수 없습니다.")

    out: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(recipients):
        if not isinstance(raw, str):
            raise PublishError("INVALID_RECIPIENTS", f"{index}번째 수신자가 문자열이 아닙니다.", {"index": index})
        if _FORBIDDEN_NAME_CHARS.search(raw):
            raise PublishError("INVALID_RECIPIENTS", f"{index}번째 수신자에 허용되지 않는 문자가 있습니다.", {"index": index})
        name = " ".join(raw.split())
        if not 1 <= len(name) <= MAX_RECIPIENT_CHARS:
            raise PublishError(
                "INVALID_RECIPIENTS", f"{index}번째 수신자 이름은 1~{MAX_RECIPIENT_CHARS}자여야 합니다.", {"index": index}
            )
        if name not in seen:
            seen.add(name)
            out.append(name)
    if len(out) > MAX_RECIPIENTS:
        raise PublishError("INVALID_RECIPIENTS", f"수신자는 최대 {MAX_RECIPIENTS}명입니다.")
    return out


def extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def check_image_meta(key: str, filename: str, size: int, head: bytes) -> str:
    """Check size and file signature (first bytes). Return the content type, or raise INVALID_IMAGE."""
    ext = extension(filename)
    if ext not in _SIGNATURES:
        raise PublishError("INVALID_IMAGE", f"이미지 '{key}': png/jpg/jpeg/gif만 가능합니다.", {"key": key})
    if not size or not head:
        raise PublishError("INVALID_IMAGE", f"이미지 '{key}': 내용이 비어 있습니다.", {"key": key})
    if size > MAX_IMAGE_BYTES:
        raise PublishError("INVALID_IMAGE", f"이미지 '{key}': 10MB를 넘습니다.", {"key": key})
    if not any(bytes(head[: len(sig)]) == sig for sig in _SIGNATURES[ext]):
        raise PublishError("INVALID_IMAGE", f"이미지 '{key}': 파일 내용이 확장자({ext})와 다릅니다.", {"key": key})
    return CONTENT_TYPES[ext]


def check_image(key: str, filename: str, data: Any) -> str:
    """Same checks for bytes held in memory (used by the image upload tool)."""
    if not isinstance(data, (bytes, bytearray)):
        raise PublishError("INVALID_IMAGE", f"이미지 '{key}': bytes가 아닙니다.", {"key": key})
    return check_image_meta(key, filename, len(data), bytes(data[:16]))


_REASON_TOKEN = re.compile(r"^[A-Z][A-Z_]{2,63}$")


def extract_reason(callback: dict[str, Any]) -> str:
    """device_agent puts the code in result.reason and error_message; exceptions only fill error_message."""
    result = callback.get("result") or {}
    for candidate in (result.get("reason"), callback.get("error_message")):
        if isinstance(candidate, str) and _REASON_TOKEN.match(candidate.strip()):
            return candidate.strip()
    return "UNKNOWN"
