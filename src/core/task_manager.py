"""
Task Manager (boa_tasks CRUD)
BOA 에이전트 업무 티켓 관리
"""
from datetime import datetime
from typing import Optional
from bson import ObjectId
from src.core.mongo_client import boa_tasks_collection

# ── 상태 상수 ─────────────────────────────────────────────────────────────────
class TaskStatus:
    PROCESSING  = "처리중"   # 사용자 메시지 인입 시
    WAITING     = "대기중"   # HIL (사용자 컨펌 대기 시)
    COMPLETED   = "완료"
    CANCELLED   = "취소"


# ── 생성 ──────────────────────────────────────────────────────────────────────
async def create_task(user_id: str, user_name: str, agent_name: str, agent_display_name: str) -> str:
    """새 task 생성. 생성된 task_id(str) 반환"""
    now = datetime.utcnow()
    doc = {
        "created_at": now,
        "updated_at": now,

        # 요청자
        "user_id": user_id,
        "user_name": user_name,

        # 업무
        "agent_name": agent_name,
        "agent_display_name": agent_display_name,
        "parameters": {},

        # 상태
        "status": TaskStatus.PROCESSING,
        "status_history": [
            {"status": TaskStatus.PROCESSING, "changed_at": now, "changed_by": "system"}
        ],

        # human-in-the-loop
        "pending_reason": None,
        "admin_memo": None,

        # 결과
        "result": None,
    }
    result = boa_tasks_collection.insert_one(doc)
    return str(result.inserted_id)


# ── 상태 변경 ─────────────────────────────────────────────────────────────────
async def update_task_status(
    task_id: str,
    status: str,
    changed_by: str = "system",
    pending_reason: Optional[str] = None,
    admin_memo: Optional[str] = None,
    result: Optional[str] = None,
):
    now = datetime.utcnow()
    update_fields = {
        "status": status,
        "updated_at": now,
    }
    if pending_reason is not None:
        update_fields["pending_reason"] = pending_reason
    if admin_memo is not None:
        update_fields["admin_memo"] = admin_memo
    if result is not None:
        update_fields["result"] = result

    boa_tasks_collection.update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": update_fields,
            "$push": {
                "status_history": {
                    "status": status,
                    "changed_at": now,
                    "changed_by": changed_by
                }
            }
        }
    )


# ── 파라미터 업데이트 ──────────────────────────────────────────────────────────
async def update_task_parameters(task_id: str, parameters: dict):
    """수집된 파라미터 스냅샷 업데이트"""
    boa_tasks_collection.update_one(
        {"_id": ObjectId(task_id)},
        {"$set": {
            "parameters": parameters,
            "updated_at": datetime.utcnow()
        }}
    )


# ── 조회 ──────────────────────────────────────────────────────────────────────
async def get_task(task_id: str) -> Optional[dict]:
    doc = boa_tasks_collection.find_one({"_id": ObjectId(task_id)})
    if doc:
        doc["task_id"] = str(doc.pop("_id"))
    return doc

async def get_tasks_by_user(user_id: str) -> list:
    docs = boa_tasks_collection.find({"user_id": user_id}).sort("created_at", -1)
    result = []
    for doc in docs:
        doc["task_id"] = str(doc.pop("_id"))
        result.append(doc)
    return result

async def get_all_tasks(status: Optional[str] = None) -> list:
    query = {"status": status} if status else {}
    docs = boa_tasks_collection.find(query).sort("created_at", -1)
    result = []
    for doc in docs:
        doc["task_id"] = str(doc.pop("_id"))
        result.append(doc)
    return result