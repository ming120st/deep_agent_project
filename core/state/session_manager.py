
from __future__ import annotations

from uuid import uuid4

from core.database.redis import (
    get_redis_client,
)

SESSION_TTL_SECONDS = 3600


class SessionManager:

    @staticmethod
    def _session_key(
        user_id: str,
        space_id: str,
    ) -> str:
        return (
            f"boa:session:"
            f"{space_id}:{user_id}"
        )

    async def get_or_create(
        self,
        user_id: str,
        space_id: str,
    ) -> str:
        client = await get_redis_client()

        key = self._session_key(
            user_id=user_id,
            space_id=space_id,
        )

        # 기존 세션 조회
        thread_id = await client.get(key)

        if thread_id is not None:
            if isinstance(thread_id, bytes):
                thread_id = thread_id.decode("utf-8")

            # 마지막 활동 기준 TTL 갱신
            await client.expire(
                key,
                SESSION_TTL_SECONDS,
            )

            return thread_id

        # 신규 세션 생성
        new_thread_id = str(uuid4())

        # 동시 요청에서 서로 다른 ID가
        # 덮어쓰이지 않도록 NX 사용
        created = await client.set(
            key,
            new_thread_id,
            ex=SESSION_TTL_SECONDS,
            nx=True,
        )

        if created:
            return new_thread_id

        # 다른 요청이 먼저 생성한 경우
        thread_id = await client.get(key)

        if thread_id is None:
            raise RuntimeError(
                "Session creation race detected."
            )

        if isinstance(thread_id, bytes):
            thread_id = thread_id.decode("utf-8")

        await client.expire(
            key,
            SESSION_TTL_SECONDS,
        )

        return thread_id


session_manager = SessionManager()
