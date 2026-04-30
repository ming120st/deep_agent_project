"""
KB v2 양방향 관계 링크 관리 — 단일 쓰기 진입점.

boa_knowledge.related_topic_ids 는 반드시 이 모듈을 통해서만 변경한다.
라우터나 파이프라인에서 직접 $set / $push 금지.
"""
import logging
from datetime import datetime, timezone

from src.core.mongo_client import boa_knowledge_collection

logger = logging.getLogger(__name__)


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────

# 조건에 맞는 문서 찾기
async def _assert_topic_exists(topic_id: str) -> None:
    doc = await boa_knowledge_collection.find_one({"_id": topic_id}, {"_id": 1}) # 존재 여부만 확인하기 위해 _id 필드만 조회
    if doc is None:
        raise ValueError(f"topic_id '{topic_id}' 가 존재하지 않습니다.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 공개 API ──────────────────────────────────────────────────────────────────

async def add_relation(topic_id_a: str, topic_id_b: str, actor: str) -> None:
    """
    두 주제를 양방향으로 연결한다.
    - 이미 연결되어 있으면 no-op.
    - 자기 자신 연결 금지 (ValueError).
    - 존재하지 않는 topic_id면 ValueError.
    """
    if topic_id_a == topic_id_b:
        raise ValueError("자기 자신과 연결할 수 없습니다.")

    # 존재 여부 확인 (둘 다 존재해야 연결 시도)
    await _assert_topic_exists(topic_id_a)
    await _assert_topic_exists(topic_id_b)

    now = _now()

    # A → B
    res_a = await boa_knowledge_collection.update_one(
        {"_id": topic_id_a, "related_topic_ids": {"$ne": topic_id_b}}, # 조건. 이미 연결되어 있으면 no-op
        {
            "$addToSet": {"related_topic_ids": topic_id_b}, # 연결 추가
            "$set": {"updated_at": now, "updated_by": actor}, # 메타데이터 업데이트
        },
    )

    # B → A (A→B 가 실패해도 B→A 는 시도 — best-effort 롤백)
    try:
        await boa_knowledge_collection.update_one(
            {"_id": topic_id_b, "related_topic_ids": {"$ne": topic_id_a}},
            {
                "$addToSet": {"related_topic_ids": topic_id_a},
                "$set": {"updated_at": now, "updated_by": actor},
            },
        )
    except Exception as exc:
        # B→A 실패 시 A→B 롤백 시도
        if res_a.modified_count: # A→B 가 실제로 추가된 경우에만 롤백 시도. modified_count=0 이면 이미 연결되어 있었던 것이므로 롤백 불필요
            try:
                await boa_knowledge_collection.update_one(
                    {"_id": topic_id_a}, # 롤백은 조건 없이 무조건 제거 시도. 이미 존재하지 않는 경우에도 $pull은 문제없이 동작하므로 조건 불필요
                    {"$pull": {"related_topic_ids": topic_id_b}},
                )
            except Exception as rollback_exc:
                logger.error(
                    "add_relation 롤백 실패 A=%s B=%s: %s", topic_id_a, topic_id_b, rollback_exc
                )
        raise exc


async def remove_relation(topic_id_a: str, topic_id_b: str, actor: str) -> None:
    """양방향 연결 제거. 연결되어 있지 않으면 no-op."""
    now = _now()

    res_a = await boa_knowledge_collection.update_one(
        {"_id": topic_id_a}, # 조건 없이 무조건 제거 시도. 이미 존재하지 않는 경우에도 $pull은 문제없이 동작하므로 조건 불필요
        {
            "$pull": {"related_topic_ids": topic_id_b},
            "$set": {"updated_at": now, "updated_by": actor},
        },
    )

    try:
        await boa_knowledge_collection.update_one(
            {"_id": topic_id_b},
            {
                "$pull": {"related_topic_ids": topic_id_a},
                "$set": {"updated_at": now, "updated_by": actor},
            },
        )
    except Exception as exc:
        # B→A 제거 실패 시 A→B 롤백 시도
        if res_a.modified_count: # A→B 가 실제로 제거된 경우에만 롤백 시도. modified_count=0 이면 이미 연결되어 있지 않았던 것이므로 롤백 불필요
            try:
                await boa_knowledge_collection.update_one(
                    {"_id": topic_id_a}, # 롤백은 조건 없이 무조건 추가 시도. 이미 존재하는 경우에도 $addToSet은 문제없이 동작하므로 조건 불필요
                    {"$addToSet": {"related_topic_ids": topic_id_b}},
                )
            except Exception as rollback_exc:
                logger.error(
                    "remove_relation 롤백 실패 A=%s B=%s: %s", topic_id_a, topic_id_b, rollback_exc
                )
        raise exc


async def set_relations(topic_id: str, related_topic_ids: list[str], actor: str) -> None:
    """
    주제의 related_topic_ids를 통째로 교체.
    현재 목록과 비교해 추가분은 add_relation, 제거분은 remove_relation 호출.
    """
    doc = await boa_knowledge_collection.find_one({"_id": topic_id}, {"related_topic_ids": 1})
    if doc is None:
        raise ValueError(f"topic_id '{topic_id}' 가 존재하지 않습니다.")

    # 현재와 원하는 목록의 차집합 계산
    current = set(doc.get("related_topic_ids") or [])
    desired = set(related_topic_ids)

    # 차집합을 이용해 추가할 항목과 제거할 항목 계산
    to_add    = desired - current
    to_remove = current - desired

    # 추가
    for rel_id in to_add:
        try:
            await add_relation(topic_id, rel_id, actor)
        except Exception as exc:
            logger.error("set_relations add 실패 %s→%s: %s", topic_id, rel_id, exc)
            raise

    # 제거
    for rel_id in to_remove:
        try:
            await remove_relation(topic_id, rel_id, actor)
        except Exception as exc:
            logger.error("set_relations remove 실패 %s→%s: %s", topic_id, rel_id, exc)
            raise


async def get_related_topics(topic_id: str) -> list[dict]:
    """
    연관 주제를 {_id, title, description, tags, domain} 경량 형태로 반환.
    본문(content)은 제외.
    """
    doc = await boa_knowledge_collection.find_one({"_id": topic_id}, {"related_topic_ids": 1})
    if doc is None:
        raise ValueError(f"topic_id '{topic_id}' 가 존재하지 않습니다.")

    rel_ids = doc.get("related_topic_ids") or []
    if not rel_ids:
        return []

    projection = {"_id": 1, "title": 1, "description": 1, "tags": 1, "domain": 1}
    cursor = boa_knowledge_collection.find({"_id": {"$in": rel_ids}}, projection)
    results = await cursor.to_list(length=None)

    for item in results:
        item["id"] = item.pop("_id")

    return results


async def check_bidirectional_consistency() -> dict:
    """
    전체 boa_knowledge를 스캔하여 단방향 링크를 탐지.
    반환: {"checked": N, "inconsistencies": [{"from": ..., "to": ..., "direction": "missing_reverse"}, ...]}
    자동 복구는 수행하지 않는다.
    """
    cursor = boa_knowledge_collection.find({}, {"_id": 1, "related_topic_ids": 1})
    docs = await cursor.to_list(length=None)

    id_to_rels: dict[str, set[str]] = {
        d["_id"]: set(d.get("related_topic_ids") or []) for d in docs
    }

    inconsistencies = []
    for topic_id, rels in id_to_rels.items():
        for rel_id in rels:
            reverse = id_to_rels.get(rel_id)
            if reverse is None:
                # rel_id 자체가 DB에 없음 (dangling reference)
                inconsistencies.append({"from": topic_id, "to": rel_id, "direction": "dangling_reference"})
            elif topic_id not in reverse:
                inconsistencies.append({"from": topic_id, "to": rel_id, "direction": "missing_reverse"})

    return {"checked": len(docs), "inconsistencies": inconsistencies}
