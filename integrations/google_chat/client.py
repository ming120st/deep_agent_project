from __future__ import annotations
import asyncio
from asyncio.log import logger
from pathlib import Path

from googleapiclient.http import (
    MediaFileUpload,
)
from integrations.google_chat.service import (
    chat_service,
)


def _create_message_sync(
    space_name: str,
    body: dict,
) -> None:
    (
        chat_service
        .spaces()
        .messages()
        .create(
            parent=space_name,
            body=body,
        )
        .execute()
    )

def _upload_file_sync(
    space_name: str,
    file_path: str,
) -> dict:
    path = Path(
        file_path
    )

    media = MediaFileUpload(
        str(path),
        mimetype=(
            "application/"
            "vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        resumable=False,
    )

    return (
        chat_service
        .media()
        .upload(
            parent=space_name,
            body={
                "filename": path.name,
            },
            media_body=media,
        )
        .execute()
    )


async def send_message(
    space_name: str,
    message: str,
    *,
    thread_name: str | None = None,
) -> bool:
    body = {
        "text": message,
    }

    if thread_name:
        body["thread"] = {
            "name": thread_name,
        }

    for attempt in range(3):
        try:
            await asyncio.to_thread(
                _create_message_sync,
                space_name,
                body,
            )

            return True

        except Exception:
            if attempt < 2:
                await asyncio.sleep(
                    0.5 * (attempt + 1)
                )

    return False

async def send_file(
    space_name: str,
    file_path: str,
    *,
    message: str | None = None,
    thread_name: str | None = None,
) -> bool:
    try:
        attachment = await asyncio.to_thread(
            _upload_file_sync,
            space_name,
            file_path,
        )

        body = {
            "attachment": [
                attachment,
            ],
        }

        if message:
            body["text"] = message

        if thread_name:
            body["thread"] = {
                "name": thread_name,
            }

        await asyncio.to_thread(
            _create_message_sync,
            space_name,
            body,
        )

        return True

    except Exception:
        logger.exception(
            "[GOOGLE_CHAT_FILE_SEND_ERROR] "
            "space=%s file=%s",
            space_name,
            file_path,
        )
        return False