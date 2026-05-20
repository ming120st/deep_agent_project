from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from src.dashboard.auth import get_current_user, require_login, is_admin
from src.core.session_logger import get_all_sessions, get_sessions_by_user, get_session

router = APIRouter(tags=["Sessions"])

# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/api/sessions")
async def api_get_sessions(request: Request, skip: int = 0, limit: int = 50):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    if await is_admin(user["email"]):
        return await get_all_sessions(skip=skip, limit=limit)
    return await get_sessions_by_user(user["email"], skip=skip, limit=limit)

@router.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    doc = await get_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    if doc["user_id"] != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다")
    return doc

# ── HTML ──────────────────────────────────────────────────────────────────────

SESSIONS_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOA 대화 로그</title>
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

  /* 헤더 */
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
  .user-name { font-size:13px; }
  .admin-badge { background:var(--accent-dim); color:#1d4ed8; font-family:'IBM Plex Mono',monospace; font-size:10px; padding:2px 8px; border-radius:4px; border:1px solid var(--accent); }
  .btn-logout { background:none; border:1px solid var(--border); border-radius:6px; padding:5px 12px; color:var(--text-dim); font-size:12px; cursor:pointer; transition:all .15s; }
  .btn-logout:hover { border-color:var(--red); color:var(--red); }

  /* 레이아웃 — 2패널 */
  .layout { display:flex; flex:1; overflow:hidden; height:calc(100vh - 56px); }

  /* 왼쪽 패널 — 세션 목록 */
  .panel-left { width:360px; flex-shrink:0; border-right:1px solid var(--border); background:var(--bg2); display:flex; flex-direction:column; overflow:hidden; }
  .panel-left-header { padding:16px 20px 12px; border-bottom:1px solid var(--border); }
  .panel-left-header h2 { font-size:13px; font-weight:600; color:var(--text-bright); margin-bottom:10px; }
  .search-input { width:100%; background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:7px 12px; font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; color:var(--text); outline:none; transition:border-color .15s; }
  .search-input:focus { border-color:var(--accent); }
  .search-input::placeholder { color:var(--text-dim); }
  .session-list { flex:1; overflow-y:auto; }
  .session-item { padding:14px 20px; border-bottom:1px solid var(--border); cursor:pointer; transition:background .1s; }
  .session-item:hover { background:var(--bg3); }
  .session-item.active { background:var(--accent-dim); border-left:2px solid var(--accent); }
  .session-item-top { display:flex; align-items:center; justify-content:space-between; margin-bottom:5px; }
  .session-user { font-size:13px; font-weight:500; color:var(--text-bright); }
  .session-time { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }
  .session-meta { display:flex; align-items:center; gap:8px; }
  .session-turns { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }
  .session-cost { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--green); }
  .session-space { font-size:11px; color:var(--text-dim); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:200px; }
  .empty-list { padding:48px 20px; text-align:center; color:var(--text-dim); font-size:13px; }

  /* 오른쪽 패널 — 세션 상세 */
  .panel-right { flex:1; overflow-y:auto; background:var(--bg); }
  .panel-right-empty { display:flex; align-items:center; justify-content:center; height:100%; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }

  /* 세션 상세 헤더 */
  .detail-header { background:var(--bg2); border-bottom:1px solid var(--border); padding:20px 28px; }
  .detail-header-top { display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:12px; }
  .detail-user { font-size:16px; font-weight:600; color:var(--text-bright); }
  .detail-session-id { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); margin-top:3px; }
  .detail-stats { display:flex; gap:20px; }
  .stat-item { text-align:center; }
  .stat-item-val { font-family:'IBM Plex Mono',monospace; font-size:18px; font-weight:600; color:var(--text-bright); }
  .stat-item-label { font-size:11px; color:var(--text-dim); margin-top:2px; }

  /* 턴 목록 */
  .turns-container { padding:20px 28px; display:flex; flex-direction:column; gap:12px; }

  /* 턴 카드 */
  .turn-card { background:var(--bg2); border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .turn-header { padding:14px 18px; cursor:pointer; display:flex; align-items:center; gap:12px; transition:background .1s; user-select:none; }
  .turn-header:hover { background:var(--bg3); }
  .turn-seq { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); min-width:32px; }
  .turn-intent-badge { padding:3px 10px; border-radius:4px; font-family:'IBM Plex Mono',monospace; font-size:11px; font-weight:500; }
  .intent-new_agent      { background:rgba(37,99,235,.1);  color:#1d4ed8; }
  .intent-continue_task  { background:rgba(16,185,129,.1); color:#065f46; }
  .intent-rag            { background:rgba(139,92,246,.1); color:#6d28d9; }
  .intent-light_response { background:rgba(245,158,11,.1); color:#92400e; }
  .intent-unknown        { background:var(--bg3); color:var(--text-dim); }
  .turn-user-msg { flex:1; font-size:13px; color:var(--text); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .turn-meta-right { display:flex; align-items:center; gap:12px; flex-shrink:0; }
  .turn-cost { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--green); }
  .turn-duration { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); }
  .turn-toggle { font-size:12px; color:var(--text-dim); transition:transform .2s; }
  .turn-toggle.open { transform:rotate(180deg); }

  /* 이벤트 타임라인 */
  .events-container { padding:0 18px 16px; display:none; }
  .events-container.open { display:block; }
  .timeline { position:relative; padding-left:24px; padding-top:8px; }
  .timeline::before { content:''; position:absolute; left:7px; top:16px; bottom:8px; width:1px; background:var(--border); }

  .event-item { position:relative; margin-bottom:10px; }
  .event-dot { position:absolute; left:-20px; top:4px; width:8px; height:8px; border-radius:50%; border:1.5px solid var(--border); background:var(--bg2); }

  /* 이벤트 타입별 도트 색상 */
  .event-dot.user_message  { background:#6366f1; border-color:#6366f1; }
  .event-dot.llm_intent    { background:#2563eb; border-color:#2563eb; }
  .event-dot.llm_extract   { background:#2563eb; border-color:#2563eb; }
  .event-dot.llm_response  { background:#2563eb; border-color:#2563eb; }
  .event-dot.llm_generic   { background:#2563eb; border-color:#2563eb; }
  .event-dot.agent_route   { background:#8b5cf6; border-color:#8b5cf6; }
  .event-dot.rag_search    { background:#8b5cf6; border-color:#8b5cf6; }
  .event-dot.api_call      { background:#f59e0b; border-color:#f59e0b; }
  .event-dot.device_job    { background:#f59e0b; border-color:#f59e0b; }
  .event-dot.hitl_wait     { background:#eab308; border-color:#eab308; }
  .event-dot.hitl_resume   { background:#22c55e; border-color:#22c55e; }
  .event-dot.chat_send     { background:#22c55e; border-color:#22c55e; }
  .event-dot.complete      { background:#22c55e; border-color:#22c55e; }
  .event-dot.cancel        { background:#6b7280; border-color:#6b7280; }
  .event-dot.error         { background:#ef4444; border-color:#ef4444; }

  .event-body { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:10px 12px; }
  .event-top { display:flex; align-items:center; gap:8px; margin-bottom:4px; flex-wrap:wrap; }
  .event-seq { font-family:'IBM Plex Mono',monospace; font-size:10px; color:var(--text-dim); }
  .event-label { font-size:12px; font-weight:500; color:var(--text-bright); }
  .event-chips { display:flex; gap:6px; margin-left:auto; align-items:center; }
  .chip { font-family:'IBM Plex Mono',monospace; font-size:10px; padding:2px 7px; border-radius:3px; }
  .chip-duration { background:rgba(107,114,128,.1); color:#6b7280; }
  .chip-cost     { background:rgba(34,197,94,.1);   color:#15803d; }
  .chip-tokens   { background:rgba(37,99,235,.1);   color:#1d4ed8; }
  .event-content { font-size:12px; color:var(--text); margin-top:4px; word-break:break-all; }
  .event-content.user-msg { font-size:13px; color:var(--text-bright); }
  .event-llm { margin-top:6px; display:flex; gap:8px; flex-wrap:wrap; }
  .event-meta-docs { margin-top:6px; }
  .doc-chip { display:inline-flex; align-items:center; gap:4px; background:rgba(139,92,246,.08); border:1px solid rgba(139,92,246,.2); border-radius:4px; padding:2px 8px; font-size:11px; color:#6d28d9; margin:2px 3px 2px 0; }
  .event-ts { font-family:'IBM Plex Mono',monospace; font-size:10px; color:var(--text-dim); margin-top:4px; }

  /* 로딩 */
  .loading-detail { display:flex; align-items:center; justify-content:center; padding:60px; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }
  .spinner { width:16px; height:16px; border:2px solid var(--border); border-top-color:var(--accent); border-radius:50%; animation:spin .7s linear infinite; margin-right:10px; }
  @keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body>

<header>
  <div class="logo">BOA <span>//</span> 대화 로그</div>
  <nav class="header-nav">
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link">구성원</a>
    <a href="/sessions" class="nav-link active">대화 로그</a>
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

<div class="layout">
  <!-- 왼쪽: 세션 목록 -->
  <div class="panel-left">
    <div class="panel-left-header">
      <h2>세션 목록</h2>
      <input class="search-input" type="text" id="search-input"
             placeholder="이름, 스페이스 검색..." oninput="renderList()">
    </div>
    <div class="session-list" id="session-list">
      <div class="empty-list">로딩 중...</div>
    </div>
  </div>

  <!-- 오른쪽: 세션 상세 -->
  <div class="panel-right" id="panel-right">
    <div class="panel-right-empty">← 세션을 선택하세요</div>
  </div>
</div>

<script>
let allSessions = [];
let currentSessionId = null;
let currentUser = null;

// ── 초기화 ──────────────────────────────────────────────────────────────────
async function init() {
  const res = await fetch('/api/me');
  currentUser = await res.json();
  document.getElementById('user-name').textContent = currentUser.name;
  document.getElementById('user-avatar').src = currentUser.picture || '';
  if (currentUser.is_admin) document.getElementById('admin-badge').style.display = 'inline-block';
  await loadSessions(1);
}

// ── 세션 목록 로드 ────────────────────────────────────────────────────────────
let currentPage = 1;
const itemsPerPage = 50;
let totalItems = 0;

async function loadSessions(page = 1) {
  currentPage = page;
  const skip = (page - 1) * itemsPerPage;
  const res = await fetch(`/api/sessions?skip=${skip}&limit=${itemsPerPage}`);
  const data = await res.json();
  allSessions = data.items;
  totalItems = data.total;
  renderList();
  renderPagination();
}

function renderPagination() {
  const totalPages = Math.ceil(totalItems / itemsPerPage);
  const paginationHtml = `
    <div style="display:flex; justify-content:center; padding:10px; gap:5px;">
      <button onclick="loadSessions(${Math.max(1, currentPage - 1)})" ${currentPage === 1 ? 'disabled' : ''}>이전</button>
      <span>${currentPage} / ${totalPages > 0 ? totalPages : 1}</span>
      <button onclick="loadSessions(${Math.min(totalPages, currentPage + 1)})" ${currentPage === totalPages || totalPages === 0 ? 'disabled' : ''}>다음</button>
    </div>
  `;
  const container = document.getElementById('session-list-container') || document.getElementById('session-list').parentElement;
  
  let pagDiv = document.getElementById('pagination-controls');
  if(!pagDiv) {
    pagDiv = document.createElement('div');
    pagDiv.id = 'pagination-controls';
    container.appendChild(pagDiv);
  }
  pagDiv.innerHTML = paginationHtml;
}

function renderList() {
  const q = document.getElementById('search-input').value.toLowerCase();
  const filtered = q
    ? allSessions.filter(s =>
        (s.user_name||'').toLowerCase().includes(q) ||
        (s.space_name||'').toLowerCase().includes(q))
    : allSessions;

  if (!filtered.length) {
    document.getElementById('session-list').innerHTML =
      '<div class="empty-list">세션이 없습니다</div>';
    return;
  }

  document.getElementById('session-list').innerHTML = filtered.map(s => `
    <div class="session-item ${s.session_id === currentSessionId ? 'active' : ''}"
         onclick="selectSession('${s.session_id}')">
      <div class="session-item-top">
        <span class="session-user">${s.user_name || '—'}</span>
        <span class="session-time">${fmtDate(s.started_at)}</span>
      </div>
      <div class="session-space">${s.space_name || ''}</div>
      <div class="session-meta" style="margin-top:5px">
        <span class="session-turns">${(s.summary?.total_turns||0)}턴</span>
        <span class="session-turns">·</span>
        <span class="session-turns">LLM ${(s.summary?.total_llm_calls||0)}회</span>
        ${s.summary?.total_cost_usd > 0
          ? `<span class="session-cost">$${s.summary.total_cost_usd.toFixed(5)}</span>` : ''}
      </div>
    </div>
  `).join('');
}

// ── 세션 선택 ─────────────────────────────────────────────────────────────────
async function selectSession(sessionId) {
  currentSessionId = sessionId;
  renderList(); // active 상태 반영

  document.getElementById('panel-right').innerHTML =
    '<div class="loading-detail"><div class="spinner"></div>로딩 중...</div>';

  const res = await fetch(`/api/sessions/${sessionId}`);
  const doc = await res.json();
  renderDetail(doc);
}

// ── 세션 상세 렌더 ────────────────────────────────────────────────────────────
function renderDetail(doc) {
  const turns = doc.turns || [];
  const s = doc.summary || {};

  const header = `
    <div class="detail-header">
      <div class="detail-header-top">
        <div>
          <div class="detail-user">${doc.user_name || '—'}</div>
          <div class="detail-session-id">${doc.session_id}</div>
          <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${doc.space_name||''}</div>
        </div>
        <div class="detail-stats">
          <div class="stat-item">
            <div class="stat-item-val">${s.total_turns||0}</div>
            <div class="stat-item-label">턴</div>
          </div>
          <div class="stat-item">
            <div class="stat-item-val">${s.total_llm_calls||0}</div>
            <div class="stat-item-label">LLM 호출</div>
          </div>
          <div class="stat-item">
            <div class="stat-item-val" style="color:var(--green)">$${(s.total_cost_usd||0).toFixed(5)}</div>
            <div class="stat-item-label">총 비용</div>
          </div>
          <div class="stat-item">
            <div class="stat-item-val">${fmtDate(doc.started_at)}</div>
            <div class="stat-item-label">시작</div>
          </div>
        </div>
      </div>
    </div>`;

  const turnsHtml = turns.map((turn, idx) => renderTurn(turn, idx)).join('');

  document.getElementById('panel-right').innerHTML =
    header + `<div class="turns-container">${turnsHtml || '<div style="color:var(--text-dim);font-size:13px">턴 없음</div>'}</div>`;
}

// ── 턴 렌더 ──────────────────────────────────────────────────────────────────
function renderTurn(turn, idx) {
  const events = turn.events || [];
  const userMsg = events.find(e => e.type === 'user_message');
  const s = turn.summary || {};
  const intent = turn.intent || 'unknown';

  const intentLabel = {
    new_agent: '새 업무',
    continue_task: '업무 진행',
    rag: 'RAG',
    light_response: 'Light',
  }[intent] || intent;

  const eventsHtml = events.map((ev, ei) => renderEvent(ev, ei)).join('');

  return `
    <div class="turn-card">
      <div class="turn-header" onclick="toggleTurn(this)">
        <span class="turn-seq">#${idx+1}</span>
        <span class="turn-intent-badge intent-${intent}">${intentLabel}</span>
        <span class="turn-user-msg">${userMsg?.content || '—'}</span>
        <div class="turn-meta-right">
          ${s.total_cost_usd > 0
            ? `<span class="turn-cost">$${s.total_cost_usd.toFixed(5)}</span>` : ''}
          ${s.duration_ms != null
            ? `<span class="turn-duration">${fmtDuration(s.duration_ms)}</span>` : ''}
          <span class="turn-toggle">▼</span>
        </div>
      </div>
      <div class="events-container">
        <div class="timeline">${eventsHtml}</div>
      </div>
    </div>`;
}

// ── 이벤트 렌더 ──────────────────────────────────────────────────────────────
function renderEvent(ev, idx) {
  const llm = ev.llm;
  const meta = ev.meta || {};

  // 칩
  const chips = [];
  if (ev.duration_ms != null) chips.push(`<span class="chip chip-duration">${fmtDuration(ev.duration_ms)}</span>`);
  if (llm?.cost_usd > 0)      chips.push(`<span class="chip chip-cost">$${llm.cost_usd.toFixed(5)}</span>`);
  if (llm?.input_tokens)      chips.push(`<span class="chip chip-tokens">↑${llm.input_tokens} ↓${llm.output_tokens}</span>`);

  // 컨텐츠
  let contentHtml = '';
  if (ev.content) {
    const cls = ev.type === 'user_message' ? 'user-msg' : '';
    contentHtml = `<div class="event-content ${cls}">${escHtml(ev.content)}</div>`;
  }

  // RAG 문서 목록
  let docsHtml = '';
  if (meta.docs?.length) {
    docsHtml = `<div class="event-meta-docs">
      ${meta.docs.map(d =>
        `<span class="doc-chip">📄 ${escHtml(d.title)}${d.score != null ? ` <span style="opacity:.6">${(d.score*100).toFixed(0)}%</span>` : ''}</span>`
      ).join('')}
    </div>`;
  }

  return `
    <div class="event-item">
      <div class="event-dot ${ev.type}"></div>
      <div class="event-body">
        <div class="event-top">
          <span class="event-seq">#${idx+1}</span>
          <span class="event-label">${escHtml(ev.label||ev.type)}</span>
          ${chips.length ? `<div class="event-chips">${chips.join('')}</div>` : ''}
        </div>
        ${contentHtml}
        ${docsHtml}
        <div class="event-ts">${fmtDateFull(ev.ts)}</div>
      </div>
    </div>`;
}

// ── 턴 토글 ──────────────────────────────────────────────────────────────────
function toggleTurn(header) {
  const container = header.nextElementSibling;
  const toggle = header.querySelector('.turn-toggle');
  const isOpen = container.classList.toggle('open');
  toggle.classList.toggle('open', isOpen);
}

// ── 유틸 ─────────────────────────────────────────────────────────────────────
function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('ko-KR',{month:'2-digit',day:'2-digit'})
    + ' ' + d.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'});
}
function fmtDateFull(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('ko-KR');
}
function fmtDuration(ms) {
  if (ms == null) return '';
  if (ms >= 60000) return (ms/60000).toFixed(1) + 'm';
  if (ms >= 1000)  return (ms/1000).toFixed(1) + 's';
  return ms + 'ms';
}
function escHtml(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

init();
</script>
</body>
</html>"""

@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect

    from src.dashboard.auth import get_current_user
    user = get_current_user(request)
    from src.core.mongo_client import boa_admins_collection
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    is_admin = doc is not None

    html_content = SESSIONS_HTML
    if not is_admin:
        html_content = html_content.replace('<a href="/spaces" class="nav-link">스페이스</a>', '')
        html_content = html_content.replace('<a href="/members" class="nav-link">구성원</a>', '')

    return HTMLResponse(content=html_content)