from __future__ import annotations

import os
from typing import Annotated

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import interrupt

from integrations.device_job.device_job_manager import (
    create_device_job,
    get_job_id_by_task_id,
)

from integrations.device_job.schemas import (
    DeviceJobCallback,
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
def _cfg(
    config: RunnableConfig,
    key: str,
    default="",
):
    return (
        config.get("configurable")
        or {}
    ).get(
        key,
        default,
    )
@tool
async def submit_wifi_line_change(
    device_number: str,
    country_code: Annotated[
        str,
        (
            "국가명을 ISO 3166-1 alpha-2 형식의 "
            "영문 대문자 2자리 코드로 변환해서 전달. "
            "예: 한국/대한민국/Korea -> KR, "
            "일본/Japan -> JP, 미국/USA -> US"
        ),
    ],
    expiry_date: str,
    config: RunnableConfig,
) -> dict:
    """
    WiFi 단말기의 회선을 변경한다.

    사용자가 실제 회선 변경을 요청한 경우 사용한다.
    device_number, country_code, expiry_date가 모두 확보되면, 바로 이 Tool 을 호출한다.
    """

    task_id = _cfg(
        config,
        "task_id",
    )

    thread_id = _cfg(
        config,
        "thread_id",
    )

    if not task_id:
        return {
            "success": False,
            "reason": "task_id가 없습니다.",
        }

    if not thread_id:
        return {
            "success": False,
            "reason": "thread_id가 없습니다.",
        }

    job_id = await get_job_id_by_task_id(
        task_id
    )

    if not job_id:
        request = DeviceJobRequest(
            thread_id=thread_id,
            task="wifi_sim_change",
            parameters={
                "imei": device_number,
                "country_code": country_code,
                "expiry_date": expiry_date,
            },
            context={},
        )

        job_id = request.job_id

        await create_device_job(
            job_id,
            thread_id,
            task_id=task_id,
        )

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{DEVICE_JOB_URL}/device-job",
                    json=request.model_dump(),
                    headers={
                        "X-Device-Job-Key":
                            DEVICE_JOB_SECRET,
                    },
                    timeout=10.0,
                )
                print(
                    "[WiFiLineChange] device-job response:",
                    response.status_code,
                    response.text,
                )
                response.raise_for_status()

                print(
                    "[WiFiLineChange] DeviceJob 전송 성공",
                    response.status_code,
                    response.text,
                )

        except Exception as e:
            # 여기서 job 상태를 FAILED 등으로
            # 업데이트하는 게 가장 좋음.
            return {
                "success": False,
                "job_id": job_id,
                "reason": (
                    "Device Agent 작업 요청 실패: "
                    f"{e}"
                ),
            }

    callback_payload: dict = interrupt(
        {
            "job_id": job_id,
            "status": "waiting",
            "message":
                "WiFi 회선 변경 작업 진행 중",
        }
    )
    print(
        "[WiFiLineChange] interrupt resume:",
        callback_payload,
    )

    callback = DeviceJobCallback(
        **callback_payload
    )

    if callback.status == "error":
        return {
            "success": False,
            "job_id": job_id,
            "reason": (
                callback.error_message
                or "UNKNOWN"
            ),
        }

    result = callback.result or {}

    if result.get("success") is False:
        return {
            "success": False,
            "job_id": job_id,
            "reason": result.get(
                "reason",
                "UNKNOWN",
            ),
        }

    return {
        "success": True,
        "job_id": job_id,
        **result,
    }