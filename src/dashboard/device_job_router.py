"""
dashboard/device_job_router.py

로컬 PC → BOA 콜백 수신 엔드포인트.

로컬 PC가 작업 완료 후 POST /device-callback 으로 결과를 전달하면
MongoDB checkpoint를 통해 대기 중인 LangGraph를 Command(resume=...) 로 재개합니다.

[인증]
  Header: X-Device-Job-Key: {DEVICE_JOB_SECRET}
  환경변수: DEVICE_JOB_SECRET
"""
import os

from fastapi import APIRouter, Header, HTTPException
from langgraph.types import Command

from src.workflow.device_job.schemas import DeviceJobCallback
from src.core.device_job_manager import get_thread_id_by_job, complete_device_job
from src.workflow.main_graph.graph import main_graph
from src.orchestrator import _get_lock
from fastapi import Body

router = APIRouter()

_SECRET = os.getenv("DEVICE_JOB_SECRET", "")


def _verify_key(key: str) -> None:
    if not _SECRET or key != _SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/device-callback")
async def device_callback(
    payload: DeviceJobCallback = Body(...),
    x_device_job_key: str = Header(...)
    ):
    _verify_key(x_device_job_key)

    thread_id = await get_thread_id_by_job(payload.job_id)
    if not thread_id:
        raise HTTPException(status_code=404, detail=f"job_id not found: {payload.job_id}")

    await complete_device_job(
        payload.job_id,
        status="done" if payload.status == "success" else "error"
    )

    config = {"configurable": {"thread_id": thread_id}}
    lock = _get_lock(thread_id)  # thread_id == user_id 이므로 동일 lock

    async with lock:
        await main_graph.ainvoke(
            Command(resume=payload.model_dump()),
            config=config,
        )

    return {"status": "resumed"}