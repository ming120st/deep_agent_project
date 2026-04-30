import logging
import os

from src.core.google_chat import send_dm
from src.core.mongo_client import boa_members_collection

logger = logging.getLogger(__name__)

DASHBOARD_BASE_URL = os.getenv("BOA_DASHBOARD_URL", "https://boa.widemobile.com")


async def _lookup_owner_chat_user(owner_member_id: str | None) -> str | None:
    if not owner_member_id:
        return None

    member = await boa_members_collection.find_one(
        {"email": owner_member_id},
        {"space_name": 1},
    )
    if not member:
        logger.warning("[KBNotifier] owner not found: %s", owner_member_id)
        return None

    space_name = member.get("space_name")
    if not space_name:
        logger.warning("[KBNotifier] owner space_name missing: %s", owner_member_id)
        return None

    return space_name


async def notify_conflict(
    topic_id: str,
    topic_title: str,
    conflict_id: str,
    owner_member_id: str | None,
    confidence: float,
) -> None:
    """
    Send a Google Chat DM to the topic owner when a conflict is detected.
    Only a dashboard link is shared; failures are logged and swallowed.
    """
    user_name = await _lookup_owner_chat_user(owner_member_id)
    if not user_name:
        return

    message = (
        "[BOA] 지식베이스 확인 요청\n\n"
        f"주제: {topic_title}\n"
        "사유: 기존 내용과 다른 설명이 자동 인입됨\n"
        f"자동 판단 신뢰도: {confidence:.0%}\n\n"
        f"대시보드에서 확인 → {DASHBOARD_BASE_URL}/knowledge#conflicts"
    )

    try:
        ok = await send_dm(user_name, message)
        if not ok:
            logger.warning(
                "[KBNotifier] conflict DM send failed topic=%s conflict=%s",
                topic_id,
                conflict_id,
            )
    except Exception as exc:
        logger.warning(
            "[KBNotifier] conflict DM exception topic=%s conflict=%s error=%s",
            topic_id,
            conflict_id,
            exc,
        )


async def notify_new_topic(
    topic_id: str,
    topic_title: str,
    owner_member_id: str | None,
) -> None:
    """
    Send a Google Chat DM when a new topic is auto-created.
    P4 uses immediate delivery; batching can be added later.
    """
    user_name = await _lookup_owner_chat_user(owner_member_id)
    if not user_name:
        return

    message = (
        "[BOA] 새 지식 주제가 생성되었습니다\n\n"
        f"주제: {topic_title}\n"
        "자동 인입 결과로 새 토픽이 초안 상태로 생성되었습니다.\n\n"
        f"대시보드에서 확인 → {DASHBOARD_BASE_URL}/knowledge"
    )

    try:
        ok = await send_dm(user_name, message)
        if not ok:
            logger.warning("[KBNotifier] new topic DM send failed topic=%s", topic_id)
    except Exception as exc:
        logger.warning(
            "[KBNotifier] new topic DM exception topic=%s error=%s",
            topic_id,
            exc,
        )
