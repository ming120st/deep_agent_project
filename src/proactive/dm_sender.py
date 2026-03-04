"""
Google Chat DM 발송 (dm_sender.py)

기존 google_chat.py는 특정 space에 메시지를 보내는 방식.
DM은 먼저 1:1 space를 생성(또는 조회)한 후 메시지를 보내야 합니다.

Google Chat API:
  - spaces.create (DM space 생성)  또는
  - spaces.findDirectMessage (기존 DM space 조회)
  → messages.create (메시지 전송)
"""
from src.services.google_chat import chat_service


def find_or_create_dm_space(user_name: str) -> str:
    """
    사용자와의 DM space를 조회하거나 생성합니다.

    Args:
        user_name: Google Chat 유저 리소스명
                   예: "users/12345678901234567"
                       "users/hong@widemobile.com"  (이메일 형식도 가능)

    Returns:
        space_name: 예 "spaces/XXXXXXXXXX"
    """
    try:
        # 기존 DM space 조회 시도
        space = chat_service.spaces().findDirectMessage(
            name=user_name
        ).execute()
        return space["name"]

    except Exception:
        # 없으면 새 DM space 생성
        space = chat_service.spaces().create(
            body={
                "spaceType": "DIRECT_MESSAGE",
                "singleUserBotDm": False,
            },
            requestId=f"dm-{user_name.replace('/', '-')}",
        ).execute()

        # DM space에 멤버 추가
        chat_service.spaces().members().create(
            parent=space["name"],
            body={"member": {"name": user_name, "type": "HUMAN"}}
        ).execute()

        return space["name"]


def send_dm(user_name: str, message: str) -> bool:
    """
    특정 사용자에게 Google Chat DM을 발송합니다.

    Args:
        user_name: Google Chat 유저 리소스명
                   예: "users/12345678901234567"
        message:   전송할 텍스트 메시지

    Returns:
        True: 성공 / False: 실패
    """
    try:
        space_name = find_or_create_dm_space(user_name)

        chat_service.spaces().messages().create(
            parent=space_name,
            body={"text": message}
        ).execute()

        print(f"[DmSender] DM 발송 완료 → {user_name}")
        return True

    except Exception as e:
        print(f"[DmSender] DM 발송 실패 ({user_name}): {e}")
        return False


def send_dm_to_space(space_name: str, message: str) -> bool:
    """
    이미 알고 있는 space_name으로 바로 메시지를 발송합니다.
    (수신자 DB에 space_name을 저장해둔 경우 사용)

    Args:
        space_name: 예 "spaces/XXXXXXXXXX"
        message:    전송할 텍스트 메시지
    """
    try:
        chat_service.spaces().messages().create(
            parent=space_name,
            body={"text": message}
        ).execute()
        print(f"[DmSender] 발송 완료 → {space_name}")
        return True

    except Exception as e:
        print(f"[DmSender] 발송 실패 ({space_name}): {e}")
        return False
