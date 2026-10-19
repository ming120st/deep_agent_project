"""device_agent를 통해 인트라넷 게시글을 게시합니다.

    from integrations.intranet_publish import get_publisher, PublishError

    result = await get_publisher().publish_post(
        post,                                # IntranetPost 또는 dict: 메타데이터 + body_html (HTML 작성 에이전트가 작성)
        images=["header.png"],  # upload_intranet_image로 먼저 업로드된 이미지 파일명
        recipients=["홍길동"],                # 업무게시판(work) 전용
        thread_id=thread_id,
        notify={"space_id": space, "thread_name": thread},  # 선택 사항: Google Chat 결과 알림
    )
    result.job_id, result.status             # "pending": 콜백을 통해 최종 완료됨

디바이스 콜백 라우트는 일반 재개 로직 이전에 ``try_handle_callback``을 호출합니다.
"""
from __future__ import annotations

import logging
from typing import Any

from .post import IntranetPost
from .config import BOARDS, PublishSettings, blob_connection_string
from .service import IntranetPublisher, PublishResult, describe
from .validation import PublishError

logger = logging.getLogger(__name__)

__all__ = [
    "BOARDS",
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
    """프로세스 전역 publisher (Mongo 저장소, Azure Blob, 기존 DeviceJobClient)."""
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
    """콜백이 인트라넷 게시 작업에 해당하면 응답을 반환하고, 그렇지 않으면 None을 반환합니다."""
    payload = callback.model_dump() if hasattr(callback, "model_dump") else dict(callback)
    if not IntranetPublisher.owns_job_id(payload.get("job_id", "")):
        return None
    publisher = get_publisher()
    if not await publisher.owns_job(payload["job_id"]):
        return None
    return await publisher.handle_callback(payload)
