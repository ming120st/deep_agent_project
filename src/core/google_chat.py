"""
core/google_chat.py

Google Chat API 클라이언트 및 메시지 전송 유틸리티.

[함수 목록]
  build_department_card()               ← 부서 선택 카드 dict 반환 (동기 응답용)
  send_message(space_name, message)     ← 텍스트 메시지 전송
  find_or_create_dm_space(user_name)    ← DM space 조회 또는 생성
  send_dm(user_name, message)           ← user_name으로 DM 발송

[핵심 설계 원칙]
  순수 Chat API 앱 (gcp-sa-chat) 기반.
  기본: Chat RestAPI로 비동기 메시지 전송, 부서선택 등 일부 기능은 동기 응답으로 즉시 표시
  
  카드 버튼 클릭 이벤트:
    event.type == "CARD_CLICKED"
    event.action.actionMethodName == "submitDepartment"  (action.function 값과 동일)
    event.common.formInputs["department"]["stringInputs"]["value"][0]
"""

import json
import os
import asyncio
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


# ── 텍스트 메시지 전송 ─────────────────────────────────────────────────────────
def _send_message_sync(space_name: str, message: str) -> bool:
    """동기 전송 (내부용)."""
    chat_service.spaces().messages().create(
        parent=space_name,
        body={"text": message},
    ).execute()
    return True

async def send_message(space_name: str, message: str) -> bool:
    """
    space_name으로 텍스트 메시지를 전송합니다.

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

# ── 카드 메시지 전송 ───────────────────────────────────────────────────────────

def _send_card_sync(space_name: str, card_body: dict) -> bool:
    """동기 카드 전송 (내부용)."""
    chat_service.spaces().messages().create(
        parent=space_name,
        body=card_body,
    ).execute()
    return True


async def send_card(space_name: str, card_body: dict) -> bool:
    """
    space_name으로 Card v2 메시지를 전송합니다.

    Args:
        space_name: Google Chat space 리소스명 (예: "spaces/XXXXXXXXXX")
        card_body:  cardsV2 포함한 메시지 body dict

    Returns:
        True: 성공 / False: 실패
    """
    try:
        await asyncio.to_thread(_send_card_sync, space_name, card_body)
        print(f"[GoogleChat] 카드 전송 완료 → {space_name}")
        return True
    except Exception as e:
        print(f"[GoogleChat] 카드 전송 실패 ({space_name}): {e}")
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
        user_name: Google Chat 유저 리소스명 (예: "users/hong@widemobile.com")

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


