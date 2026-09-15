"""
core/device_job_manager.py

DeviceJob MongoDB CRUD.

[boa_device_jobs 문서 구조]
{
  "job_id":     "uuid",
  "thread_id":  "user@widemobile.com",   # graph resume 라우팅 키
  "task_id":    "ObjectId str",          # LangGraph 체크포인트 누락 대비 복구용 키
  "status":     "pending|done|error|expired",
  "created_at": datetime,
  "expires_at": datetime,                # TTL 인덱스용 (기본 30분)
}
"""
from datetime import datetime, timedelta

from core.database.mongo import mongo_db

_JOB_TTL_MINUTES = 30

boa_device_jobs_collection = mongo_db.boa_device_jobs

async def create_device_job(job_id: str, thread_id: str, task_id: str | None = None, is_cs_api: bool = False, agent_name: str | None = None) -> None:
    """신규 DeviceJob 도큐먼트 저장."""
    doc = {
        "job_id":     job_id,
        "thread_id":  thread_id,
        "status":     "pending",
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(minutes=_JOB_TTL_MINUTES),
        "is_cs_api":  is_cs_api,
    }
    if task_id:
        doc["task_id"] = task_id
    if agent_name:
        doc["agent_name"] = agent_name
    await mongo_db.boa_device_jobs.insert_one(doc)
    print(f"[DeviceJob] 생성: {job_id} → thread={thread_id}, task_id={task_id}")


async def get_job_info_by_job(job_id: str) -> dict | None:
    """job_id로 job 상세 정보(thread_id, is_cs_api 등) 조회."""
    doc = await boa_device_jobs_collection.find_one(
        {"job_id": job_id},
        {"thread_id": 1, "status": 1, "is_cs_api": 1, "agent_name": 1}
    )
    if not doc:
        print(f"[DeviceJob] job_id 없음: {job_id}")
        return None
    if doc.get("status") in ("expired", "done", "error"):
        print(f"[DeviceJob] 이미 처리된/만료된 job: {job_id}, status={doc.get('status')}")
        return None
    return doc


async def get_thread_id_by_job(job_id: str) -> str | None:
    """job_id로 thread_id 조회."""
    doc = await boa_device_jobs_collection.find_one(
        {"job_id": job_id},
        {"thread_id": 1, "status": 1}
    )
    if not doc:
        print(f"[DeviceJob] job_id 없음: {job_id}")
        return None
    if doc.get("status") in ("expired", "done", "error"):
        print(f"[DeviceJob] 이미 처리된/만료된 job: {job_id}, status={doc.get('status')}")
        return None
    return doc["thread_id"]


async def complete_device_job(job_id: str, status: str = "done") -> None:
    """job 상태를 done/error로 업데이트."""
    await boa_device_jobs_collection.update_one(
        {"job_id": job_id},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}}
    )
    print(f"[DeviceJob] 완료: {job_id} → {status}")


async def get_job_id_by_task_id(task_id: str) -> str | None:
    """
    task_id로 job_id 복구.
    LangGraph interrupt() 시 _job_id 체크포인트 누락 케이스 대비.
    task_id는 다른 노드에서 return으로 세팅되므로 checkpoint에 안전하게 저장됨.
    """
    doc = await boa_device_jobs_collection.find_one(
        {"task_id": task_id},
        {"job_id": 1, "status": 1},
        sort=[("created_at", -1)],
    )
    if not doc:
        return None
    status = doc.get("status")
    if status in ("done", "error", "expired"):
        print(f"[DeviceJob] task_id={task_id} → job already {status}, 재전송 차단")
        return f"__already_{status}__"  # 재전송 차단 시그널
    print(f"[DeviceJob] task_id로 job_id 복구: {doc['job_id']} (status={status})")
    return doc["job_id"]