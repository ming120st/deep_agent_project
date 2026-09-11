from typing import Literal

from pydantic import BaseModel


class ModelConfig(BaseModel):
    key: str

    type: Literal[
        "chat",
        "embedding",
    ]

    provider: Literal[
        "openai",
        "google",
    ]

    model: str

    temperature: float | None = None
    request_timeout: int | None = None
    max_retries: int | None = None

    enabled: bool = True