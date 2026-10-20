"""업로드 및 전송 전에 BOA 내부에서 수행하는 유효성 검사 함수 모음 (순수 함수)."""
from __future__ import annotations

import re
from typing import Any

from .config import JOB_ID_PREFIX, MAX_IMAGE_BYTES, MAX_RECIPIENT_CHARS, MAX_RECIPIENTS, BoardSpec


class PublishError(Exception):
    """publish_post에서 발생하는 예외. ``code``는 호출자가 구분할 수 있는 고정 코드이며, ``message``는 한글 안내 메시지입니다.

    메시지에는 SAS URL, 연결 문자열, 비밀키 또는 수신자 이름이 절대 포함되지 않습니다.
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
    """결정론적 생성: 같은 글의 재시도 시 동일한 ID가 재사용됩니다 (device_agent의 원장에 키로 사용됨)."""
    return f"{JOB_ID_PREFIX}{board}-{post_key}"


def check_board(board: str, boards: dict[str, BoardSpec]) -> BoardSpec:
    spec = boards.get(board)
    if spec is None:
        raise PublishError("INVALID_BOARD", f"허용되지 않은 게시판입니다. 사용 가능: {', '.join(sorted(boards))}")
    return spec


def normalize_recipients(recipients: Any, board: BoardSpec) -> list[str]:
    """device_agent 어댑터와 동일한 규칙. 오류 발생 시 수신자 이름 대신 인덱스 번호만 참조합니다."""
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


def check_image_meta(filename: str, size: int, head: bytes) -> str:
    """크기와 파일 시그니처(첫 바이트)를 검사합니다. content-type을 반환하거나 INVALID_IMAGE 예외를 발생시킵니다."""
    ext = extension(filename)
    if ext not in _SIGNATURES:
        raise PublishError("INVALID_IMAGE", f"이미지 '{filename}': png/jpg/jpeg/gif만 가능합니다.", {"filename": filename})
    if not size or not head:
        raise PublishError("INVALID_IMAGE", f"이미지 '{filename}': 내용이 비어 있습니다.", {"filename": filename})
    if size > MAX_IMAGE_BYTES:
        raise PublishError("INVALID_IMAGE", f"이미지 '{filename}': 10MB를 넘습니다.", {"filename": filename})
    if not any(bytes(head[: len(sig)]) == sig for sig in _SIGNATURES[ext]):
        raise PublishError("INVALID_IMAGE", f"이미지 '{filename}': 파일 내용이 확장자({ext})와 다릅니다.", {"filename": filename})
    return CONTENT_TYPES[ext]


def check_image(filename: str, data: Any) -> str:
    """메모리에 있는 bytes 데이터에 대한 동일한 검사 (이미지 업로드 도구에서 사용)."""
    if not isinstance(data, (bytes, bytearray)):
        raise PublishError("INVALID_IMAGE", f"이미지 '{filename}': bytes가 아닙니다.", {"filename": filename})
    return check_image_meta(filename, len(data), bytes(data[:16]))


_REASON_TOKEN = re.compile(r"^[A-Z][A-Z_]{2,63}$")


def extract_reason(callback: dict[str, Any]) -> str:
    """device_agent는 사유 코드를 result.reason 또는 error_message에 기록합니다. 예외 발생 시에는 error_message만 채워집니다."""
    result = callback.get("result") or {}
    for candidate in (result.get("reason"), callback.get("error_message")):
        if isinstance(candidate, str) and _REASON_TOKEN.match(candidate.strip()):
            return candidate.strip()
    return "UNKNOWN"
