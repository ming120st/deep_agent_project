"""Publish intranet posts through the device_agent.

    from integrations.intranet_publish import get_publisher, PublishError

    result = await get_publisher().publish_post(
        post,                                # IntranetPost or dict: metadata + body_html (written by the HTML agent)
        images=[{"key": "header", "filename": "header.png"}],  # uploaded first with upload_intranet_image
        recipients=["홍길동"],                # work board only
        thread_id=thread_id,
        notify={"space_id": space, "thread_name": thread},  # optional Google Chat notice of the outcome
    )
    result.job_id, result.status             # "pending": the callback will finish it

The device callback route calls ``try_handle_callback`` before the generic resume logic.
"""
from __future__ import annotations

import logging
from typing import Any

from .post import ImageSpec, IntranetPost
from .config import BOARDS, PublishSettings, blob_connection_string
from .service import IntranetPublisher, PublishResult, describe
from .validation import PublishError

logger = logging.getLogger(__name__)

__all__ = [
    "BOARDS",
    "ImageSpec",
    "IntranetPost",
    "IntranetPublisher",
    "PublishError",
    "PublishResult",
    "PublishSettings",
    "get_publisher",
    "try_handle_callback",
]

_publisher: IntranetPublisher | None = None


async def google_chat_notifier(record: dict[str, Any]) -> None:
    notify = record.get("notify") or {}
    space = notify.get("space_id")
    if not space:
        return
    from integrations.google_chat.message_sender import send_message

    await send_message(space_name=space, message=describe(record), thread_name=notify.get("thread_name"))


def get_publisher() -> IntranetPublisher:
    """Process-wide publisher (Mongo store, Azure Blob, existing DeviceJobClient)."""
    global _publisher
    if _publisher is None:
        from integrations.device_job.client import DeviceJobClient

        from .blob import AzureImageBlobStore
        from .store import MongoPublishStore

        settings = PublishSettings.from_env()
        _publisher = IntranetPublisher(
            store=MongoPublishStore(),
            blob=lambda: AzureImageBlobStore(blob_connection_string(), settings.blob_container),
            device=DeviceJobClient(),
            settings=settings,
            notifier=google_chat_notifier,
        )
    return _publisher


async def try_handle_callback(callback: Any) -> dict[str, Any] | None:
    """Return a response if the callback belongs to an intranet publish job, else None."""
    payload = callback.model_dump() if hasattr(callback, "model_dump") else dict(callback)
    if not IntranetPublisher.owns_job_id(payload.get("job_id", "")):
        return None
    publisher = get_publisher()
    if not await publisher.owns_job(payload["job_id"]):
        return None
    return await publisher.handle_callback(payload)
