from __future__ import annotations

import asyncio
from typing import Annotated

from langchain_core.tools import tool

from tools.common.smtp_mail import send_mail


@tool
async def send_email(
    to_list: Annotated[
        list[str],
        "수신자 이메일 주소 목록.",
    ],
    subject: Annotated[
        str,
        "이메일 제목.",
    ],
    message: Annotated[
        str,
        "이메일 본문 (일반 텍스트).",
    ],
) -> dict:
    """
    지정한 수신자에게 이메일을 발송한다.

    사용자가 다른 Tool의 조회 결과 등 특정 내용을 이메일로 받고 싶어할 때 사용한다.
    to_list, subject, message가 모두 확보되면 이 Tool을 호출한다.
    이메일 주소가 확보되지 않았다면 먼저 사용자에게 물어봐야 한다.
    본문(message)에는 사용자에게 보여준 결과를 빠짐없이 포함시켜야 한다.
    """
    try:
        success = await asyncio.to_thread(
            send_mail,
            to_list,
            message,
            subject,
        )
    except Exception as e:
        return {
            "success": False,
            "reason": f"메일 전송 중 오류가 발생했습니다: {e}",
        }

    if not success:
        return {
            "success": False,
            "reason": "메일 전송에 실패했습니다. 수신자 주소나 SMTP 설정(GMAIL_APP_PW_INFO)을 확인해주세요.",
        }

    return {
        "success": True,
        "to_list": to_list,
        "subject": subject,
    }
