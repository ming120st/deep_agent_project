"""
core/google_chat.py

Google Chat API 클라이언트 및 메시지 전송 유틸리티.

[함수 목록]
  send_message(space_name, message)           ← 텍스트 메시지 전송
  send_department_card(space_name)            ← 부서 선택 카드 전송 (Chat REST API 비동기)
  find_or_create_dm_space(user_name)          ← DM space 조회 또는 생성
  send_dm(user_name, message)                 ← user_name으로 DM 발송

[핵심 설계 원칙]
  이 앱은 Google Workspace Events(Pub/Sub) 방식으로 이벤트를 수신합니다.
  Google Chat은 웹훅에 대해 HTTP 200 응답만 기대하며, return {"cardsV2": ...} 형태의
  동기 응답은 메시지로 처리되지 않습니다.
  따라서 카드 포함 모든 메시지는 Chat REST API를 통해 비동기로 전송해야 합니다.
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


# ── 부서 목록 ──────────────────────────────────────────────────────────────────
DEPARTMENT_LIST = [
    "영업/제휴팀",
    "도시락모바일팀",
    "브랜드마케팅팀",
    "크리에이티브전략팀",
    "디자인팀",
    "AI팀",
    "제품기획팀",
    "서울기술지원팀",
    "서울고객지원팀",
    "청구관리팀",
    "미래콜센터",
    "연구개발실",
    "인천공항로밍센터",
    "김포공항로밍센터",
    "인천기술지원팀",
    "공항기술지원팀",
    "인천고객지원팀",
    "문래콜센터",
    "부산사무실",
    "김해공항로밍센터",
    "대구공항로밍센터",
    "청주공항로밍센터",
    "경영지원팀",
    "로보와이드",
]


def _build_department_card_body() -> dict:
    """부서 선택 카드 body 생성 (Chat REST API 전송용)."""
    department_items = [
        {"text": dept, "value": dept, "selected": False}
        for dept in DEPARTMENT_LIST
    ]
    return {
        "cardsV2": [{
            "cardId": "department-selection-card",
            "card": {
                "header": {
                    "title": "구성원 등록을 도와드릴게요! 현재 부서를 선택해주세요."
                },
                "sections": [{
                    "widgets": [
                        {
                            "selectionInput": {
                                "name": "department",
                                "label": "소속 부서",
                                "type": "RADIO_BUTTON",
                                "items": department_items,
                            }
                        },
                        {
                            "buttonList": {
                                "buttons": [{
                                    "text": "등록",
                                    "onClick": {
                                        "action": {
                                            "function": "submitDepartment"
                                        }
                                    }
                                }]
                            }
                        }
                    ]
                }]
            }
        }]
    }


def _send_card_sync(space_name: str, card_body: dict) -> bool:
    """카드 전송 동기 함수 (내부용)."""
    chat_service.spaces().messages().create(
        parent=space_name,
        body=card_body,
    ).execute()
    return True


async def send_department_card(space_name: str) -> bool:
    """
    부서 선택 카드를 Chat REST API로 발송합니다.

    이 앱은 Pub/Sub 방식으로 이벤트를 수신하므로 HTTP 응답으로 카드를 반환해도
    Google Chat이 메시지로 처리하지 않습니다. 반드시 이 함수처럼 Chat REST API를
    통해 직접 전송해야 합니다.

    Args:
        space_name: Google Chat DM space 리소스명 (예: "spaces/XXXXXXXXXX")

    Returns:
        True: 성공 / False: 실패
    """
    try:
        card_body = _build_department_card_body()
        await asyncio.to_thread(_send_card_sync, space_name, card_body)
        print(f"[GoogleChat] 부서 선택 카드 발송 → {space_name}")
        return True
    except Exception as e:
        print(f"[GoogleChat] 카드 발송 실패 ({space_name}): {e}")
        return False


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
