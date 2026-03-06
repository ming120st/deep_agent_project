"""
Task Manager (boa_tasks CRUD)
BOA 에이전트 업무 티켓 관리
에이전트 업무 수행 시 진행상황을 boa_tasks 컬렉션에 기록합니다.
"""
from datetime import datetime
from typing import Optional, Any
from bson import ObjectId
from src.core.mongo_client import boa_tasks_collection

# ── 상태 상수 ─────────────────────────────────────────────────────────────────
# 단계별로 기록되는 상태값 정의
class TaskStatus:
    PROCESSING  = "처리중"   # 노드 안에서 업무 처리 중. 노드 시작 지점에 PROCESSING으로 기록, 노드 내에서 처리 결과에 따라 WAITING/COMPLETED/ERROR로 업데이트
    WAITING     = "대기중"   # HIL (사용자 답변 대기 시)
    COMPLETED   = "완료"     # 단계 처리 완료 시
    CANCELLED   = "취소"     # 사용자가 업무 취소 시
    ERROR       = "오류"     # 작업 처리 중 에러 발생 시


# ── 에이전트 연동 공통 헬퍼 ──────────────────────────────────────────────────────
# GraphState와 연동하여 상태를 업데이트합니다. (순환 참조 방지를 위해 Any 타입 사용)
async def set_waiting(state: Any, result: Optional[str] = None):
    if state.task_id:
        params = state.collected_parameters.get(state.current_agent or "", {})
        await update_task_status(task_id=state.task_id, status=TaskStatus.WAITING, result=result, parameters=params)

async def set_completed(state: Any, result: Optional[str] = None):
    if state.task_id:
        params = state.collected_parameters.get(state.current_agent or "", {})
        await update_task_status(task_id=state.task_id, status=TaskStatus.COMPLETED, result=result, parameters=params)

async def set_cancelled(state: Any, result: Optional[str] = None):
    if state.task_id:
        params = state.collected_parameters.get(state.current_agent or "", {})
        await update_task_status(task_id=state.task_id, status=TaskStatus.CANCELLED, result=result, parameters=params)


# ── 생성 ──────────────────────────────────────────────────────────────────────
# task 문서를 새로 생성하는 함수. 업무 시작 시 호출됩니다. 생성된 task_id(str)를 반환합니다.
async def create_task(user_id: str, user_name: str, agent_name: str, agent_display_name: str, parameters: Optional[dict] = None) -> str:
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

        # 파라미터
        "parameters": parameters or {},

        # 상태
        "status": TaskStatus.PROCESSING,
        "task_history": [
            {"status": TaskStatus.PROCESSING, "changed_at": now, "changed_by": "agent"}
        ],

        # 결과 
        "result": "시작",
    }
    result = await boa_tasks_collection.insert_one(doc)
    return str(result.inserted_id)


# ── 상태 변경 ─────────────────────────────────────────────────────────────────
# boa_tasks 컬렉션의 task_id에 해당하는 문서의 상태를 업데이트하는 함수. 업무 처리 중 상태 변경 시마다 호출.
async def update_task_status(
    task_id: str,
    status: str,
    changed_by: str = "agent",
    pending_reason: Optional[str] = None,
    admin_memo: Optional[str] = None,
    result: Optional[str] = None,
    parameters: Optional[dict] = None,
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
        update_fields["result"] = result # 최종 업무 처리 결과
    if parameters is not None:
        update_fields["parameters"] = parameters
    
    task_history_entry = {
        "status": status,
        "changed_at": now,
        "changed_by": changed_by
        }
    if result is not None:
        task_history_entry["result"] = result

    await boa_tasks_collection.update_one(
        {"_id": ObjectId(task_id)},
        {
            "$set": update_fields,
            "$push": {
                "task_history": task_history_entry
            }
        }
    )


# ── 조회 ──────────────────────────────────────────────────────────────────────
async def get_task(task_id: str) -> Optional[dict]:
    doc = await boa_tasks_collection.find_one({"_id": ObjectId(task_id)})
    if doc:
        doc["task_id"] = str(doc.pop("_id"))
    return doc

async def get_tasks_by_user(user_id: str) -> list:
    cursor = boa_tasks_collection.find({"user_id": user_id}).sort("created_at", -1)
    docs = await cursor.to_list(length=None)  # 모든 문서 가져오기
    result = []
    for doc in docs:
        doc["task_id"] = str(doc.pop("_id"))
        result.append(doc)
    return result

async def get_all_tasks(status: Optional[str] = None) -> list:
    query = {"status": status} if status else {}
    cursor = boa_tasks_collection.find(query).sort("created_at", -1)
    docs = await cursor.to_list(length=None)  # 모든 문서 가져오기
    result = []
    for doc in docs:
        doc["task_id"] = str(doc.pop("_id"))
        result.append(doc)
    return result