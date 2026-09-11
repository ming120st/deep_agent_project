from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI


def create_model(
    config: dict[str, Any],
):
    provider = config["provider"]
    model_name = config["model"]

    if provider == "openai":
        return ChatOpenAI(
            model=model_name,
            temperature=config.get(
                "temperature",
                0,
            ),
            max_tokens=config.get(
                "max_tokens",
            ),
        )

    raise ValueError(
        f"Unsupported model provider: {provider}"
    )