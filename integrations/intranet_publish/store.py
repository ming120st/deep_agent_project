"""Where publish records live. One record per job_id (= one post on one board).

Record fields: job_id, thread_id, post_key, series, period, board, title, body_html,
images[{key, filename, blob_path, content_type}], recipients, context, notify,
status, attempt, reason, result, created_at, updated_at.

status: pending (sent, waiting for the callback) | retry_wait | succeeded | failed | needs_check
Blob SAS URLs are never stored (only blob paths); they are minted fresh for every send.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Protocol

COLLECTION = "boa_intranet_posts"


class PublishStore(Protocol):
    async def get(self, job_id: str) -> dict[str, Any] | None: ...

    async def save(self, record: dict[str, Any]) -> None: ...

    async def transition(
        self, job_id: str, statuses: list[str], update: dict[str, Any], *, reason: str | None = None
    ) -> dict[str, Any] | None:
        """Atomically apply ``update`` only if the record's status is in ``statuses``
        (and, when given, its reason equals ``reason``). Returns the updated record or None."""
        ...

    async def stale(self, statuses: list[str], before: datetime) -> list[dict[str, Any]]: ...


class MemoryPublishStore:
    """For tests and local dry runs. No awaits inside the methods, so each call is atomic."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    async def get(self, job_id: str) -> dict[str, Any] | None:
        record = self._records.get(job_id)
        return copy.deepcopy(record) if record else None

    async def save(self, record: dict[str, Any]) -> None:
        self._records[record["job_id"]] = copy.deepcopy(record)

    async def transition(self, job_id, statuses, update, *, reason=None):
        record = self._records.get(job_id)
        if record is None or record.get("status") not in statuses:
            return None
        if reason is not None and record.get("reason") != reason:
            return None
        record.update(copy.deepcopy(update))
        return copy.deepcopy(record)

    async def stale(self, statuses, before):
        return [copy.deepcopy(r) for r in self._records.values() if r.get("status") in statuses and r["updated_at"] < before]


class MongoPublishStore:
    def __init__(self, collection=None) -> None:
        if collection is None:
            from core.database.mongo import mongo_db  # imported lazily: needs MONGODB_URI

            collection = mongo_db[COLLECTION]
        self._col = collection
        self._indexed = False

    async def _ensure_index(self) -> None:
        if not self._indexed:
            await self._col.create_index("job_id", unique=True)
            await self._col.create_index([("status", 1), ("updated_at", 1)])
            self._indexed = True

    async def get(self, job_id):
        return await self._col.find_one({"job_id": job_id}, {"_id": 0})

    async def save(self, record):
        await self._ensure_index()
        await self._col.replace_one({"job_id": record["job_id"]}, record, upsert=True)

    async def transition(self, job_id, statuses, update, *, reason=None):
        from pymongo import ReturnDocument

        query: dict[str, Any] = {"job_id": job_id, "status": {"$in": statuses}}
        if reason is not None:
            query["reason"] = reason
        return await self._col.find_one_and_update(
            query, {"$set": update}, projection={"_id": 0}, return_document=ReturnDocument.AFTER
        )

    async def stale(self, statuses, before):
        cursor = self._col.find({"status": {"$in": statuses}, "updated_at": {"$lt": before}}, {"_id": 0})
        return [doc async for doc in cursor]
