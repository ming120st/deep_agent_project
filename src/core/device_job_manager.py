"""
core/device_job_manager.py

DeviceJob MongoDB CRUD.

[boa_device_jobs 문서 구조]
{
  "job_id":     "uuid",
  "thread_id":  "user@widemobile.com",   # graph resume 라우팅 키
  "status":     "pending|done|error|expired",
  "created_at": datetime,
  "expires_at": datetime,                # TTL 인덱스용 (기본 30분)
}
"""
from datetime import datetime, timedelta

from src.core.mongo_client import boa_device_jobs_collection

_JOB_TTL_MINUTES = 30


async def create_device_job(job_id: str, thread_id: str) -> None:
    """신규 DeviceJob 도큐먼트 저장."""
    await boa_device_jobs_collection.insert_one({
        "job_id":     job_id,
        "thread_id":  thread_id,
        "status":     "pending",
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(minutes=_JOB_TTL_MINUTES),
    })
    print(f"[DeviceJob] 생성: {job_id} → thread={thread_id}")


async def get_thread_id_by_job(job_id: str) -> str | None:
    """job_id로 thread_id 조회."""
    doc = await boa_device_jobs_collection.find_one(
        {"job_id": job_id},
        {"thread_id": 1, "status": 1}
    )
    if not doc:
        print(f"[DeviceJob] job_id 없음: {job_id}")
        return None
    if doc.get("status") == "expired":
        print(f"[DeviceJob] 만료된 job: {job_id}")
        return None
    return doc["thread_id"]


async def complete_device_job(job_id: str, status: str = "done") -> None:
    """job 상태를 done/error로 업데이트."""
    await boa_device_jobs_collection.update_one(
        {"job_id": job_id},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}}
    )
    print(f"[DeviceJob] 완료: {job_id} → {status}")