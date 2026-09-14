from __future__ import annotations

from core.llm.model_factory import (
    create_model,
)
from core.llm.model_repository import (
    model_repository,
)


class ModelService:
    async def get_model(
        self,
        model: str,
    ):
        config = await (
            model_repository
            .get_model_config(
                model
            )
        )

        if config is None:
            raise ValueError(
                "Model config not found: "
                f"{model}"
            )

        return create_model(
            config
        )
    
    async def get_default_model_key(
        self,
    ) -> str:
        model_key = await (
            model_repository
            .get_default_chat_model_key()
        )

        if model_key is None:
            raise ValueError(
                "Default chat model not found."
            )

        return model_key


model_service = ModelService()