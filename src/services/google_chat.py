from google.oauth2 import service_account
from googleapiclient.discovery import build
import json
import os

# ──────────────────────────────────────────
# Google Chat Service 초기화
# ──────────────────────────────────────────
def _build_chat_service():
    try:
        service_account_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH"))
    except (TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_KEY_PATH 환경변수 에러: {e}")

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=[
            "https://www.googleapis.com/auth/chat.bot"
            ]
    )
    return build("chat", "v1", credentials=credentials)

chat_service = _build_chat_service()

# ──────────────────────────────────────────
# Google Chat에 메시지 보내는 함수
def send_response(space_name: str, message: str = ""):
    try:
        body = {"text": message}

        chat_service.spaces().messages().create(
            parent=space_name,
            body=body,
        ).execute()
        print(f"Successfully sent response to {space_name}")
    except Exception as e:
        print(f"Error sending response: {e}")