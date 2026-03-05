"""
src/agent/sync.py

앱 시작(lifespan) 시 registry → MongoDB sync
  - 새 에이전트: insert (enabled = 코드 초기값)
  - 기존 에이전트: display_name/description/type만 갱신 (enabled 유지)
  - 레지스트리에서 빠진 에이전트: MongoDB에서 삭제
"""
from datetime import datetime
from src.agent.registry import ALL_REGISTRIES
from src.core.mongo_client import boa_agents_collection

# 앱 시작 시 registry → MongoDB sync
async def sync_registry_to_mongo() -> None:
    for agent_id, meta in ALL_REGISTRIES.items():
        await boa_agents_collection.update_one(
            {"agent_id": agent_id},
            {
                "$set": {
                    "agent_id":    agent_id,
                    "type":        meta["type"],
                    "display_name":meta["display_name"],
                    "description": meta["description"],
                    "updated_at":  datetime.utcnow(),
                },
                "$setOnInsert": {
                    # enabled는 최초 생성 시만 코드 초기값 사용
                    # 이후엔 대시보드 토글값 유지
                    "enabled":    meta["enabled"],
                    "created_at": datetime.utcnow(),
                }
            },
            upsert=True
        )
    print(f"[AgentSync] {len(ALL_REGISTRIES)}개 에이전트 sync 완료")

    # 레지스트리에서 빠진 에이전트 삭제
    all_agent_ids = list(ALL_REGISTRIES.keys())
    delete_result = await boa_agents_collection.delete_many(
        {"agent_id": {"$nin": all_agent_ids}}
    )
    if delete_result.deleted_count > 0:
        print(f"[AgentSync] {delete_result.deleted_count}개 에이전트 삭제 완료 (레지스트리 제외 항목)")
