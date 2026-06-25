import os

from fastapi import APIRouter, Header, HTTPException, Body
from langgraph.types import Command
from pymongo import MongoClient
from langgraph.checkpoint.mongodb import MongoDBSaver

from src.workflow.device_job.schemas import DeviceJobCallback
from src.core.device_job_manager import get_job_info_by_job, complete_device_job
from src.workflow.main_graph.graph import main_graph
from src.orchestrator import _get_lock
# 💡 MongoDB URI 및 공통 옵션 임포트 (기존 mongo_client 위치에 맞게 경로를 맞춰주세요)
from src.core.mongo_client import MONGODB_URI, MONGODB_DB_NAME, _MONGO_OPTS 

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
        # 💡 [핵심 변경] 이 요청을 처리하는 동안에만 살아있을 싱글 세션 클라이언트를 엽니다.
        # Broken Pipe 에러를 완벽하게 차단합니다.
        with MongoClient(MONGODB_URI, **_MONGO_OPTS) as request_sync_client:
            request_checkpointer = MongoDBSaver(
                request_sync_client, 
                db_name=MONGODB_DB_NAME, 
                ttl=60 * 60 * 24 * 1
            )

            if is_cs_api and agent_name:
                from src.agent.registry import AGENT_REGISTRY
                base_graph = AGENT_REGISTRY.get(agent_name, {}).get("graph")
                if base_graph:
                    # 새로 만든 깨끗한 checkpointer로 동적 컴파일
                    graph = base_graph.builder.compile(checkpointer=request_checkpointer)
                    await graph.ainvoke(
                        Command(resume=payload.model_dump()),
                        config=config,
                    )
                else:
                    print(f"[DeviceCallback] 에이전트 {agent_name}의 그래프를 찾을 수 없습니다.")
            else:
                # 💡 main_graph가 이미 전역 checkpointer로 컴파일되어 있다면, 
                # config를 통해 이 요청 전용 checkpointer를 덮어씌워 줍니다(Overriding).
                config["configurable"]["checkpointer"] = request_checkpointer
                
                await main_graph.ainvoke(
                    Command(resume=payload.model_dump()),
                    config=config,
                )

    return {"status": "resumed"}