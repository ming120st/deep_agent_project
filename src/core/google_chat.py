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
import copy

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


def _build_message_body(
    *,
    text: str | None = None,
    card_body: dict | None = None,
    thread_name: str | None = None,
    quoted_message_name: str | None = None,
    quoted_message_last_update_time: str | None = None,
) -> dict:
    """
    Google Chat messages.create body 생성.

    지원:
      - 일반 텍스트 메시지
      - 카드 메시지
      - 특정 thread에 답장
      - 원문 메시지 인용 답장
    """
    if card_body is not None:
        body = copy.deepcopy(card_body)
    else:
        body = {
            "text": text or ""
        }

    # 같은 스레드에 답장
    if thread_name:
        body["thread"] = {
            "name": thread_name
        }

    # 원문 메시지 인용
    if quoted_message_name and quoted_message_last_update_time:
        body["quotedMessageMetadata"] = {
            "name": quoted_message_name,
            "lastUpdateTime": quoted_message_last_update_time,
        }

    return body


def _create_message_sync(space_name: str, body: dict) -> dict:
    """
    Google Chat 메시지 동기 생성.
    googleapiclient는 동기 execute() 기반이라 asyncio.to_thread에서 호출한다.
    """
    return chat_service.spaces().messages().create(
        parent=space_name,
        body=body,
    ).execute()


async def send_message(
    space_name: str,
    message: str,
    *,
    thread_name: str | None = None,
    quoted_message_name: str | None = None,
    quoted_message_last_update_time: str | None = None,
) -> bool:
    """
    Google Chat 메시지를 전송한다.
    성공하면 True, 최종 실패하면 False를 반환한다.

    Args:
        space_name: Google Chat space 리소스명
        message: 전송할 텍스트
        thread_name: 특정 스레드에 답장할 때 사용
        quoted_message_name: 인용할 원문 메시지 name
        quoted_message_last_update_time: 인용할 원문 메시지 lastUpdateTime 또는 createTime
    """
    max_attempts = 3

    body = _build_message_body(
        text=message,
        thread_name=thread_name,
        quoted_message_name=quoted_message_name,
        quoted_message_last_update_time=quoted_message_last_update_time,
    )

    for attempt in range(1, max_attempts + 1):
        try:
            await asyncio.to_thread(
                _create_message_sync,
                space_name,
                body,
            )

            print(
                f"[GoogleChat] 전송 완료 → {space_name} | "
                f"attempt={attempt} | "
                f"thread={thread_name} | "
                f"quoted={quoted_message_name}"
            )
            return True

        except Exception as e:
            print(
                f"[GoogleChat] 전송 실패 → {space_name} | "
                f"attempt={attempt}/{max_attempts} | "
                f"thread={thread_name} | "
                f"quoted={quoted_message_name} | "
                f"{type(e).__name__}: {e}"
            )

            if attempt < max_attempts:
                await asyncio.sleep(0.5 * attempt)

    print(f"[GoogleChat] 최종 전송 실패 → {space_name}")
    return False


# ── 카드 메시지 전송 ───────────────────────────────────────────────────────────


async def send_card(
    space_name: str,
    card_body: dict,
    *,
    thread_name: str | None = None,
    quoted_message_name: str | None = None,
    quoted_message_last_update_time: str | None = None,
) -> bool:
    """
    Google Chat Card v2 메시지를 전송한다.

    Args:
        space_name: Google Chat space 리소스명
        card_body: cardsV2 포함 메시지 body
        thread_name: 특정 스레드에 답장할 때 사용
        quoted_message_name: 인용할 원문 메시지 name
        quoted_message_last_update_time: 인용할 원문 메시지 lastUpdateTime 또는 createTime

    Returns:
        True: 성공 / False: 최종 실패
    """
    max_attempts = 3

    body = _build_message_body(
        card_body=card_body,
        thread_name=thread_name,
        quoted_message_name=quoted_message_name,
        quoted_message_last_update_time=quoted_message_last_update_time,
    )

    for attempt in range(1, max_attempts + 1):
        try:
            await asyncio.to_thread(
                _create_message_sync,
                space_name,
                body,
            )

            print(
                f"[GoogleChat] 카드 전송 완료 → {space_name} | "
                f"attempt={attempt} | "
                f"thread={thread_name} | "
                f"quoted={quoted_message_name}"
            )
            return True

        except Exception as e:
            print(
                f"[GoogleChat] 카드 전송 실패 → {space_name} | "
                f"attempt={attempt}/{max_attempts} | "
                f"thread={thread_name} | "
                f"quoted={quoted_message_name} | "
                f"{type(e).__name__}: {e}"
            )

            if attempt < max_attempts:
                await asyncio.sleep(0.5 * attempt)

    print(f"[GoogleChat] 카드 최종 전송 실패 → {space_name}")
    return False


# ── DM 유틸리티 ───────────────────────────────────────────────────────────────
def _find_or_create_dm_space_sync(user_name: str) -> str:
    """동기 DM space 조회/생성."""
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
            body={"member": {"name": user_name, "type": "HUMAN"}},
        ).execute()
        return space["name"]


async def find_or_create_dm_space(user_name: str) -> str:
    """
    사용자와의 DM space를 조회하거나 없으면 생성합니다.

    Args:
        user_name: Google Chat 유저 리소스명 (예: "users/hong@widemobile.com")
    """
    return await asyncio.to_thread(_find_or_create_dm_space_sync, user_name)


async def send_dm(user_name: str, message: str) -> bool:
    """
    user_name으로 DM을 발송합니다.
    space_name을 모를 때 사용합니다. space_name을 알면 send_message를 사용합니다.
    """
    try:
        space_name = await find_or_create_dm_space(user_name)
        return await send_message(space_name, message)
    except Exception as e:
        print(f"[GoogleChat] DM 발송 실패 ({user_name}): {e}")
        return False
