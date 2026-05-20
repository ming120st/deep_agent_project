"""
src/dashboard/reports_router.py

리포트 관리 — HTML 페이지 + API

[페이지]
  GET /reports                  리포트 목록 페이지

[API]
  GET    /api/reports           열람 가능한 리포트 목록
  POST   /api/reports           리포트 등록 (multipart: file + metadata)
  PUT    /api/reports/{id}      메타데이터/공개범위 수정
  DELETE /api/reports/{id}      삭제
  GET    /api/reports/{id}/url  SAS URL 발급 (2시간 유효)
  GET    /api/reports/members   구성원 목록 (specific 공개범위 UI용)

[visibility]
  public    전체 공개
  team      owner_teams 소속 팀원만 (boa_members.department 기준)
  specific  allowed_emails 목록에 포함된 사람만
  private   업로더 본인만
"""

import asyncio
from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from src.dashboard.auth import require_login, get_current_user
from src.core.mongo_client import boa_reports_collection, boa_members_collection

router = APIRouter()


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _serialize(doc: dict) -> dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return doc


async def _can_view(doc: dict, email: str) -> bool:
    v = doc.get("visibility", "private")
    if v == "public":
        return True
    if v == "private":
        return doc.get("created_by") == email
    if v == "specific":
        return email in doc.get("allowed_emails", [])
    if v == "team":
        member = await boa_members_collection.find_one({"email": email}, {"department": 1})
        if not member:
            return False
        return member.get("department") in doc.get("owner_teams", [])
    return False


# ── HTML 페이지 ───────────────────────────────────────────────────────────────

