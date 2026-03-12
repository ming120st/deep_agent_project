"""
BOA 지식베이스 라우터 v2

서비스별 계층 구조, 버전 히스토리(diff), 문서 상태 관리,
유효기간, 담당자 지정, 문서 잠금, Gemini RAG 동기화,
Google Chat 변경 알림까지 통합된 위키형 지식베이스.

API 목록:
GET    /knowledge                           → HTML 지식베이스 페이지
GET    /api/knowledge/meta/services         → 서비스/카테고리 트리
GET    /api/knowledge                       → 문서 목록
GET    /api/knowledge/{doc_id}              → 문서 상세
POST   /api/knowledge                       → 문서 등록
PUT    /api/knowledge/{doc_id}              → 문서 수정 (버전 생성)
DELETE /api/knowledge/{doc_id}              → 문서 아카이브 (소프트 삭제)
GET    /api/knowledge/{doc_id}/history      → 버전 히스토리 목록
GET    /api/knowledge/{doc_id}/history/{v}  → 특정 버전 diff
POST   /api/knowledge/{doc_id}/lock         → 편집 잠금
DELETE /api/knowledge/{doc_id}/lock         → 잠금 해제
GET    /api/knowledge/search/rag            → RAG 검색 (Gemini)
"""

import uuid
import difflib
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.dashboard.auth import require_login, get_current_user
from src.core.mongo_client import (
    boa_knowledge_collection,
    boa_admins_collection,
    boa_members_collection,
    db,
)
from src.core.google_chat import send_message

router = APIRouter()

# ── 컬렉션 ────────────────────────────────────────────────────────────────────
boa_knowledge_versions_collection = db["boa_knowledge_versions"]

# ── 상수 ─────────────────────────────────────────────────────────────────────
LOCK_EXPIRE_MINUTES = 15

SERVICES = {
    "wifi":      {"label": "와이파이 도시락", "emoji": "📡",
                  "categories": ["이용방법", "요금/결제", "수령/반납", "장애/AS", "이벤트", "정책", "기타"]},
    "usim":      {"label": "유심",           "emoji": "💳",
                  "categories": ["이용방법", "요금/결제", "배송", "교환/환불", "이벤트", "정책", "기타"]},
    "esim":      {"label": "이심",           "emoji": "📲",
                  "categories": ["이용방법", "요금/결제", "설치/설정", "교환/환불", "이벤트", "정책", "기타"]},
    "insurance": {"label": "보험",           "emoji": "🛡️",
                  "categories": ["상품안내", "가입/해지", "보험금청구", "이벤트", "정책", "기타"]},
    "other":     {"label": "기타",           "emoji": "📁",
                  "categories": ["공지", "업무매뉴얼", "이벤트", "기타"]},
}

STATUSES = ["draft", "review", "published", "archived"]


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def serialize_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    for key in ("created_at", "updated_at", "locked_at", "synced_at",
                "last_reviewed_at", "valid_from", "valid_until"):
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()
    return doc


def serialize_version(v: dict) -> dict:
    v = dict(v)
    v.pop("_id", None)
    if isinstance(v.get("changed_at"), datetime):
        v["changed_at"] = v["changed_at"].isoformat()
    return v


async def is_admin(email: str) -> bool:
    return await boa_admins_collection.find_one({"email": email}) is not None


def is_lock_expired(doc: dict) -> bool:
    """잠금이 만료됐는지 확인"""
    if not doc.get("locked_by") or not doc.get("locked_at"):
        return True
    expire_at = doc["locked_at"] + timedelta(minutes=LOCK_EXPIRE_MINUTES)
    return datetime.utcnow() > expire_at


def compute_diff(old_text: str, new_text: str) -> list:
    """줄 단위 diff 계산 (difflib 기반)"""
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    result = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for line in old_lines[i1:i2]:
                result.append({"type": "unchanged", "text": line})
        elif tag == "replace":
            for line in old_lines[i1:i2]:
                result.append({"type": "delete", "text": line})
            for line in new_lines[j1:j2]:
                result.append({"type": "add", "text": line})
        elif tag == "delete":
            for line in old_lines[i1:i2]:
                result.append({"type": "delete", "text": line})
        elif tag == "insert":
            for line in new_lines[j1:j2]:
                result.append({"type": "add", "text": line})
    return result


async def get_owner_space(owner_id: str) -> Optional[str]:
    """담당자의 Google Chat DM space_name 조회"""
    member = await boa_members_collection.find_one({"user_id": owner_id})
    return member.get("space_name") if member else None


async def notify_change(doc: dict, changed_by: str, change_type: str = "updated"):
    """문서 변경 시 담당자에게 Google Chat 알림"""
    owner_id = doc.get("owner_id")
    if not owner_id or owner_id == changed_by:
        return
    space_name = await get_owner_space(owner_id)
    if not space_name:
        return
    service_label = SERVICES.get(doc.get("service", ""), {}).get("label", doc.get("service", ""))
    msg = (
        f"📝 *[지식베이스 변경 알림]*\n"
        f"담당 문서가 수정되었습니다.\n\n"
        f"• 문서: *{doc.get('title', '')}*\n"
        f"• 서비스: {service_label} > {doc.get('category', '')}\n"
        f"• 수정자: {changed_by}\n"
        f"• 상태: {doc.get('status', '')}"
    )
    await asyncio.to_thread(send_message, space_name, msg)


async def sync_to_gemini(doc_id: str):
    """
    Gemini FileSearchStore 동기화 (추후 구현)
    현재는 sync_status만 업데이트
    TODO: Gemini API 통합 후 실제 임베딩 업로드 구현
    """
    try:
        # TODO: Gemini FileSearchStore 연동
        # 1. 기존 file_search_doc_id 있으면 삭제
        # 2. 새 내용 업로드
        # 3. operation 완료 대기
        # 4. file_search_doc_id 업데이트

        await boa_knowledge_collection.update_one(
            {"doc_id": doc_id},
            {"$set": {"sync_status": "synced", "synced_at": datetime.utcnow()}}
        )
    except Exception as e:
        print(f"[Gemini Sync] 실패 doc_id={doc_id}: {e}")
        await boa_knowledge_collection.update_one(
            {"doc_id": doc_id},
            {"$set": {"sync_status": "failed"}}
        )


# ── API: 메타 ─────────────────────────────────────────────────────────────────

@router.get("/api/knowledge/meta/services")
async def api_get_services(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    return SERVICES


# ── API: 목록 ─────────────────────────────────────────────────────────────────

@router.get("/api/knowledge")
async def api_list_knowledge(
    request: Request,
    service: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    owner_id: Optional[str] = None,
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    query: dict = {}
    if service:
        query["service"] = service
    if category:
        query["category"] = category
    if status:
        query["status"] = status
    else:
        # 기본: archived 제외
        query["status"] = {"$ne": "archived"}
    if owner_id:
        query["owner_id"] = owner_id
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"content": {"$regex": search, "$options": "i"}},
            {"tags": {"$elemMatch": {"$regex": search, "$options": "i"}}},
        ]

    cursor = boa_knowledge_collection.find(query).sort("updated_at", -1)
    docs = await cursor.to_list(length=None)

    # 잠금 만료 자동 해제
    result = []
    for doc in docs:
        if doc.get("locked_by") and is_lock_expired(doc):
            doc["locked_by"] = None
            doc["locked_at"] = None
        result.append(serialize_doc(doc))
    return result


# ── API: 상세 ─────────────────────────────────────────────────────────────────

