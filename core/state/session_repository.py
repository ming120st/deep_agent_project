from __future__ import annotations

from core.database.mongo import mongo_db


class SessionRepository:

    COLLECTION_NAME = "session_logs"

    @property
    def collection(self):
        return mongo_db[
            self.COLLECTION_NAME
        ]

    async def create(
        self,
        data: dict,
    ) -> None:
        await self.collection.insert_one(
            data
        )

    async def update(
        self,
        session_id: str,
        data: dict,
    ) -> None:
        await self.collection.update_one(
            {
                "session_id": session_id,
            },
            {
                "$set": data,
            },
            upsert=True,
        )

    async def add_message(
        self,
        session_id: str,
        message: dict,
    ) -> None:
        await self.collection.update_one(
            {
                "session_id": session_id,
            },
            {
                "$push": {
                    "messages": message,
                },
            },
            upsert=True,
        )

    async def add_event(
        self,
        session_id: str,
        event: dict,
    ) -> None:
        await self.collection.update_one(
            {
                "session_id": session_id,
            },
            {
                "$push": {
                    "events": event,
                },
            },
            upsert=True,
        )

    async def get(
        self,
        session_id: str,
    ) -> dict | None:
        return await self.collection.find_one(
            {
                "session_id": session_id,
            }
        )


session_repository = SessionRepository()