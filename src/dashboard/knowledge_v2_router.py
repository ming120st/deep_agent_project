"""
KB v2 전용 라우터 — Topic-Graph RAG 기반 지식베이스 CRUD.

권장 인덱스 (운영자가 수동으로 한 번 실행):
  boa_knowledge:
    - ("schema_version", 1)
    - ("tags", 1)
    - ("domain", 1)
    - ("status", 1)
    - ("owner_member_id", 1)

  boa_knowledge_conflicts:
    - ("topic_id", 1)
    - ("status", 1), ("detected_at", -1)  # compound

  boa_knowledge_gaps:
    - ("status", 1), ("duplicate_count", -1)  # compound
    - ("last_occurred_at", -1)

  boa_knowledge_ingest_log:
    - ("created_at", -1)
    - ("decision", 1)
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from src.core.mongo_client import boa_knowledge_collection
from src.core.knowledge_relation_manager import (
    add_relation,
    remove_relation,
    set_relations,
    get_related_topics,
    check_bidirectional_consistency,
)
from src.dashboard.auth import get_current_user, is_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge/v2")


# ── 인덱스 생성 ───────────────────────────────────────────────────────────────

async def ensure_kb_v2_indexes() -> None:
    """
    KB v2 권장 인덱스를 생성한다. 앱 기동 시 lifespan에서 호출.
    이미 존재하는 인덱스는 Motor가 no-op 처리한다.
    """
    from src.core.mongo_client import (
        boa_knowledge_conflicts_collection,
        boa_knowledge_gaps_collection,
        boa_knowledge_ingest_log_collection,
    )
    from pymongo import ASCENDING, DESCENDING

    # boa_knowledge
    await boa_knowledge_collection.create_index([("schema_version", ASCENDING)])
    await boa_knowledge_collection.create_index([("tags", ASCENDING)])
    await boa_knowledge_collection.create_index([("domain", ASCENDING)])
    await boa_knowledge_collection.create_index([("status", ASCENDING)])
    await boa_knowledge_collection.create_index([("owner_member_id", ASCENDING)])

    # boa_knowledge_conflicts
    await boa_knowledge_conflicts_collection.create_index([("topic_id", ASCENDING)])
    await boa_knowledge_conflicts_collection.create_index(
        [("status", ASCENDING), ("detected_at", DESCENDING)]
    )

    # boa_knowledge_gaps
    await boa_knowledge_gaps_collection.create_index(
        [("status", ASCENDING), ("duplicate_count", DESCENDING)]
    )
    await boa_knowledge_gaps_collection.create_index([("last_occurred_at", DESCENDING)])

    # boa_knowledge_ingest_log
    await boa_knowledge_ingest_log_collection.create_index([("created_at", DESCENDING)])
    await boa_knowledge_ingest_log_collection.create_index([("decision", ASCENDING)])

    logger.info("KB v2 인덱스 확인/생성 완료")


# ── 인증 헬퍼 ─────────────────────────────────────────────────────────────────

def _require_user(request: Request) -> dict:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


async def _require_admin(request: Request) -> dict:
    user = _require_user(request)
    if not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class TopicV2CreateRequest(BaseModel):
    title: str
    description: str
    content: str
    tags: list[str] = []
    domain: Optional[str] = None
    related_topic_ids: list[str] = []


class TopicV2PatchRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    domain: Optional[str] = None
    related_topic_ids: Optional[list[str]] = None


class RelationAddRequest(BaseModel):
    related_topic_id: str


# ── 직렬화 헬퍼 ───────────────────────────────────────────────────────────────

def _serialize(doc: dict) -> dict:
    """_id → id, datetime → isoformat."""
    doc["id"] = str(doc.pop("_id"))
    for field in ("created_at", "updated_at"):
        if isinstance(doc.get(field), datetime):
            doc[field] = doc[field].isoformat()
    return doc


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.get("/topics")
async def list_topics(
    request: Request,
    tag: Optional[str] = None,
    domain: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """v2 주제 리스트 (schema_version: 2 필터 + 선택 필터)."""
    _require_user(request)

    query: dict = {"schema_version": 2}
    if tag:
        query["tags"] = tag
    if domain:
        query["domain"] = domain
    if status:
        query["status"] = status
    if owner:
        query["owner_member_id"] = owner

    projection = {"content": 0}
    cursor = boa_knowledge_collection.find(query, projection).skip(skip).limit(limit)
    docs = await cursor.to_list(length=None)
    return [_serialize(d) for d in docs]


@router.get("/topics/{topic_id}")
async def get_topic(topic_id: str, request: Request):
    """단일 주제 조회 (v2 필드 포함, 본문 포함)."""
    _require_user(request)

    doc = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not doc:
        raise HTTPException(status_code=404, detail="주제를 찾을 수 없습니다.")
    return _serialize(doc)


@router.post("/topics", status_code=201)
async def create_topic(body: TopicV2CreateRequest, request: Request):
    """v2 포맷으로 신규 주제 생성."""
    user = _require_user(request)

    # related_topic_ids 에 존재하지 않는 ID 포함 시 400
    invalid_ids = []
    for rel_id in body.related_topic_ids:
        exists = await boa_knowledge_collection.find_one({"_id": rel_id}, {"_id": 1})
        if not exists:
            invalid_ids.append(rel_id)
    if invalid_ids:
        raise HTTPException(
            status_code=400,
            detail={"message": "존재하지 않는 topic_id가 포함되어 있습니다.", "invalid_ids": invalid_ids},
        )

    now = _now()
    topic_id = uuid.uuid4().hex
    doc = {
        "_id": topic_id,
        "title": body.title,
        "description": body.description,
        "content": body.content,
        "tags": body.tags,
        "domain": body.domain,
        "related_topic_ids": [],
        "schema_version": 2,
        "author_type": "human",
        "status": "draft",
        "owner_member_id": None,
        "created_at": now,
        "updated_at": now,
        "created_by": user["email"],
        "updated_by": user["email"],
    }
    await boa_knowledge_collection.insert_one(doc)

    # 양방향 관계 설정
    if body.related_topic_ids:
        await set_relations(topic_id, body.related_topic_ids, actor=user["email"])

    created = await boa_knowledge_collection.find_one({"_id": topic_id})
    return _serialize(created)


@router.patch("/topics/{topic_id}")
async def patch_topic(topic_id: str, body: TopicV2PatchRequest, request: Request):
    """v2 필드 부분 업데이트."""
    user = _require_user(request)

    existing = await boa_knowledge_collection.find_one({"_id": topic_id}, {"_id": 1})
    if not existing:
        raise HTTPException(status_code=404, detail="주제를 찾을 수 없습니다.")

    update_fields: dict = {"updated_at": _now(), "updated_by": user["email"]}

    if body.title is not None:
        update_fields["title"] = body.title
    if body.description is not None:
        update_fields["description"] = body.description
    if body.content is not None:
        update_fields["content"] = body.content
    if body.tags is not None:
        update_fields["tags"] = body.tags
    if body.domain is not None:
        update_fields["domain"] = body.domain

    # related_topic_ids 는 반드시 relation_manager 경유
    if body.related_topic_ids is not None:
        await set_relations(topic_id, body.related_topic_ids, actor=user["email"])

    if update_fields:
        await boa_knowledge_collection.update_one(
            {"_id": topic_id}, {"$set": update_fields}
        )

    updated = await boa_knowledge_collection.find_one({"_id": topic_id})
    return _serialize(updated)


@router.get("/topics/{topic_id}/related")
async def get_related(topic_id: str, request: Request):
    """연관 주제 경량 조회 ({id, title, description, tags, domain})."""
    _require_user(request)

    try:
        return await get_related_topics(topic_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/topics/{topic_id}/relations", status_code=201)
async def add_relation_endpoint(
    topic_id: str, body: RelationAddRequest, request: Request
):
    """단일 관계 추가."""
    user = _require_user(request)

    if topic_id == body.related_topic_id:
        raise HTTPException(status_code=400, detail="self-reference not allowed")

    try:
        await add_relation(topic_id, body.related_topic_id, actor=user["email"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True}


@router.delete("/topics/{topic_id}/relations/{related_id}", status_code=200)
async def remove_relation_endpoint(
    topic_id: str, related_id: str, request: Request
):
    """단일 관계 제거."""
    user = _require_user(request)

    try:
        await remove_relation(topic_id, related_id, actor=user["email"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {"ok": True}


@router.get("/admin/consistency-check")
async def consistency_check(request: Request):
    """양방향 링크 정합성 체크 (admin only)."""
    await _require_admin(request)
    return await check_bidirectional_consistency()