@router.get("/api/knowledge/{doc_id}")
async def api_get_knowledge(doc_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    doc = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    # 잠금 만료 자동 해제
    if doc.get("locked_by") and is_lock_expired(doc):
        await boa_knowledge_collection.update_one(
            {"doc_id": doc_id},
            {"$set": {"locked_by": None, "locked_at": None}}
        )
        doc["locked_by"] = None
        doc["locked_at"] = None

    return serialize_doc(doc)


# ── API: 생성 ─────────────────────────────────────────────────────────────────

class KnowledgeCreateRequest(BaseModel):
    title: str
    service: str
    category: str
    content: str
    status: str = "draft"
    tags: Optional[List[str]] = []
    owner_type: str = "user"
    owner_id: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    review_cycle_days: int = 90
    source: str = "manual"


@router.post("/api/knowledge")
async def api_create_knowledge(
    body: KnowledgeCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    if body.service not in SERVICES:
        raise HTTPException(status_code=400, detail=f"서비스는 {list(SERVICES.keys())} 중 하나여야 합니다")
    if body.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"상태는 {STATUSES} 중 하나여야 합니다")

    now = datetime.utcnow()
    doc_id = str(uuid.uuid4()).replace("-", "")[:16]

    doc = {
        "doc_id": doc_id,
        "title": body.title,
        "service": body.service,
        "category": body.category,
        "content": body.content,
        "status": body.status,
        "tags": body.tags or [],
        "source": body.source,
        "owner_type": body.owner_type,
        "owner_id": body.owner_id or user["email"],
        "valid_from": datetime.fromisoformat(body.valid_from) if body.valid_from else None,
        "valid_until": datetime.fromisoformat(body.valid_until) if body.valid_until else None,
        "review_cycle_days": body.review_cycle_days,
        "last_reviewed_at": now,
        "locked_by": None,
        "locked_at": None,
        "file_search_doc_id": None,
        "sync_status": "pending",
        "synced_at": None,
        "created_by": user["email"],
        "updated_by": user["email"],
        "created_at": now,
        "updated_at": now,
    }

    await boa_knowledge_collection.insert_one(doc)

    # 초기 버전 저장
    await boa_knowledge_versions_collection.insert_one({
        "doc_id": doc_id,
        "version": 1,
        "title_snapshot": body.title,
        "content_snapshot": body.content,
        "diff": [{"type": "add", "text": line} for line in body.content.splitlines()],
        "changed_by": user["email"],
        "changed_at": now,
        "change_summary": "문서 최초 등록",
    })

    # Gemini 동기화 (백그라운드)
    background_tasks.add_task(sync_to_gemini, doc_id)

    # FAISS 인덱스 업데이트 (백그라운드)
    from src.core.rag_engine import get_engine
    background_tasks.add_task(
        get_engine().upsert_doc,
        doc_id, body.title, body.content,
        body.service, body.category, body.status, body.tags or []
    )

    return serialize_doc(doc)


# ── API: 수정 ─────────────────────────────────────────────────────────────────

class KnowledgeUpdateRequest(BaseModel):
    title: Optional[str] = None
    service: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None
    owner_type: Optional[str] = None
    owner_id: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    review_cycle_days: Optional[int] = None
    change_summary: Optional[str] = None


@router.put("/api/knowledge/{doc_id}")
async def api_update_knowledge(
    doc_id: str,
    body: KnowledgeUpdateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    existing = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not existing:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    # 잠금 확인: 다른 사람이 잠금 중이면 거부
    if (existing.get("locked_by")
            and existing["locked_by"] != user["email"]
            and not is_lock_expired(existing)
            and not await is_admin(user["email"])):
        raise HTTPException(
            status_code=423,
            detail=f"'{existing['locked_by']}'님이 편집 중입니다. 잠시 후 다시 시도해주세요."
        )

    if body.status and body.status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"상태는 {STATUSES} 중 하나여야 합니다")

    now = datetime.utcnow()

    # 버전 계산
    last_version = await boa_knowledge_versions_collection.find_one(
        {"doc_id": doc_id}, sort=[("version", -1)]
    )
    next_version = (last_version["version"] + 1) if last_version else 1

    # diff 계산 (content 변경 시)
    old_content = existing.get("content", "")
    new_content = body.content if body.content is not None else old_content
    diff = compute_diff(old_content, new_content) if body.content is not None else []

    # 버전 저장
    await boa_knowledge_versions_collection.insert_one({
        "doc_id": doc_id,
        "version": next_version,
        "title_snapshot": body.title if body.title is not None else existing.get("title", ""),
        "content_snapshot": new_content,
        "diff": diff,
        "changed_by": user["email"],
        "changed_at": now,
        "change_summary": body.change_summary or f"버전 {next_version} 업데이트",
    })

    # 업데이트 필드 구성
    update: dict = {"updated_by": user["email"], "updated_at": now, "sync_status": "pending",
                    "locked_by": None, "locked_at": None}
    if body.title is not None:        update["title"] = body.title
    if body.service is not None:      update["service"] = body.service
    if body.category is not None:     update["category"] = body.category
    if body.content is not None:      update["content"] = body.content
    if body.status is not None:       update["status"] = body.status
    if body.tags is not None:         update["tags"] = body.tags
    if body.owner_type is not None:   update["owner_type"] = body.owner_type
    if body.owner_id is not None:     update["owner_id"] = body.owner_id
    if body.review_cycle_days is not None: update["review_cycle_days"] = body.review_cycle_days
    if body.valid_from is not None:
        update["valid_from"] = datetime.fromisoformat(body.valid_from) if body.valid_from else None
    if body.valid_until is not None:
        update["valid_until"] = datetime.fromisoformat(body.valid_until) if body.valid_until else None

    await boa_knowledge_collection.update_one({"doc_id": doc_id}, {"$set": update})
    updated = await boa_knowledge_collection.find_one({"doc_id": doc_id})

    # 알림 + Gemini 동기화 (백그라운드)
    background_tasks.add_task(notify_change, dict(updated), user["email"], "updated")
    background_tasks.add_task(sync_to_gemini, doc_id)

    # FAISS 인덱스 업데이트 (백그라운드)
    from src.core.rag_engine import get_engine
    background_tasks.add_task(
        get_engine().upsert_doc,
        doc_id,
        update.get("title", existing.get("title", "")),
        update.get("content", existing.get("content", "")),
        update.get("service", existing.get("service", "")),
        update.get("category", existing.get("category", "")),
        update.get("status", existing.get("status", "")),
        update.get("tags", existing.get("tags", [])),
    )

    return serialize_doc(updated)


# ── API: 검토 완료 ────────────────────────────────────────────────────────────

@router.post("/api/knowledge/{doc_id}/review")
async def api_mark_reviewed(doc_id: str, request: Request):
    """
    내용 수정 없이 '검토 완료' 처리.
    last_reviewed_at 갱신 + stale_notified_at 초기화 (재알림 타이머 리셋)
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    doc = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    now = datetime.utcnow()
    await boa_knowledge_collection.update_one(
        {"doc_id": doc_id},
        {"$set": {
            "last_reviewed_at": now,
            "stale_notified_at": None,   # 알림 타이머 리셋
            "reviewed_by": user["email"],
        }}
    )
    return {
        "ok": True,
        "last_reviewed_at": now.isoformat(),
        "reviewed_by": user["email"],
    }


# ── API: 아카이브 (소프트 삭제) ───────────────────────────────────────────────

@router.delete("/api/knowledge/{doc_id}")
async def api_archive_knowledge(doc_id: str, request: Request, background_tasks: BackgroundTasks):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    existing = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not existing:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    if existing.get("created_by") != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다")

    await boa_knowledge_collection.update_one(
        {"doc_id": doc_id},
        {"$set": {"status": "archived", "updated_by": user["email"], "updated_at": datetime.utcnow()}}
    )

    # FAISS 인덱스에서 제거 (백그라운드)
    from src.core.rag_engine import get_engine
    background_tasks.add_task(get_engine().remove_doc, doc_id)

    return {"ok": True}


# ── API: 버전 히스토리 ────────────────────────────────────────────────────────

@router.get("/api/knowledge/{doc_id}/history")
async def api_get_history(doc_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    cursor = boa_knowledge_versions_collection.find(
        {"doc_id": doc_id}, {"content_snapshot": 0}  # 목록에서는 snapshot 제외
    ).sort("version", -1)
    versions = await cursor.to_list(length=None)
    return [serialize_version(v) for v in versions]


@router.get("/api/knowledge/{doc_id}/history/{version}")
async def api_get_version(doc_id: str, version: int, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    v = await boa_knowledge_versions_collection.find_one(
        {"doc_id": doc_id, "version": version}
    )
    if not v:
        raise HTTPException(status_code=404, detail="버전을 찾을 수 없습니다")
    return serialize_version(v)


# ── API: 문서 잠금 ────────────────────────────────────────────────────────────

@router.post("/api/knowledge/{doc_id}/lock")
async def api_lock_document(doc_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    doc = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    # 이미 다른 사람이 잠금 중
    if (doc.get("locked_by")
            and doc["locked_by"] != user["email"]
            and not is_lock_expired(doc)):
        raise HTTPException(
            status_code=423,
            detail=f"'{doc['locked_by']}'님이 편집 중입니다."
        )

    now = datetime.utcnow()
    await boa_knowledge_collection.update_one(
        {"doc_id": doc_id},
        {"$set": {"locked_by": user["email"], "locked_at": now}}
    )
    expire_at = (now + timedelta(minutes=LOCK_EXPIRE_MINUTES)).isoformat()
    return {"locked_by": user["email"], "locked_at": now.isoformat(), "expire_at": expire_at}


@router.delete("/api/knowledge/{doc_id}/lock")
async def api_unlock_document(doc_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    doc = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    admin = await is_admin(user["email"])
    if doc.get("locked_by") != user["email"] and not admin:
        raise HTTPException(status_code=403, detail="잠금을 해제할 권한이 없습니다")

    await boa_knowledge_collection.update_one(
        {"doc_id": doc_id},
        {"$set": {"locked_by": None, "locked_at": None}}
    )
    return {"ok": True}


# ── API: RAG 검색 ─────────────────────────────────────────────────────────────

@router.get("/api/knowledge/search/rag")
async def api_rag_search(
    request: Request,
    q: str = "",
    service: Optional[str] = None,
    use_hyde: bool = True,
    use_rerank: bool = True,
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    if not q.strip():
        return {"results": [], "pipeline": "empty", "query": q}

    from src.core.rag_engine import search_knowledge
    return await search_knowledge(
        query=q,
        top_k=5,
        service=service,
        use_hyde=use_hyde,
        use_rerank=use_rerank,
    )


# ── HTML ───────────────────────────────────────────────────────────────────────

KNOWLEDGE_HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOA 지식베이스</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0d0f12; --bg2: #13161b; --bg3: #1a1e25; --bg4: #1f242c;
  --border: #252931; --border2: #2e3540;
  --text: #c8d0db; --text-dim: #5a6272; --text-bright: #eef1f6;
  --accent: #3b82f6; --accent-dim: #1d3d6e; --accent-hover: #2563eb;
  --green: #22c55e; --green-dim: #14532d;
  --yellow: #eab308; --yellow-dim: #713f12;
  --red: #ef4444; --red-dim: #7f1d1d;
  --purple: #a855f7; --purple-dim: #3b1d6e;
  --teal: #14b8a6; --teal-dim: #134e4a;
  --orange: #f97316; --orange-dim: #7c2d12;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--bg); color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 14px; min-height: 100vh; display: flex; flex-direction: column; overflow: hidden; height: 100vh; }

/* ── 헤더 ── */
header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 0 20px; height: 52px; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; z-index: 100; }
.logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 15px; color: var(--text-bright); letter-spacing: .05em; }
.logo span { color: var(--accent); }
.header-nav { display: flex; align-items: center; gap: 2px; }
.nav-link { padding: 5px 12px; border-radius: 5px; font-size: 13px; color: var(--text-dim); text-decoration: none; transition: all .15s; }
.nav-link:hover { color: var(--text); background: var(--bg3); }
.nav-link.active { color: var(--accent); background: var(--accent-dim); }
.header-right { display: flex; align-items: center; gap: 12px; }
.user-info { display: flex; align-items: center; gap: 8px; }
.user-avatar { width: 26px; height: 26px; border-radius: 50%; border: 1px solid var(--border); }
.user-name { font-size: 13px; }
.admin-badge { background: var(--accent-dim); color: #60a5fa; font-family: 'IBM Plex Mono', monospace; font-size: 10px; padding: 2px 7px; border-radius: 4px; border: 1px solid var(--accent); }
.btn-logout { background: none; border: 1px solid var(--border); border-radius: 5px; padding: 4px 10px; color: var(--text-dim); font-size: 12px; cursor: pointer; transition: all .15s; }
.btn-logout:hover { border-color: var(--red); color: var(--red); }

/* ── 레이아웃 ── */
.app-body { display: flex; flex: 1; overflow: hidden; }

/* ── 사이드바 ── */
.sidebar { width: 220px; background: var(--bg2); border-right: 1px solid var(--border); overflow-y: auto; flex-shrink: 0; padding: 12px 0; }
.sidebar-section { margin-bottom: 4px; }
.service-btn { width: 100%; display: flex; align-items: center; gap: 8px; padding: 7px 14px; background: none; border: none; color: var(--text-dim); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; cursor: pointer; transition: all .15s; text-align: left; }
.service-btn:hover { color: var(--text); background: var(--bg3); }
.service-btn.active { color: var(--accent); background: var(--accent-dim); }
.service-btn .svc-emoji { font-size: 14px; width: 18px; text-align: center; }
.service-btn .svc-label { flex: 1; }
.service-btn .svc-count { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--text-dim); background: var(--bg3); padding: 1px 6px; border-radius: 3px; }
.service-btn .chevron { font-size: 10px; color: var(--text-dim); transition: transform .2s; }
.service-btn.expanded .chevron { transform: rotate(90deg); }

.cat-list { overflow: hidden; max-height: 0; transition: max-height .25s ease; }
.cat-list.open { max-height: 400px; }
.cat-btn { width: 100%; display: flex; align-items: center; justify-content: space-between; padding: 5px 14px 5px 36px; background: none; border: none; color: var(--text-dim); font-size: 12px; cursor: pointer; transition: all .15s; text-align: left; font-family: 'IBM Plex Sans KR', sans-serif; }
.cat-btn:hover { color: var(--text); }
.cat-btn.active { color: var(--accent); }
.cat-btn .cat-count { font-family: 'IBM Plex Mono', monospace; font-size: 10px; }

.sidebar-all { padding: 7px 14px; color: var(--text-dim); font-size: 13px; cursor: pointer; display: flex; align-items: center; gap: 8px; transition: all .15s; }
.sidebar-all:hover { color: var(--text); }
.sidebar-all.active { color: var(--accent); }
.sidebar-divider { border: none; border-top: 1px solid var(--border); margin: 8px 12px; }

/* ── 메인 영역 ── */
.main-area { flex: 1; overflow: hidden; display: flex; flex-direction: column; }

/* ── 검색바 + 액션 ── */
.top-bar { padding: 12px 20px; background: var(--bg2); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; flex-shrink: 0; position: relative; }
.search-wrap { flex: 1; position: relative; }
.search-input { width: 100%; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 8px 14px 8px 36px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; transition: border-color .15s; }
.search-input:focus { border-color: var(--accent); }
.search-input::placeholder { color: var(--text-dim); }
.search-icon { position: absolute; left: 11px; top: 50%; transform: translateY(-50%); color: var(--text-dim); font-size: 14px; pointer-events: none; }
.rag-badge { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); font-family: 'IBM Plex Mono', monospace; font-size: 10px; background: var(--purple-dim); color: #c084fc; padding: 2px 7px; border-radius: 3px; border: 1px solid rgba(168,85,247,.3); display: none; }
.rag-badge.visible { display: block; }

/* 검색 드롭다운 */
.search-dropdown { position: absolute; top: calc(100% + 2px); left: 0; right: 0; background: var(--bg2); border: 1px solid var(--border2); border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,.5); z-index: 300; max-height: 380px; overflow-y: auto; display: none; }
.search-dropdown.open { display: block; }
.search-result-item { padding: 10px 14px; cursor: pointer; transition: background .1s; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 4px; }
.search-result-item:last-child { border-bottom: none; }
.search-result-item:hover { background: var(--bg3); }
.sri-header { display: flex; align-items: center; gap: 8px; }
.sri-title { font-size: 13px; font-weight: 500; color: var(--text-bright); flex: 1; }
.sri-preview { font-size: 11px; color: var(--text-dim); line-height: 1.5; }
.search-empty { padding: 20px; text-align: center; color: var(--text-dim); font-size: 12px; font-family: 'IBM Plex Mono', monospace; }

.status-filter { display: flex; gap: 4px; }
.sf-btn { padding: 5px 11px; border-radius: 5px; font-size: 11px; font-family: 'IBM Plex Mono', monospace; border: 1px solid var(--border); background: var(--bg3); color: var(--text-dim); cursor: pointer; transition: all .15s; }
.sf-btn:hover { border-color: var(--accent); color: var(--text); }
.sf-btn.active { border-color: var(--accent); background: var(--accent-dim); color: #60a5fa; }
.btn-new { background: var(--accent); border: none; border-radius: 6px; padding: 7px 16px; color: white; font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; font-weight: 500; cursor: pointer; transition: opacity .15s; white-space: nowrap; }
.btn-new:hover { background: var(--accent-hover); }

/* ── 문서 목록 ── */
.doc-list-area { flex: 1; overflow-y: auto; padding: 16px 20px; }
.list-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.list-title { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .08em; }
.list-count { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-dim); }
.doc-table { width: 100%; border-collapse: collapse; }
.doc-table th { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .08em; padding: 6px 12px; border-bottom: 1px solid var(--border); text-align: left; font-weight: 500; }
.doc-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
.doc-table tr.doc-row { cursor: pointer; transition: background .1s; }
.doc-table tr.doc-row:hover { background: var(--bg3); }
.doc-table tr.doc-row:last-child td { border-bottom: none; }
.col-title { color: var(--text-bright); font-size: 13px; font-weight: 500; }
.col-meta { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-dim); }
.col-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag { background: var(--bg3); border: 1px solid var(--border); border-radius: 3px; padding: 1px 7px; font-size: 10px; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; }

/* 상태 뱃지 */
.status-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 4px; font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 500; border: 1px solid; white-space: nowrap; }
.status-draft     { background: rgba(90,98,114,.15); color: #8a96a8; border-color: rgba(90,98,114,.3); }
.status-review    { background: rgba(234,179,8,.12); color: #fbbf24; border-color: rgba(234,179,8,.3); }
.status-published { background: rgba(34,197,94,.12); color: #4ade80; border-color: rgba(34,197,94,.3); }
.status-archived  { background: rgba(239,68,68,.1);  color: #f87171; border-color: rgba(239,68,68,.25); }

/* 서비스 뱃지 */
.svc-badge { display: inline-block; padding: 2px 7px; border-radius: 3px; font-family: 'IBM Plex Mono', monospace; font-size: 10px; border: 1px solid; }
.svc-wifi      { background: rgba(59,130,246,.12); color: #60a5fa; border-color: rgba(59,130,246,.3); }
.svc-usim      { background: rgba(168,85,247,.12); color: #c084fc; border-color: rgba(168,85,247,.3); }
.svc-esim      { background: rgba(20,184,166,.12); color: #2dd4bf; border-color: rgba(20,184,166,.3); }
.svc-insurance { background: rgba(34,197,94,.12);  color: #4ade80; border-color: rgba(34,197,94,.3); }
.svc-other     { background: rgba(107,114,128,.12);color: #9ca3af; border-color: rgba(107,114,128,.3); }

/* 소스 뱃지 */
.src-badge { display: inline-flex; align-items: center; gap: 3px; padding: 1px 6px; border-radius: 3px; font-family: 'IBM Plex Mono', monospace; font-size: 10px; }
.src-auto { background: rgba(249,115,22,.1); color: #fb923c; border: 1px solid rgba(249,115,22,.3); }

/* 동기화 상태 */
.sync-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; }
.sync-pending { background: var(--accent); animation: pulse 1.5s infinite; }
.sync-synced  { background: var(--green); }
.sync-failed  { background: var(--red); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

/* 잠금 아이콘 */
.lock-icon { font-size: 12px; }

/* 유효기간 경고 */
.expiry-warn { color: var(--yellow); font-size: 10px; font-family: 'IBM Plex Mono', monospace; }
.expiry-expired { color: var(--red); }

/* 빈 상태 */
.empty-state { padding: 80px 20px; text-align: center; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; font-size: 13px; }
.empty-icon { font-size: 36px; margin-bottom: 12px; }
.loading-state { padding: 60px 20px; text-align: center; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; font-size: 12px; }

/* ── 문서 상세 패널 ── */
.detail-panel { position: absolute; inset: 0; background: var(--bg); z-index: 50; display: none; flex-direction: column; overflow: hidden; }
.detail-panel.open { display: flex; }
.detail-header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 14px 20px; display: flex; align-items: flex-start; gap: 12px; flex-shrink: 0; }
.btn-back { background: none; border: 1px solid var(--border); border-radius: 5px; padding: 5px 10px; color: var(--text-dim); font-size: 12px; cursor: pointer; transition: all .15s; white-space: nowrap; }
.btn-back:hover { border-color: var(--accent); color: var(--accent); }
.detail-header-info { flex: 1; }
.detail-title { font-size: 17px; font-weight: 600; color: var(--text-bright); line-height: 1.4; margin-bottom: 8px; }
.detail-badges { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.detail-actions { display: flex; gap: 6px; align-items: flex-start; flex-shrink: 0; }

.detail-body { display: flex; flex: 1; overflow: hidden; }
.detail-content-area { flex: 1; overflow-y: auto; padding: 20px 24px; }
.detail-meta-panel { width: 220px; border-left: 1px solid var(--border); background: var(--bg2); padding: 16px; overflow-y: auto; flex-shrink: 0; }
.meta-section { margin-bottom: 20px; }
.meta-label { font-family: 'IBM Plex Mono', monospace; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--text-dim); margin-bottom: 6px; }
.meta-value { font-size: 12px; color: var(--text); line-height: 1.6; }
.meta-value a { color: var(--accent); text-decoration: none; }

.detail-content { background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 18px; font-size: 13px; line-height: 1.9; color: var(--text); white-space: pre-wrap; word-break: break-word; }
.lock-notice { background: rgba(234,179,8,.08); border: 1px solid rgba(234,179,8,.25); border-radius: 6px; padding: 8px 14px; font-size: 12px; color: var(--yellow); margin-bottom: 12px; display: flex; align-items: center; gap: 6px; }

/* ── 편집 패널 ── */
.edit-panel { position: absolute; inset: 0; background: var(--bg); z-index: 60; display: none; flex-direction: column; overflow: hidden; }
.edit-panel.open { display: flex; }
.edit-header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.edit-title-label { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--text-dim); }
.edit-body { flex: 1; overflow-y: auto; padding: 20px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 14px; }
.form-group { display: flex; flex-direction: column; gap: 5px; }
.form-group.full { grid-column: 1 / -1; }
.form-label { font-family: 'IBM Plex Mono', monospace; font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--text-dim); }
.form-label .required { color: var(--red); margin-left: 3px; }
.form-input, .form-select { background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; padding: 8px 11px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; transition: border-color .15s; }
.form-input:focus, .form-select:focus { border-color: var(--accent); }
.form-input::placeholder { color: var(--text-dim); }
.form-textarea { background: var(--bg3); border: 1px solid var(--border); border-radius: 5px; padding: 10px 12px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; resize: none; line-height: 1.8; transition: border-color .15s; width: 100%; }
.form-textarea:focus { border-color: var(--accent); }
.form-hint { font-size: 11px; color: var(--text-dim); }
.edit-footer { background: var(--bg2); border-top: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; justify-content: space-between; flex-shrink: 0; }
.edit-footer-left { display: flex; gap: 6px; }

/* ── 히스토리 모달 ── */
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 200; backdrop-filter: blur(2px); align-items: center; justify-content: center; }
.modal-overlay.open { display: flex; }
.modal { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; width: 800px; max-width: 95vw; max-height: 86vh; display: flex; flex-direction: column; animation: slideUp .2s ease; }
@keyframes slideUp { from{transform:translateY(12px);opacity:0} to{transform:translateY(0);opacity:1} }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.modal-title { font-size: 14px; font-weight: 600; color: var(--text-bright); }
.modal-close { background: none; border: none; color: var(--text-dim); font-size: 18px; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
.modal-close:hover { background: var(--bg3); color: var(--text); }
.modal-body { display: flex; flex: 1; overflow: hidden; }

.history-list { width: 240px; border-right: 1px solid var(--border); overflow-y: auto; flex-shrink: 0; }
.history-item { padding: 10px 14px; cursor: pointer; transition: background .1s; border-bottom: 1px solid var(--border); }
.history-item:hover { background: var(--bg3); }
.history-item.active { background: var(--accent-dim); }
.hi-version { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--accent); margin-bottom: 3px; }
.hi-summary { font-size: 12px; color: var(--text); margin-bottom: 4px; line-height: 1.4; }
.hi-meta { font-family: 'IBM Plex Mono', monospace; font-size: 10px; color: var(--text-dim); }

.diff-view { flex: 1; overflow-y: auto; padding: 14px; font-family: 'IBM Plex Mono', monospace; font-size: 12px; line-height: 1.7; }
.diff-line { padding: 1px 10px; display: flex; gap: 10px; }
.diff-line.add    { background: rgba(34,197,94,.08); }
.diff-line.delete { background: rgba(239,68,68,.08); }
.diff-line .dl-sign { width: 12px; flex-shrink: 0; }
.diff-line.add    .dl-sign { color: var(--green); }
.diff-line.delete .dl-sign { color: var(--red); }
.diff-line .dl-text { color: var(--text); white-space: pre-wrap; word-break: break-all; }
.diff-line.add    .dl-text { color: #86efac; }
.diff-line.delete .dl-text { color: #fca5a5; }
.diff-separator { text-align: center; color: var(--text-dim); font-size: 11px; padding: 4px; border-top: 1px dashed var(--border); border-bottom: 1px dashed var(--border); margin: 2px 0; }
.diff-empty { padding: 40px; text-align: center; color: var(--text-dim); }

/* ── 버튼 ── */
.btn { border: none; border-radius: 5px; padding: 7px 16px; font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; font-weight: 500; cursor: pointer; transition: all .15s; display: inline-flex; align-items: center; gap: 5px; }
.btn-primary   { background: var(--accent); color: white; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { background: var(--bg3); border: 1px solid var(--border); color: var(--text); }
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); }
.btn-ghost     { background: none; border: 1px solid var(--border); color: var(--text-dim); }
.btn-ghost:hover { border-color: var(--border2); color: var(--text); background: var(--bg3); }
.btn-danger    { background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.3); color: var(--red); }
.btn-danger:hover { background: rgba(239,68,68,.2); }
.btn-sm { padding: 5px 11px; font-size: 12px; }

/* ── 스크롤바 ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
</style>
</head>
<body>

<header>
  <div class="logo">BOA <span>//</span> 지식베이스</div>
  <nav class="header-nav">
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link active">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link">구성원</a>
  </nav>
  <div class="header-right">
    <div class="user-info">
      <img class="user-avatar" id="user-avatar" src="" alt="">
      <span class="user-name" id="user-name">로딩중...</span>
      <span class="admin-badge" id="admin-badge" style="display:none">ADMIN</span>
    </div>
    <button class="btn-logout" onclick="location.href='/auth/logout'">로그아웃</button>
  </div>
</header>

<div class="app-body">

  <!-- 사이드바 -->
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-all active" id="all-btn" onclick="selectAll()">
      <span>📋</span> 전체 문서
    </div>
    <hr class="sidebar-divider">
    <div id="service-tree"></div>
  </aside>

  <!-- 메인 -->
  <div class="main-area" style="position:relative;">

    <!-- 상단 검색/액션바 -->
    <div class="top-bar">
      <div class="search-wrap" id="search-wrap">
        <span class="search-icon">🔍</span>
        <input class="search-input" id="search-input" placeholder="검색어 입력... (AI 시맨틱 검색)" oninput="onSearchInput()" onfocus="onSearchFocus()" onblur="onSearchBlur()">
        <span class="rag-badge" id="rag-badge">AI 검색 중...</span>
        <div class="search-dropdown" id="search-dropdown"></div>
      </div>
      <div class="status-filter" id="status-filter">
        <button class="sf-btn active" onclick="setStatusFilter(null, this)">전체</button>
        <button class="sf-btn" onclick="setStatusFilter('draft', this)">초안</button>
        <button class="sf-btn" onclick="setStatusFilter('review', this)">검토중</button>
        <button class="sf-btn" onclick="setStatusFilter('published', this)">게시됨</button>
        <button class="sf-btn" onclick="setStatusFilter('archived', this)">아카이브</button>
      </div>
      <button class="btn-new" onclick="openNewDoc()">+ 새 문서</button>
    </div>

    <!-- 문서 목록 -->
    <div class="doc-list-area" id="doc-list-area">
      <div class="loading-state">로딩 중...</div>
    </div>

    <!-- 문서 상세 패널 -->
    <div class="detail-panel" id="detail-panel">
      <div class="detail-header">
        <button class="btn-back btn btn-ghost btn-sm" onclick="closeDetail()">← 목록</button>
        <div class="detail-header-info">
          <div class="detail-title" id="dp-title">—</div>
          <div class="detail-badges" id="dp-badges"></div>
        </div>
        <div class="detail-actions" id="dp-actions"></div>
      </div>
      <div class="detail-body">
        <div class="detail-content-area" id="dp-content-area"></div>
        <div class="detail-meta-panel" id="dp-meta-panel"></div>
      </div>
    </div>

    <!-- 편집 패널 -->
    <div class="edit-panel" id="edit-panel">
      <div class="edit-header">
        <button class="btn btn-ghost btn-sm" onclick="cancelEdit()">← 취소</button>
        <span class="edit-title-label" id="edit-panel-label">새 문서 등록</span>
      </div>
      <div class="edit-body">
        <div class="form-grid">
          <div class="form-group full">
            <label class="form-label">제목 <span class="required">*</span></label>
            <input class="form-input" id="f-title" placeholder="문서 제목을 입력하세요">
          </div>
          <div class="form-group">
            <label class="form-label">서비스 <span class="required">*</span></label>
            <select class="form-select" id="f-service" onchange="updateCategoryOptions()">
              <option value="wifi">📡 와이파이 도시락</option>
              <option value="usim">💳 유심</option>
              <option value="esim">📲 이심</option>
              <option value="insurance">🛡️ 보험</option>
              <option value="other">📁 기타</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">카테고리 <span class="required">*</span></label>
            <select class="form-select" id="f-category"></select>
          </div>
          <div class="form-group">
            <label class="form-label">상태</label>
            <select class="form-select" id="f-status">
              <option value="draft">초안</option>
              <option value="review">검토중</option>
              <option value="published">게시됨</option>
              <option value="archived">아카이브</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">담당자 이메일</label>
            <input class="form-input" id="f-owner" placeholder="owner@widemobile.com">
          </div>
          <div class="form-group">
            <label class="form-label">유효기간 시작</label>
            <input class="form-input" id="f-valid-from" type="date">
          </div>
          <div class="form-group">
            <label class="form-label">유효기간 종료 <span style="font-size:10px;color:var(--text-dim)">(없으면 상시)</span></label>
            <input class="form-input" id="f-valid-until" type="date">
          </div>
          <div class="form-group">
            <label class="form-label">검토 주기 (일)</label>
            <input class="form-input" id="f-review-cycle" type="number" value="90" min="7" max="365">
          </div>
          <div class="form-group">
            <label class="form-label">태그</label>
            <input class="form-input" id="f-tags" placeholder="쉼표로 구분 (예: 환불, 정책)">
            <div class="form-hint">쉼표(,)로 여러 태그 입력</div>
          </div>
          <div class="form-group full">
            <label class="form-label">변경 요약</label>
            <input class="form-input" id="f-change-summary" placeholder="이번 수정에서 변경한 내용을 간단히 적어주세요">
          </div>
          <div class="form-group full">
            <label class="form-label">내용 <span class="required">*</span></label>
            <textarea class="form-textarea" id="f-content" style="height:360px;" placeholder="문서 내용을 입력하세요...&#10;&#10;마크다운 형식을 지원합니다."></textarea>
          </div>
        </div>
      </div>
      <div class="edit-footer">
        <div class="edit-footer-left">
          <button class="btn btn-danger btn-sm" id="archive-btn" onclick="archiveCurrentDoc()" style="display:none">아카이브</button>
        </div>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-secondary btn-sm" onclick="cancelEdit()">취소</button>
          <button class="btn btn-primary btn-sm" onclick="submitEdit()">💾 저장</button>
        </div>
      </div>
    </div>

  </div><!-- /main-area -->
</div><!-- /app-body -->

<!-- 히스토리 모달 -->
<div class="modal-overlay" id="history-overlay" onclick="closeHistoryOutside(event)">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title">📜 버전 히스토리</div>
      <button class="modal-close" onclick="closeHistory()">×</button>
    </div>
    <div class="modal-body">
      <div class="history-list" id="history-list"></div>
      <div class="diff-view" id="diff-view"><div class="diff-empty">버전을 선택하세요</div></div>
    </div>
  </div>
</div>

<script>
// ── 전역 상태 ──────────────────────────────────────────────────────────────
let currentUser = null;
let services = {};
let allDocs = [];
let currentDoc = null;
let editingDocId = null;

let filterService = null;
let filterCategory = null;
let filterStatus = null;

let searchTimer = null;
let searchActive = false;

// ── 초기화 ─────────────────────────────────────────────────────────────────
async function init() {
  await Promise.all([loadMe(), loadServices()]);
  await loadDocs();
}

async function loadMe() {
  const res = await fetch('/api/me');
  currentUser = await res.json();
  document.getElementById('user-name').textContent = currentUser.name || currentUser.email;
  document.getElementById('user-avatar').src = currentUser.picture || '';
  if (currentUser.is_admin) document.getElementById('admin-badge').style.display = 'inline-block';
}

async function loadServices() {
  const res = await fetch('/api/knowledge/meta/services');
  services = await res.json();
  renderSidebar();
}

// ── 사이드바 ───────────────────────────────────────────────────────────────
function renderSidebar() {
  const tree = document.getElementById('service-tree');
  tree.innerHTML = Object.entries(services).map(([key, svc]) => `
    <div class="sidebar-section">
      <button class="service-btn" id="svc-btn-${key}" onclick="toggleService('${key}', event)">
        <span class="svc-emoji">${svc.emoji}</span>
        <span class="svc-label">${svc.label}</span>
        <span class="svc-count" id="cnt-${key}">0</span>
        <span class="chevron">▶</span>
      </button>
      <div class="cat-list" id="cats-${key}">
        ${svc.categories.map(cat => `
          <button class="cat-btn" id="catbtn-${key}-${encodeURIComponent(cat)}"
            onclick="selectCategory('${key}', '${cat}', event)">
            <span>${cat}</span>
            <span class="cat-count" id="catcnt-${key}-${encodeURIComponent(cat)}"></span>
          </button>
        `).join('')}
      </div>
    </div>
  `).join('');
}

function toggleService(key, e) {
  e.stopPropagation();
  const catList = document.getElementById('cats-' + key);
  const btn = document.getElementById('svc-btn-' + key);
  const isOpen = catList.classList.contains('open');

  if (!isOpen) {
    // 서비스 선택
    selectServiceOnly(key);
    catList.classList.add('open');
    btn.classList.add('expanded');
  } else {
    catList.classList.remove('open');
    btn.classList.remove('expanded');
  }
}

function selectServiceOnly(key) {
  filterService = key;
  filterCategory = null;
  updateSidebarActive();
  loadDocs();
}

function selectCategory(key, cat, e) {
  e.stopPropagation();
  filterService = key;
  filterCategory = cat;
  updateSidebarActive();
  loadDocs();
}

function selectAll() {
  filterService = null;
  filterCategory = null;
  updateSidebarActive();
  loadDocs();
}

function updateSidebarActive() {
  document.getElementById('all-btn').classList.toggle('active', !filterService);
  Object.keys(services).forEach(key => {
    const btn = document.getElementById('svc-btn-' + key);
    if (btn) btn.classList.toggle('active', filterService === key && !filterCategory);
    services[key].categories.forEach(cat => {
      const catBtn = document.getElementById('catbtn-' + key + '-' + encodeURIComponent(cat));
      if (catBtn) catBtn.classList.toggle('active', filterService === key && filterCategory === cat);
    });
  });
}

function updateSidebarCounts() {
  const counts = {};
  const catCounts = {};
  allDocs.forEach(doc => {
    counts[doc.service] = (counts[doc.service] || 0) + 1;
    const ck = doc.service + '||' + doc.category;
    catCounts[ck] = (catCounts[ck] || 0) + 1;
  });
  Object.keys(services).forEach(key => {
    const el = document.getElementById('cnt-' + key);
    if (el) el.textContent = counts[key] || 0;
    services[key].categories.forEach(cat => {
      const el2 = document.getElementById('catcnt-' + key + '-' + encodeURIComponent(cat));
      if (el2) el2.textContent = catCounts[key + '||' + cat] || '';
    });
  });
}

// ── 문서 목록 로드 ─────────────────────────────────────────────────────────
async function loadDocs() {
  const params = new URLSearchParams();
  if (filterService) params.set('service', filterService);
  if (filterCategory) params.set('category', filterCategory);
  if (filterStatus) params.set('status', filterStatus);
  else params.set('status', filterStatus || '');

  // status 파라미터 처리
  if (!filterStatus) {
    // archived 제외는 서버에서 기본 처리
  }

  const search = document.getElementById('search-input').value.trim();
  if (search) params.set('search', search);

  const url = '/api/knowledge?' + params.toString();
  const area = document.getElementById('doc-list-area');
  area.innerHTML = '<div class="loading-state">로딩 중...</div>';

  try {
    const res = await fetch(url);
    allDocs = await res.json();
    updateSidebarCounts();
    renderDocList();
  } catch(e) {
    area.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div>로드 실패</div>';
  }
}

function setStatusFilter(status, el) {
  filterStatus = status;
  document.querySelectorAll('.sf-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  loadDocs();
}

// ── 문서 목록 렌더 ─────────────────────────────────────────────────────────
function renderDocList() {
  const area = document.getElementById('doc-list-area');
  if (!allDocs.length) {
    area.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div>등록된 문서가 없습니다</div>';
    return;
  }

  const now = new Date();
  const rows = allDocs.map(doc => {
    const svc = services[doc.service] || {};
    const svcLabel = svc.label || doc.service;

    // 유효기간 표시
    let expiryHtml = '';
    if (doc.valid_until) {
      const until = new Date(doc.valid_until);
      const daysLeft = Math.ceil((until - now) / (1000 * 86400));
      if (daysLeft < 0) {
        expiryHtml = `<span class="expiry-warn expiry-expired">만료됨</span>`;
      } else if (daysLeft <= 7) {
        expiryHtml = `<span class="expiry-warn">D-${daysLeft}</span>`;
      }
    }

    // 잠금 표시
    let lockHtml = '';
    if (doc.locked_by) {
      const isSelf = doc.locked_by === currentUser?.email;
      const editorName = isSelf ? '내가' : (doc.locked_by.split('@')[0] + '님이');
      const lockColor = isSelf ? 'color:#60a5fa' : 'color:#fbbf24';
      lockHtml = `<span style="font-size:11px;font-family:'IBM Plex Mono',monospace;${lockColor};margin-left:6px" title="${doc.locked_by}">🔒 ${editorName} 편집중</span>`;
    }

    // 동기화 상태
    const syncClass = doc.sync_status === 'synced' ? 'sync-synced' : doc.sync_status === 'failed' ? 'sync-failed' : 'sync-pending';
    const syncTitle = doc.sync_status === 'synced' ? 'AI 검색 반영됨' : doc.sync_status === 'failed' ? 'AI 동기화 실패' : 'AI 동기화 중...';

    return `
      <tr class="doc-row" onclick="openDetail('${doc.doc_id}')">
        <td>
          <div class="col-title">${escapeHtml(doc.title)} ${lockHtml}</div>
          ${doc.tags?.length ? `<div class="col-tags" style="margin-top:4px">${doc.tags.slice(0,3).map(t=>`<span class="tag">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
        </td>
        <td><span class="svc-badge svc-${doc.service}">${svcLabel}</span><br><span class="col-meta" style="margin-top:3px;display:block">${escapeHtml(doc.category)}</span></td>
        <td><span class="status-badge status-${doc.status}">${statusLabel(doc.status)}</span></td>
        <td class="col-meta">${doc.owner_id ? doc.owner_id.split('@')[0] : '—'}</td>
        <td class="col-meta">
          ${expiryHtml || (doc.valid_until ? `<span style="font-size:10px">${fmtDate(doc.valid_until)}</span>` : '<span style="color:var(--text-dim)">상시</span>')}
        </td>
        <td class="col-meta">
          <span class="sync-dot ${syncClass}" title="${syncTitle}" style="margin-right:4px"></span>
          ${fmtDateTime(doc.updated_at)}<br>${doc.updated_by?.split('@')[0] || ''}
        </td>
      </tr>
    `;
  }).join('');

  area.innerHTML = `
    <div class="list-header">
      <span class="list-title">${getTitleLabel()}</span>
      <span class="list-count">${allDocs.length}개 문서</span>
    </div>
    <table class="doc-table">
      <thead>
        <tr>
          <th>제목 / 태그</th>
          <th>서비스 / 카테고리</th>
          <th>상태</th>
          <th>담당자</th>
          <th>유효기간</th>
          <th>최종 수정</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function getTitleLabel() {
  if (filterCategory) return filterCategory;
  if (filterService) return (services[filterService] || {}).label || filterService;
  return '전체 문서';
}

function statusLabel(s) {
  return {draft:'초안', review:'검토중', published:'게시됨', archived:'아카이브'}[s] || s;
}

// ── 문서 상세 ──────────────────────────────────────────────────────────────
async function openDetail(docId) {
  const res = await fetch(`/api/knowledge/${docId}`);
  if (!res.ok) return;
  currentDoc = await res.json();
  renderDetail();
  document.getElementById('detail-panel').classList.add('open');
}

function renderDetail() {
  const doc = currentDoc;
  const svc = services[doc.service] || {};
  const canEdit = !!currentUser;  // 로그인한 모든 멤버 편집 가능 (삭제는 본인/관리자만)

  document.getElementById('dp-title').textContent = doc.title;

  // 뱃지
  const now = new Date();
  let expiryBadge = '';
  if (doc.valid_until) {
    const until = new Date(doc.valid_until);
    const daysLeft = Math.ceil((until - now) / (1000 * 86400));
    if (daysLeft < 0) expiryBadge = `<span class="status-badge status-archived">만료됨</span>`;
    else if (daysLeft <= 7) expiryBadge = `<span class="status-badge status-review">D-${daysLeft}</span>`;
  }

  document.getElementById('dp-badges').innerHTML = `
    <span class="svc-badge svc-${doc.service}">${svc.emoji || ''} ${svc.label || doc.service}</span>
    <span class="col-meta">${escapeHtml(doc.category)}</span>
    <span class="status-badge status-${doc.status}">${statusLabel(doc.status)}</span>
    ${doc.source === 'auto_crawl' ? '<span class="src-badge src-auto">🤖 자동수집</span>' : ''}
    ${expiryBadge}
    ${doc.sync_status === 'synced' ? '<span style="font-size:11px;color:var(--green)">✓ AI반영</span>'
      : doc.sync_status === 'failed' ? '<span style="font-size:11px;color:var(--red)">⚠ 동기화실패</span>'
      : '<span style="font-size:11px;color:var(--accent)">↺ 동기화중</span>'}
  `;

  // 액션 버튼
  let actions = `<button class="btn btn-ghost btn-sm" onclick="openHistory('${doc.doc_id}')">📜 히스토리</button>`;
  if (canEdit) {
    const isLocked = doc.locked_by && doc.locked_by !== currentUser?.email;
    const lockedByName = isLocked ? doc.locked_by.split('@')[0] : '';
    // 잠금 중이어도 편집 가능 — 저장 시 서버에서 최종 체크
    actions += `<button class="btn btn-secondary btn-sm" onclick="openEditDoc('${doc.doc_id}')"
      title="${isLocked ? lockedByName + '님이 편집 중이지만 수정할 수 있습니다' : ''}">✏️ 편집${isLocked ? ' ⚠️' : ''}</button>`;
    actions += `<button class="btn btn-sm" style="background:rgba(20,184,166,.15);border:1px solid rgba(20,184,166,.3);color:#2dd4bf" onclick="markReviewed('${doc.doc_id}')" title="내용 변경 없이 검토 완료 처리">✅ 검토 완료</button>`;
  }
  document.getElementById('dp-actions').innerHTML = actions;

  // 잠금 공지
  let lockNotice = '';
  if (doc.locked_by) {
    const isSelf = doc.locked_by === currentUser?.email;
    if (isSelf) {
      lockNotice = `<div class="lock-notice" style="background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.25);color:#60a5fa">✏️ 내가 편집 중입니다.</div>`;
    } else {
      const lockedName = doc.locked_by.split('@')[0];
      lockNotice = `<div class="lock-notice">🔒 <strong>${lockedName}</strong>님이 편집 중입니다. 잠시 후 수정해주세요.</div>`;
    }
  }

  // 본문
  document.getElementById('dp-content-area').innerHTML = `
    ${lockNotice}
    <div class="detail-content">${escapeHtml(doc.content)}</div>
  `;

  // 메타 패널
  document.getElementById('dp-meta-panel').innerHTML = `
    <div class="meta-section">
      <div class="meta-label">담당자</div>
      <div class="meta-value">${doc.owner_id || '—'}</div>
    </div>
    <div class="meta-section">
      <div class="meta-label">유효기간</div>
      <div class="meta-value">
        ${doc.valid_from ? fmtDate(doc.valid_from) + ' ~' : '시작일 없음'}<br>
        ${doc.valid_until ? fmtDate(doc.valid_until) + ' 까지' : '상시'}
      </div>
    </div>
    <div class="meta-section">
      <div class="meta-label">검토 주기</div>
      <div class="meta-value">${doc.review_cycle_days || 90}일</div>
    </div>
    <div class="meta-section">
      <div class="meta-label">마지막 검토</div>
      <div class="meta-value" id="meta-last-reviewed">
        ${doc.last_reviewed_at ? fmtDateTime(doc.last_reviewed_at) : '—'}<br>
        <span style="color:var(--text-dim)">${doc.reviewed_by ? doc.reviewed_by.split('@')[0] : (doc.updated_by ? doc.updated_by.split('@')[0] : '')}</span>
      </div>
    </div>
    <div class="meta-section">
      <div class="meta-label">태그</div>
      <div class="meta-value" style="display:flex;flex-wrap:wrap;gap:4px">
        ${(doc.tags || []).map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('') || '—'}
      </div>
    </div>
    <hr style="border:none;border-top:1px solid var(--border);margin:12px 0">
    <div class="meta-section">
      <div class="meta-label">등록</div>
      <div class="meta-value">${doc.created_by?.split('@')[0] || '—'}<br>${fmtDateTime(doc.created_at)}</div>
    </div>
    <div class="meta-section">
      <div class="meta-label">최종 수정</div>
      <div class="meta-value">${doc.updated_by?.split('@')[0] || '—'}<br>${fmtDateTime(doc.updated_at)}</div>
    </div>
    <div class="meta-section">
      <div class="meta-label">AI 동기화</div>
      <div class="meta-value">
        <span class="sync-dot ${doc.sync_status === 'synced' ? 'sync-synced' : doc.sync_status === 'failed' ? 'sync-failed' : 'sync-pending'}" style="margin-right:5px"></span>
        ${doc.sync_status === 'synced' ? '반영됨' : doc.sync_status === 'failed' ? '실패' : '대기중'}
        ${doc.synced_at ? '<br>' + fmtDateTime(doc.synced_at) : ''}
      </div>
    </div>
  `;
}

function closeDetail() {
  document.getElementById('detail-panel').classList.remove('open');
  currentDoc = null;
}

// ── 편집 ───────────────────────────────────────────────────────────────────
function openNewDoc() {
  editingDocId = null;
  document.getElementById('edit-panel-label').textContent = '새 문서 등록';
  document.getElementById('archive-btn').style.display = 'none';
  document.getElementById('f-title').value = '';
  document.getElementById('f-service').value = 'wifi';
  updateCategoryOptions();
  document.getElementById('f-status').value = 'draft';
  document.getElementById('f-owner').value = currentUser?.email || '';
  document.getElementById('f-valid-from').value = '';
  document.getElementById('f-valid-until').value = '';
  document.getElementById('f-review-cycle').value = '90';
  document.getElementById('f-tags').value = '';
  document.getElementById('f-content').value = '';
  document.getElementById('f-change-summary').value = '';
  document.getElementById('edit-panel').classList.add('open');
}

async function openEditDoc(docId) {
  // 잠금 요청
  try {
    await fetch(`/api/knowledge/${docId}/lock`, { method: 'POST' });
  } catch(e) {}

  const res = await fetch(`/api/knowledge/${docId}`);
  const doc = await res.json();

  editingDocId = docId;
  document.getElementById('edit-panel-label').textContent = '문서 수정';
  document.getElementById('archive-btn').style.display = 'inline-flex';
  document.getElementById('f-title').value = doc.title || '';
  document.getElementById('f-service').value = doc.service || 'wifi';
  updateCategoryOptions();
  document.getElementById('f-category').value = doc.category || '';
  document.getElementById('f-status').value = doc.status || 'draft';
  document.getElementById('f-owner').value = doc.owner_id || '';
  document.getElementById('f-valid-from').value = doc.valid_from ? doc.valid_from.slice(0, 10) : '';
  document.getElementById('f-valid-until').value = doc.valid_until ? doc.valid_until.slice(0, 10) : '';
  document.getElementById('f-review-cycle').value = doc.review_cycle_days || 90;
  document.getElementById('f-tags').value = (doc.tags || []).join(', ');
  document.getElementById('f-content').value = doc.content || '';
  document.getElementById('f-change-summary').value = '';
  document.getElementById('edit-panel').classList.add('open');
}

function cancelEdit() {
  // 잠금 해제
  if (editingDocId) {
    fetch(`/api/knowledge/${editingDocId}/lock`, { method: 'DELETE' }).catch(() => {});
  }
  document.getElementById('edit-panel').classList.remove('open');
  editingDocId = null;
}

function updateCategoryOptions() {
  const service = document.getElementById('f-service').value;
  const cats = (services[service] || {}).categories || [];
  const sel = document.getElementById('f-category');
  const currentVal = sel.value;
  sel.innerHTML = cats.map(c => `<option value="${c}">${c}</option>`).join('');
  if (cats.includes(currentVal)) sel.value = currentVal;
}

async function submitEdit() {
  const title = document.getElementById('f-title').value.trim();
  const content = document.getElementById('f-content').value.trim();
  if (!title || !content) { alert('제목과 내용은 필수입니다.'); return; }

  const tagsRaw = document.getElementById('f-tags').value;
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(Boolean);
  const validFrom = document.getElementById('f-valid-from').value;
  const validUntil = document.getElementById('f-valid-until').value;

  const payload = {
    title,
    service: document.getElementById('f-service').value,
    category: document.getElementById('f-category').value,
    content,
    status: document.getElementById('f-status').value,
    tags,
    owner_type: 'user',
    owner_id: document.getElementById('f-owner').value.trim() || currentUser?.email,
    valid_from: validFrom || null,
    valid_until: validUntil || null,
    review_cycle_days: parseInt(document.getElementById('f-review-cycle').value) || 90,
    change_summary: document.getElementById('f-change-summary').value.trim(),
  };

  try {
    let res;
    if (editingDocId) {
      res = await fetch(`/api/knowledge/${editingDocId}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
    } else {
      res = await fetch('/api/knowledge', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      });
    }
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || '저장 실패'); }
    document.getElementById('edit-panel').classList.remove('open');
    editingDocId = null;
    await loadDocs();
  } catch(e) {
    alert('저장 실패: ' + e.message);
  }
}

async function archiveCurrentDoc() {
  if (!editingDocId) return;
  if (!confirm('이 문서를 아카이브하시겠습니까?')) return;
  const res = await fetch(`/api/knowledge/${editingDocId}`, { method: 'DELETE' });
  if (res.ok) {
    document.getElementById('edit-panel').classList.remove('open');
    document.getElementById('detail-panel').classList.remove('open');
    editingDocId = null;
    await loadDocs();
  }
}

// ── 히스토리 ───────────────────────────────────────────────────────────────
async function openHistory(docId) {
  document.getElementById('history-overlay').classList.add('open');
  document.getElementById('history-list').innerHTML = '<div style="padding:20px;color:var(--text-dim);font-size:12px">로딩중...</div>';
  document.getElementById('diff-view').innerHTML = '<div class="diff-empty">버전을 선택하세요</div>';

  const res = await fetch(`/api/knowledge/${docId}/history`);
  const versions = await res.json();

  if (!versions.length) {
    document.getElementById('history-list').innerHTML = '<div style="padding:20px;color:var(--text-dim);font-size:12px">히스토리 없음</div>';
    return;
  }

  document.getElementById('history-list').innerHTML = versions.map(v => `
    <div class="history-item" onclick="loadVersion('${docId}', ${v.version}, this)">
      <div class="hi-version">v${v.version}</div>
      <div class="hi-summary">${escapeHtml(v.change_summary || '—')}</div>
      <div class="hi-meta">${v.changed_by?.split('@')[0] || '—'} · ${fmtDateTime(v.changed_at)}</div>
    </div>
  `).join('');

  // 최신 버전 자동 선택
  const firstItem = document.querySelector('.history-item');
  if (firstItem) firstItem.click();
}

async function loadVersion(docId, version, el) {
  document.querySelectorAll('.history-item').forEach(i => i.classList.remove('active'));
  el.classList.add('active');

  const res = await fetch(`/api/knowledge/${docId}/history/${version}`);
  const v = await res.json();
  renderDiff(v.diff || []);
}

function renderDiff(diff) {
  if (!diff.length) {
    document.getElementById('diff-view').innerHTML = '<div class="diff-empty">변경 내용 없음</div>';
    return;
  }

  // 변경된 부분 주변만 표시 (context 3줄)
  const CONTEXT = 3;
  const changed = diff.reduce((acc, item, i) => {
    if (item.type !== 'unchanged') {
      for (let j = Math.max(0, i - CONTEXT); j <= Math.min(diff.length - 1, i + CONTEXT); j++) acc.add(j);
    }
    return acc;
  }, new Set());

  let html = '';
  let prevShown = -1;
  diff.forEach((item, i) => {
    if (!changed.has(i)) return;
    if (prevShown !== -1 && i - prevShown > 1) {
      html += `<div class="diff-separator">···</div>`;
    }
    const sign = item.type === 'add' ? '+' : item.type === 'delete' ? '-' : ' ';
    html += `<div class="diff-line ${item.type === 'unchanged' ? '' : item.type}">
      <span class="dl-sign">${sign}</span>
      <span class="dl-text">${escapeHtml(item.text)}</span>
    </div>`;
    prevShown = i;
  });

  document.getElementById('diff-view').innerHTML = html || '<div class="diff-empty">변경 없음</div>';
}

function closeHistory() { document.getElementById('history-overlay').classList.remove('open'); }
function closeHistoryOutside(e) { if (e.target === document.getElementById('history-overlay')) closeHistory(); }

// ── 검색 ───────────────────────────────────────────────────────────────────
function onSearchInput() {
  clearTimeout(searchTimer);
  const q = document.getElementById('search-input').value.trim();
  if (!q) {
    closeSearchDropdown();
    loadDocs();
    return;
  }
  searchTimer = setTimeout(() => {
    loadDocs();
    doRagSearch(q);
  }, 300);
}

function onSearchFocus() {
  const q = document.getElementById('search-input').value.trim();
  if (q) doRagSearch(q);
}

function onSearchBlur() {
  setTimeout(() => closeSearchDropdown(), 200);
}

async function doRagSearch(q) {
  const badge = document.getElementById('rag-badge');
  badge.classList.add('visible');
  try {
    const res = await fetch(`/api/knowledge/search/rag?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    renderSearchDropdown(data.results || [], data.source);
  } catch(e) {
    closeSearchDropdown();
  } finally {
    badge.classList.remove('visible');
  }
}

function renderSearchDropdown(results, source) {
  const dd = document.getElementById('search-dropdown');
  if (!results.length) {
    dd.innerHTML = '<div class="search-empty">검색 결과가 없습니다</div>';
  } else {
    const sourceLabel = source === 'gemini_rag' ? '🤖 AI 시맨틱 검색' : '🔍 텍스트 검색';
    dd.innerHTML = `
      <div style="padding:6px 14px;font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--text-dim);border-bottom:1px solid var(--border)">${sourceLabel}</div>
      ${results.map(r => `
        <div class="search-result-item" onclick="openDetail('${r.doc_id}');closeSearchDropdown()">
          <div class="sri-header">
            <span class="sri-title">${escapeHtml(r.title)}</span>
            <span class="svc-badge svc-${r.service}" style="font-size:9px;padding:1px 5px">${(services[r.service]||{}).label||r.service}</span>
            <span class="status-badge status-${r.status}" style="font-size:9px">${statusLabel(r.status)}</span>
          </div>
          ${r.content_preview ? `<div class="sri-preview">${escapeHtml(r.content_preview)}</div>` : ''}
        </div>
      `).join('')}
    `;
  }
  dd.classList.add('open');
}

function closeSearchDropdown() {
  document.getElementById('search-dropdown').classList.remove('open');
}

// ── 유틸 ───────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('ko-KR', {year:'numeric', month:'2-digit', day:'2-digit'});
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  d.setHours(d.getHours() + 9);
  return d.toLocaleDateString('ko-KR', {month:'2-digit', day:'2-digit'}) + ' ' +
         d.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit'});
}

// ── 검토 완료 ──────────────────────────────────────────────────────────────
async function markReviewed(docId) {
  if (!confirm('검토를 완료 처리하시겠습니까?\n내용 변경 없이 검토 일시만 갱신됩니다.')) return;
  try {
    const res = await fetch(`/api/knowledge/${docId}/review`, { method: 'POST' });
    if (!res.ok) throw new Error('실패');
    const data = await res.json();

    // 메타 패널 즉시 업데이트 (전체 리로드 없이)
    const el = document.getElementById('meta-last-reviewed');
    if (el) {
      el.innerHTML = `${fmtDateTime(data.last_reviewed_at)}<br>
        <span style="color:var(--text-dim)">${data.reviewed_by.split('@')[0]}</span>
        <span style="color:var(--green);font-size:11px;margin-left:4px">✓ 방금 검토</span>`;
    }
    // currentDoc 도 갱신
    if (currentDoc) {
      currentDoc.last_reviewed_at = data.last_reviewed_at;
      currentDoc.reviewed_by = data.reviewed_by;
    }
  } catch(e) {
    alert('검토 완료 처리에 실패했습니다.');
  }
}

// ── 키보드 단축키 ──────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (document.getElementById('history-overlay').classList.contains('open')) { closeHistory(); return; }
    if (document.getElementById('edit-panel').classList.contains('open')) { cancelEdit(); return; }
    if (document.getElementById('detail-panel').classList.contains('open')) { closeDetail(); return; }
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    document.getElementById('search-input').focus();
  }
});

init();

// ── 잠금 상태 폴링 (30초마다 목록 갱신) ─────────────────────────────────────
setInterval(() => {
  // 편집 패널이나 상세 패널이 열려 있으면 갱신 스킵
  const editOpen   = document.getElementById('edit-panel').classList.contains('open');
  const detailOpen = document.getElementById('detail-panel').classList.contains('open');
  if (!editOpen && !detailOpen) loadDocs();
}, 30000);
</script>
</body>
</html>"""


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return HTMLResponse(content=KNOWLEDGE_HTML)