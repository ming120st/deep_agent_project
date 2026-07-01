"""
KB v2 전용 라우터 — Topic-Graph RAG 기반 지식베이스 CRUD.

권장 인덱스 (운영자가 수동으로 한 번 실행):
  boa_knowledge:
    - ("schema_version", 1)
    - ("tags", 1)
    - ("domain_id", 1)
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
import asyncio
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from src.core.mongo_client import (
    boa_knowledge_collection,
    boa_knowledge_conflicts_collection,
    boa_knowledge_gaps_collection,
    boa_knowledge_versions_collection,
)
from src.core.knowledge_relation_manager import (
    add_relation,
    remove_relation,
    set_relations,
    get_related_topics,
    check_bidirectional_consistency,
)
from src.core.rag_engine import get_engine
from src.core.topic_ingest_pipeline import run_ingest_pipeline
from src.dashboard.auth import get_current_user, is_admin

logger = logging.getLogger(__name__)

router = APIRouter()
api_router = APIRouter(prefix="/api/knowledge/v2")


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
    await boa_knowledge_collection.create_index([("domain_id", ASCENDING)])
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
    domain_id: Optional[str] = None
    visibility: str = "internal"
    related_topic_ids: list[str] = []


class TopicV2PatchRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    domain_id: Optional[str] = None
    visibility: Optional[str] = None
    status: Optional[str] = None
    related_topic_ids: Optional[list[str]] = None


class RelationAddRequest(BaseModel):
    related_topic_id: str


class ConflictResolveRequest(BaseModel):
    action: str
    resolution_note: Optional[str] = None
    manual_content: Optional[str] = None


class GapStatusRequest(BaseModel):
    status: str                             # in_progress | resolved | wontfix | open
    resolved_topic_id: Optional[str] = None


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


async def _refresh_topic_index(topic_id: str) -> None:
    topic = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not topic:
        return
    await get_engine().upsert_doc(
        doc_id=str(topic["_id"]),
        title=topic.get("title", ""),
        content=topic.get("content", ""),
        status=topic.get("status", ""),
        tags=topic.get("tags", []),
        description=topic.get("description", ""),
        domain_id=topic.get("domain_id"),
    )


async def _append_conflict_content(topic_id: str, incoming_content: str, actor: str) -> None:
    topic = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not topic:
        raise HTTPException(status_code=404, detail="Related topic not found")

    now = _now()
    original_content = topic.get("content", "")
    new_content = (
        f"{original_content}\n\n---\n*자동 병합 (conflict_resolution)*\n\n{incoming_content}"
    )

    await boa_knowledge_versions_collection.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "topic_id": topic_id,
            "content": original_content,
            "snapshot_at": now,
            "snapshot_by": actor,
        }
    )
    await boa_knowledge_collection.update_one(
        {"_id": topic_id},
        {
            "$set": {
                "content": new_content,
                "updated_at": now,
                "updated_by": actor,
            }
        },
    )
    await _refresh_topic_index(topic_id)


async def _replace_topic_content(topic_id: str, manual_content: str, actor: str) -> None:
    topic = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not topic:
        raise HTTPException(status_code=404, detail="Related topic not found")

    now = _now()
    await boa_knowledge_versions_collection.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "topic_id": topic_id,
            "content": topic.get("content", ""),
            "snapshot_at": now,
            "snapshot_by": actor,
        }
    )
    await boa_knowledge_collection.update_one(
        {"_id": topic_id},
        {
            "$set": {
                "content": manual_content,
                "updated_at": now,
                "updated_by": actor,
            }
        },
    )
    await _refresh_topic_index(topic_id)


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@api_router.get("/topics")
async def list_topics(
    request: Request,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    domain_id: Optional[str] = None,
    visibility: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
):
    """v2 주제 리스트 (schema_version: 2 필터 + 선택 필터). RAG 벡터 엔진으로 의미적 검색을 지원."""
    _require_user(request)

    query: dict = {"schema_version": 2}
    if tag:
        query["tags"] = tag
    if domain_id:
        query["domain_id"] = domain_id
    if visibility:
        query["visibility"] = visibility
    
    if status:
        query["status"] = status
    else:
        query["status"] = {"$ne": "deleted"}
        
    if owner:
        query["owner_member_id"] = owner

    if q:
        from src.core.rag_engine import search_knowledge
        search_res = await search_knowledge(q, top_k=limit)
        topic_ids = [r["topic_id"] for r in search_res.get("results", []) if r.get("topic_id")]
        
        if not topic_ids:
            return []
            
        query["_id"] = {"$in": topic_ids}

    projection = {"content": 0}
    
    total = await boa_knowledge_collection.count_documents(query)
    
    cursor = boa_knowledge_collection.find(query, projection)
    
    if not q:
        # q가 없을 때만 db 수준의 limit 적용. q가 있으면 검색된 top_k 수만큼 전부 로드 후 정렬.
        cursor = cursor.skip(skip).limit(limit)
        cursor = cursor.sort("updated_at", -1)
        
    docs = await cursor.to_list(length=None)
    
    if q:
        # FAISS가 반환한 score 순위 그대로 정렬
        doc_map = {str(d["_id"]): d for d in docs}
        sorted_docs = [doc_map[tid] for tid in topic_ids if tid in doc_map]
        docs = sorted_docs

    return {"items": [_serialize(d) for d in docs], "total": total, "skip": skip, "limit": limit}


@api_router.get("/topics/{topic_id}")
async def get_topic(topic_id: str, request: Request):
    """단일 주제 조회 (v2 필드 포함, 본문 포함)."""
    _require_user(request)

    doc = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not doc:
        raise HTTPException(status_code=404, detail="주제를 찾을 수 없습니다.")
    return _serialize(doc)


@api_router.post("/topics", status_code=201)
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
        "domain_id": body.domain_id,
        "visibility": body.visibility,
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

    await boa_knowledge_versions_collection.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "topic_id": topic_id,
            "action": "create",
            "snapshot": doc,
            "content": doc.get("content", ""),
            "snapshot_at": now,
            "snapshot_by": user["email"],
        }
    )

    # 양방향 관계 설정
    if body.related_topic_ids:
        await set_relations(topic_id, body.related_topic_ids, actor=user["email"])

    await _refresh_topic_index(topic_id)

    created = await boa_knowledge_collection.find_one({"_id": topic_id})
    return _serialize(created)


@api_router.patch("/topics/{topic_id}")
async def patch_topic(topic_id: str, body: TopicV2PatchRequest, request: Request):
    """v2 필드 부분 업데이트."""
    user = _require_user(request)

    existing = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not existing:
        raise HTTPException(status_code=404, detail="주제를 찾을 수 없습니다.")

    now = _now()
    update_fields: dict = {"updated_at": now, "updated_by": user["email"]}

    if body.title is not None:
        update_fields["title"] = body.title
    if body.description is not None:
        update_fields["description"] = body.description
    if body.content is not None:
        update_fields["content"] = body.content
    if body.tags is not None:
        update_fields["tags"] = body.tags
    if body.domain_id is not None:
        update_fields["domain_id"] = body.domain_id
    if body.visibility is not None:
        update_fields["visibility"] = body.visibility
    if body.status is not None:
        update_fields["status"] = body.status

    # related_topic_ids 는 반드시 relation_manager 경유
    if body.related_topic_ids is not None:
        await set_relations(topic_id, body.related_topic_ids, actor=user["email"])

    if update_fields:
        # 이력 스냅샷 남기기 (기존 상태 보존)
        await boa_knowledge_versions_collection.insert_one(
            {
                "_id": uuid.uuid4().hex,
                "topic_id": topic_id,
                "action": "update",
                "snapshot": existing,
                "content": existing.get("content", ""), # 하위 호환성 유지
                "snapshot_at": now,
                "snapshot_by": user["email"],
            }
        )
        
        await boa_knowledge_collection.update_one(
            {"_id": topic_id}, {"$set": update_fields}
        )

    await _refresh_topic_index(topic_id)

    updated = await boa_knowledge_collection.find_one({"_id": topic_id})
    return _serialize(updated)


@api_router.get("/topics/{topic_id}/related")
async def get_related(topic_id: str, request: Request):
    """연관 주제 경량 조회 ({id, title, description, tags, domain_id})."""
    _require_user(request)

    try:
        return await get_related_topics(topic_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@api_router.get("/topics/{topic_id}/history")
async def get_topic_history(topic_id: str, request: Request):
    """주제 변경 이력 조회."""
    _require_user(request)

    # 이력 문서 조회 (최신 순)
    from pymongo import DESCENDING
    cursor = boa_knowledge_versions_collection.find({"topic_id": topic_id}).sort("snapshot_at", DESCENDING)
    versions = await cursor.to_list(length=100)
    
    # 직전 상태와의 비교를 위해 정렬: 생성(과거) -> 최근 -> 변경
    # 버전을 과거순으로 정렬하면 N번째 이력에서 바뀐 부분을 (N-1번째 snapshot 대비) 계산하기 수월함
    versions.reverse()

    history = []
    # prev_snapshot 은 직전 버전의 snapshot (업데이트 시 바뀐 내용 비교용)
    prev_snapshot = None
    
    for v in versions:
        action = v.get("action", "update") # 예전 포맷은 action이 없을 수 있음
        snapshot = v.get("snapshot", {})
        if not snapshot and v.get("content"):
            snapshot = {"content": v.get("content")}
            
        diff = {}
        if prev_snapshot:
            # title, description, content, status, tags, domain_id, visibility 변경 비교
            for field in ["title", "description", "content", "status", "domain_id", "visibility"]:
                old_val = prev_snapshot.get(field, "")
                new_val = snapshot.get(field, "")
                if old_val != new_val:
                    diff[field] = {"old": old_val, "new": new_val}
            
            old_tags = prev_snapshot.get("tags") or []
            new_tags = snapshot.get("tags") or []
            if sorted(old_tags) != sorted(new_tags):
                diff["tags"] = {"old": old_tags, "new": new_tags}
        else:
            diff["all"] = "생성됨"
            
        history.append({
            "id": v["_id"],
            "action": action,
            "snapshot_at": v["snapshot_at"].isoformat() if isinstance(v.get("snapshot_at"), datetime) else None,
            "snapshot_by": v.get("snapshot_by"),
            "diff": diff,
            "snapshot": snapshot,
        })
        prev_snapshot = snapshot

    # 마지막으로 현재 라이브 객체와의 diff 추가 (버전 컬렉션은 patch 직전 상태를 저장하므로, 현재 DB 데이터가 마지막 변경분임)
    current = await boa_knowledge_collection.find_one({"_id": topic_id})
    if current and prev_snapshot:
        diff = {}
        for field in ["title", "description", "content", "status", "domain_id", "visibility"]:
            old_val = prev_snapshot.get(field, "")
            new_val = current.get(field, "")
            if old_val != new_val:
                diff[field] = {"old": old_val, "new": new_val}
        
        old_tags = prev_snapshot.get("tags") or []
        new_tags = current.get("tags") or []
        if sorted(old_tags) != sorted(new_tags):
            diff["tags"] = {"old": old_tags, "new": new_tags}
            
        if diff:
            history.append({
                "id": "current",
                "action": "latest",
                "snapshot_at": (current.get("updated_at") or current.get("created_at") or datetime.now()).isoformat(),
                "snapshot_by": current.get("updated_by", current.get("created_by")),
                "diff": diff,
                "snapshot": current
            })

    # 다시 최신순으로 정렬해서 반환
    history.reverse()
    return history


@api_router.post("/ingest/upload", status_code=202)
async def ingest_upload(request: Request, file: UploadFile = File(...)):
    user = _require_user(request)

    filename = file.filename or ""
    if not filename.lower().endswith((".txt", ".md", ".xlsx")):
        raise HTTPException(
            status_code=400,
            detail="지원하지 않는 파일 형식입니다. .txt, .md, .xlsx만 지원합니다.",
        )

    raw_bytes = await file.read()
    
    # ── Blob Storage에 지식베이스 원본 파일 저장 ──
    from src.core.blob_knowledge_client import upload_knowledge_source
    import time
    import logging
    _local_logger = logging.getLogger(__name__)

    unique_filename = f"{int(time.time())}_{filename}"
    try:
        blob_path = await asyncio.to_thread(upload_knowledge_source, unique_filename, raw_bytes)
        _local_logger.info(f"[Ingest Upload] 원본 파일 Blob 저장 완료: {blob_path}")
    except Exception as e:
        _local_logger.error(f"[Ingest Upload] 원본 파일 Blob 저장 실패 (무시하고 진행): {e}")
        
    if filename.lower().endswith(".xlsx"):
        import io
        import openpyxl
        import logging
        logger = logging.getLogger(__name__)

        async def process_excel_background(file_bytes: bytes, fname: str, actor: str):
            try:
                wb = openpyxl.load_workbook(filename=io.BytesIO(file_bytes), data_only=True)
                ws = wb.active
                # A2셀부터 마지막 줄까지 순차적으로 처리
                for row_idx in range(2, ws.max_row + 1):
                    cell_val = ws.cell(row=row_idx, column=1).value
                    if not cell_val:
                        continue
                    
                    text = str(cell_val).strip()
                    if not text:
                        continue
                        
                    try:
                        await run_ingest_pipeline(
                            content=text,
                            source_type="document_upload",
                            source_ref=f"{fname} (Row {row_idx})",
                            actor=actor,
                        )
                    except Exception as e:
                        logger.error(f"[Excel Ingest] Row {row_idx} failed: {e}")
            except Exception as e:
                logger.error(f"[Excel Ingest] Failed to load workbook: {e}")

        asyncio.create_task(process_excel_background(raw_bytes, filename, user["email"]))
        
    else:
        import chardet
        detected = chardet.detect(raw_bytes)
        encoding = detected.get("encoding") or "utf-8"
        text = raw_bytes.decode(encoding, errors="replace").strip()
        
        if not text:
            raise HTTPException(status_code=400, detail="업로드한 파일이 비어 있습니다.")

        asyncio.create_task(
            run_ingest_pipeline(
                content=text,
                source_type="document_upload",
                source_ref=filename,
                actor=user["email"],
            )
        )

    return {
        "message": "인입 파이프라인이 시작되었습니다.",
        "filename": filename,
    }


@api_router.post("/topics/{topic_id}/relations", status_code=201)
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


@api_router.delete("/topics/{topic_id}/relations/{related_id}", status_code=200)
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


@api_router.delete("/topics/{topic_id}", status_code=200)
async def delete_topic(topic_id: str, request: Request):
    """Soft-delete a topic by setting status to 'deleted' and archiving history."""
    user = _require_user(request)
    actor = user["email"]

    doc = await boa_knowledge_collection.find_one({"_id": topic_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Topic not found")

    now = _now()
    
    # 이력 기록
    await boa_knowledge_versions_collection.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "topic_id": topic_id,
            "action": "delete",
            "snapshot": doc,
            "content": doc.get("content", ""),
            "snapshot_at": now,
            "snapshot_by": actor,
        }
    )

    await boa_knowledge_collection.update_one(
        {"_id": topic_id},
        {"$set": {"status": "deleted", "updated_at": now, "updated_by": actor}}
    )

    await _refresh_topic_index(topic_id)
    return {"ok": True}


@api_router.get("/conflicts")
async def list_conflicts(request: Request, status: Optional[str] = None):
    _require_user(request)

    query: dict = {}
    if status:
        query["status"] = status

    cursor = boa_knowledge_conflicts_collection.find(query).sort("detected_at", -1)
    conflicts = await cursor.to_list(length=None)

    topic_ids = list({c.get("topic_id") for c in conflicts if c.get("topic_id")})
    topic_map: dict[str, dict] = {}
    if topic_ids:
        topic_cursor = boa_knowledge_collection.find(
            {"_id": {"$in": topic_ids}},
            {"title": 1},
        )
        topic_docs = await topic_cursor.to_list(length=None)
        topic_map = {str(doc["_id"]): doc for doc in topic_docs}

    serialized = []
    for conflict in conflicts:
        topic_id = conflict.get("topic_id")
        conflict["id"] = str(conflict.pop("_id"))
        conflict["topic_title"] = topic_map.get(topic_id, {}).get("title", "(삭제된 주제)")
        for field in ("detected_at", "notified_at", "resolved_at"):
            if isinstance(conflict.get(field), datetime):
                conflict[field] = conflict[field].isoformat()
        serialized.append(conflict)
    return serialized


@api_router.patch("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    request: Request,
    conflict_id: str,
    body: ConflictResolveRequest,
):
    user = _require_user(request)
    conflict = await boa_knowledge_conflicts_collection.find_one({"_id": conflict_id})
    if not conflict:
        raise HTTPException(status_code=404, detail="Conflict not found")

    topic_id = conflict.get("topic_id")
    action = body.action
    now = _now()
    resolution_fields = {
        "resolved_at": now,
        "resolved_by": user["email"],
        "resolution_note": body.resolution_note,
    }

    if action == "merge":
        await _append_conflict_content(
            topic_id=topic_id,
            incoming_content=conflict.get("incoming_content", ""),
            actor=user["email"],
        )
        resolution_fields["status"] = "merged"
    elif action == "reject":
        resolution_fields["status"] = "rejected"
    elif action == "manual_edit":
        if not body.manual_content:
            raise HTTPException(status_code=400, detail="manual_content is required")
        await _replace_topic_content(
            topic_id=topic_id,
            manual_content=body.manual_content,
            actor=user["email"],
        )
        resolution_fields["status"] = "manual_edited"
        resolution_fields["manual_content"] = body.manual_content
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    await boa_knowledge_conflicts_collection.update_one(
        {"_id": conflict_id},
        {"$set": resolution_fields},
    )

    updated = await boa_knowledge_conflicts_collection.find_one({"_id": conflict_id})
    updated["id"] = str(updated.pop("_id"))
    for field in ("detected_at", "notified_at", "resolved_at"):
        if isinstance(updated.get(field), datetime):
            updated[field] = updated[field].isoformat()
    return updated


@api_router.get("/admin/consistency-check")
async def consistency_check(request: Request):
    """양방향 링크 정합성 체크 (admin only)."""
    await _require_admin(request)
    return await check_bidirectional_consistency()


@api_router.get("/ingest-logs")
async def list_ingest_logs(request: Request, limit: int = 100):
    """지식 인입(Ingest) 로그 조회."""
    _require_user(request)
    
    from src.core.mongo_client import boa_knowledge_ingest_log_collection
    cursor = boa_knowledge_ingest_log_collection.find({}).sort("created_at", -1).limit(limit)
    logs = await cursor.to_list(length=None)
    for log in logs:
        log["id"] = str(log.pop("_id"))
        if isinstance(log.get("created_at"), datetime):
            log["created_at"] = log["created_at"].isoformat()
    return logs

@api_router.get("/gaps")
async def list_gaps(
    request: Request,
    status: Optional[str] = None,
    limit: int = 50,
):
    """Gap 목록 조회 (빈도 내림차순). query_embedding 벡터는 응답에서 제외."""
    _require_user(request)
    query: dict = {}
    if status:
        query["status"] = status

    cursor = boa_knowledge_gaps_collection.find(
        query,
        {"query_embedding": 0},
    ).sort("duplicate_count", -1).limit(limit)

    gaps = await cursor.to_list(length=None)
    for gap in gaps:
        gap["id"] = str(gap.pop("_id"))
        for field in ("created_at", "last_occurred_at"):
            if isinstance(gap.get(field), datetime):
                gap[field] = gap[field].isoformat()
    return gaps


@api_router.patch("/gaps/{gap_id}/status")
async def update_gap_status(request: Request, gap_id: str, body: GapStatusRequest):
    """Gap 상태 변경."""
    _require_user(request)
    update: dict = {"status": body.status}
    if body.resolved_topic_id:
        update["resolved_topic_id"] = body.resolved_topic_id
    await boa_knowledge_gaps_collection.update_one(
        {"_id": gap_id}, {"$set": update}
    )
    return {"ok": True}


router.include_router(api_router)


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")
    
    from src.core.mongo_client import boa_admins_collection
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    is_admin = doc is not None

    html_content = KNOWLEDGE_HTML
    if not is_admin:
        html_content = html_content.replace('<a href="/spaces" class="nav-link">스페이스</a>', '')
        html_content = html_content.replace('<a href="/members" class="nav-link">구성원</a>', '')

    return HTMLResponse(html_content)


KNOWLEDGE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>지식베이스 | BOA</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #f5f6fa; --surface: #ffffff; --border: #e2e7f0;
      --text: #1e2436; --muted: #7b8499; --accent: #4f46e5;
      --green: #22c55e; --red: #ef4444; --yellow: #f59e0b; --blue: #3b82f6;
          <!-- Domain_id 필터 / 뱃지 처리 부분 -->
    }
    body { font-family: 'IBM Plex Sans KR', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; font-size: 15px; }
    button, input, select, textarea { font: inherit; }

    .top-nav { background: #ffffff; border-bottom: 1px solid #e5e7eb; padding: 0 28px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
    .logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 16px; color: #1e2436; letter-spacing: .05em; }
    .logo span { color: #3b82f6; }
    .nav-links { display: flex; align-items: center; gap: 4px; }
    .nav-link { padding: 6px 14px; border-radius: 6px; font-size: 14px; color: var(--muted); text-decoration: none; transition: all .15s; }
    .nav-link:hover { color: var(--text); background: #f0f2f8; }
    .nav-link.active { color: #4f46e5; background: #ede9fe; }
    .btn-logout { background: none; border: 1px solid #e2e7f0; border-radius: 6px; padding: 5px 12px; color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: none; transition: all .15s; }
    .btn-logout:hover { border-color: #ef4444; color: #ef4444; }

    .page-body { padding: 32px 24px; }
    .header { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }
    .header h1 { font-size: 22px; font-weight: 600; margin-bottom: 6px; }
    .header p { color: var(--muted); line-height: 1.6; }

    .tabs { display: flex; gap: 4px; margin-bottom: 20px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 6px; width: fit-content; }
    .tab { padding: 8px 20px; border-radius: 7px; font-size: 13px; font-weight: 500; cursor: pointer; color: var(--muted); transition: all .15s; border: none; background: none; }
    .tab:hover { color: var(--text); }
    .tab.active { background: var(--accent); color: white; }

    .panel { display: none; }
    .panel.active { display: block; }

    .toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
    .toolbar-left { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .search-bar, .filter-select, .input, .textarea {
      background: var(--surface); border: 1px solid var(--border); border-radius: 8px; color: var(--text); outline: none; transition: border-color .15s, box-shadow .15s;
    }
    .search-bar, .filter-select, .input { height: 40px; padding: 0 14px; }
    .search-bar { width: 300px; }
    .filter-select { min-width: 140px; }
    .search-bar:focus, .filter-select:focus, .input:focus, .textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.08); }
    
    .search-container { display: flex; justify-content: center; width: 100%; margin: 32px 0 24px 0; }
    .main-search {
      width: 100%; max-width: 800px; height: 56px; border-radius: 28px; padding: 0 24px;
      font-size: 15px; border: 1px solid var(--border); box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
      transition: box-shadow 0.2s, border-color 0.2s;
    }
    .main-search:focus { box-shadow: 0 8px 24px rgba(79, 70, 229, 0.12); outline: none; }
    .main-search::placeholder { color: var(--muted); }
    .btn-primary, .btn-secondary, .btn-danger, .icon-btn {
      border-radius: 8px; cursor: pointer; transition: all .15s; border: 1px solid transparent;
    }
    .btn-primary { background: var(--accent); color: white; padding: 10px 16px; }
    .btn-primary:hover { opacity: .9; }
    .btn-secondary { background: white; color: var(--muted); border-color: var(--border); padding: 10px 16px; }
    .btn-secondary:hover { color: var(--text); border-color: var(--text); }
    .btn-danger { background: var(--red); color: white; padding: 10px 16px; }
    .btn-danger:hover { opacity: .88; }
    .icon-btn { background: none; color: var(--muted); border-color: transparent; padding: 6px 10px; }
    .icon-btn:hover { color: var(--text); background: rgba(0,0,0,0.04); }

    .table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
    table { width: 100%; border-collapse: collapse; }
    th { text-align: left; padding: 14px 20px; font-size: 12px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(0,0,0,0.02); white-space: nowrap; text-transform: uppercase; letter-spacing: .06em; font-family: 'IBM Plex Mono', monospace; }
    td { padding: 16px 20px; font-size: 14px; border-bottom: 1px solid rgba(0,0,0,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(0,0,0,0.018); }

    .topic-title-btn { border: none; background: none; padding: 0; text-align: left; cursor: pointer; }
    .topic-title { font-size: 15px; font-weight: 600; color: #1a2035; margin-bottom: 4px; }
    .topic-title-btn:hover .topic-title { color: var(--accent); }
    .topic-desc { font-size: 12px; color: var(--muted); line-height: 1.6; max-width: 420px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .td-mono { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--muted); }
    .actions { display: flex; align-items: center; gap: 6px; }

    .badge { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; font-size: 11px; font-weight: 500; font-family: 'IBM Plex Mono', monospace; white-space: nowrap; }
    .badge-domain-wifi { background: rgba(37, 99, 235, 0.12); color: #2563eb; }
    .badge-domain-esim { background: rgba(14, 165, 233, 0.12); color: #0ea5e9; }
    .badge-domain-usim { background: rgba(139, 92, 246, 0.12); color: #8b5cf6; }
    .badge-domain-hr { background: rgba(234, 88, 12, 0.12); color: #ea580c; }
    .badge-domain-analytics { background: rgba(16, 185, 129, 0.12); color: #10b981; }
    .badge-domain-commerce { background: rgba(244, 63, 94, 0.12); color: #f43f5e; }
    .badge-domain-finance { background: rgba(217, 70, 239, 0.12); color: #d946ef; }
    .badge-domain-other { background: rgba(148, 163, 184, 0.12); color: #64748b; }
    .badge-domain-empty { background: rgba(148, 163, 184, 0.12); color: #64748b; }
    .badge-status-active { background: rgba(34, 197, 94, 0.12); color: #15803d; }
    .badge-status-draft { background: rgba(245, 158, 11, 0.14); color: #b45309; }
    .badge-status-archived { background: rgba(148, 163, 184, 0.16); color: #64748b; }
    .badge-status-deleted { background: rgba(239, 68, 68, 0.12); color: #b91c1c; }

    .tag-list { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .tag-chip, .related-chip {
      display: inline-flex; align-items: center; gap: 6px; background: rgba(59,130,246,0.08); color: #1d4ed8;
      border: 1px solid rgba(59,130,246,0.14); border-radius: 999px; padding: 4px 9px; font-size: 11px; font-family: 'IBM Plex Mono', monospace;
    }
    .related-chip { cursor: pointer; }
    .related-chip:hover { background: rgba(59,130,246,0.14); }
    .chip-remove { background: none; border: none; color: inherit; cursor: pointer; font-size: 11px; padding: 0; }

    .empty-state, .loading-state, .error-state {
      text-align: center; padding: 64px 24px; background: var(--surface); border: 1px solid var(--border); border-radius: 12px; color: var(--muted);
    }
    .empty-state h3, .error-state h3 { font-size: 18px; color: var(--text); margin-bottom: 10px; }
    .empty-state p, .error-state p { line-height: 1.7; margin-bottom: 18px; }

    .modal-backdrop {
      display: none; position: fixed; inset: 0; background: rgba(15, 23, 42, 0.62);
      z-index: 200; align-items: center; justify-content: center; padding: 20px;
    }
    .modal-backdrop.open { display: flex; }
    .modal {
      background: white; border: 1px solid var(--border); border-radius: 16px; box-shadow: 0 24px 80px rgba(15, 23, 42, 0.22);
      width: min(720px, 100%); max-height: calc(100vh - 40px); overflow: auto;
    }
    .modal-header { display: flex; align-items: start; justify-content: space-between; gap: 14px; padding: 24px 24px 16px; border-bottom: 1px solid rgba(0,0,0,0.05); }
    .modal-title { font-size: 20px; font-weight: 600; }
    .modal-subtitle { color: var(--muted); margin-top: 6px; line-height: 1.6; }
    .modal-close { background: none; border: none; color: var(--muted); font-size: 24px; cursor: pointer; line-height: 1; }
    .modal-close:hover { color: var(--text); }
    .modal-body { padding: 24px; }
    .modal-footer { display: flex; justify-content: flex-end; gap: 10px; padding: 0 24px 24px; }

    .field { margin-bottom: 18px; }
    .field label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 8px; }
    .field .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
    .textarea { width: 100%; min-height: 120px; padding: 12px 14px; resize: vertical; }
    .textarea.content-field { min-height: 200px; }
    .markdown-editor { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: white; }
    .markdown-editor:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.08); }
    .markdown-editor .textarea { border: 0; border-radius: 0; box-shadow: none; min-height: 260px; }
    .markdown-editor .textarea:focus { box-shadow: none; }
    .markdown-editor.field-error { border-color: var(--red) !important; box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.08) !important; }
    .markdown-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px; border-bottom: 1px solid var(--border); background: #f8fafc; }
    .markdown-tool-group { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .markdown-tool, .markdown-tab { border: 1px solid var(--border); background: white; color: var(--muted); border-radius: 6px; min-width: 32px; height: 30px; padding: 0 9px; font-size: 12px; font-weight: 600; cursor: pointer; }
    .markdown-tool:hover, .markdown-tab:hover { color: var(--text); border-color: #cbd5e1; }
    .markdown-tab.active { color: var(--accent); background: rgba(79,70,229,0.08); border-color: rgba(79,70,229,0.2); }
    .markdown-preview { min-height: 260px; padding: 16px; background: #fff; border: 0; overflow: auto; }
    .markdown-preview.empty { color: var(--muted); }
    .markdown-body { line-height: 1.85; white-space: normal; overflow-wrap: anywhere; }
    .markdown-body h1, .markdown-body h2, .markdown-body h3 { color: var(--text); line-height: 1.35; margin: 18px 0 10px; }
    .markdown-body h1:first-child, .markdown-body h2:first-child, .markdown-body h3:first-child { margin-top: 0; }
    .markdown-body h1 { font-size: 22px; }
    .markdown-body h2 { font-size: 18px; }
    .markdown-body h3 { font-size: 15px; }
    .markdown-body p { margin: 0 0 12px; }
    .markdown-body ul, .markdown-body ol { margin: 0 0 12px 22px; }
    .markdown-body blockquote { margin: 0 0 12px; padding: 10px 12px; border-left: 3px solid var(--accent); background: #f8fafc; color: #475569; }
    .markdown-body code { font-family: 'IBM Plex Mono', monospace; background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 5px; padding: 1px 5px; font-size: 12px; }
    .markdown-body pre { margin: 0 0 12px; padding: 12px; background: #0f172a; color: #e2e8f0; border-radius: 8px; overflow: auto; }
    .markdown-body pre code { background: transparent; border: 0; color: inherit; padding: 0; }
    .field-error { border-color: var(--red) !important; box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.08) !important; }
    .form-grid { display: grid; grid-template-columns: 1fr 180px; gap: 16px; }

    .detail-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
    .detail-section { margin-bottom: 24px; }
    .detail-section h3 { font-size: 13px; font-family: 'IBM Plex Mono', monospace; text-transform: uppercase; color: var(--muted); letter-spacing: .08em; margin-bottom: 12px; }
    .detail-text { line-height: 1.85; white-space: pre-wrap; }
    .detail-related { display: flex; gap: 8px; flex-wrap: wrap; }
    .detail-actions { display: flex; gap: 8px; }
    .muted { color: var(--muted); }
    .hidden { display: none !important; }

    .related-picker { position: relative; }
    .related-search { width: 100%; }
    .selected-related { display: flex; gap: 8px; flex-wrap: wrap; min-height: 32px; margin-bottom: 10px; }
    .related-suggestions {
      position: absolute; top: calc(100% + 8px); left: 0; right: 0; background: white; border: 1px solid var(--border);
      border-radius: 12px; box-shadow: 0 16px 32px rgba(15, 23, 42, 0.12); max-height: 220px; overflow: auto; z-index: 20; display: none;
    }
    .related-suggestions.open { display: block; }
    .related-option { padding: 12px 14px; cursor: pointer; border-bottom: 1px solid rgba(0,0,0,0.04); }
    .related-option:last-child { border-bottom: none; }
    .related-option:hover { background: rgba(79, 70, 229, 0.06); }
    .related-option-title { font-weight: 600; margin-bottom: 4px; }
    .related-option-desc { font-size: 12px; color: var(--muted); line-height: 1.6; }
    .status-chip { display: inline-flex; align-items: center; gap: 6px; background: white; color: var(--muted); border: 1px solid var(--border); border-radius: 999px; padding: 8px 12px; font-size: 11px; font-family: 'IBM Plex Mono', monospace; cursor: pointer; }
    .status-chip.active { color: var(--accent); border-color: rgba(79,70,229,0.2); background: rgba(79,70,229,0.08); }
    .compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .compare-box { background: #f8fafc; border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; white-space: pre-wrap; line-height: 1.75; max-height: 260px; overflow: auto; }
    .subpanel { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-top: 18px; }
    .subpanel h3 { font-size: 13px; font-family: 'IBM Plex Mono', monospace; text-transform: uppercase; color: var(--muted); letter-spacing: .08em; margin-bottom: 12px; }
    .toast { position: fixed; right: 20px; bottom: 20px; background: #111827; color: white; padding: 12px 14px; border-radius: 10px; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.28); z-index: 400; display: none; }

    @media (max-width: 860px) {
      .form-grid { grid-template-columns: 1fr; }
      .compare-grid { grid-template-columns: 1fr; }
      .search-bar { width: 100%; }
      .toolbar { align-items: stretch; }
      .toolbar-left { width: 100%; }
      .toolbar-left > * { flex: 1 1 180px; }
      .table-wrap { overflow-x: auto; }
      table { min-width: 920px; }
    }
  </style>
</head>
<body>
  <nav class="top-nav">
    <div class="logo">BOA <span>//</span> 지식베이스</div>
    <div class="nav-links">
      <a href="/reports" class="nav-link">리포트</a>
      <a href="/dashboard" class="nav-link">업무 대시보드</a>
      <a href="/knowledge" class="nav-link active">지식베이스</a>
      <a href="/agents" class="nav-link">에이전트</a>
      <a href="/spaces" class="nav-link">스페이스</a>
      <a href="/members" class="nav-link">구성원</a>
      <a href="/sessions" class="nav-link">대화 로그</a>
    </div>
    <a href="/auth/logout" class="btn-logout">로그아웃</a>
  </nav>

  <div class="page-body">
    <div class="header">
      <div>
        <h1>지식베이스 관리</h1>
        <p>토픽을 생성하고 수정하면서 운영 지식을 정리합니다. Conflicts와 Gaps는 후속 단계에서 실제 데이터가 연결됩니다.</p>
      </div>
    </div>

    <div class="tabs">
      <button class="tab active" data-tab="topics">Topics</button>
      <button class="tab" data-tab="conflicts">Conflicts</button>
      <button class="tab" data-tab="gaps">Gaps</button>
      <button class="tab" data-tab="ingestLogs">Ingest Logs</button>
    </div>

    <section class="panel active" id="panel-topics">
      <div class="toolbar">
        <div class="toolbar-left" style="flex: 1;">
          <select class="filter-select" id="domainFilter">
            <option value="">전체 도메인</option>
            <option value="wifi">wifi</option>
            <option value="esim">esim</option>
            <option value="usim">usim</option>
            <option value="analytics">analytics</option>
            <option value="hr">hr</option>
            <option value="finance">finance</option>
            <option value="commerce">commerce</option>
          </select>
          <select class="filter-select" id="statusFilter">
            <option value="">전체 상태</option>
            <option value="draft">draft</option>
            <option value="active">active</option>
            <option value="archived">archived</option>
            <option value="deleted">deleted</option>
          </select>
          <input class="search-bar" id="searchInput" type="text" placeholder="찾으려는 문서는 어떤 내용인가요? 키워드가 일치하지 않아도 의미 기반으로 유사한 토픽을 찾아드립니다." style="flex: 1; min-width: 400px; max-width: 100%;">
        </div>
        <div class="toolbar-right" style="display:flex; gap:10px;">
          <input type="file" id="ingestFileInput" class="hidden" accept=".txt,.md">
          <button class="btn-secondary" id="uploadIngestBtn">파일로 인입</button>
          <button class="btn-primary" id="createTopicBtn">+ 새 토픽</button>
        </div>
      </div>

      <div id="topicsState" class="loading-state">토픽을 불러오는 중입니다...</div>

      <div class="table-wrap hidden" id="topicsTableWrap">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Domain</th>
              <th>Visibility</th>
              <th>Status</th>
              <th>Tags</th>
              <th>Updated</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody id="topicsTbody"></tbody>
        </table>
      </div>
    </section>

    <section class="panel" id="panel-conflicts">
      <div class="toolbar-left" style="margin-bottom:18px;" id="conflictStatusChips">
        <button class="status-chip active" data-conflict-status="">all</button>
        <button class="status-chip" data-conflict-status="pending">pending</button>
        <button class="status-chip" data-conflict-status="merged">merged</button>
        <button class="status-chip" data-conflict-status="rejected">rejected</button>
        <button class="status-chip" data-conflict-status="manual_edited">manual_edited</button>
        <button class="status-chip" data-conflict-status="stale">stale</button>
      </div>

      <div id="conflictsState" class="loading-state hidden">충돌 목록을 불러오는 중입니다...</div>
      <div class="table-wrap hidden" id="conflictsTableWrap">
        <table>
          <thead>
            <tr>
              <th>Topic / Reasoning</th>
              <th>Type / Confidence</th>
              <th>상태 / 처리자</th>
              <th>감지일시 / 해결일시</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody id="conflictsTbody"></tbody>
        </table>
      </div>

      <div class="subpanel hidden" id="conflictDetailPanel">
        <div class="detail-meta" style="justify-content:space-between; margin-bottom:18px;">
          <div>
            <div class="modal-title" id="conflictTopicTitle">충돌 상세</div>
            <div class="modal-subtitle" id="conflictMetaLine"></div>
          </div>
          <div class="toolbar-left">
            <span id="conflictTypeBadge"></span>
            <span id="conflictStatusBadge"></span>
          </div>
        </div>

        <div class="subpanel" style="margin-top:0; padding:16px;">
          <h3>AI Reasoning</h3>
          <div class="detail-text" id="conflictReasoning"></div>
        </div>

        <div class="compare-grid" style="margin-top:16px;">
          <div class="subpanel" style="margin-top:0; padding:16px;">
            <h3>Existing</h3>
            <div class="compare-box" id="conflictExisting"></div>
          </div>
          <div class="subpanel" style="margin-top:0; padding:16px;">
            <h3>Incoming</h3>
            <div class="compare-box" id="conflictIncoming"></div>
          </div>
        </div>

        <div class="field" style="margin-top:18px;">
          <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:8px;">
            <label for="conflictResolutionNote" style="margin-bottom:0px;">Resolution Note</label>
            <span id="conflictResolutionInfo" style="font-size:12px; color:var(--muted);"></span>
          </div>
          <textarea class="textarea" id="conflictResolutionNote" placeholder="처리 사유를 기록합니다."></textarea>
        </div>
        <div class="field">
          <label for="conflictManualContent">Manual Content</label>
          <textarea class="textarea content-field" id="conflictManualContent" placeholder="manual_edit 적용 시 사용할 본문입니다."></textarea>
        </div>
        <div class="modal-footer" style="padding:0;" id="conflictActionFooter">
          <button class="btn-secondary" id="rejectConflictBtn">기각 후 기존 유지</button>
          <button class="btn-secondary" id="manualConflictBtn">직접 수정 후 반영</button>
          <button class="btn-primary" id="mergeConflictBtn">자동 병합 반영</button>
        </div>
      </div>
      <div class="empty-state">
        <h3>아직 감지된 충돌이 없습니다.</h3>
        <p>P4에서 conflict 데이터가 연결되면 이 탭에서 pending, merged, rejected 상태를 확인할 수 있습니다.</p>
      </div>
    </section>

    <section class="panel" id="panel-gaps">
      <div class="toolbar-left" style="margin-bottom:18px;" id="gapStatusChips">
        <button class="status-chip active" data-gap-status="open">open</button>
        <button class="status-chip" data-gap-status="in_progress">in_progress</button>
        <button class="status-chip" data-gap-status="resolved">resolved</button>
        <button class="status-chip" data-gap-status="wontfix">wontfix</button>
        <button class="status-chip" data-gap-status="">all</button>
      </div>

      <div id="gapsState" class="loading-state hidden">갭 목록을 불러오는 중입니다...</div>
      <div class="table-wrap hidden" id="gapsTableWrap">
        <table>
          <thead>
            <tr>
              <th>Query</th>
              <th>발생횟수</th>
              <th>AI 제안</th>
              <th>마지막발생</th>
              <th>처리</th>
            </tr>
          </thead>
          <tbody id="gapsTbody"></tbody>
        </table>
      </div>

      <div class="subpanel hidden" id="gapDetailPanel">
        <div class="detail-meta" style="justify-content:space-between; margin-bottom:18px;">
          <div>
            <div class="modal-title" id="gapDetailQuery" style="font-size:16px;"></div>
          </div>
          <div><span id="gapDetailVerdictBadge"></span></div>
        </div>

        <div class="subpanel" style="margin-top:0; padding:16px;">
          <h3>AI 제안</h3>
          <div class="detail-text" id="gapDetailSuggestion"></div>
        </div>

        <div class="subpanel" style="margin-top:16px; padding:16px;">
          <h3>탐색한 주제</h3>
          <div class="detail-related" id="gapDetailTopics"></div>
        </div>

        <div class="modal-footer" style="padding:0; margin-top:18px;">
          <button class="btn-secondary" id="gapWontfixBtn">wontfix</button>
          <button class="btn-secondary" id="gapInProgressBtn">처리중으로 변경</button>
          <button class="btn-primary" id="gapCreateTopicBtn">새 토픽 만들기</button>
        </div>
      </div>
    </section>

    <section class="panel" id="panel-ingestLogs">
      <div class="toolbar-left" style="margin-bottom:18px;">
        <button class="btn-secondary" onclick="fetchIngestLogs()">새로고침</button>
      </div>

      <div id="ingestLogsState" class="loading-state hidden">업로드 기록을 불러오는 중입니다...</div>
      <div class="table-wrap hidden" id="ingestLogsTableWrap">
        <table>
          <thead>
            <tr>
              <th>인입 소스 / 일시 / 작업자</th>
              <th>최종 상태</th>
              <th>생성/업데이트 요약</th>
            </tr>
          </thead>
          <tbody id="ingestLogsTbody"></tbody>
        </table>
      </div>
    </section>
  </div>
  <div class="toast" id="toast"></div>

  <div class="modal-backdrop" id="formModal">
    <div class="modal">
      <div class="modal-header">
        <div>
          <div class="modal-title" id="formModalTitle">새 토픽</div>
          <div class="modal-subtitle">필수 정보를 입력해 토픽을 생성하거나 수정합니다.</div>
        </div>
        <button class="modal-close" data-close="formModal">&times;</button>
      </div>
      <div class="modal-body">
        <div class="field">
          <label for="topicTitleInput">제목 *</label>
          <input class="input" id="topicTitleInput" type="text" placeholder="예: eSIM 취소 처리 방법" style="width:100%;">
        </div>
        <div class="field">
          <label for="topicDescriptionInput">설명 *</label>
          <textarea class="textarea" id="topicDescriptionInput" placeholder="이 토픽이 무엇을 다루는지 80~200자 안팎으로 설명합니다."></textarea>
          <div class="hint" id="descriptionCount">(0/200)</div>
        </div>
        <div class="field">
          <label for="topicContentInput">본문 *</label>
          <div class="markdown-editor" id="topicContentEditor">
            <div class="markdown-toolbar">
              <div class="markdown-tool-group">
                <button class="markdown-tool" type="button" data-md-insert="heading">H2</button>
                <button class="markdown-tool" type="button" data-md-insert="bold">B</button>
                <button class="markdown-tool" type="button" data-md-insert="list">List</button>
                <button class="markdown-tool" type="button" data-md-insert="code">Code</button>
              </div>
              <div class="markdown-tool-group">
                <button class="markdown-tab active" type="button" data-md-tab="write">작성</button>
                <button class="markdown-tab" type="button" data-md-tab="preview">미리보기</button>
              </div>
            </div>
            <textarea class="textarea content-field" id="topicContentInput" placeholder="## 처리 기준&#10;- 핵심 조건&#10;- 예외 상황&#10;&#10;```&#10;필요한 예시나 응답 템플릿&#10;```"></textarea>
            <div class="markdown-preview markdown-body hidden empty" id="topicContentPreview">미리볼 본문이 없습니다.</div>
          </div>
          <div class="hint">Markdown 문법을 사용할 수 있습니다. 저장 시 원문 Markdown이 그대로 보관됩니다.</div>
        </div>
        <div class="field">
          <label for="topicTagsInput">태그</label>
          <input class="input" id="topicTagsInput" type="text" placeholder="예: esim, cancel, cs-operation" style="width:100%;">
          <div class="hint">쉼표로 구분해 입력합니다.</div>
        </div>
        <div class="form-grid" style="grid-template-columns: 1fr 1fr 1fr;">
          <div class="field">
            <label for="topicDomainInput">담당 도메인 (domain_id)</label>
            <select class="filter-select" id="topicDomainInput" style="width:100%;">
              <option value="">전체/공통</option>
              <option value="wifi">wifi</option>
              <option value="esim">esim</option>
              <option value="usim">usim</option>
              <option value="analytics">analytics</option>
              <option value="hr">hr</option>
              <option value="finance">finance</option>
              <option value="commerce">commerce</option>
            </select>
          </div>
          <div class="field">
            <label for="topicVisibilityInput">공개 범위 (visibility)</label>
            <select class="filter-select" id="topicVisibilityInput" style="width:100%;">
              <option value="internal">내부용 (internal)</option>
              <option value="public">공개용 (public)</option>
            </select>
          </div>
          <div class="field" id="topicStatusField" style="display:none;">
            <label for="topicStatusInput">상태</label>
            <select class="filter-select" id="topicStatusInput" style="width:100%;">
              <option value="draft">draft</option>
              <option value="active">active</option>
              <option value="archived">archived</option>
              <option value="deleted">deleted</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label>관련 토픽</label>
          <div class="related-picker">
            <div class="selected-related" id="selectedRelated"></div>
            <input class="input related-search" id="relatedTopicSearch" type="text" placeholder="토픽 제목으로 검색해 관련 토픽 추가">
            <div class="related-suggestions" id="relatedSuggestions"></div>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn-secondary" data-close="formModal">취소</button>
        <button class="btn-primary" id="saveTopicBtn">저장</button>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="detailModal">
    <div class="modal">
      <div class="modal-header">
        <div>
          <div class="modal-title" id="detailTitle">토픽 상세</div>
          <div class="modal-subtitle" id="detailSubtitle">상세 정보를 불러오는 중입니다.</div>
        </div>
        <div class="detail-actions">
          <button class="btn-secondary" id="historyTopicBtn">변경이력</button>
          <button class="btn-secondary" id="editTopicBtn">편집</button>
          <button class="btn-danger" id="deleteTopicBtn">삭제</button>
          <button class="modal-close" data-close="detailModal">&times;</button>
        </div>
      </div>
      <div class="modal-body">
        <div id="detailLoading" class="muted">불러오는 중입니다...</div>
        <div id="detailContent" class="hidden">
          <div class="detail-meta">
            <span id="detailDomainBadge"></span>
            <span id="detailVisibilityBadge"></span>
            <span id="detailStatusBadge"></span>
            <span id="detailTags" class="tag-list"></span>
          </div>
          <div class="detail-section">
            <h3>설명</h3>
            <div class="detail-text" id="detailDescription"></div>
          </div>
          <div class="detail-section">
            <h3>본문</h3>
            <div class="detail-text markdown-body" id="detailBody"></div>
          </div>
          <div class="detail-section">
            <h3>관련 토픽</h3>
            <div class="detail-related" id="detailRelated"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="modal-backdrop" id="historyModal">
    <div class="modal">
      <div class="modal-header">
        <div>
          <div class="modal-title">변경 이력</div>
          <div class="modal-subtitle">이 토픽의 변경 내역입니다. (클릭하여 상세 부분 확인)</div>
        </div>
        <button class="modal-close" data-close="historyModal">&times;</button>
      </div>
      <div class="modal-body">
        <div id="historyLoading" class="muted">이력을 불러오는 중입니다...</div>
        <div id="historyContent" class="hidden">
          <div class="table-wrap">
            <table style="min-width: unset;">
              <thead>
                <tr>
                  <th style="width:140px;">일시</th>
                  <th style="width:160px;">수정자</th>
                  <th>변경 사항</th>
                </tr>
              </thead>
              <tbody id="historyTbody"></tbody>
            </table>
          </div>
          <div id="historyDiffPanel" class="subpanel hidden" style="margin-top:20px; padding:16px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
              <h3 style="margin-bottom:0;">상세 변경 내역</h3>
              <button class="icon-btn" onclick="document.getElementById('historyDiffPanel').classList.add('hidden')">&times;</button>
            </div>
            <div id="historyDiffContent"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let topics = [];
    let conflicts = [];
    let gaps = [];
    let ingestLogs = [];
    let currentTab = 'topics';
    let editingTopicId = null;
    let viewingTopicId = null;
    let selectedRelatedTopicIds = [];
    let currentConflictStatus = '';
    let viewingConflictId = null;
    let currentGapStatus = 'open';
    let viewingGapId = null;

    const topicsState = document.getElementById('topicsState');
    const topicsTableWrap = document.getElementById('topicsTableWrap');
    const topicsTbody = document.getElementById('topicsTbody');
    const conflictsState = document.getElementById('conflictsState');
    const conflictsTableWrap = document.getElementById('conflictsTableWrap');
    const conflictsTbody = document.getElementById('conflictsTbody');
    const gapsState = document.getElementById('gapsState');
    const gapsTableWrap = document.getElementById('gapsTableWrap');
    const gapsTbody = document.getElementById('gapsTbody');
    const ingestLogsState = document.getElementById('ingestLogsState');
    const ingestLogsTableWrap = document.getElementById('ingestLogsTableWrap');
    const ingestLogsTbody = document.getElementById('ingestLogsTbody');
    const searchInput = document.getElementById('searchInput');
    const domainFilter = document.getElementById('domainFilter');
    const statusFilter = document.getElementById('statusFilter');
    const formModal = document.getElementById('formModal');
    const detailModal = document.getElementById('detailModal');
    const historyModal = document.getElementById('historyModal');
    const descriptionInput = document.getElementById('topicDescriptionInput');

    let historyData = [];

    function escHtml(value) {
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function inlineMarkdown(value) {
      return escHtml(value)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
        .replace(/\\*([^*]+)\\*/g, '<em>$1</em>');
    }

    function renderMarkdown(value) {
      const source = String(value || '').replace(/\\r\\n/g, '\\n').trim();
      if (!source) return '<span class="muted">-</span>';

      const blocks = source.split(/```/);
      let html = '';
      blocks.forEach((block, index) => {
        if (index % 2 === 1) {
          html += `<pre><code>${escHtml(block.trim())}</code></pre>`;
          return;
        }

        const lines = block.split('\\n');
        let paragraph = [];
        let list = null;

        function flushParagraph() {
          if (!paragraph.length) return;
          html += `<p>${inlineMarkdown(paragraph.join(' '))}</p>`;
          paragraph = [];
        }

        function flushList() {
          if (!list) return;
          html += `<${list.type}>${list.items.map(item => `<li>${inlineMarkdown(item)}</li>`).join('')}</${list.type}>`;
          list = null;
        }

        lines.forEach(line => {
          const trimmed = line.trim();
          if (!trimmed) {
            flushParagraph();
            flushList();
            return;
          }

          const heading = trimmed.match(/^(#{1,3})\\s+(.+)$/);
          if (heading) {
            flushParagraph();
            flushList();
            const level = heading[1].length;
            html += `<h${level}>${inlineMarkdown(heading[2])}</h${level}>`;
            return;
          }

          const quote = trimmed.match(/^>\\s?(.+)$/);
          if (quote) {
            flushParagraph();
            flushList();
            html += `<blockquote>${inlineMarkdown(quote[1])}</blockquote>`;
            return;
          }

          const unordered = trimmed.match(/^[-*]\\s+(.+)$/);
          const ordered = trimmed.match(/^\\d+\\.\\s+(.+)$/);
          if (unordered || ordered) {
            flushParagraph();
            const type = unordered ? 'ul' : 'ol';
            if (!list || list.type !== type) flushList();
            if (!list) list = { type, items: [] };
            list.items.push((unordered || ordered)[1]);
            return;
          }

          flushList();
          paragraph.push(trimmed);
        });

        flushParagraph();
        flushList();
      });

      return html || '<span class="muted">-</span>';
    }

    function updateMarkdownPreview() {
      const input = document.getElementById('topicContentInput');
      const preview = document.getElementById('topicContentPreview');
      if (!input || !preview) return;
      preview.innerHTML = renderMarkdown(input.value);
      preview.classList.toggle('empty', !input.value.trim());
    }

    function setMarkdownMode(mode) {
      const input = document.getElementById('topicContentInput');
      const preview = document.getElementById('topicContentPreview');
      if (!input || !preview) return;
      updateMarkdownPreview();
      input.classList.toggle('hidden', mode === 'preview');
      preview.classList.toggle('hidden', mode !== 'preview');
      document.querySelectorAll('[data-md-tab]').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.mdTab === mode);
      });
    }

    function insertMarkdownSnippet(kind) {
      const input = document.getElementById('topicContentInput');
      if (!input) return;
      const snippets = {
        heading: '## 제목',
        bold: '**강조할 내용**',
        list: '- 항목',
        code: '```\\n예시 코드 또는 템플릿\\n```',
      };
      const snippet = snippets[kind] || '';
      const start = input.selectionStart || 0;
      const end = input.selectionEnd || 0;
      const before = input.value.slice(0, start);
      const after = input.value.slice(end);
      const needsNewline = before && !before.endsWith('\\n') ? '\\n' : '';
      input.value = before + needsNewline + snippet + after;
      const cursor = before.length + needsNewline.length + snippet.length;
      input.focus();
      input.setSelectionRange(cursor, cursor);
      updateMarkdownPreview();
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.style.display = 'block';
      clearTimeout(showToast._timer);
      showToast._timer = setTimeout(() => {
        toast.style.display = 'none';
      }, 2600);
    }

    function badgeHtml(kind, value) {
      const safe = escHtml(value || '-');
      if (kind === 'domain_id') {
        if (!value) return '<span class="badge badge-domain-empty">-</span>';
        const lower = value.toLowerCase();
        return `<span class="badge badge-domain-${lower}" style="background:#f1f5f9; color:#475569; border:1px solid #cbd5e1;">${safe}</span>`;
      }
      if (kind === 'visibility') {
        if (value === 'public') return '<span class="badge" style="background: rgba(16, 185, 129, 0.12); color: #059669;">공개용</span>';
        if (value === 'internal') return '<span class="badge" style="background: rgba(139, 92, 246, 0.12); color: #7c3aed;">내부용</span>';
        return `<span class="badge" style="background:#f1f5f9; color:#475569;">${safe}</span>`;
      }
      if (kind === 'conflict_type') {
        if (value === 'conflict') return '<span class="badge badge-status-archived" style="background: rgba(239, 68, 68, 0.12); color: #b91c1c;">conflict</span>';
        return `<span class="badge" style="background:#f1f5f9; color:#475569; border:1px solid #cbd5e1;">${safe}</span>`;
      }
      if (kind === 'conflict_status') {
        if (value === 'merged') return '<span class="badge badge-status-active">merged</span>';
        if (value === 'pending') return '<span class="badge badge-status-draft">pending</span>';
        return `<span class="badge badge-status-archived">${safe}</span>`;
      }
      if (value === 'active') return '<span class="badge badge-status-active">active</span>';
      if (value === 'draft') return '<span class="badge badge-status-draft">draft</span>';
      if (value === 'deleted') return '<span class="badge badge-status-deleted">deleted</span>';
      return `<span class="badge badge-status-archived">${safe}</span>`;
    }

    function formatDate(value) {
      if (!value) return '-';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return '-';
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return `${month}-${day}`;
    }

    function normalizeTags(raw) {
      return String(raw || '')
        .split(',')
        .map(tag => tag.trim())
        .filter(Boolean);
    }

    function setLoadingState(message) {
      topicsState.className = 'loading-state';
      topicsState.textContent = message;
      topicsState.classList.remove('hidden');
      topicsTableWrap.classList.add('hidden');
    }

    function setErrorState(message) {
      topicsState.className = 'error-state';
      topicsState.innerHTML = `<h3>불러오지 못했습니다</h3><p>${escHtml(message)}</p>`;
      topicsState.classList.remove('hidden');
      topicsTableWrap.classList.add('hidden');
    }

    function setEmptyState() {
      topicsState.className = 'empty-state';
      topicsState.innerHTML = '<h3>지식이 아직 없어요</h3><p>+ 새 토픽 버튼으로 첫 번째 토픽을 등록해보세요.</p><button class="btn-primary" id="emptyCreateBtn">+ 새 토픽</button>';
      topicsState.classList.remove('hidden');
      topicsTableWrap.classList.add('hidden');
      const btn = document.getElementById('emptyCreateBtn');
      if (btn) btn.addEventListener('click', () => openCreateModal());
    }

    function getFilteredTopics() {
      // Backend handles Q, domain, and status filtering via RAG engine.
      return topics;
    }

    function renderTopics() {
      const filtered = getFilteredTopics();
      if (!topics.length || !filtered.length) {
        if (!topics.length) {
          setEmptyState();
        } else {
          topicsState.className = 'empty-state';
          topicsState.innerHTML = '<h3>조건에 맞는 토픽이 없습니다</h3><p>검색어나 필터를 조정해 다시 확인해보세요.</p>';
          topicsState.classList.remove('hidden');
          topicsTableWrap.classList.add('hidden');
        }
        return;
      }

      topicsState.classList.add('hidden');
      topicsTableWrap.classList.remove('hidden');
      topicsTbody.innerHTML = filtered.map(topic => {
        const tags = Array.isArray(topic.tags) ? topic.tags : [];
        const visibleTags = tags.slice(0, 3).map(tag => `<span class="tag-chip">${escHtml(tag)}</span>`).join('');
        const moreTag = tags.length > 3 ? `<span class="tag-chip">+${tags.length - 3}</span>` : '';
        return `
          <tr>
            <td>
              <button class="topic-title-btn" data-topic-open="${escHtml(topic.id)}">
                <div class="topic-title">${escHtml(topic.title || '(제목 없음)')}</div>
                <div class="td-mono" style="font-size:10px; color:#94a3b8; margin-top:2px;">ID: ${escHtml(topic.id)}</div>
                <div class="topic-desc">${escHtml(topic.description || '')}</div>
              </button>
            </td>
            <td>${badgeHtml('domain_id', topic.domain_id)}</td>
            <td>${badgeHtml('visibility', topic.visibility)}</td>
            <td>${badgeHtml('status', topic.status)}</td>
            <td><div class="tag-list">${visibleTags || '<span class="muted">-</span>'}${moreTag}</div></td>
            <td class="td-mono">${formatDate(topic.updated_at)}</td>
            <td>
              <div class="actions">
                <button class="icon-btn" data-topic-edit="${escHtml(topic.id)}">편집</button>
                <button class="icon-btn" data-topic-delete="${escHtml(topic.id)}">삭제</button>
              </div>
            </td>
          </tr>
        `;
      }).join('');
    }

    async function apiFetch(url, options = {}) {
      const headers = { ...(options.headers || {}) };
      if (!(options.body instanceof FormData) && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
      }
      const response = await fetch(url, {
        credentials: 'include',
        headers,
        ...options,
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      if (!response.ok) {
        const detail = payload && payload.detail;
        const message = typeof detail === 'string'
          ? detail
          : (detail && detail.message) || '요청 처리 중 오류가 발생했습니다.';
        throw new Error(message);
      }
      return payload;
    }

    let currentTopicsPage = 1;
    const topicsPerPage = 50;
    let totalTopics = 0;

    async function fetchTopics(page = 1) {
      currentTopicsPage = page;
      const skip = (page - 1) * topicsPerPage;
      const q = searchInput.value.trim();
      const domain_id = domainFilter.value;
      const status = statusFilter.value;
      
      const params = new URLSearchParams();
      if (q) params.append('q', q);
      if (domain_id) params.append('domain_id', domain_id);
      if (status) params.append('status', status);
      params.append('skip', skip);
      params.append('limit', topicsPerPage);

      setLoadingState('토픽을 불러오는 중입니다...');
      try {
        const data = await apiFetch(`/api/knowledge/v2/topics?${params.toString()}`);
        if(data && Array.isArray(data.items)) {
          topics = data.items;
          totalTopics = data.total || 0;
        } else {
          topics = Array.isArray(data) ? data : [];
          totalTopics = topics.length;
        }
        renderTopics();
        renderTopicsPagination();
      } catch (error) {
        setErrorState(error.message || '데이터를 불러오지 못했습니다.');
      }
    }
    
    function renderTopicsPagination() {
      const totalPages = Math.ceil(totalTopics / topicsPerPage);
      const paginationHtml = `
        <div style="display:flex; justify-content:center; padding:10px; gap:5px;">
          <button class="btn-secondary" onclick="fetchTopics(${Math.max(1, currentTopicsPage - 1)})" ${currentTopicsPage === 1 ? 'disabled' : ''}>이전</button>
          <span style="padding: 6px;">${currentTopicsPage} / ${totalPages > 0 ? totalPages : 1}</span>
          <button class="btn-secondary" onclick="fetchTopics(${Math.min(totalPages, currentTopicsPage + 1)})" ${currentTopicsPage === totalPages || totalPages === 0 ? 'disabled' : ''}>다음</button>
        </div>
      `;
      const container = topicsTableWrap; // Or whichever container wraps the table
      
      let pagDiv = document.getElementById('topics-pagination-controls');
      if(!pagDiv) {
        pagDiv = document.createElement('div');
        pagDiv.id = 'topics-pagination-controls';
        container.appendChild(pagDiv);
      }
      pagDiv.innerHTML = paginationHtml;
    }

    async function fetchConflicts() {
      const legacyEmpty = document.querySelector('#panel-conflicts > .empty-state');
      if (legacyEmpty) legacyEmpty.style.display = 'none';
      conflictsState.className = 'loading-state';
      conflictsState.textContent = '충돌 목록을 불러오는 중입니다...';
      conflictsState.classList.remove('hidden');
      conflictsTableWrap.classList.add('hidden');
      document.getElementById('conflictDetailPanel').classList.add('hidden');
      try {
        const query = currentConflictStatus ? `?status=${encodeURIComponent(currentConflictStatus)}` : '';
        const data = await apiFetch(`/api/knowledge/v2/conflicts${query}`);
        conflicts = Array.isArray(data) ? data : [];
        renderConflicts();
      } catch (error) {
        conflictsState.className = 'error-state';
        conflictsState.innerHTML = `<h3>불러오지 못했습니다</h3><p>${escHtml(error.message || '오류가 발생했습니다.')}</p>`;
      }
    }

    function renderConflicts() {
      if (!conflicts.length) {
        conflictsState.className = 'empty-state';
        conflictsState.innerHTML = '<h3>현재 조건에 맞는 충돌이 없습니다.</h3><p>새 충돌이 생기면 여기에서 검토하고 처리할 수 있습니다.</p>';
        conflictsState.classList.remove('hidden');
        conflictsTableWrap.classList.add('hidden');
        return;
      }

      conflictsState.classList.add('hidden');
      conflictsTableWrap.classList.remove('hidden');
      conflictsTbody.innerHTML = conflicts.map(conflict => {
        const isResolved = conflict.status !== 'pending';
        const resolvedBy = conflict.resolved_by ? conflict.resolved_by.split('@')[0] : '';
        const btnText = isResolved ? '조회' : '처리';
        const btnClass = isResolved ? 'btn-secondary' : 'btn-primary';
        
        return `
        <tr>
          <td>
            <button class="topic-title-btn" data-conflict-open="${escHtml(conflict.id)}">
              <div class="topic-title">${escHtml(conflict.topic_title || '(삭제된 주제)')}</div>
              <div class="topic-desc">${escHtml((conflict.llm_reasoning || '').slice(0, 120) || '충돌 상세를 열어 확인하세요.')}</div>
            </button>
          </td>
          <td>
            <div>${badgeHtml('conflict_type', conflict.conflict_type)}</div>
            <div class="td-mono" style="margin-top:6px; font-size:11px;">LLM: ${Math.round((conflict.confidence || 0) * 100)}% / Match: ${conflict.match_confidence != null ? Math.round(conflict.match_confidence * 100) : "-"}%</div>
          </td>
          <td>
            <div>${badgeHtml('conflict_status', conflict.status)}</div>
            ${resolvedBy ? `<div style="margin-top:6px; font-size:11px; color:var(--muted);">${escHtml(resolvedBy)}</div>` : ''}
          </td>
          <td class="td-mono">
            <div>감지: <span style="color:var(--text);">${formatDate(conflict.detected_at)}</span></div>
            ${conflict.resolved_at ? `<div style="margin-top:4px;">처리: <span style="color:var(--text);">${formatDate(conflict.resolved_at)}</span></div>` : ''}
          </td>
          <td><button class="${btnClass}" data-conflict-open="${escHtml(conflict.id)}">${btnText}</button></td>
        </tr>
      `}).join('');
    }

    function openConflictDetail(conflictId) {
      const conflict = conflicts.find(item => item.id === conflictId);
      if (!conflict) return;
      viewingConflictId = conflictId;
      document.getElementById('conflictDetailPanel').classList.remove('hidden');
      document.getElementById('conflictTopicTitle').textContent = conflict.topic_title || '충돌 상세';
      document.getElementById('conflictMetaLine').textContent = `감지 ${formatDate(conflict.detected_at)} / LLM판정 ${Math.round((conflict.confidence || 0) * 100)}% / 매칭 ${conflict.match_confidence != null ? Math.round(conflict.match_confidence * 100) : "-"}%`;
      document.getElementById('conflictTypeBadge').innerHTML = badgeHtml('conflict_type', conflict.conflict_type);
      document.getElementById('conflictStatusBadge').innerHTML = badgeHtml('conflict_status', conflict.status);
      document.getElementById('conflictReasoning').textContent = conflict.llm_reasoning || '추가 메모가 없습니다.';
      document.getElementById('conflictExisting').textContent = conflict.existing_excerpt || '-';
      document.getElementById('conflictIncoming').textContent = conflict.incoming_content || '-';
      
      const isResolved = conflict.status !== 'pending';
      const resolutionNoteEl = document.getElementById('conflictResolutionNote');
      const manualContentEl = document.getElementById('conflictManualContent');
      const resolutionInfoEl = document.getElementById('conflictResolutionInfo');
      const actionFooter = document.getElementById('conflictActionFooter');
      
      resolutionNoteEl.value = conflict.resolution_note || '';
      
      // 미처리 상태일 때는 빈 칸이 디폴트
      // 처리 완료(manual_edited)일 때는 저장했던 manual_content를 불러옴
      manualContentEl.value = isResolved && conflict.status === 'manual_edited' ? (conflict.manual_content || '') : '';
      
      if (isResolved) {
        resolutionNoteEl.readOnly = true;
        manualContentEl.readOnly = true;
        actionFooter.style.display = 'none';
        resolutionInfoEl.textContent = `처리: ${conflict.resolved_by ? conflict.resolved_by.split('@')[0] : '알수없음'} (${formatDate(conflict.resolved_at)})`;
      } else {
        resolutionNoteEl.readOnly = false;
        manualContentEl.readOnly = false;
        actionFooter.style.display = 'flex';
        resolutionInfoEl.textContent = '';
      }
    }

    async function resolveConflict(action) {
      if (!viewingConflictId) return;
      const payload = {
        action,
        resolution_note: document.getElementById('conflictResolutionNote').value.trim() || null,
      };
      if (action === 'manual_edit') {
        payload.manual_content = document.getElementById('conflictManualContent').value;
      }
      try {
        await apiFetch(`/api/knowledge/v2/conflicts/${viewingConflictId}/resolve`, {
          method: 'PATCH',
          body: JSON.stringify(payload),
        });
        showToast('충돌 상태를 업데이트했습니다.');
        await Promise.all([fetchConflicts(), fetchTopics(currentTopicsPage)]);
      } catch (error) {
        alert(error.message || '충돌 처리에 실패했습니다.');
      }
    }

    async function uploadIngestFile(file) {
      const formData = new FormData();
      formData.append('file', file);
      const result = await apiFetch('/api/knowledge/v2/ingest/upload', {
        method: 'POST',
        body: formData,
      });
      showToast(result.message || '인입 파이프라인이 시작되었습니다.');
    }

    function openModal(el) {
      el.classList.add('open');
    }

    function closeModal(el) {
      el.classList.remove('open');
    }

    function updateDescriptionCount() {
      document.getElementById('descriptionCount').textContent = `(${descriptionInput.value.length}/200)`;
    }

    function clearFieldErrors() {
      ['topicTitleInput', 'topicDescriptionInput', 'topicContentInput'].forEach(id => {
        document.getElementById(id).classList.remove('field-error');
      });
      document.getElementById('topicContentEditor').classList.remove('field-error');
    }

    function validateForm() {
      clearFieldErrors();
      let valid = true;
      ['topicTitleInput', 'topicDescriptionInput', 'topicContentInput'].forEach(id => {
        const el = document.getElementById(id);
        if (!el.value.trim()) {
          if (id === 'topicContentInput') {
            document.getElementById('topicContentEditor').classList.add('field-error');
          } else {
            el.classList.add('field-error');
          }
          valid = false;
        }
      });
      return valid;
    }

    function resetForm() {
      editingTopicId = null;
      selectedRelatedTopicIds = [];
      document.getElementById('formModalTitle').textContent = '새 토픽';
      document.getElementById('topicTitleInput').value = '';
      document.getElementById('topicDescriptionInput').value = '';
      document.getElementById('topicContentInput').value = '';
      document.getElementById('topicTagsInput').value = '';
      document.getElementById('topicDomainInput').value = '';
      document.getElementById('topicVisibilityInput').value = 'internal';
      document.getElementById('relatedTopicSearch').value = '';
      document.getElementById('relatedSuggestions').classList.remove('open');
      clearFieldErrors();
      updateDescriptionCount();
      setMarkdownMode('write');
      renderSelectedRelated();
    }

    function renderSelectedRelated() {
      const wrap = document.getElementById('selectedRelated');
      const selected = selectedRelatedTopicIds
        .map(id => topics.find(topic => topic.id === id))
        .filter(Boolean);
      wrap.innerHTML = selected.map(topic => `
        <span class="related-chip">
          ${escHtml(topic.title)}
          <button class="chip-remove" type="button" data-related-remove="${escHtml(topic.id)}">&times;</button>
        </span>
      `).join('');
    }

    function renderRelatedSuggestions() {
      const search = document.getElementById('relatedTopicSearch').value.trim().toLowerCase();
      const suggestions = topics.filter(topic => {
        if (topic.id === editingTopicId) return false;
        if (selectedRelatedTopicIds.includes(topic.id)) return false;
        if (!search) return false;
        return (topic.title || '').toLowerCase().includes(search);
      }).slice(0, 8);
      const box = document.getElementById('relatedSuggestions');
      if (!suggestions.length) {
        box.innerHTML = '';
        box.classList.remove('open');
        return;
      }
      box.innerHTML = suggestions.map(topic => `
        <div class="related-option" data-related-add="${escHtml(topic.id)}">
          <div class="related-option-title">${escHtml(topic.title)}</div>
          <div class="related-option-desc">${escHtml(topic.description || '')}</div>
        </div>
      `).join('');
      box.classList.add('open');
    }

    function openCreateModal() {
      resetForm();
      document.getElementById('topicStatusField').style.display = 'none';
      openModal(formModal);
    }

    async function openEditModal(topicId) {
      let topic = topics.find(item => item.id === topicId);
      let relatedTopics = [];
      try {
        [topic, relatedTopics] = await Promise.all([
          apiFetch(`/api/knowledge/v2/topics/${topicId}`),
          apiFetch(`/api/knowledge/v2/topics/${topicId}/related`),
        ]);
      } catch (error) {
        alert(error.message || '토픽 상세를 불러오지 못했습니다.');
        return;
      }
      editingTopicId = topicId;
      document.getElementById('formModalTitle').textContent = '토픽 편집';
      document.getElementById('topicTitleInput').value = topic.title || '';
      document.getElementById('topicDescriptionInput').value = topic.description || '';
      document.getElementById('topicContentInput').value = topic.content || '';
      document.getElementById('topicTagsInput').value = Array.isArray(topic.tags) ? topic.tags.join(', ') : '';
      document.getElementById('topicDomainInput').value = topic.domain_id || '';
      document.getElementById('topicVisibilityInput').value = topic.visibility || 'internal';
      document.getElementById('topicStatusField').style.display = 'block';
      document.getElementById('topicStatusInput').value = topic.status || 'draft';
      selectedRelatedTopicIds = Array.isArray(topic.related_topic_ids) ? [...topic.related_topic_ids] : [];
      document.getElementById('relatedTopicSearch').value = '';
      clearFieldErrors();
      updateDescriptionCount();
      setMarkdownMode('write');
      renderSelectedRelated();
      renderRelatedSuggestions();
      const index = topics.findIndex(item => item.id === topic.id);
      if (index >= 0) topics[index] = topic;
      else topics.push(topic);
      relatedTopics.forEach(relatedTopic => {
        if (!topics.some(item => item.id === relatedTopic.id)) {
          topics.push(relatedTopic);
        }
      });
      openModal(formModal);
    }

    async function saveTopic() {
      if (!validateForm()) return;

      const payload = {
        title: document.getElementById('topicTitleInput').value.trim(),
        description: document.getElementById('topicDescriptionInput').value.trim(),
        content: document.getElementById('topicContentInput').value.trim(),
        tags: normalizeTags(document.getElementById('topicTagsInput').value),
        domain_id: document.getElementById('topicDomainInput').value || null,
        visibility: document.getElementById('topicVisibilityInput').value,
        related_topic_ids: selectedRelatedTopicIds,
      };

      if (editingTopicId && document.getElementById('topicStatusField').style.display !== 'none') {
        payload.status = document.getElementById('topicStatusInput').value || 'draft';
      }

      const btn = document.getElementById('saveTopicBtn');
      const original = btn.textContent;
      btn.disabled = true;
      btn.textContent = '저장 중...';

      try {
        if (editingTopicId) {
          await apiFetch(`/api/knowledge/v2/topics/${editingTopicId}`, {
            method: 'PATCH',
            body: JSON.stringify(payload),
          });
        } else {
          await apiFetch('/api/knowledge/v2/topics', {
            method: 'POST',
            body: JSON.stringify(payload),
          });
        }
        closeModal(formModal);
        await fetchTopics(currentTopicsPage);
      } catch (error) {
        alert(error.message || '저장에 실패했습니다.');
      } finally {
        btn.disabled = false;
        btn.textContent = original;
      }
    }

    async function openDetailModal(topicId) {
      viewingTopicId = topicId;
      document.getElementById('detailLoading').classList.remove('hidden');
      document.getElementById('detailContent').classList.add('hidden');
      document.getElementById('detailTitle').textContent = '토픽 상세';
      document.getElementById('detailSubtitle').textContent = '상세 정보를 불러오는 중입니다.';
      openModal(detailModal);

      try {
        const [topic, related] = await Promise.all([
          apiFetch(`/api/knowledge/v2/topics/${topicId}`),
          apiFetch(`/api/knowledge/v2/topics/${topicId}/related`),
        ]);
        const index = topics.findIndex(item => item.id === topic.id);
        if (index >= 0) topics[index] = topic;
        else topics.push(topic);

        document.getElementById('detailTitle').textContent = topic.title || '제목 없음';
        document.getElementById('detailSubtitle').innerHTML = `ID: <span class="td-mono">${escHtml(topic.id)}</span>`;
        document.getElementById('detailDomainBadge').innerHTML = badgeHtml('domain_id', topic.domain_id);
        document.getElementById('detailVisibilityBadge').innerHTML = badgeHtml('visibility', topic.visibility);
        document.getElementById('detailStatusBadge').innerHTML = badgeHtml('status', topic.status);
        document.getElementById('detailDescription').textContent = topic.description || '-';
        document.getElementById('detailBody').innerHTML = renderMarkdown(topic.content || '-');
        document.getElementById('detailTags').innerHTML = (topic.tags || []).length
          ? topic.tags.map(tag => `<span class="tag-chip">${escHtml(tag)}</span>`).join('')
          : '<span class="muted">태그 없음</span>';
        document.getElementById('detailRelated').innerHTML = Array.isArray(related) && related.length
          ? related.map(item => `<button class="related-chip" data-topic-open="${escHtml(item.id)}">${escHtml(item.title)}</button>`).join('')
          : '<span class="muted">관련 토픽이 없습니다.</span>';

        document.getElementById('detailLoading').classList.add('hidden');
        document.getElementById('detailContent').classList.remove('hidden');
      } catch (error) {
        document.getElementById('detailLoading').textContent = error.message || '상세 정보를 불러오지 못했습니다.';
      }
    }

    async function openHistoryModal() {
      if (!viewingTopicId) return;
      document.getElementById('historyLoading').classList.remove('hidden');
      document.getElementById('historyContent').classList.add('hidden');
      document.getElementById('historyDiffPanel').classList.add('hidden');
      openModal(historyModal);

      try {
        historyData = await apiFetch(`/api/knowledge/v2/topics/${viewingTopicId}/history`);
        const tbody = document.getElementById('historyTbody');
        
        if (!historyData.length) {
          tbody.innerHTML = '<tr><td colspan="3" class="muted" style="text-align:center;">변경 이력이 없습니다.</td></tr>';
        } else {
          tbody.innerHTML = historyData.map((h, i) => {
            let summary = '';
            if (h.diff && h.diff.all === '생성됨') {
              summary = '<span class="badge" style="background:#22c55e22; color:#15803d;">생성됨</span>';
            } else if (h.diff && Object.keys(h.diff).length > 0) {
              const keys = Object.keys(h.diff).join(', ');
              summary = `<span class="muted" style="font-size:12px;">변경: ${escHtml(keys)}</span>`;
            } else {
              summary = '<span class="muted" style="font-size:12px;">(데이터 변동 없음)</span>';
            }

            let modifier = h.snapshot_by ? h.snapshot_by.split('@')[0] : 'System/Agent';
            return `
              <tr style="cursor:pointer;" onclick="renderHistoryDiff(${i})">
                <td class="td-mono">${formatDate(h.snapshot_at)} ${new Date(h.snapshot_at).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', hour12:false})}</td>
                <td><span class="badge" style="background:#f1f5f9; color:#475569; font-weight:normal;">${escHtml(modifier)}</span></td>
                <td>${summary}</td>
              </tr>
            `;
          }).join('');
        }
        document.getElementById('historyLoading').classList.add('hidden');
        document.getElementById('historyContent').classList.remove('hidden');
      } catch (error) {
        document.getElementById('historyLoading').textContent = error.message || '이력을 불러오지 못했습니다.';
      }
    }

    function renderHistoryDiff(index) {
      const h = historyData[index];
      const panel = document.getElementById('historyDiffPanel');
      const content = document.getElementById('historyDiffContent');
      panel.classList.remove('hidden');

      if (!h || !h.diff) {
        content.innerHTML = '<div class="muted">비교할 변경 사항이 없습니다.</div>';
        return;
      }
      if (h.diff.all === '생성됨') {
        content.innerHTML = '<div class="muted">토픽이 처음 생성된 시점입니다.</div>';
        return;
      }

      const diffs = Object.keys(h.diff).map(key => {
        const d = h.diff[key];
        const oldVal = Array.isArray(d.old) ? d.old.join(', ') : (d.old || '(없음)');
        const newVal = Array.isArray(d.new) ? d.new.join(', ') : (d.new || '(없음)');
        return `
          <div style="margin-bottom:16px;">
            <div style="font-weight:600; font-size:13px; color:var(--accent); margin-bottom:6px; text-transform:uppercase;">[${escHtml(key)}]</div>
            <div class="compare-grid" style="column-gap:12px;">
              <div class="compare-box" style="border-color:#fca5a5; background:#fef2f2;">${escHtml(oldVal)}</div>
              <div class="compare-box" style="border-color:#86efac; background:#f0fdf4;">${escHtml(newVal)}</div>
            </div>
          </div>
        `;
      }).join('');
      content.innerHTML = diffs;
    }

    async function deleteTopic(topicId) {
      const topic = topics.find(item => item.id === topicId);
      const title = topic && topic.title ? topic.title : '이 토픽';
      const ok = window.confirm(`${title} 토픽을 삭제하시겠습니까? 관련 토픽 연결도 함께 제거됩니다.`);
      if (!ok) return;

      try {
        await apiFetch(`/api/knowledge/v2/topics/${topicId}`, { method: 'DELETE' });
        if (viewingTopicId === topicId) closeModal(detailModal);
        await fetchTopics(currentTopicsPage);
      } catch (error) {
        alert(error.message || '삭제에 실패했습니다.');
      }
    }

    function switchTab(tab) {
      currentTab = tab;
      document.querySelectorAll('.tab').forEach(button => {
        button.classList.toggle('active', button.dataset.tab === tab);
      });
      document.querySelectorAll('.panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `panel-${tab}`);
      });
      if (tab === 'conflicts') {
        fetchConflicts();
      } else if (tab === 'gaps') {
        fetchGaps();
      } else if (tab === 'ingestLogs') {
        fetchIngestLogs();
      }
    }

    async function fetchGaps() {
      document.getElementById('gapDetailPanel').classList.add('hidden');
      gapsState.className = 'loading-state';
      gapsState.textContent = '갭 목록을 불러오는 중입니다...';
      gapsState.classList.remove('hidden');
      gapsTableWrap.classList.add('hidden');
      try {
        const query = currentGapStatus ? `?status=${encodeURIComponent(currentGapStatus)}` : '';
        const data = await apiFetch(`/api/knowledge/v2/gaps${query}`);
        gaps = Array.isArray(data) ? data : [];
        renderGaps();
      } catch (error) {
        gapsState.className = 'error-state';
        gapsState.innerHTML = `<h3>불러오지 못했습니다</h3><p>${escHtml(error.message || '오류가 발생했습니다.')}</p>`;
      }
    }

    function renderGaps() {
      if (!gaps.length) {
        gapsState.className = 'empty-state';
        gapsState.innerHTML = '<h3>현재 미해결 지식 갭이 없습니다.</h3><p>새 갭이 생기면 여기에서 확인하고 처리할 수 있습니다.</p>';
        gapsState.classList.remove('hidden');
        gapsTableWrap.classList.add('hidden');
        return;
      }
      gapsState.classList.add('hidden');
      gapsTableWrap.classList.remove('hidden');
      gapsTbody.innerHTML = gaps.map(gap => `
        <tr>
          <td>
            <button class="topic-title-btn" data-gap-open="${escHtml(gap.id)}">
              <div class="topic-title">${escHtml(gap.query || '')}</div>
            </button>
          </td>
          <td class="td-mono">${gap.duplicate_count || 1}</td>
          <td style="max-width:260px; font-size:13px; color:var(--muted);">${escHtml((gap.ai_suggestion || '-').slice(0, 80))}</td>
          <td class="td-mono">${formatDate(gap.last_occurred_at)}</td>
          <td><button class="btn-secondary" data-gap-open="${escHtml(gap.id)}">처리</button></td>
        </tr>
      `).join('');
    }

    async function fetchIngestLogs() {
      ingestLogsState.className = 'loading-state';
      ingestLogsState.textContent = '업로드 기록을 불러오는 중입니다...';
      ingestLogsState.classList.remove('hidden');
      ingestLogsTableWrap.classList.add('hidden');
      try {
        const data = await apiFetch(`/api/knowledge/v2/ingest-logs`);
        ingestLogs = Array.isArray(data) ? data : [];
        renderIngestLogs();
      } catch (error) {
        ingestLogsState.className = 'error-state';
        ingestLogsState.innerHTML = `<h3>불러오지 못했습니다</h3><p>${escHtml(error.message || '오류가 발생했습니다.')}</p>`;
      }
    }

    function renderIngestLogs() {
      if (!ingestLogs.length) {
        ingestLogsState.className = 'empty-state';
        ingestLogsState.innerHTML = '<h3>인입 로그가 없습니다.</h3><p>문서를 업로드하면 처리 결과가 여기에 표시됩니다.</p>';
        ingestLogsState.classList.remove('hidden');
        ingestLogsTableWrap.classList.add('hidden');
        return;
      }
      ingestLogsState.classList.add('hidden');
      ingestLogsTableWrap.classList.remove('hidden');
      ingestLogsTbody.innerHTML = ingestLogs.map(log => {
        let statusBadge = '';
        const decision = log.decision || log.status || 'unknown';
        if (decision === 'success' || decision === 'created_new_topic' || decision === 'updated_existing_topic' || decision === 'updated_existing') {
          statusBadge = '<span class="badge badge-status-active">성공/반영</span>';
        } else if (decision === 'conflict' || decision === 'flagged_conflict') {
          statusBadge = '<span class="badge badge-status-archived" style="background:#fef2f2; color:#b91c1c;">충돌 감지</span>';
        } else if (decision === 'duplicate' || decision === 'duplicate_skipped') {
          statusBadge = '<span class="badge badge-status-draft">중복 스킵</span>';
        } else if (decision === 'failed') {
          statusBadge = '<span class="badge badge-status-deleted">failed</span>';
        } else {
          statusBadge = `<span class="badge badge-status-archived">${escHtml(decision)}</span>`;
        }

        const filename = log.source_ref || log.filename || 'Unknown';
        const actor = log.actor || 'System';
        
        let processDetailsHtml = '';
        if (log.process_details && Array.isArray(log.process_details) && log.process_details.length > 0) {
          processDetailsHtml = '<ul style="margin:0; padding-left:16px;">' + 
            log.process_details.map(item => `<li><strong>${escHtml(item.action)}</strong>: ${escHtml(item.tentative_title)} <br/><span class="td-mono" style="font-size:11px;">topic_id: ${escHtml(item.topic_id)}</span></li>`).join('') +
          '</ul>';
        } else {
          const message = log.decision_reason || log.reason || log.conflict_type || (log.raw_content ? log.raw_content.slice(0, 80) + '...' : '');
          processDetailsHtml = `<div style="max-width:300px; font-size:13px; color:var(--text);">${escHtml(message)}</div>`;
        }

        return `
        <tr>
          <td>
            <div style="font-weight:500; font-size:14px; margin-bottom:4px;">${escHtml(filename)}</div>
            <div class="td-mono">${formatDate(log.created_at)} ${log.created_at ? new Date(log.created_at).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', hour12:false}) : ''}</div>
            <div style="font-size:12px; color:var(--muted); margin-top:2px;">등록: <strong>${escHtml(actor)}</strong></div>
          </td>
          <td>${statusBadge}</td>
          <td>
            ${processDetailsHtml}
          </td>
        </tr>
      `}).join('');
    }

    function openGapDetail(gapId) {
      const gap = gaps.find(item => item.id === gapId);
      if (!gap) return;
      viewingGapId = gapId;
      document.getElementById('gapDetailPanel').classList.remove('hidden');
      document.getElementById('gapDetailQuery').textContent = gap.query || '';
      document.getElementById('gapDetailSuggestion').textContent = gap.ai_suggestion || '(아직 생성 중입니다. 잠시 후 새로 고침하세요.)';
      document.getElementById('gapDetailVerdictBadge').innerHTML =
        `<span class="badge" style="background:#f1f5f9; color:#475569; border:1px solid #cbd5e1;">${escHtml(gap.ai_verdict || '')}</span>`;

      const topicsEl = document.getElementById('gapDetailTopics');
      const searchedTopics = Array.isArray(gap.searched_topics) ? gap.searched_topics : [];
      if (searchedTopics.length) {
        topicsEl.innerHTML = searchedTopics.map(t =>
          `<span class="tag-chip">${escHtml(t.topic_id || '')}</span>`
        ).join('');
      } else {
        topicsEl.innerHTML = '<span class="muted">탐색한 주제 없음</span>';
      }
    }

    async function updateGapStatus(status) {
      if (!viewingGapId) return;
      try {
        await apiFetch(`/api/knowledge/v2/gaps/${viewingGapId}/status`, {
          method: 'PATCH',
          body: JSON.stringify({ status }),
        });
        showToast('갭 상태를 업데이트했습니다.');
        await fetchGaps();
      } catch (error) {
        alert(error.message || '상태 변경에 실패했습니다.');
      }
    }

    function openCreateModalWithPrefill(suggestion) {
      openCreateModal();
      if (suggestion) {
        document.getElementById('topicTitleInput').value = suggestion;
      }
    }

    document.addEventListener('click', async (event) => {
      const target = event.target;
      const closeTarget = target.getAttribute('data-close');
      if (closeTarget) {
        closeModal(document.getElementById(closeTarget));
        return;
      }
      if (target.classList.contains('modal-backdrop')) {
        closeModal(target);
        return;
      }
      const tab = target.getAttribute('data-tab');
      if (tab) {
        switchTab(tab);
        return;
      }
      const conflictStatus = target.getAttribute('data-conflict-status');
      if (conflictStatus !== null) {
        currentConflictStatus = conflictStatus;
        document.querySelectorAll('[data-conflict-status]').forEach(el => {
          el.classList.toggle('active', el.getAttribute('data-conflict-status') === conflictStatus);
        });
        await fetchConflicts();
        return;
      }
      const gapStatus = target.getAttribute('data-gap-status');
      if (gapStatus !== null) {
        currentGapStatus = gapStatus;
        document.querySelectorAll('[data-gap-status]').forEach(el => {
          el.classList.toggle('active', el.getAttribute('data-gap-status') === gapStatus);
        });
        await fetchGaps();
        return;
      }
      const gapOpenId = target.getAttribute('data-gap-open') || target.closest('[data-gap-open]')?.getAttribute('data-gap-open');
      if (gapOpenId) {
        openGapDetail(gapOpenId);
        return;
      }
      const openId = target.getAttribute('data-topic-open') || target.closest('[data-topic-open]')?.getAttribute('data-topic-open');
      if (openId) {
        await openDetailModal(openId);
        return;
      }
      const conflictOpenId = target.getAttribute('data-conflict-open') || target.closest('[data-conflict-open]')?.getAttribute('data-conflict-open');
      if (conflictOpenId) {
        openConflictDetail(conflictOpenId);
        return;
      }
      const editId = target.getAttribute('data-topic-edit');
      if (editId) {
        await openEditModal(editId);
        return;
      }
      const deleteId = target.getAttribute('data-topic-delete');
      if (deleteId) {
        await deleteTopic(deleteId);
        return;
      }
      const addRelatedId = target.getAttribute('data-related-add') || target.closest('[data-related-add]')?.getAttribute('data-related-add');
      if (addRelatedId) {
        selectedRelatedTopicIds.push(addRelatedId);
        document.getElementById('relatedTopicSearch').value = '';
        renderSelectedRelated();
        renderRelatedSuggestions();
        return;
      }
      const removeRelatedId = target.getAttribute('data-related-remove');
      if (removeRelatedId) {
        selectedRelatedTopicIds = selectedRelatedTopicIds.filter(id => id !== removeRelatedId);
        renderSelectedRelated();
        renderRelatedSuggestions();
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeModal(formModal);
        closeModal(detailModal);
        closeModal(historyModal);
        document.getElementById('conflictDetailPanel').classList.add('hidden');
      }
    });

    document.getElementById('createTopicBtn').addEventListener('click', openCreateModal);
    document.getElementById('uploadIngestBtn').addEventListener('click', () => {
      document.getElementById('ingestFileInput').click();
    });
    document.getElementById('saveTopicBtn').addEventListener('click', saveTopic);
    document.getElementById('editTopicBtn').addEventListener('click', async () => {
      if (viewingTopicId) {
        closeModal(detailModal);
        await openEditModal(viewingTopicId);
      }
    });
    document.getElementById('historyTopicBtn').addEventListener('click', () => {
      openHistoryModal();
    });
    document.getElementById('deleteTopicBtn').addEventListener('click', async () => {
      if (viewingTopicId) {
        await deleteTopic(viewingTopicId);
      }
    });
    document.getElementById('rejectConflictBtn').addEventListener('click', () => resolveConflict('reject'));
    document.getElementById('manualConflictBtn').addEventListener('click', () => resolveConflict('manual_edit'));
    document.getElementById('mergeConflictBtn').addEventListener('click', () => resolveConflict('merge'));
    document.getElementById('gapWontfixBtn').addEventListener('click', () => updateGapStatus('wontfix'));
    document.getElementById('gapInProgressBtn').addEventListener('click', () => updateGapStatus('in_progress'));
    document.getElementById('gapCreateTopicBtn').addEventListener('click', () => {
      const gap = gaps.find(item => item.id === viewingGapId);
      openCreateModalWithPrefill(gap && gap.ai_suggestion ? gap.ai_suggestion : '');
    });
    document.getElementById('ingestFileInput').addEventListener('change', async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        await uploadIngestFile(file);
      } catch (error) {
        alert(error.message || '업로드에 실패했습니다.');
      } finally {
        event.target.value = '';
      }
    });

    let searchTimeout;
    searchInput.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        fetchTopics(1);
      }, 400); 
    });
    domainFilter.addEventListener('change', () => fetchTopics(1));
    statusFilter.addEventListener('change', () => fetchTopics(1));
    descriptionInput.addEventListener('input', updateDescriptionCount);
    document.getElementById('topicContentInput').addEventListener('input', updateMarkdownPreview);
    document.querySelectorAll('[data-md-tab]').forEach(tab => {
      tab.addEventListener('click', () => setMarkdownMode(tab.dataset.mdTab));
    });
    document.querySelectorAll('[data-md-insert]').forEach(button => {
      button.addEventListener('click', () => insertMarkdownSnippet(button.dataset.mdInsert));
    });
    document.getElementById('relatedTopicSearch').addEventListener('input', renderRelatedSuggestions);

    document.addEventListener('DOMContentLoaded', async () => {
      updateDescriptionCount();
      updateMarkdownPreview();
      await Promise.all([fetchTopics(1), fetchConflicts()]);
    });
  </script>
</body>
</html>"""
