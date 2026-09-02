from __future__ import annotations

import os
import httpx
from integrations.device_job.schemas import (
    DeviceJobRequest,
)
DEVICE_JOB_URL = os.getenv(
    "DEVICE_JOB_URL",
    "",
)

DEVICE_JOB_SECRET = os.getenv(
    "DEVICE_JOB_SECRET",
    "",
)


_http_client: httpx.AsyncClient | None = None


async def init_device_job_client() -> None:
    global _http_client

    if _http_client is None:
        _http_client = httpx.AsyncClient()


async def close_device_job_client() -> None:
    global _http_client

    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _get_http_client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError(
            "Device Job HTTP Client가 초기화되지 않았습니다."
        )

    return _http_client


class DeviceJobClient:

    def __init__(
        self,
        base_url: str | None = None,
        secret: str | None = None,
    ):
        self.base_url = (
            base_url
            if base_url is not None
            else DEVICE_JOB_URL
        )

        self.secret = (
            secret
            if secret is not None
            else DEVICE_JOB_SECRET
        )

    async def submit(
        self,
        request: DeviceJobRequest,
    ) -> None:

        if not self.base_url:
            raise RuntimeError(
                "DEVICE_JOB_URL이 설정되지 않았습니다."
            )

        headers = {
            "Content-Type": "application/json",
            "X-Device-Job-Key": self.secret,
        }

        client = _get_http_client()

        response = await client.post(
            f"{self.base_url.rstrip('/')}/device-job",
            json=request.model_dump(),
            headers=headers,
            timeout=10.0,
        )

        response.raise_for_status()