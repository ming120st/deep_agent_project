import os

def append_to_mongo():
    path = "src/core/mongo_client.py"
    if not os.path.exists(path): return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\nsession_logs_collection = db['session_logs']\n")
        f.write("# session_logs_collection.create_index('session_id', unique=True)\n")
        f.write("# session_logs_collection.create_index('user_id')\n")
        f.write("# session_logs_collection.create_index('started_at')\n")
        f.write("# session_logs_collection.create_index('turns.events.task_id')\n")

def append_to_task_router():
    path = "src/dashboard/task_router.py"
    if not os.path.exists(path): return
    with open(path, "a", encoding="utf-8") as f:
        f.write("\nfrom src.core.session_logger import get_task_trace\n")
        f.write("""
@router.get("/api/tasks/{task_id}/trace")
async def api_get_task_trace(task_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    task = await get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="태스크를 찾을 수 없습니다")
    if task["user_id"] != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다")

    events = await get_task_trace(task_id)
    return {"task_id": task_id, "events": events}
""")

append_to_mongo()
append_to_task_router()
print("Done")
