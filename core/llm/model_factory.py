from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI


def create_model(
    config: dict[str, Any],
) -> ChatOpenAI:

    provider = config["provider"]
    model_name = config["model"]

    if provider != "openai":
        raise ValueError(
            f"Unsupported model provider: {provider}"
        )

    reasoning_effort = config.get(
        "reasoning_effort"
    )

    use_responses_api = config.get(
        "use_responses_api",
        False,
    )

    kwargs = {
        "model": model_name,
        "use_responses_api": use_responses_api,
        "request_timeout": config.get(
            "request_timeout",
            60,
        ),
        "max_retries": config.get(
            "max_retries",
            1,
        ),
    }

    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort

    if reasoning_effort in (None, "none"):
        temperature = config.get("temperature")

        if temperature is not None:
            kwargs["temperature"] = temperature

    max_tokens = config.get("max_tokens")

    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    return ChatOpenAI(**kwargs)