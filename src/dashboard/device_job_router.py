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

router = APIRouter()

_SECRET = os.getenv("DEVICE_JOB_SECRET", "")


def _verify_key(key: str) -> None:
    if not _SECRET or key != _SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/device-callback")
async def device_callback(
    payload: DeviceJobCallback,
    x_device_job_key: str = Header(...),
):
    """로컬 PC 작업 완료 콜백. 대기 중인 그래프를 재개합니다."""
    _verify_key(x_device_job_key)

    thread_id = await get_thread_id_by_job(payload.job_id)
    if not thread_id:
        raise HTTPException(status_code=404, detail=f"job_id not found or expired: {payload.job_id}")

    config = {"configurable": {"thread_id": thread_id}}

    # job 먼저 완료 처리 → 중복 콜백 재시도가 들어와도 get_thread_id_by_job에서 차단됨
    await complete_device_job(
        payload.job_id,
        status="done" if payload.status == "success" else "error"
    )

    # 그래프 재개 — interrupt()가 멈춘 지점부터 재시작
    await main_graph.ainvoke(
        Command(resume=payload.model_dump()),
        config=config,
    )

    print(f"[DeviceCallback] 그래프 재개 완료: job={payload.job_id}, thread={thread_id}")
    return {"status": "resumed"}