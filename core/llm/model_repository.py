from __future__ import annotations

from typing import Any

from core.database.mongo import mongo_db


class ModelRepository:

    async def get_model_config(
        self,
        model: str,
    ) -> dict | None:
        return await mongo_db[
            "llm_models"
        ].find_one(
            {
                "key": model,
                "enabled": True,
            },
            {
                "_id": 0,
            },
        )
    async def get_default_chat_model_key(
        self,
    ) -> str | None:
        config = await mongo_db[
            "llm_models"
        ].find_one(
            {
                "type": "chat",
                "enabled": True,
                "is_default": True,
            },
            {
                "_id": 0,
                "key": 1,
            },
        )

        if config is None:
            return None

        return config["key"]
model_repository = ModelRepository()