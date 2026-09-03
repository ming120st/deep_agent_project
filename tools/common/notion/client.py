# tools/common/notion/client.py

from __future__ import annotations

import json
import os
from typing import Any

from notion_client import Client, RetryOptions
from notion_client.errors import (
    APIResponseError,
    HTTPResponseError,
    RequestTimeoutError,
)


# --------------------------------------------------------------------------
# 환경변수 / 클라이언트
# --------------------------------------------------------------------------


def _require_env(name: str) -> str:
    value = os.getenv(name)

    if not value:
        raise RuntimeError(
            f"환경변수 '{name}'가 설정되어 있지 않습니다."
        )

    return value


def _build_client() -> Client:
    token = _require_env("NOTION_TOKEN")

    max_retries = int(
        os.getenv(
            "NOTION_MAX_RETRIES",
            "5",
        )
    )

    return Client(
        options={
            "auth": token,
            "retry": RetryOptions(
                max_retries=max_retries,
                initial_retry_delay_ms=500,
                max_retry_delay_ms=20_000,
            ),
        },
    )


_client: Client | None = None


def get_notion_client() -> Client:
    global _client

    if _client is None:
        _client = _build_client()

    return _client


# --------------------------------------------------------------------------
# 에러 처리
# --------------------------------------------------------------------------


_ERROR_MESSAGES: dict[str, str] = {
    "unauthorized": (
        "Notion 인증에 실패했습니다. "
        "NOTION_TOKEN이 올바른지 확인하세요."
    ),
    "restricted_resource": (
        "이 리소스에 접근할 권한이 없습니다. "
        "Notion에서 해당 페이지/데이터베이스를 "
        "이 Integration에 Connect 했는지 확인하세요."
    ),
    "object_not_found": (
        "요청한 페이지/블록/데이터베이스를 찾을 수 없습니다. "
        "ID가 올바른지, 이 Integration에 Connect 되어 있는지 확인하세요."
    ),
    "validation_error": (
        "요청 형식이 올바르지 않습니다: {message}"
    ),
    "invalid_request": (
        "요청이 잘못되었습니다: {message}"
    ),
    "rate_limited": (
        "Notion API 요청 한도를 초과했습니다 "
        "(자동 재시도 후에도 실패). 잠시 후 다시 시도하세요."
    ),
    "conflict_error": (
        "다른 요청과 충돌이 발생했습니다. "
        "잠시 후 다시 시도하세요."
    ),
    "internal_server_error": (
        "Notion 서버 내부 오류입니다 "
        "(자동 재시도 후에도 실패). 잠시 후 다시 시도하세요."
    ),
    "service_unavailable": (
        "Notion 서비스가 일시적으로 불안정합니다 "
        "(자동 재시도 후에도 실패). 잠시 후 다시 시도하세요."
    ),
}


def describe_error(
    exc: Exception,
) -> dict[str, Any]:

    if isinstance(exc, APIResponseError):
        code = (
            exc.code.value
            if hasattr(exc.code, "value")
            else str(exc.code)
        )

        template = _ERROR_MESSAGES.get(
            code,
            "Notion API 오류({code}): {message}",
        )

        return {
            "error": template.format(
                code=code,
                message=str(exc),
            ),
            "code": code,
        }

    if isinstance(exc, RequestTimeoutError):
        return {
            "error": (
                "Notion API 요청이 시간 초과되었습니다. "
                "네트워크 상태를 확인하고 다시 시도하세요."
            ),
            "code": "request_timeout",
        }

    if isinstance(exc, HTTPResponseError):
        return {
            "error": (
                f"Notion API 통신 오류"
                f"(status={exc.status}): {exc}"
            ),
            "code": str(exc.code),
        }

    return {
        "error": f"예상하지 못한 오류: {exc}",
        "code": "unknown",
    }


def error_json(
    exc: Exception,
) -> str:

    return json.dumps(
        describe_error(exc),
        ensure_ascii=False,
    )