REPORTS_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOA 리포트</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#f5f6fa; --bg2:#ffffff; --bg3:#f0f2f8;
    --border:#e2e7f0; --text:#1e2436; --text-dim:#7b8499;
    --text-bright:#0f1628; --accent:#2563eb; --accent-dim:#dbeafe;
    --green:#22c55e; --yellow:#eab308; --red:#ef4444;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:15px; min-height:100vh; }

  header { background:var(--bg2); border-bottom:1px solid var(--border); padding:0 28px; height:56px; box-shadow:0 1px 3px rgba(0,0,0,.06); display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:100; }
  .logo { font-family:'IBM Plex Mono',monospace; font-weight:600; font-size:16px; color:var(--text-bright); letter-spacing:.05em; }
  .logo span { color:var(--accent); }
  .header-nav { display:flex; align-items:center; gap:4px; }
  .nav-link { padding:6px 14px; border-radius:6px; font-size:13px; color:var(--text-dim); text-decoration:none; transition:all .15s; }
  .nav-link:hover { color:var(--text); background:#eef0f8; }
  .nav-link.active { color:var(--accent); background:var(--accent-dim); }
  .header-right { display:flex; align-items:center; gap:16px; }
  .user-info { display:flex; align-items:center; gap:10px; }
  .user-avatar { width:28px; height:28px; border-radius:50%; border:1px solid var(--border); }
  .user-name { font-size:13px; color:var(--text); }
  .btn-logout { background:none; border:1px solid var(--border); border-radius:6px; padding:5px 12px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; }
  .btn-logout:hover { border-color:var(--red); color:var(--red); }

  .container { padding:24px 28px; max-width:1400px; margin:0 auto; }

  .page-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:24px; }
  .page-title { font-size:18px; font-weight:600; color:var(--text-bright); }
  .page-title span { font-family:'IBM Plex Mono',monospace; color:var(--text-dim); font-size:13px; font-weight:400; margin-left:10px; }

  .toolbar { display:flex; align-items:center; gap:12px; margin-bottom:20px; }
  .search-input { flex:1; background:var(--bg2); border:1px solid var(--border); border-radius:6px; padding:8px 14px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:14px; outline:none; transition:border-color .15s; }
  .search-input:focus { border-color:var(--accent); }
  .search-input::placeholder { color:var(--text-dim); }

  .btn-primary { background:var(--accent); border:none; border-radius:6px; padding:9px 18px; color:#fff; font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; font-weight:500; cursor:pointer; transition:opacity .15s; white-space:nowrap; }
  .btn-primary:hover { opacity:.85; }
  .btn-secondary { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:8px 16px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; cursor:pointer; transition:all .15s; }
  .btn-secondary:hover { border-color:var(--accent); color:var(--accent); }
  .btn-danger { background:none; border:1px solid var(--border); border-radius:6px; padding:6px 12px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; }
  .btn-danger:hover { border-color:var(--red); color:var(--red); }

  .cards-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:16px; }

  .report-card { background:var(--bg2); border:1px solid var(--border); border-radius:10px; padding:20px; display:flex; flex-direction:column; gap:12px; transition:box-shadow .15s; }
  .report-card:hover { box-shadow:0 2px 12px rgba(0,0,0,.08); }
  .card-top { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
  .card-title { font-size:14px; font-weight:600; color:var(--text-bright); line-height:1.4; }
  .card-desc { font-size:13px; color:var(--text-dim); line-height:1.5; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .card-meta { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .badge { display:inline-flex; align-items:center; gap:4px; padding:3px 8px; border-radius:4px; font-family:'IBM Plex Mono',monospace; font-size:11px; font-weight:500; }
  .badge-public  { background:rgba(34,197,94,.1);  color:#15803d; }
  .badge-team    { background:rgba(37,99,235,.1);  color:#1d4ed8; }
  .badge-specific{ background:rgba(124,58,237,.1); color:#7c3aed; }
  .badge-private { background:rgba(107,114,128,.1);color:#6b7280; }
  .meta-text { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }
  .card-actions { display:flex; align-items:center; gap:8px; margin-top:4px; }
  .btn-open { background:var(--accent); border:none; border-radius:6px; padding:7px 16px; color:#fff; font-size:12px; font-weight:500; cursor:pointer; transition:opacity .15s; }
  .btn-open:hover { opacity:.85; }
  .btn-edit { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:6px 12px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; }
  .btn-edit:hover { border-color:var(--accent); color:var(--accent); }

  .empty-state { padding:80px; text-align:center; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }
  .loading { padding:60px; text-align:center; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }

  /* 모달 */
  .modal-overlay { display:none; position:fixed; inset:0; background:rgba(15,17,30,.55); z-index:200; backdrop-filter:blur(2px); align-items:center; justify-content:center; }
  .modal-overlay.open { display:flex; }
  .modal { background:var(--bg2); border:1px solid var(--border); border-radius:10px; width:520px; max-width:95vw; max-height:90vh; overflow-y:auto; animation:slideUp .2s ease; }
  @keyframes slideUp { from{transform:translateY(16px);opacity:0} to{transform:translateY(0);opacity:1} }
  .modal-header { display:flex; align-items:center; justify-content:space-between; padding:20px 24px 16px; border-bottom:1px solid var(--border); }
  .modal-title { font-size:15px; font-weight:600; color:var(--text-bright); }
  .modal-close { background:none; border:none; color:var(--text-dim); font-size:20px; cursor:pointer; padding:2px 6px; border-radius:4px; }
  .modal-close:hover { color:var(--text); background:var(--bg3); }
  .modal-body { padding:20px 24px 24px; display:flex; flex-direction:column; gap:16px; }
  .form-group { display:flex; flex-direction:column; gap:6px; }
  .form-label { font-size:12px; font-weight:500; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:.06em; }
  .form-input, .form-select, .form-textarea { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:9px 12px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; outline:none; transition:border-color .15s; width:100%; }
  .form-input:focus, .form-select:focus, .form-textarea:focus { border-color:var(--accent); }
  .form-textarea { resize:vertical; min-height:72px; }
  .file-zone { border:1.5px dashed var(--border); border-radius:8px; padding:28px; text-align:center; cursor:pointer; transition:border-color .15s; }
  .file-zone:hover { border-color:var(--accent); }
  .file-zone-label { font-size:13px; color:var(--text-dim); }
  .file-zone-label span { color:var(--accent); text-decoration:underline; cursor:pointer; }
  .file-chosen { font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--accent); margin-top:6px; }
  .modal-footer { display:flex; gap:10px; justify-content:flex-end; padding:0 24px 20px; }

  /* 구성원 선택 */
  .member-search { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:8px 12px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; outline:none; width:100%; margin-bottom:8px; }
  .member-search:focus { border-color:var(--accent); }
  .member-list { max-height:160px; overflow-y:auto; border:1px solid var(--border); border-radius:6px; background:var(--bg2); }
  .member-item { display:flex; align-items:center; gap:10px; padding:8px 12px; border-bottom:1px solid var(--border); cursor:pointer; transition:background .1s; }
  .member-item:last-child { border-bottom:none; }
  .member-item:hover { background:var(--bg3); }
  .member-item input[type=checkbox] { accent-color:var(--accent); }
  .member-name { font-size:13px; color:var(--text); }
  .member-email { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }

  .cond-field { display:none; }
  .cond-field.show { display:flex; flex-direction:column; gap:6px; }
</style>
</head>
<body>

<header>
  <div class="logo">BOA <span>//</span> 리포트</div>
  <nav class="header-nav">
    <a href="/reports" class="nav-link active">리포트</a>
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link">구성원</a>
    <a href="/sessions" class="nav-link">대화 로그</a>
  </nav>
  <div class="header-right">
    <div class="user-info">
      <img class="user-avatar" id="user-avatar" src="" alt="">
      <span class="user-name" id="user-name">로딩중...</span>
    </div>
    <button class="btn-logout" onclick="location.href='/auth/logout'">로그아웃</button>
  </div>
</header>

<div class="container">
  <div class="page-header">
    <div class="page-title">리포트 <span id="report-count"></span></div>
    <button class="btn-primary" onclick="openUploadModal()">+ 리포트 등록</button>
  </div>

  <div class="toolbar">
    <input type="text" class="search-input" id="search-input" placeholder="리포트 제목 검색..." oninput="renderCards()">
  </div>

  <div id="cards-container" class="cards-grid">
    <div class="loading">로딩 중...</div>
  </div>
</div>

<!-- 업로드 모달 -->
<div class="modal-overlay" id="upload-modal" onclick="closeModalOutside(event, 'upload-modal')">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title">리포트 등록</div>
      <button class="modal-close" onclick="closeModal('upload-modal')">×</button>
    </div>
    <div class="modal-body">
      <div class="form-group">
        <div class="form-label">HTML 파일</div>
        <div class="file-zone" onclick="document.getElementById('file-input').click()">
          <div class="file-zone-label">파일을 클릭하거나 <span>여기서 선택</span>하세요</div>
          <div class="file-chosen" id="file-chosen-name"></div>
        </div>
        <input type="file" id="file-input" accept=".html" style="display:none" onchange="onFileChosen(this)">
      </div>
      <div class="form-group">
        <div class="form-label">제목</div>
        <input type="text" class="form-input" id="upload-title" placeholder="리포트 제목">
      </div>
      <div class="form-group">
        <div class="form-label">설명</div>
        <textarea class="form-textarea" id="upload-desc" placeholder="간단한 설명 (선택)"></textarea>
      </div>
      <div class="form-group">
        <div class="form-label">공개 범위</div>
        <select class="form-select" id="upload-visibility" onchange="onVisibilityChange('upload')">
          <option value="private">나만 보기</option>
          <option value="team">팀 공개</option>
          <option value="specific">특정 인원</option>
          <option value="public">전체 공개</option>
        </select>
      </div>
      <div class="cond-field" id="upload-team-field">
        <div class="form-label">팀 이름 (콤마 구분)</div>
        <input type="text" class="form-input" id="upload-teams" placeholder="예: AI팀, 마켓운영팀">
      </div>
      <div class="cond-field" id="upload-specific-field">
        <div class="form-label">열람 가능 구성원</div>
        <input type="text" class="member-search" id="upload-member-search" placeholder="이름 또는 이메일 검색..." oninput="filterMembers('upload')">
        <div class="member-list" id="upload-member-list"></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn-secondary" onclick="closeModal('upload-modal')">취소</button>
      <button class="btn-primary" onclick="submitUpload()">등록</button>
    </div>
  </div>
</div>

<!-- 수정 모달 -->
<div class="modal-overlay" id="edit-modal" onclick="closeModalOutside(event, 'edit-modal')">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title">리포트 수정</div>
      <button class="modal-close" onclick="closeModal('edit-modal')">×</button>
    </div>
    <div class="modal-body">
      <input type="hidden" id="edit-id">
      <div class="form-group">
        <div class="form-label">제목</div>
        <input type="text" class="form-input" id="edit-title">
      </div>
      <div class="form-group">
        <div class="form-label">설명</div>
        <textarea class="form-textarea" id="edit-desc"></textarea>
      </div>
      <div class="form-group">
        <div class="form-label">공개 범위</div>
        <select class="form-select" id="edit-visibility" onchange="onVisibilityChange('edit')">
          <option value="private">나만 보기</option>
          <option value="team">팀 공개</option>
          <option value="specific">특정 인원</option>
          <option value="public">전체 공개</option>
        </select>
      </div>
      <div class="cond-field" id="edit-team-field">
        <div class="form-label">팀 이름 (콤마 구분)</div>
        <input type="text" class="form-input" id="edit-teams">
      </div>
      <div class="cond-field" id="edit-specific-field">
        <div class="form-label">열람 가능 구성원</div>
        <input type="text" class="member-search" id="edit-member-search" placeholder="이름 또는 이메일 검색..." oninput="filterMembers('edit')">
        <div class="member-list" id="edit-member-list"></div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn-danger" onclick="deleteReport()">삭제</button>
      <div style="flex:1"></div>
      <button class="btn-secondary" onclick="closeModal('edit-modal')">취소</button>
      <button class="btn-primary" onclick="submitEdit()">저장</button>
    </div>
  </div>
</div>

<script>
let allReports = [];
let allMembers = [];
let selectedUploadEmails = new Set();
let selectedEditEmails = new Set();
let currentUser = null;

const VISIBILITY_LABEL = {
  public: '전체 공개', team: '팀 공개', specific: '특정 인원', private: '나만 보기'
};
const VISIBILITY_BADGE = {
  public: 'badge-public', team: 'badge-team', specific: 'badge-specific', private: 'badge-private'
};

async function apiFetch(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

async function init() {
  try {
    const me = await apiFetch('/api/me');
    currentUser = me;
    document.getElementById('user-name').textContent = me.name || me.email;
    const av = document.getElementById('user-avatar');
    if (me.picture) av.src = me.picture; else av.style.display = 'none';
  } catch(e) {}

  await loadReports();
  allMembers = await apiFetch('/api/reports/members').catch(() => []);
}

async function loadReports() {
  try {
    allReports = await apiFetch('/api/reports');
    document.getElementById('report-count').textContent = `(${allReports.length})`;
    renderCards();
  } catch(e) {
    document.getElementById('cards-container').innerHTML = '<div class="empty-state">리포트를 불러오지 못했습니다.</div>';
  }
}

function renderCards() {
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  const filtered = allReports.filter(r =>
    !q || r.title.toLowerCase().includes(q) || (r.description||'').toLowerCase().includes(q)
  );

  if (!filtered.length) {
    document.getElementById('cards-container').innerHTML = '<div class="empty-state">리포트가 없습니다.</div>';
    return;
  }

  const html = filtered.map(r => {
    const isOwner = currentUser && r.created_by === currentUser.email;
    const badgeCls = VISIBILITY_BADGE[r.visibility] || 'badge-private';
    const badgeLabel = VISIBILITY_LABEL[r.visibility] || r.visibility;
    const date = r.created_at ? new Date(r.created_at).toLocaleDateString('ko-KR') : '';
    return `
    <div class="report-card">
      <div class="card-top">
        <div class="card-title">${escHtml(r.title)}</div>
        <span class="badge ${badgeCls}">${badgeLabel}</span>
      </div>
      ${r.description ? `<div class="card-desc">${escHtml(r.description)}</div>` : ''}
      <div class="card-meta">
        <span class="meta-text">${escHtml(r.created_by || '')}</span>
        <span class="meta-text">·</span>
        <span class="meta-text">${date}</span>
      </div>
      <div class="card-actions">
        <button class="btn-open" onclick="openReport('${r.id}')">열기 ↗</button>
        ${isOwner ? `<button class="btn-edit" onclick="openEditModal(${JSON.stringify(r).replace(/"/g, '&quot;')})">수정</button>` : ''}
      </div>
    </div>`;
  }).join('');

  document.getElementById('cards-container').innerHTML = html;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function openReport(id) {
  try {
    const { url } = await apiFetch(`/api/reports/${id}/url`);
    window.open(url, '_blank');
  } catch(e) {
    alert('열람 URL을 불러오지 못했습니다: ' + e.message);
  }
}

// ── 업로드 모달 ──
function openUploadModal() {
  document.getElementById('upload-title').value = '';
  document.getElementById('upload-desc').value = '';
  document.getElementById('upload-visibility').value = 'private';
  document.getElementById('file-input').value = '';
  document.getElementById('file-chosen-name').textContent = '';
  selectedUploadEmails = new Set();
  onVisibilityChange('upload');
  document.getElementById('upload-modal').classList.add('open');
}

function onFileChosen(input) {
  const name = input.files[0]?.name || '';
  document.getElementById('file-chosen-name').textContent = name;
}

async function submitUpload() {
  const file = document.getElementById('file-input').files[0];
  const title = document.getElementById('upload-title').value.trim();
  if (!file) { alert('파일을 선택해주세요.'); return; }
  if (!title) { alert('제목을 입력해주세요.'); return; }

  const vis = document.getElementById('upload-visibility').value;
  const teams = document.getElementById('upload-teams').value;
  const emails = [...selectedUploadEmails].join(',');

  const fd = new FormData();
  fd.append('file', file);
  fd.append('title', title);
  fd.append('description', document.getElementById('upload-desc').value);
  fd.append('visibility', vis);
  fd.append('owner_teams', teams);
  fd.append('allowed_emails', emails);

  try {
    await fetch('/api/reports', { method: 'POST', body: fd });
    closeModal('upload-modal');
    await loadReports();
  } catch(e) {
    alert('등록 실패: ' + e.message);
  }
}

// ── 수정 모달 ──
function openEditModal(r) {
  document.getElementById('edit-id').value = r.id;
  document.getElementById('edit-title').value = r.title;
  document.getElementById('edit-desc').value = r.description || '';
  document.getElementById('edit-visibility').value = r.visibility;
  document.getElementById('edit-teams').value = (r.owner_teams || []).join(', ');
  selectedEditEmails = new Set(r.allowed_emails || []);
  onVisibilityChange('edit');
  document.getElementById('edit-modal').classList.add('open');
}

async function submitEdit() {
  const id = document.getElementById('edit-id').value;
  const vis = document.getElementById('edit-visibility').value;
  const teams = document.getElementById('edit-teams').value.split(',').map(s=>s.trim()).filter(Boolean);
  const emails = [...selectedEditEmails];

  try {
    await apiFetch(`/api/reports/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: document.getElementById('edit-title').value.trim(),
        description: document.getElementById('edit-desc').value,
        visibility: vis,
        owner_teams: teams,
        allowed_emails: emails,
      })
    });
    closeModal('edit-modal');
    await loadReports();
  } catch(e) {
    alert('저장 실패: ' + e.message);
  }
}

async function deleteReport() {
  const id = document.getElementById('edit-id').value;
  if (!confirm('이 리포트를 삭제하시겠습니까?')) return;
  try {
    await apiFetch(`/api/reports/${id}`, { method: 'DELETE' });
    closeModal('edit-modal');
    await loadReports();
  } catch(e) {
    alert('삭제 실패: ' + e.message);
  }
}

// ── visibility 조건부 필드 ──
function onVisibilityChange(prefix) {
  const vis = document.getElementById(`${prefix}-visibility`).value;
  const teamField = document.getElementById(`${prefix}-team-field`);
  const specificField = document.getElementById(`${prefix}-specific-field`);
  teamField.className = 'cond-field' + (vis === 'team' ? ' show' : '');
  specificField.className = 'cond-field' + (vis === 'specific' ? ' show' : '');
  if (vis === 'specific') renderMemberList(prefix);
}

function filterMembers(prefix) {
  renderMemberList(prefix);
}

function renderMemberList(prefix) {
  const q = document.getElementById(`${prefix}-member-search`).value.trim().toLowerCase();
  const selected = prefix === 'upload' ? selectedUploadEmails : selectedEditEmails;
  const filtered = allMembers.filter(m =>
    !q || (m.name||'').toLowerCase().includes(q) || (m.email||'').toLowerCase().includes(q)
  );
  const container = document.getElementById(`${prefix}-member-list`);
  container.innerHTML = filtered.map(m => `
    <label class="member-item">
      <input type="checkbox" ${selected.has(m.email) ? 'checked' : ''}
        onchange="toggleMember('${prefix}','${m.email}',this.checked)">
      <div>
        <div class="member-name">${escHtml(m.name||m.email)}</div>
        <div class="member-email">${escHtml(m.email)}</div>
      </div>
    </label>
  `).join('');
}

function toggleMember(prefix, email, checked) {
  const set = prefix === 'upload' ? selectedUploadEmails : selectedEditEmails;
  checked ? set.add(email) : set.delete(email);
}

// ── 모달 닫기 ──
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
function closeModalOutside(e, id) { if (e.target.id === id) closeModal(id); }

init();
</script>
</body>
</html>"""


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    return HTMLResponse(content=REPORTS_HTML)


# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/api/reports/members")
async def list_members(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    cursor = boa_members_collection.find(
        {}, {"email": 1, "name": 1, "department": 1, "_id": 0}
    ).sort("name", 1)
    members = await cursor.to_list(length=500)
    return JSONResponse(members)


@router.get("/api/reports")
async def list_reports(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)
    email = user["email"]
    all_docs = await boa_reports_collection.find().sort("created_at", -1).to_list(length=500)
    visible = [_serialize(doc) for doc in all_docs if await _can_view(doc, email)]
    return JSONResponse(visible)


@router.post("/api/reports")
async def create_report(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: str = Form(""),
    visibility: str = Form("private"),
    owner_teams: str = Form(""),
    allowed_emails: str = Form(""),
):
    redirect = require_login(request)
    if redirect:
        return redirect
    if visibility not in ("public", "team", "specific", "private"):
        raise HTTPException(status_code=400, detail="잘못된 visibility 값입니다.")

    user = get_current_user(request)
    email = user["email"]
    content = await file.read()
    filename = file.filename or "report.html"
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    blob_path = f"{ts}_{filename}"

    from src.core.blob_report_client import upload_report
    await asyncio.to_thread(upload_report, blob_path, content)

    teams = [t.strip() for t in owner_teams.split(",") if t.strip()]
    emails = [e.strip() for e in allowed_emails.split(",") if e.strip()]

    doc = {
        "blob_path": blob_path,
        "title": title,
        "description": description,
        "visibility": visibility,
        "owner_teams": teams,
        "allowed_emails": emails,
        "created_by": email,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    result = await boa_reports_collection.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    for k, v in doc.items():
        if isinstance(v, datetime):
            doc[k] = v.isoformat()
    return JSONResponse(doc, status_code=201)


@router.put("/api/reports/{report_id}")
async def update_report(report_id: str, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)
    email = user["email"]
    doc = await boa_reports_collection.find_one({"_id": ObjectId(report_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
    if doc.get("created_by") != email:
        raise HTTPException(status_code=403, detail="수정 권한이 없습니다.")

    body = await request.json()
    allowed = {"title", "description", "visibility", "owner_teams", "allowed_emails"}
    update = {k: v for k, v in body.items() if k in allowed}
    if "visibility" in update and update["visibility"] not in ("public", "team", "specific", "private"):
        raise HTTPException(status_code=400, detail="잘못된 visibility 값입니다.")
    update["updated_at"] = datetime.utcnow()

    await boa_reports_collection.update_one({"_id": ObjectId(report_id)}, {"$set": update})
    updated = await boa_reports_collection.find_one({"_id": ObjectId(report_id)})
    return JSONResponse(_serialize(updated))


@router.delete("/api/reports/{report_id}")
async def delete_report(report_id: str, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)
    email = user["email"]
    doc = await boa_reports_collection.find_one({"_id": ObjectId(report_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
    if doc.get("created_by") != email:
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다.")

    from src.core.blob_report_client import delete_report as blob_delete
    try:
        await asyncio.to_thread(blob_delete, doc["blob_path"])
    except Exception as e:
        print(f"[Reports] Blob 삭제 실패 (무시): {e}")

    await boa_reports_collection.delete_one({"_id": ObjectId(report_id)})
    return JSONResponse({"ok": True})


@router.get("/api/reports/{report_id}/url")
async def get_report_url(report_id: str, request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    user = get_current_user(request)
    email = user["email"]
    doc = await boa_reports_collection.find_one({"_id": ObjectId(report_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="리포트를 찾을 수 없습니다.")
    if not await _can_view(doc, email):
        raise HTTPException(status_code=403, detail="열람 권한이 없습니다.")

    from src.core.blob_report_client import get_report_sas_url
    url = await asyncio.to_thread(get_report_sas_url, doc["blob_path"], 2)
    return JSONResponse({"url": url})
