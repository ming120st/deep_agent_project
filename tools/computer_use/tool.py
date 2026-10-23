
"""Skill 기반 Playwright 공통 도구.

사용자 및 대화 정보는 신뢰된 RunnableConfig에서 전달받는다.
"""

import logging
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from tools.computer_use.service import (
    BrowserOwner,
    computer_use_service,
)

logger = logging.getLogger(__name__)


async def _invoke(method, config, skill, **kwargs):
    try:
        return await method(
            BrowserOwner.from_config(config),
            skill,
            **kwargs,
        )

    except (PermissionError, ValueError, FileNotFoundError) as exc:
        return {
            "status": "error",
            "code": type(exc).__name__,
            "message": (
                "사용자·대화 정보, Skill 설정 또는 "
                "브라우저 상태를 확인하세요."
            ),
        }

    except Exception:
        logger.exception(
            "브라우저 작업 실패: %s",
            method.__name__,
        )
        return {
            "status": "error",
            "code": "BROWSER_EXECUTION_ERROR",
            "message": (
                "브라우저 작업에 실패했습니다. "
                "서버 로그에서 오류를 확인하세요."
            ),
        }


@tool
async def start_browser(
    skill: str,
    url: str | None = None,
    config: RunnableConfig = None,
) -> dict:
    """브라우저를 시작하거나 기존 세션을 재사용한다."""
    return await _invoke(
        computer_use_service.start,
        config,
        skill,
        url=url,
    )


@tool
async def browser_action(
    skill: str,
    action: Literal[
        "navigate",
        "read",
        "check_login",
        "login",
        "read_rows",
        "download",
        "click",
        "fill",
        "select",
        "scroll",
        "keypress",
        "wait",
    ],
    params: dict | None = None,
    browser_id: str | None = None,
    config: RunnableConfig = None,
) -> dict:
    """Skill에 정의된 절차대로 Playwright 작업을 실행한다.

    로그인 계정정보는 서버 환경변수에서만 조회한다.

    로그인 반환 상태:
    - authenticated: 인증 성공 확인
    - credentials_required: 서버 계정 설정 누락
    - login_submitted_unverified: 제출 완료, 검증 선택자 없음
    - login_unconfirmed: 인증 성공 확인 불가

    검증되지 않은 상태를 로그인 성공으로 보고하지 않는다.
    """
    return await _invoke(
        computer_use_service.action,
        config,
        skill,
        action=action,
        params=params or {},
        browser_id=browser_id,
    )


@tool
async def get_browser_state(
    skill: str,
    browser_id: str | None = None,
    config: RunnableConfig = None,
) -> dict:
    """현재 브라우저의 URL과 실행 상태를 확인한다."""
    return await _invoke(
        computer_use_service.get_state,
        config,
        skill,
        browser_id=browser_id,
    )


@tool
async def close_browser(
    skill: str,
    browser_id: str | None = None,
    config: RunnableConfig = None,
) -> dict:
    """현재 사용자·대화의 브라우저 세션을 종료한다."""
    return await _invoke(
        computer_use_service.close,
        config,
        skill,
        browser_id=browser_id,
    )
