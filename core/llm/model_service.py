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


model_service = ModelService()