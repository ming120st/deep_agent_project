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
from src.core.device_job_manager import get_job_info_by_job, complete_device_job
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

    job_info = await get_job_info_by_job(payload.job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail=f"job_id not found or already processed: {payload.job_id}")

    thread_id = job_info.get("thread_id")
    is_cs_api = job_info.get("is_cs_api", False)
    agent_name = job_info.get("agent_name", "")

    await complete_device_job(
        payload.job_id,
        status="done" if payload.status == "success" else "error"
    )

    config = {"configurable": {"thread_id": thread_id}}
    lock = _get_lock(thread_id)

    async with lock:
        if is_cs_api and agent_name:
            from src.agent.registry import AGENT_REGISTRY
            from src.core.mongo_client import checkpointer
            base_graph = AGENT_REGISTRY.get(agent_name, {}).get("graph")
            if base_graph:
                graph = base_graph.builder.compile(checkpointer=checkpointer)
                await graph.ainvoke(
                    Command(resume=payload.model_dump()),
                    config=config,
                )
            else:
                print(f"[DeviceCallback] 에이전트 {agent_name}의 그래프를 찾을 수 없습니다.")
        else:
            await main_graph.ainvoke(
                Command(resume=payload.model_dump()),
                config=config,
            )

    return {"status": "resumed"}