"""
core/google_chat.py

Google Chat API 클라이언트 및 메시지 전송 유틸리티.

[함수 목록]
  send_message(space_name, message)     ← 일반 space / DM space 구분 없이 전송
  find_or_create_dm_space(user_name)    ← DM space 조회 또는 생성 (space_name 반환)
  send_dm(user_name, message)           ← user_name으로 DM 발송 (space_name 모를 때)

[사용 패턴]
  # boa_members에 space_name이 저장된 경우 (일반적)
  send_message(member["space_name"], "안녕하세요")

  # space_name을 모르고 user_name만 아는 경우
  send_dm("users/hong@widemobile.com", "안녕하세요")
"""

import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build


# ── 클라이언트 초기화 ──────────────────────────────────────────────────────────
def _build_chat_service():
    try:
        service_account_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH"))
    except (TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_KEY_PATH 환경변수 에러: {e}")

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=["https://www.googleapis.com/auth/chat.bot"]
    )
    return build("chat", "v1", credentials=credentials)

chat_service = _build_chat_service()


# ── 메시지 전송 ───────────────────────────────────────────────────────────────
import asyncio

def _send_message_sync(space_name: str, message: str) -> bool:
    """동기 전송 (내부용). googleapiclient가 sync 라이브러리라 별도 분리."""
    chat_service.spaces().messages().create(
        parent=space_name,
        body={"text": message},
    ).execute()
    return True

async def send_message(space_name: str, message: str) -> bool:
    """
    space_name으로 메시지를 전송합니다.
    일반 space, 그룹 채팅, DM space 구분 없이 동일하게 사용합니다.

    Args:
        space_name: Google Chat space 리소스명 (예: "spaces/XXXXXXXXXX")
        message:    전송할 텍스트 메시지

    Returns:
        True: 성공 / False: 실패
    """
    try:
        await asyncio.to_thread(_send_message_sync, space_name, message)
        print(f"[GoogleChat] 전송 완료 → {space_name}")
        return True
    except Exception as e:
        print(f"[GoogleChat] 전송 실패 ({space_name}): {e}")
        return False


# ── DM 유틸리티 ───────────────────────────────────────────────────────────────
def _find_or_create_dm_space_sync(user_name: str) -> str:
    """동기 DM space 조회/생성 (내부용)."""
    try:
        space = chat_service.spaces().findDirectMessage(name=user_name).execute()
        return space["name"]
    except Exception:
        space = chat_service.spaces().create(
            body={
                "spaceType": "DIRECT_MESSAGE",
                "singleUserBotDm": False,
            },
            requestId=f"dm-{user_name.replace('/', '-')}",
        ).execute()

        chat_service.spaces().members().create(
            parent=space["name"],
            body={"member": {"name": user_name, "type": "HUMAN"}}
        ).execute()

        return space["name"]


async def find_or_create_dm_space(user_name: str) -> str:
    """
    사용자와의 DM space를 조회하거나 없으면 생성합니다.

    Args:
        user_name: Google Chat 유저 리소스명
                   예: "users/12345678901234567"
                       "users/hong@widemobile.com"

    Returns:
        space_name: 예 "spaces/XXXXXXXXXX"
    """
    return await asyncio.to_thread(_find_or_create_dm_space_sync, user_name)


async def send_dm(user_name: str, message: str) -> bool:
    """
    user_name으로 DM을 발송합니다.
    space_name을 모를 때 사용합니다. (space_name을 알면 send_message 사용)

    Args:
        user_name: Google Chat 유저 리소스명 (예: "users/hong@widemobile.com")
        message:   전송할 텍스트 메시지

    Returns:
        True: 성공 / False: 실패
    """
    try:
        space_name = await find_or_create_dm_space(user_name)
        return await send_message(space_name, message)
    except Exception as e:
        print(f"[GoogleChat] DM 발송 실패 ({user_name}): {e}")
        return False
