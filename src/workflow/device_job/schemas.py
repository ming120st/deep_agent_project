"""
workflow/device_job/schemas.py

BOA ↔ 로컬 PC 간 DeviceJob 통신 스키마.

흐름:
  BOA → 로컬: DeviceJobRequest  (POST http://ngrok-url/device-job)
  로컬 → BOA: DeviceJobCallback (POST https://boa-server/device-callback)
"""
from typing import Any, Literal
from pydantic import BaseModel, Field
import uuid


class DeviceJobRequest(BaseModel):
    """BOA가 로컬 PC에 전달하는 작업 요청."""

    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str                    # user_id (graph resume 라우팅 키)
    task: str                         # 수행할 작업 이름/설명
    parameters: dict[str, Any] = {}  # 작업에 필요한 파라미터
    context: dict[str, Any] = {}     # state에서 추출한 필요 컨텍스트


class DeviceJobCallback(BaseModel):
    """로컬 PC가 완료 후 BOA에 전달하는 콜백."""

    job_id: str
    status: Literal["success", "error"]
    result: dict[str, Any] = {}           # 작업 결과
    updated_fields: dict[str, Any] = {}   # state에 머지할 필드
    error_message: str | None = None