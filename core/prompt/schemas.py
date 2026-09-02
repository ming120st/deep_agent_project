# 프롬프트 데이터 구조를 정의하는 Pydantic 모델/스키마 파일

from datetime import datetime
from pydantic import BaseModel, Field

class PromptVariable(BaseModel):
    name: str
    required: bool = True
    default: str | None = None
    description: str | None = None


class PromptConfig(BaseModel):
    key: str
    name: str
    description: str | None = None

    domain: str
    agent: str
    prompt_type: str

    version: int
    content: str

    variables: list[PromptVariable] = Field(
        default_factory=list
    )

    llm_config_key: str | None = None
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None