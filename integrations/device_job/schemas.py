# Device agent 에서 주고받는 데이터를 공통으로 사용하기 위해 생성
# agent 별로 고정값이 아니거나 관리가 필요하지 않다면 제외 고려

from __future__ import annotations
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field

class DeviceJobRequest(BaseModel):
    """
    Device Agent에 전달하는 공통 Job 요청 DTO.
    """

    job_id: str = Field(
        default_factory=lambda: str(uuid4())
    )

    thread_id: str

    task: str

    parameters: dict[str, Any] = Field(
        default_factory=dict
    )

    context: dict[str, Any] = Field(
        default_factory=dict
    )


class DeviceJobCallback(BaseModel):
    """
    Device Agent에서 Deep Agent로 전달되는 callback DTO.
    """

    job_id: str

    status: Literal[
        "completed",
        "error",
    ]

    result: dict[str, Any] = Field(
        default_factory=dict
    )

    error_message: str | None = None