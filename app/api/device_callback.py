from __future__ import annotations

import os

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Request,
)

from langgraph.types import Command

from integrations.device_job.device_job_manager import (
    complete_device_job,
    get_job_info_by_job,
)

from integrations.device_job.schemas import (
    DeviceJobCallback,
)


router = APIRouter()

DEVICE_JOB_SECRET = os.getenv(
    "DEVICE_JOB_SECRET",
    "",
)


def _verify_key(
    key: str,
) -> None:
    if (
        not DEVICE_JOB_SECRET
        or key != DEVICE_JOB_SECRET
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )

@router.post("/device-callback")
async def device_callback(
    callback: DeviceJobCallback,
    request: Request,
    x_device_job_key: str = Header(...),
) -> dict:

    # 1. 인증 먼저
    _verify_key(
        x_device_job_key
    )

    # 2. Job 조회
    job_info = await get_job_info_by_job(
        callback.job_id
    )

    if not job_info:
        raise HTTPException(
            status_code=404,
            detail=(
                "job_id not found or already processed: "
                f"{callback.job_id}"
            ),
        )

    # 3. callback이 실제 도착했다는 표시
    await complete_device_job(
        callback.job_id,
        status="callback_received",
    )

    thread_id = job_info.get(
        "thread_id"
    )

    if not thread_id:
        raise HTTPException(
            status_code=500,
            detail="thread_id가 없습니다.",
        )

    deep_agent = (
        request.app.state.deep_agent
    )

    try:
        result = await deep_agent.ainvoke(
            Command(
                resume=callback.model_dump()
            ),
            config={
                "configurable": {
                    "thread_id": thread_id,
                }
            },
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Deep Agent resume 실패: {e}"
            ),
        ) from e

    # 4. resume까지 성공하면 최종 상태
    await complete_device_job(
        callback.job_id,
        status=(
            "done"
            if callback.status == "success"
            else "error"
        ),
    )

    print(
        "[DeviceCallback] resume result:",
        result,
    )

    return {
        "status": "resumed",
        "job_id": callback.job_id,
    }