"""
BOA 지식베이스 라우터
GET    /knowledge                    → HTML 지식베이스 페이지 (로그인 필요)
GET    /api/knowledge                → 문서 목록 (카테고리/검색 필터)
POST   /api/knowledge                → 문서 등록
PATCH  /api/knowledge/{doc_id}       → 문서 수정
DELETE /api/knowledge/{doc_id}       → 문서 삭제 (관리자 전용)
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid
from src.dashboard.auth import require_login, get_current_user
from src.core.mongo_client import db
from src.core.mongo_client import boa_admins_collection

router = APIRouter()

boa_knowledge_collection = db["boa_knowledge"]

CATEGORIES = ["SOP", "정책", "이벤트", "가이드", "기타"]

# ── 관리자 확인 ───────────────────────────────────────────────────────────────
async def is_admin(email: str) -> bool:
    return await boa_admins_collection.find_one({"email": email}) is not None

# ── 직렬화 헬퍼 ───────────────────────────────────────────────────────────────
def serialize_doc(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    for key in ("created_at", "updated_at"):
        if isinstance(doc.get(key), datetime):
            doc[key] = doc[key].isoformat()
    return doc

# ── API 엔드포인트 ─────────────────────────────────────────────────────────────

@router.get("/api/knowledge")
async def api_get_knowledge(
    request: Request,
    category: Optional[str] = None,
    search: Optional[str] = None,
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    query = {}
    if category:
        query["category"] = category
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"content": {"$regex": search, "$options": "i"}},
            {"tags": {"$elemMatch": {"$regex": search, "$options": "i"}}},
        ]

    cursor = boa_knowledge_collection.find(query).sort("updated_at", -1)
    docs = await cursor.to_list(length=None)
    return [serialize_doc(d) for d in docs]


class KnowledgeCreateRequest(BaseModel):
    title: str
    category: str
    content: str
    tags: Optional[List[str]] = []


@router.post("/api/knowledge")
async def api_create_knowledge(body: KnowledgeCreateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    if body.category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"카테고리는 {CATEGORIES} 중 하나여야 합니다")

    now = datetime.utcnow()
    doc = {
        "doc_id": str(uuid.uuid4()).replace("-", "")[:16],
        "title": body.title,
        "category": body.category,
        "content": body.content,
        "tags": body.tags or [],
        "created_by": user["email"],
        "updated_by": user["email"],
        "created_at": now,
        "updated_at": now,
    }
    await boa_knowledge_collection.insert_one(doc)
    return serialize_doc(doc)


class KnowledgeUpdateRequest(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None


@router.patch("/api/knowledge/{doc_id}")
async def api_update_knowledge(doc_id: str, body: KnowledgeUpdateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    existing = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not existing:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    # 본인 또는 관리자만 수정 가능
    if existing["created_by"] != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다")

    update_fields = {"updated_by": user["email"], "updated_at": datetime.utcnow()}
    if body.title is not None:
        update_fields["title"] = body.title
    if body.category is not None:
        if body.category not in CATEGORIES:
            raise HTTPException(status_code=400, detail=f"카테고리는 {CATEGORIES} 중 하나여야 합니다")
        update_fields["category"] = body.category
    if body.content is not None:
        update_fields["content"] = body.content
    if body.tags is not None:
        update_fields["tags"] = body.tags

    await boa_knowledge_collection.update_one({"doc_id": doc_id}, {"$set": update_fields})
    updated = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    return serialize_doc(updated)


@router.delete("/api/knowledge/{doc_id}")
async def api_delete_knowledge(doc_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    existing = await boa_knowledge_collection.find_one({"doc_id": doc_id})
    if not existing:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다")

    # 본인 또는 관리자만 삭제 가능
    if existing["created_by"] != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다")

    await boa_knowledge_collection.delete_one({"doc_id": doc_id})
    return {"ok": True}


# ── HTML 지식베이스 페이지 ─────────────────────────────────────────────────────

KNOWLEDGE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOA 지식베이스</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0d0f12; --bg2: #13161b; --bg3: #1a1e25;
    --border: #252931; --text: #c8d0db; --text-dim: #5a6272;
    --text-bright: #eef1f6; --accent: #3b82f6; --accent-dim: #1d3d6e;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
    --purple: #a855f7; --purple-dim: #3b1d6e;
    --orange: #f97316; --teal: #14b8a6;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 14px; min-height: 100vh; }

  /* 헤더 */
  header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 0 28px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
  .logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 16px; color: var(--text-bright); letter-spacing: .05em; }
  .logo span { color: var(--accent); }
  .header-nav { display: flex; align-items: center; gap: 4px; }
  .nav-link { padding: 6px 14px; border-radius: 6px; font-size: 13px; color: var(--text-dim); text-decoration: none; transition: all .15s; }
  .nav-link:hover { color: var(--text); background: var(--bg3); }
  .nav-link.active { color: var(--accent); background: var(--accent-dim); }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .user-info { display: flex; align-items: center; gap: 10px; }
  .user-avatar { width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--border); }
  .user-name { font-size: 13px; color: var(--text); }
  .admin-badge { background: var(--accent-dim); color: #60a5fa; font-family: 'IBM Plex Mono', monospace; font-size: 10px; padding: 2px 8px; border-radius: 4px; border: 1px solid var(--accent); }
  .btn-logout { background: none; border: 1px solid var(--border); border-radius: 6px; padding: 5px 12px; color: var(--text-dim); font-size: 12px; cursor: pointer; transition: all .15s; }
  .btn-logout:hover { border-color: var(--red); color: var(--red); }

  /* 레이아웃 */
  .container { padding: 24px 28px; max-width: 1400px; margin: 0 auto; }

  /* 상단 액션 바 */
  .action-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
  .search-wrap { flex: 1; min-width: 200px; position: relative; }
  .search-wrap input { width: 100%; background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 8px 14px 8px 36px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; transition: border-color .15s; }
  .search-wrap input:focus { border-color: var(--accent); }
  .search-wrap input::placeholder { color: var(--text-dim); }
  .search-icon { position: absolute; left: 12px; top: 50%; transform: translateY(-50%); color: var(--text-dim); font-size: 14px; pointer-events: none; }
  .category-tabs { display: flex; gap: 6px; flex-wrap: wrap; }
  .cat-tab { padding: 6px 14px; border-radius: 6px; font-size: 12px; font-family: 'IBM Plex Mono', monospace; border: 1px solid var(--border); background: var(--bg2); color: var(--text-dim); cursor: pointer; transition: all .15s; white-space: nowrap; }
  .cat-tab:hover { border-color: var(--accent); color: var(--text); }
  .cat-tab.active { border-color: var(--accent); background: var(--accent-dim); color: #60a5fa; }
  .btn-new { background: var(--accent); border: none; border-radius: 6px; padding: 8px 18px; color: white; font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; font-weight: 500; cursor: pointer; transition: opacity .15s; white-space: nowrap; }
  .btn-new:hover { opacity: .85; }

  /* 카테고리 색상 */
  .cat-SOP      { background: rgba(59,130,246,.15); color: #60a5fa; border-color: rgba(59,130,246,.3); }
  .cat-정책     { background: rgba(168,85,247,.15); color: #c084fc; border-color: rgba(168,85,247,.3); }
  .cat-이벤트   { background: rgba(234,179,8,.15);  color: #fbbf24; border-color: rgba(234,179,8,.3); }
  .cat-가이드   { background: rgba(20,184,166,.15); color: #2dd4bf; border-color: rgba(20,184,166,.3); }
  .cat-기타     { background: rgba(107,114,128,.15);color: #9ca3af; border-color: rgba(107,114,128,.3); }

  /* 문서 그리드 */
  .docs-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
  .doc-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 18px 20px; cursor: pointer; transition: border-color .15s, background .15s; display: flex; flex-direction: column; gap: 10px; }
  .doc-card:hover { border-color: var(--accent); background: var(--bg3); }
  .doc-card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
  .doc-title { font-size: 14px; font-weight: 600; color: var(--text-bright); line-height: 1.4; flex: 1; }
  .cat-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 500; border: 1px solid; white-space: nowrap; }
  .doc-content-preview { font-size: 12px; color: var(--text-dim); line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .doc-tags { display: flex; flex-wrap: wrap; gap: 5px; }
  .tag { background: var(--bg3); border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; font-size: 11px; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; }
  .doc-footer { display: flex; align-items: center; justify-content: space-between; font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-dim); border-top: 1px solid var(--border); padding-top: 10px; margin-top: 2px; }

  /* 빈 상태 */
  .empty-state { padding: 80px; text-align: center; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; font-size: 13px; grid-column: 1/-1; }
  .empty-icon { font-size: 40px; margin-bottom: 12px; }
  .loading { padding: 80px; text-align: center; color: var(--text-dim); font-family: 'IBM Plex Mono', monospace; font-size: 13px; grid-column: 1/-1; }

  /* 모달 공통 */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.7); z-index: 200; backdrop-filter: blur(2px); align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; width: 680px; max-width: 95vw; max-height: 88vh; overflow-y: auto; animation: slideUp .2s ease; }
  @keyframes slideUp { from{transform:translateY(16px);opacity:0} to{transform:translateY(0);opacity:1} }
  .modal-header { display: flex; align-items: center; justify-content: space-between; padding: 20px 24px 16px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg2); z-index: 1; }
  .modal-title { font-size: 15px; font-weight: 600; color: var(--text-bright); }
  .modal-close { background: none; border: none; color: var(--text-dim); font-size: 20px; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
  .modal-close:hover { color: var(--text); background: var(--bg3); }
  .modal-body { padding: 20px 24px; }

  /* 상세 모달 */
  .detail-category { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
  .detail-content { background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 16px; font-size: 13px; line-height: 1.8; color: var(--text); white-space: pre-wrap; word-break: break-word; max-height: 400px; overflow-y: auto; }
  .detail-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .detail-meta { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-dim); margin-top: 14px; display: flex; gap: 20px; flex-wrap: wrap; }
  .detail-actions { display: flex; gap: 8px; margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }

  /* 등록/수정 모달 */
  .form-group { margin-bottom: 14px; }
  .form-label { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; display: block; }
  .form-input { width: 100%; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; transition: border-color .15s; }
  .form-input:focus { border-color: var(--accent); }
  .form-textarea { width: 100%; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; resize: vertical; min-height: 180px; line-height: 1.7; transition: border-color .15s; }
  .form-textarea:focus { border-color: var(--accent); }
  .form-select { width: 100%; background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 9px 12px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; outline: none; }
  .form-select:focus { border-color: var(--accent); }
  .form-hint { font-size: 11px; color: var(--text-dim); margin-top: 4px; }

  /* 버튼 */
  .btn { border: none; border-radius: 6px; padding: 9px 20px; font-family: 'IBM Plex Sans KR', sans-serif; font-size: 13px; font-weight: 500; cursor: pointer; transition: opacity .15s; }
  .btn-primary { background: var(--accent); color: white; flex: 1; }
  .btn-primary:hover { opacity: .85; }
  .btn-secondary { background: var(--bg3); border: 1px solid var(--border); color: var(--text); }
  .btn-secondary:hover { border-color: var(--accent); color: var(--accent); }
  .btn-danger { background: rgba(239,68,68,.15); border: 1px solid rgba(239,68,68,.3); color: var(--red); }
  .btn-danger:hover { background: rgba(239,68,68,.25); }
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

<div class="container">
  <div class="action-bar">
    <div class="search-wrap">
      <span class="search-icon">🔍</span>
      <input type="text" id="search-input" placeholder="제목, 내용, 태그 검색..." oninput="onSearch()">
    </div>
    <div class="category-tabs">
      <div class="cat-tab active" onclick="filterCategory(null, this)">전체</div>
      <div class="cat-tab" onclick="filterCategory('SOP', this)">SOP</div>
      <div class="cat-tab" onclick="filterCategory('정책', this)">정책</div>
      <div class="cat-tab" onclick="filterCategory('이벤트', this)">이벤트</div>
      <div class="cat-tab" onclick="filterCategory('가이드', this)">가이드</div>
      <div class="cat-tab" onclick="filterCategory('기타', this)">기타</div>
    </div>
    <button class="btn-new" onclick="openFormModal()">+ 문서 등록</button>
  </div>

  <div class="docs-grid" id="docs-grid">
    <div class="loading">로딩 중...</div>
  </div>
</div>

<!-- 상세 모달 -->
<div class="modal-overlay" id="detail-overlay" onclick="closeDetailOutside(event)">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title" id="detail-title">—</div>
      <button class="modal-close" onclick="closeDetail()">×</button>
    </div>
    <div class="modal-body" id="detail-body"></div>
  </div>
</div>

<!-- 등록/수정 모달 -->
<div class="modal-overlay" id="form-overlay" onclick="closeFormOutside(event)">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title" id="form-title">문서 등록</div>
      <button class="modal-close" onclick="closeForm()">×</button>
    </div>
    <div class="modal-body">
      <div class="form-group">
        <label class="form-label">제목 *</label>
        <input class="form-input" id="f-title" placeholder="문서 제목을 입력하세요">
      </div>
      <div class="form-group">
        <label class="form-label">카테고리 *</label>
        <select class="form-select" id="f-category">
          <option value="SOP">SOP</option>
          <option value="정책">정책</option>
          <option value="이벤트">이벤트</option>
          <option value="가이드">가이드</option>
          <option value="기타">기타</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">내용 *</label>
        <textarea class="form-textarea" id="f-content" placeholder="문서 내용을 입력하세요..."></textarea>
      </div>
      <div class="form-group">
        <label class="form-label">태그</label>
        <input class="form-input" id="f-tags" placeholder="쉼표로 구분 (예: 와이파이, 해지, 요금제)">
        <div class="form-hint">쉼표(,)로 여러 태그를 입력할 수 있습니다</div>
      </div>
      <div style="display:flex;gap:8px;margin-top:4px">
        <button class="btn btn-secondary" onclick="closeForm()">취소</button>
        <button class="btn btn-primary" onclick="submitForm()">저장</button>
      </div>
    </div>
  </div>
</div>

<script>
let allDocs = [];
let currentCategory = null;
let currentUser = null;
let editingDocId = null;
let searchTimer = null;

// ── 초기화 ──
async function init() {
  await loadMe();
  await loadDocs();
}

async function loadMe() {
  const res = await fetch('/api/me');
  currentUser = await res.json();
  document.getElementById('user-name').textContent = currentUser.name;
  document.getElementById('user-avatar').src = currentUser.picture || '';
  if (currentUser.is_admin) {
    document.getElementById('admin-badge').style.display = 'inline-block';
  }
}

// ── 문서 로드 ──
async function loadDocs() {
  const search = document.getElementById('search-input').value.trim();
  let url = '/api/knowledge';
  const params = new URLSearchParams();
  if (currentCategory) params.set('category', currentCategory);
  if (search) params.set('search', search);
  if (params.toString()) url += '?' + params.toString();

  const grid = document.getElementById('docs-grid');
  grid.innerHTML = '<div class="loading">로딩 중...</div>';

  try {
    const res = await fetch(url);
    allDocs = await res.json();
    renderGrid();
  } catch(e) {
    grid.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div>데이터 로드 실패</div>';
  }
}

function onSearch() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadDocs, 300);
}

// ── 카테고리 필터 ──
function filterCategory(cat, el) {
  currentCategory = cat;
  document.querySelectorAll('.cat-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  loadDocs();
}

// ── 그리드 렌더 ──
function renderGrid() {
  const grid = document.getElementById('docs-grid');
  if (!allDocs.length) {
    grid.innerHTML = '<div class="empty-state"><div class="empty-icon">📭</div>등록된 문서가 없습니다</div>';
    return;
  }

  grid.innerHTML = allDocs.map(doc => `
    <div class="doc-card" onclick="openDetail('${doc.doc_id}')">
      <div class="doc-card-header">
        <div class="doc-title">${escapeHtml(doc.title)}</div>
        <span class="cat-badge cat-${doc.category}">${doc.category}</span>
      </div>
      <div class="doc-content-preview">${escapeHtml(doc.content)}</div>
      ${doc.tags?.length ? `
        <div class="doc-tags">
          ${doc.tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')}
        </div>` : ''}
      <div class="doc-footer">
        <span>${doc.created_by?.split('@')[0] || '—'}</span>
        <span>${formatDate(doc.updated_at)}</span>
      </div>
    </div>
  `).join('');
}

// ── 상세 모달 ──
function openDetail(docId) {
  const doc = allDocs.find(d => d.doc_id === docId);
  if (!doc) return;

  document.getElementById('detail-title').textContent = doc.title;

  const canEdit = currentUser?.email === doc.created_by || currentUser?.is_admin;

  document.getElementById('detail-body').innerHTML = `
    <div class="detail-category">
      <span class="cat-badge cat-${doc.category}">${doc.category}</span>
      ${doc.tags?.length ? doc.tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('') : ''}
    </div>
    <div class="detail-content">${escapeHtml(doc.content)}</div>
    <div class="detail-meta">
      <span>작성자: ${doc.created_by || '—'}</span>
      <span>최종 수정: ${doc.updated_by || '—'}</span>
      <span>등록: ${formatDateFull(doc.created_at)}</span>
      <span>수정: ${formatDateFull(doc.updated_at)}</span>
    </div>
    ${canEdit ? `
    <div class="detail-actions">
      <button class="btn btn-secondary" onclick="closeDetail();openFormModal('${doc.doc_id}')">✏️ 수정</button>
      <button class="btn btn-danger" onclick="deleteDoc('${doc.doc_id}')">🗑 삭제</button>
    </div>` : ''}
  `;

  document.getElementById('detail-overlay').classList.add('open');
}

function closeDetail() { document.getElementById('detail-overlay').classList.remove('open'); }
function closeDetailOutside(e) { if (e.target === document.getElementById('detail-overlay')) closeDetail(); }

// ── 등록/수정 모달 ──
function openFormModal(docId = null) {
  editingDocId = docId;
  const doc = docId ? allDocs.find(d => d.doc_id === docId) : null;

  document.getElementById('form-title').textContent = doc ? '문서 수정' : '문서 등록';
  document.getElementById('f-title').value = doc?.title || '';
  document.getElementById('f-category').value = doc?.category || 'SOP';
  document.getElementById('f-content').value = doc?.content || '';
  document.getElementById('f-tags').value = doc?.tags?.join(', ') || '';

  document.getElementById('form-overlay').classList.add('open');
}

function closeForm() {
  document.getElementById('form-overlay').classList.remove('open');
  editingDocId = null;
}
function closeFormOutside(e) { if (e.target === document.getElementById('form-overlay')) closeForm(); }

async function submitForm() {
  const title = document.getElementById('f-title').value.trim();
  const category = document.getElementById('f-category').value;
  const content = document.getElementById('f-content').value.trim();
  const tagsRaw = document.getElementById('f-tags').value;
  const tags = tagsRaw.split(',').map(t => t.trim()).filter(Boolean);

  if (!title || !content) {
    alert('제목과 내용은 필수입니다');
    return;
  }

  const body = { title, category, content, tags };

  try {
    const url = editingDocId ? `/api/knowledge/${editingDocId}` : '/api/knowledge';
    const method = editingDocId ? 'PATCH' : 'POST';
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '저장 실패');
    }
    closeForm();
    await loadDocs();
  } catch(e) {
    alert('저장 실패: ' + e.message);
  }
}

// ── 삭제 ──
async function deleteDoc(docId) {
  if (!confirm('정말 삭제하시겠습니까?')) return;
  try {
    const res = await fetch(`/api/knowledge/${docId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('삭제 실패');
    closeDetail();
    await loadDocs();
  } catch(e) {
    alert('삭제 실패: ' + e.message);
  }
}

// ── 유틸 ──
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  d.setHours(d.getHours() + 9);
  return d.toLocaleDateString('ko-KR', {month:'2-digit', day:'2-digit'}) + ' ' +
         d.toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit'});
}
function formatDateFull(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  d.setHours(d.getHours() + 9);
  return d.toLocaleString('ko-KR');
}

init();
</script>
</body>
</html>"""

@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return HTMLResponse(content=KNOWLEDGE_HTML)