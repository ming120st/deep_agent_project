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
model_repository = ModelRepository()