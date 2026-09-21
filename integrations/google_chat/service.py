from __future__ import annotations

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build


def _build_chat_service():
    try:
        service_account_info = json.loads(
            os.getenv(
                "GOOGLE_SERVICE_ACCOUNT_KEY_PATH"
            )
        )
    except (
        TypeError,
        json.JSONDecodeError,
    ) as e:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_KEY_PATH "
            f"환경변수 에러: {e}"
        )

    credentials = (
        service_account
        .Credentials
        .from_service_account_info(
            service_account_info,
            scopes=[
                "https://www.googleapis.com/auth/chat.bot"
            ],
        )
    )

    return build(
        "chat",
        "v1",
        credentials=credentials,
    )


chat_service = _build_chat_service()