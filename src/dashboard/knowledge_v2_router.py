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
from fastapi.responses import HTMLResponse, RedirectResponse
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
    status: Optional[str] = None
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

@api_router.get("/topics")
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


@api_router.patch("/topics/{topic_id}")
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
    if body.status is not None:
        update_fields["status"] = body.status

    # related_topic_ids 는 반드시 relation_manager 경유
    if body.related_topic_ids is not None:
        await set_relations(topic_id, body.related_topic_ids, actor=user["email"])

    if update_fields:
        await boa_knowledge_collection.update_one(
            {"_id": topic_id}, {"$set": update_fields}
        )

    updated = await boa_knowledge_collection.find_one({"_id": topic_id})
    return _serialize(updated)


@api_router.get("/topics/{topic_id}/related")
async def get_related(topic_id: str, request: Request):
    """연관 주제 경량 조회 ({id, title, description, tags, domain})."""
    _require_user(request)

    try:
        return await get_related_topics(topic_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


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
    """Delete a topic after removing all bidirectional relations."""
    user = _require_user(request)
    actor = user["email"]

    doc = await boa_knowledge_collection.find_one(
        {"_id": topic_id},
        {"related_topic_ids": 1},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Topic not found")

    for rel_id in (doc.get("related_topic_ids") or []):
        await remove_relation(topic_id, rel_id, actor)

    await boa_knowledge_collection.delete_one({"_id": topic_id})
    return {"ok": True}


@api_router.get("/admin/consistency-check")
async def consistency_check(request: Request):
    """양방향 링크 정합성 체크 (admin only)."""
    await _require_admin(request)
    return await check_bidirectional_consistency()
router.include_router(api_router)


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")
    return HTMLResponse(KNOWLEDGE_HTML)


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
      --internal: #7c3aed; --general: #0891b2;
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
    .badge-domain-internal { background: rgba(124, 58, 237, 0.12); color: var(--internal); }
    .badge-domain-general { background: rgba(8, 145, 178, 0.12); color: var(--general); }
    .badge-domain-empty { background: rgba(148, 163, 184, 0.12); color: #64748b; }
    .badge-status-active { background: rgba(34, 197, 94, 0.12); color: #15803d; }
    .badge-status-draft { background: rgba(245, 158, 11, 0.14); color: #b45309; }
    .badge-status-archived { background: rgba(148, 163, 184, 0.16); color: #64748b; }

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

    @media (max-width: 860px) {
      .form-grid { grid-template-columns: 1fr; }
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
    </div>

    <section class="panel active" id="panel-topics">
      <div class="toolbar">
        <div class="toolbar-left">
          <input class="search-bar" id="searchInput" type="text" placeholder="토픽 제목 또는 설명 검색">
          <select class="filter-select" id="domainFilter">
            <option value="">전체 도메인</option>
            <option value="internal">internal</option>
            <option value="general">general</option>
          </select>
          <select class="filter-select" id="statusFilter">
            <option value="">전체 상태</option>
            <option value="draft">draft</option>
            <option value="active">active</option>
            <option value="archived">archived</option>
          </select>
        </div>
        <button class="btn-primary" id="createTopicBtn">+ 새 토픽</button>
      </div>

      <div id="topicsState" class="loading-state">토픽을 불러오는 중입니다...</div>

      <div class="table-wrap hidden" id="topicsTableWrap">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Domain</th>
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
      <div class="empty-state">
        <h3>아직 감지된 충돌이 없습니다.</h3>
        <p>P4에서 conflict 데이터가 연결되면 이 탭에서 pending, merged, rejected 상태를 확인할 수 있습니다.</p>
      </div>
    </section>

    <section class="panel" id="panel-gaps">
      <div class="empty-state">
        <h3>아직 기록된 지식 갭이 없습니다.</h3>
        <p>P5에서 gap recorder가 연결되면 이 탭에서 빈도 기반 갭 목록과 처리 상태를 확인할 수 있습니다.</p>
      </div>
    </section>
  </div>

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
          <textarea class="textarea content-field" id="topicContentInput" placeholder="운영 지식을 마크다운 또는 일반 텍스트로 입력합니다."></textarea>
        </div>
        <div class="field">
          <label for="topicTagsInput">태그</label>
          <input class="input" id="topicTagsInput" type="text" placeholder="예: esim, cancel, cs-operation" style="width:100%;">
          <div class="hint">쉼표로 구분해 입력합니다.</div>
        </div>
        <div class="form-grid" style="grid-template-columns: 1fr 1fr;">
          <div class="field">
            <label for="topicDomainInput">도메인</label>
            <select class="filter-select" id="topicDomainInput" style="width:100%;">
              <option value="">선택 안 함</option>
              <option value="internal">internal</option>
              <option value="general">general</option>
            </select>
          </div>
          <div class="field" id="topicStatusField" style="display:none;">
            <label for="topicStatusInput">상태</label>
            <select class="filter-select" id="topicStatusInput" style="width:100%;">
              <option value="draft">draft</option>
              <option value="active">active</option>
              <option value="archived">archived</option>
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
            <span id="detailStatusBadge"></span>
            <span id="detailTags" class="tag-list"></span>
          </div>
          <div class="detail-section">
            <h3>설명</h3>
            <div class="detail-text" id="detailDescription"></div>
          </div>
          <div class="detail-section">
            <h3>본문</h3>
            <div class="detail-text" id="detailBody"></div>
          </div>
          <div class="detail-section">
            <h3>관련 토픽</h3>
            <div class="detail-related" id="detailRelated"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let topics = [];
    let currentTab = 'topics';
    let editingTopicId = null;
    let viewingTopicId = null;
    let selectedRelatedTopicIds = [];

    const topicsState = document.getElementById('topicsState');
    const topicsTableWrap = document.getElementById('topicsTableWrap');
    const topicsTbody = document.getElementById('topicsTbody');
    const searchInput = document.getElementById('searchInput');
    const domainFilter = document.getElementById('domainFilter');
    const statusFilter = document.getElementById('statusFilter');
    const formModal = document.getElementById('formModal');
    const detailModal = document.getElementById('detailModal');
    const descriptionInput = document.getElementById('topicDescriptionInput');

    function escHtml(value) {
      return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function badgeHtml(kind, value) {
      const safe = escHtml(value || '-');
      if (kind === 'domain') {
        if (value === 'internal') return '<span class="badge badge-domain-internal">internal</span>';
        if (value === 'general') return '<span class="badge badge-domain-general">general</span>';
        return '<span class="badge badge-domain-empty">-</span>';
      }
      if (value === 'active') return '<span class="badge badge-status-active">active</span>';
      if (value === 'draft') return '<span class="badge badge-status-draft">draft</span>';
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
      const query = searchInput.value.trim().toLowerCase();
      const domain = domainFilter.value;
      const status = statusFilter.value;
      return topics.filter(topic => {
        if (domain && (topic.domain || '') !== domain) return false;
        if (status && (topic.status || '') !== status) return false;
        if (!query) return true;
        const haystack = `${topic.title || ''} ${topic.description || ''}`.toLowerCase();
        return haystack.includes(query);
      });
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
                <div class="topic-desc">${escHtml(topic.description || '')}</div>
              </button>
            </td>
            <td>${badgeHtml('domain', topic.domain)}</td>
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
      const response = await fetch(url, {
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(options.headers || {}),
        },
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

    async function fetchTopics() {
      setLoadingState('토픽을 불러오는 중입니다...');
      try {
        const data = await apiFetch('/api/knowledge/v2/topics');
        topics = Array.isArray(data) ? data : [];
        renderTopics();
      } catch (error) {
        setErrorState(error.message || '데이터를 불러오지 못했습니다.');
      }
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
    }

    function validateForm() {
      clearFieldErrors();
      let valid = true;
      ['topicTitleInput', 'topicDescriptionInput', 'topicContentInput'].forEach(id => {
        const el = document.getElementById(id);
        if (!el.value.trim()) {
          el.classList.add('field-error');
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
      document.getElementById('relatedTopicSearch').value = '';
      document.getElementById('relatedSuggestions').classList.remove('open');
      clearFieldErrors();
      updateDescriptionCount();
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
      document.getElementById('topicDomainInput').value = topic.domain || '';
      document.getElementById('topicStatusField').style.display = 'block';
      document.getElementById('topicStatusInput').value = topic.status || 'draft';
      selectedRelatedTopicIds = Array.isArray(topic.related_topic_ids) ? [...topic.related_topic_ids] : [];
      document.getElementById('relatedTopicSearch').value = '';
      clearFieldErrors();
      updateDescriptionCount();
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
        domain: document.getElementById('topicDomainInput').value || null,
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
        await fetchTopics();
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
        document.getElementById('detailSubtitle').textContent = `마지막 수정 ${formatDate(topic.updated_at)}`;
        document.getElementById('detailDomainBadge').innerHTML = badgeHtml('domain', topic.domain);
        document.getElementById('detailStatusBadge').innerHTML = badgeHtml('status', topic.status);
        document.getElementById('detailDescription').textContent = topic.description || '-';
        document.getElementById('detailBody').textContent = topic.content || '-';
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

    async function deleteTopic(topicId) {
      const topic = topics.find(item => item.id === topicId);
      const title = topic && topic.title ? topic.title : '이 토픽';
      const ok = window.confirm(`${title} 토픽을 삭제하시겠습니까? 관련 토픽 연결도 함께 제거됩니다.`);
      if (!ok) return;

      try {
        await apiFetch(`/api/knowledge/v2/topics/${topicId}`, { method: 'DELETE' });
        if (viewingTopicId === topicId) closeModal(detailModal);
        await fetchTopics();
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
      const openId = target.getAttribute('data-topic-open') || target.closest('[data-topic-open]')?.getAttribute('data-topic-open');
      if (openId) {
        await openDetailModal(openId);
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
      }
    });

    document.getElementById('createTopicBtn').addEventListener('click', openCreateModal);
    document.getElementById('saveTopicBtn').addEventListener('click', saveTopic);
    document.getElementById('editTopicBtn').addEventListener('click', async () => {
      if (viewingTopicId) {
        closeModal(detailModal);
        await openEditModal(viewingTopicId);
      }
    });
    document.getElementById('deleteTopicBtn').addEventListener('click', async () => {
      if (viewingTopicId) {
        await deleteTopic(viewingTopicId);
      }
    });

    searchInput.addEventListener('input', renderTopics);
    domainFilter.addEventListener('change', renderTopics);
    statusFilter.addEventListener('change', renderTopics);
    descriptionInput.addEventListener('input', updateDescriptionCount);
    document.getElementById('relatedTopicSearch').addEventListener('input', renderRelatedSuggestions);

    document.addEventListener('DOMContentLoaded', async () => {
      updateDescriptionCount();
      await fetchTopics();
    });
  </script>
</body>
</html>"""
