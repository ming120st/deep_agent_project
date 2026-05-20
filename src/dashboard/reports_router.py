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
import mimetypes
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
    # 업로더 본인은 공개범위 무관하게 항상 열람 가능
    if doc.get("created_by") == email:
        return True
    v = doc.get("visibility", "private")
    if v == "public":
        return True
    if v == "private":
        return False
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
  body { background:var(--bg); color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:15px; min-height:100vh; display:flex; flex-direction:column; }

  header { background:var(--bg2); border-bottom:1px solid var(--border); padding:0 28px; height:56px; box-shadow:0 1px 3px rgba(0,0,0,.06); display:flex; align-items:center; justify-content:space-between; position:sticky; top:0; z-index:100; flex-shrink:0; }
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

  /* 2패널 레이아웃 */
  .layout { display:flex; flex:1; overflow:hidden; height:calc(100vh - 56px); }

  /* 왼쪽 패널 — 리포트 목록 */
  .panel-left { width:340px; flex-shrink:0; border-right:1px solid var(--border); background:var(--bg2); display:flex; flex-direction:column; overflow:hidden; }
  .panel-left-header { padding:16px 20px 12px; border-bottom:1px solid var(--border); }
  .panel-left-title { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
  .panel-left-title h2 { font-size:13px; font-weight:600; color:var(--text-bright); }
  .panel-left-title span { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }
  .search-input { width:100%; background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:7px 12px; font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; color:var(--text); outline:none; transition:border-color .15s; margin-bottom:8px; }
  .search-input:focus { border-color:var(--accent); }
  .search-input::placeholder { color:var(--text-dim); }
  .btn-upload { width:100%; background:var(--accent); border:none; border-radius:6px; padding:8px 14px; color:#fff; font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; font-weight:500; cursor:pointer; transition:opacity .15s; }
  .btn-upload:hover { opacity:.85; }
  .report-list { flex:1; overflow-y:auto; }
  .report-item { padding:14px 20px; border-bottom:1px solid var(--border); cursor:pointer; transition:background .1s; }
  .report-item:hover { background:var(--bg3); }
  .report-item.active { background:var(--accent-dim); border-left:3px solid var(--accent); }
  .report-item-top { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; margin-bottom:4px; }
  .report-item-title { font-size:13px; font-weight:500; color:var(--text-bright); line-height:1.4; flex:1; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }
  .report-item-badge { font-family:'IBM Plex Mono',monospace; font-size:10px; padding:2px 6px; border-radius:3px; flex-shrink:0; }
  .badge-public  { background:rgba(34,197,94,.1);  color:#15803d; }
  .badge-team    { background:rgba(37,99,235,.1);  color:#1d4ed8; }
  .badge-specific{ background:rgba(124,58,237,.1); color:#7c3aed; }
  .badge-private { background:rgba(107,114,128,.1);color:#6b7280; }
  .report-item-desc { font-size:12px; color:var(--text-dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-top:2px; }
  .report-item-meta { display:flex; align-items:center; gap:4px; margin-top:5px; }
  .report-item-author { font-size:11px; color:var(--text-dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:140px; }
  .report-item-date { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }
  .empty-list { padding:48px 20px; text-align:center; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }

  /* 오른쪽 패널 — 리포트 뷰어 */
  .panel-right { flex:1; display:flex; flex-direction:column; overflow:hidden; background:var(--bg); }
  .panel-right-empty { display:flex; align-items:center; justify-content:center; height:100%; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }

  /* 뷰어 헤더 */
  .viewer-header { background:var(--bg2); border-bottom:1px solid var(--border); padding:14px 24px; display:flex; align-items:center; gap:10px; flex-shrink:0; }
  .viewer-title { font-size:14px; font-weight:600; color:var(--text-bright); flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .viewer-meta { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); white-space:nowrap; }
  .btn-open-tab { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:6px 14px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn-open-tab:hover { border-color:var(--accent); color:var(--accent); }
  .btn-edit-report { background:none; border:1px solid var(--border); border-radius:6px; padding:6px 14px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; white-space:nowrap; }
  .btn-edit-report:hover { border-color:var(--accent); color:var(--accent); }
  .report-iframe { flex:1; border:none; width:100%; background:var(--bg2); }

  /* 로딩 */
  .loading-viewer { display:flex; align-items:center; justify-content:center; flex:1; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }
  .spinner { width:16px; height:16px; border:2px solid var(--border); border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; margin-right:10px; }
  @keyframes spin { to { transform:rotate(360deg); } }

  /* 공통 버튼 */
  .btn-primary { background:var(--accent); border:none; border-radius:6px; padding:9px 18px; color:#fff; font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; font-weight:500; cursor:pointer; transition:opacity .15s; white-space:nowrap; }
  .btn-primary:hover { opacity:.85; }
  .btn-secondary { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:8px 16px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; cursor:pointer; transition:all .15s; }
  .btn-secondary:hover { border-color:var(--accent); color:var(--accent); }
  .btn-danger { background:none; border:1px solid var(--border); border-radius:6px; padding:6px 12px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; }
  .btn-danger:hover { border-color:var(--red); color:var(--red); }

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

<div class="layout">
  <!-- 왼쪽: 리포트 목록 -->
  <div class="panel-left">
    <div class="panel-left-header">
      <div class="panel-left-title">
        <h2>리포트 목록</h2>
        <span id="report-count"></span>
      </div>
      <input type="text" class="search-input" id="search-input"
             placeholder="제목 검색..." oninput="renderList()">
      <button class="btn-upload" onclick="openUploadModal()">+ 리포트 등록</button>
    </div>
    <div class="report-list" id="report-list">
      <div class="empty-list">로딩 중...</div>
    </div>
  </div>

  <!-- 오른쪽: 리포트 뷰어 -->
  <div class="panel-right" id="panel-right">
    <div class="panel-right-empty">← 리포트를 선택하세요</div>
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
        <input type="file" id="file-input" accept=".html,.pdf,.png,.jpg,.jpeg,.svg,.webp" style="display:none" onchange="onFileChosen(this)">
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
let currentReportId = null;

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
    renderList();
  } catch(e) {
    document.getElementById('report-list').innerHTML = '<div class="empty-list">리포트를 불러오지 못했습니다.</div>';
  }
}

function renderList() {
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  const filtered = q
    ? allReports.filter(r => r.title.toLowerCase().includes(q) || (r.description||'').toLowerCase().includes(q))
    : allReports;

  document.getElementById('report-count').textContent = filtered.length;

  if (!filtered.length) {
    document.getElementById('report-list').innerHTML = '<div class="empty-list">리포트가 없습니다.</div>';
    return;
  }

  document.getElementById('report-list').innerHTML = filtered.map(r => {
    const badgeCls = VISIBILITY_BADGE[r.visibility] || 'badge-private';
    const badgeLabel = VISIBILITY_LABEL[r.visibility] || r.visibility;
    const date = r.created_at ? new Date(r.created_at).toLocaleDateString('ko-KR', {month:'2-digit', day:'2-digit'}) : '';
    const author = (r.created_by || '').split('@')[0];
    return `
    <div class="report-item ${r.id === currentReportId ? 'active' : ''}"
         onclick="selectReport('${r.id}')">
      <div class="report-item-top">
        <div class="report-item-title">${escHtml(r.title)}</div>
        <span class="report-item-badge ${badgeCls}">${badgeLabel}</span>
      </div>
      ${r.description ? `<div class="report-item-desc">${escHtml(r.description)}</div>` : ''}
      <div class="report-item-meta">
        <span class="report-item-author">${escHtml(author)}</span>
        <span class="report-item-date">· ${date}</span>
      </div>
    </div>`;
  }).join('');
}

async function selectReport(reportId) {
  currentReportId = reportId;
  renderList();

  const right = document.getElementById('panel-right');
  right.innerHTML = '<div class="loading-viewer"><div class="spinner"></div>로딩 중...</div>';

  try {
    const { url } = await apiFetch(`/api/reports/${reportId}/url`);
    const report = allReports.find(r => r.id === reportId);
    const isOwner = currentUser && report && report.created_by === currentUser.email;
    const date = report?.created_at ? new Date(report.created_at).toLocaleDateString('ko-KR') : '';
    const badgeCls = VISIBILITY_BADGE[report?.visibility] || 'badge-private';
    const badgeLabel = VISIBILITY_LABEL[report?.visibility] || report?.visibility;

    const ct = report?.content_type || 'text/html';
    let viewerBody;
    if (ct.startsWith('image/')) {
      viewerBody = `<div style="flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;padding:24px;background:var(--bg3)">
        <img src="${url}" alt="${escHtml(report?.title||'')}" style="max-width:100%;max-height:100%;border-radius:6px;box-shadow:0 2px 16px rgba(0,0,0,.12)">
      </div>`;
    } else {
      const iframeType = ct === 'application/pdf' ? ' type="application/pdf"' : '';
      viewerBody = `<iframe class="report-iframe" src="${url}"${iframeType} title="${escHtml(report?.title||'')}"></iframe>`;
    }

    right.innerHTML = `
      <div class="viewer-header">
        <div class="viewer-title">${escHtml(report?.title || '')}</div>
        <span class="report-item-badge ${badgeCls}" style="padding:3px 8px">${badgeLabel}</span>
        <span class="viewer-meta">${escHtml((report?.created_by||'').split('@')[0])} · ${date}</span>
        ${isOwner ? `<button class="btn-edit-report" onclick='openEditModal(${JSON.stringify(report).replace(/'/g,"\\'")})'>\u00a0수정</button>` : ''}
        <button class="btn-open-tab" onclick="window.open('${url}','_blank')">새 탭 ↗</button>
      </div>
      ${viewerBody}
    `;
  } catch(e) {
    right.innerHTML = `<div class="panel-right-empty" style="color:var(--red)">${escHtml(e.message)}</div>`;
  }
}

function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
    if (currentReportId === id) selectReport(id);
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
    if (currentReportId === id) {
      currentReportId = null;
      document.getElementById('panel-right').innerHTML = '<div class="panel-right-empty">← 리포트를 선택하세요</div>';
    }
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

    detected_type, _ = mimetypes.guess_type(filename)
    content_type = detected_type or file.content_type or "application/octet-stream"
    # 텍스트 계열은 charset 명시 (한글 깨짐 방지)
    if content_type.startswith("text/") and "charset" not in content_type:
        content_type += "; charset=utf-8"

    from src.core.blob_report_client import upload_report
    await asyncio.to_thread(upload_report, blob_path, content, content_type)

    teams = [t.strip() for t in owner_teams.split(",") if t.strip()]
    emails = [e.strip() for e in allowed_emails.split(",") if e.strip()]

    doc = {
        "blob_path": blob_path,
        "title": title,
        "description": description,
        "visibility": visibility,
        "owner_teams": teams,
        "allowed_emails": emails,
        "content_type": content_type,
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
