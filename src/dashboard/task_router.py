"""
BOA 대시보드 라우터
GET  /dashboard                  → HTML 대시보드 (로그인 필요)
GET  /api/tasks                  → task 목록 (관리자: 전체 / 사용자: 본인 것만)
GET  /api/me                     → 현재 로그인 유저 정보 + 관리자 여부
PATCH /api/tasks/{task_id}/status → 상태 변경 (관리자 전용)
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from src.core.task_manager import get_all_tasks, get_tasks_by_user, update_task_status, get_task
from src.dashboard.auth import require_login, get_current_user
from src.core.mongo_client import boa_admins_collection
from src.agent.registry import AGENT_REGISTRY
from src.core.trace_logger import get_trace

router = APIRouter()

# ── 관리자 확인 ───────────────────────────────────────────────────────────────
async def is_admin(email: str) -> bool:
    return await boa_admins_collection.find_one({"email": email}) is not None

# ── 직렬화 헬퍼 ───────────────────────────────────────────────────────────────
def serialize_tasks(tasks: list) -> list:
    for task in tasks:
        for key, val in task.items():
            if isinstance(val, datetime):
                task[key] = val.isoformat()
        if "task_history" in task:
            for entry in task["task_history"]:
                for k, v in entry.items():
                    if isinstance(v, datetime):
                        entry[k] = v.isoformat()
    return tasks

# ── API 엔드포인트 ─────────────────────────────────────────────────────────────

@router.get("/api/agents")
async def api_get_agents():
    """에이전트별 파라미터 display_name 맵 반환"""
    result = {}
    for agent_name, agent_info in AGENT_REGISTRY.items():
        result[agent_name] = {
            key: param.get("display_name", key)
            for key, param in agent_info.get("parameters", {}).items()
        }
    return result

@router.get("/api/me")
async def api_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    return {
        **user,
        "is_admin": await is_admin(user["email"])
    }

@router.get("/api/tasks")
async def api_get_tasks(request: Request, status: Optional[str] = None, skip: int = 0, limit: int = 50):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    if await is_admin(user["email"]):
        data = await get_all_tasks(status=status, skip=skip, limit=limit)
    else:
        data = await get_tasks_by_user(user["email"], status=status, skip=skip, limit=limit)

    return {"items": serialize_tasks(data["items"]), "total": data["total"], "skip": skip, "limit": limit}

class StatusUpdateRequest(BaseModel):
    status: str
    admin_memo: Optional[str] = None

@router.patch("/api/tasks/{task_id}/status")
async def api_update_task_status(task_id: str, body: StatusUpdateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")
    if not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="관리자만 상태를 변경할 수 있습니다")
    try:
        await update_task_status(
            task_id=task_id,
            status=body.status,
            changed_by=user["email"],
            admin_memo=body.admin_memo,
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
  
@router.get("/api/tasks/{task_id}/trace")
async def api_get_trace(task_id: str, request: Request):
    """task_id에 대한 실행 시퀀스 + summary 반환"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    # 본인 task이거나 관리자만 조회 가능
    task = await get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="태스크를 찾을 수 없습니다")
    if task["user_id"] != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다")

    trace = await get_trace(task_id)
    if not trace:
        return {"task_id": task_id, "steps": [], "summary": None}
    return trace


# ── HTML 대시보드 ──────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOA 업무 대시보드</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #f5f6fa; --bg2: #ffffff; --bg3: #f0f2f8;
    --border: #e2e7f0; --text: #1e2436; --text-dim: #7b8499;
    --text-bright: #0f1628; --accent: #2563eb; --accent-dim: #dbeafe;
    --green: #22c55e; --yellow: #eab308; --red: #ef4444;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 15px; min-height: 100vh; }

  /* 헤더 */
  header { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 0 28px; height: 56px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
  .logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 16px; color: var(--text-bright); letter-spacing: .05em; }
  .logo span { color: var(--accent); }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .user-info { display: flex; align-items: center; gap: 10px; }
  .user-avatar { width: 28px; height: 28px; border-radius: 50%; border: 1px solid var(--border); }
  .user-name { font-size: 13px; color: var(--text); }
  .admin-badge { background: var(--accent-dim); color: #1d4ed8; font-family: 'IBM Plex Mono', monospace; font-size: 10px; padding: 2px 8px; border-radius: 4px; border: 1px solid var(--accent); }
  
  /* 네비게이션 링크 */
  .header-nav { display: flex; align-items: center; gap: 4px; }
  .nav-link { padding: 6px 14px; border-radius: 6px; font-size: 13px; color: var(--text-dim); text-decoration: none; transition: all .15s; }
  .nav-link:hover { color: var(--text); background: #eef0f8; }
  .nav-link.active { color: var(--accent); background: var(--accent-dim); }

  .btn-logout { background: none; border: 1px solid var(--border); border-radius: 6px; padding: 5px 12px; color: var(--text-dim); font-size: 12px; cursor: pointer; transition: all .15s; }
  .btn-logout:hover { border-color: var(--red); color: var(--red); }
  .live-badge { display: flex; align-items: center; gap: 6px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--text-dim); }
  .live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

  /* 알림 배너 */
  #notif-banner { display: none; background: rgba(254,243,199,0.9); border-bottom: 1px solid rgba(234,179,8,.4); padding: 10px 28px; align-items: center; justify-content: space-between; gap: 12px; }
  #notif-banner.show { display: flex; }
  .notif-text { display: flex; align-items: center; gap: 8px; color: #fbbf24; font-size: 13px; }
  .notif-icon { font-size: 16px; }
  .btn-notif-close { background: none; border: none; color: #fbbf24; cursor: pointer; font-size: 18px; opacity: .7; }
  .btn-notif-close:hover { opacity: 1; }

  /* 레이아웃 */
  .container { padding: 24px 28px; max-width: 1400px; margin: 0 auto; }

  /* 통계 카드 */
  .stats-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 24px; }
  .stat-card { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; cursor: pointer; transition: border-color .15s, background .15s; }
  .stat-card:hover { border-color: var(--accent); }
  .stat-card.active { border-color: var(--accent); background: var(--accent-dim); }
  .stat-label { font-size: 11px; color: var(--text-dim); text-transform: uppercase; letter-spacing: .08em; margin-bottom: 8px; font-family: 'IBM Plex Mono', monospace; }
  .stat-value { font-family: 'IBM Plex Mono', monospace; font-size: 28px; font-weight: 600; color: var(--text-bright); }

  /* 필터 바 */
  .filter-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
  .filter-bar input { flex: 1; background: var(--bg2); border: 1px solid var(--border); border-radius: 6px; padding: 8px 14px; color: var(--text); font-family: 'IBM Plex Sans KR', sans-serif; font-size: 14px; outline: none; transition: border-color .15s; }
  .filter-bar input:focus { border-color: var(--accent); }
  .filter-bar input::placeholder { color: var(--text-dim); }
  .btn-refresh { background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; padding: 8px 16px; color: var(--text); font-family: 'IBM Plex Mono', monospace; font-size: 12px; cursor: pointer; transition: border-color .15s, color .15s; }
  .btn-refresh:hover { border-color: var(--accent); color: var(--accent); }

  /* 테이블 */
  .table-wrap { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; }
  thead tr { background: var(--bg3); border-bottom: 1px solid var(--border); }
  th { padding: 11px 16px; text-align: left; font-family: 'IBM Plex Mono', monospace; font-size: 12px; font-weight: 500; color: var(--text-dim); text-transform: uppercase; letter-spacing: .06em; white-space: nowrap; }
  tbody tr { border-bottom: 1px solid var(--border); transition: background .1s; cursor: pointer; }
  tbody tr:last-child { border-bottom: none; }
  tbody tr:hover { background: #f0f2f8; }
  tbody tr.waiting-row { border-left: 2px solid var(--yellow); }
  td { padding: 12px 16px; vertical-align: middle; }
  .td-mono { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--text-dim); }

  /* 뱃지 */
  .badge { display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 4px; font-family: 'IBM Plex Mono', monospace; font-size: 11px; font-weight: 500; white-space: nowrap; }
  .badge::before { content:''; width:5px; height:5px; border-radius:50%; background:currentColor; }
  .badge-처리중 { background:rgba(37,99,235,.1); color:#1d4ed8; }
  .badge-대기중 { background:rgba(180,131,9,.1); color:#92400e; }
  .badge-완료   { background:rgba(21,128,61,.1);  color:#15803d; }
  .badge-취소   { background:rgba(107,114,128,.1);color:#6b7280; }
  .badge-오류   { background:rgba(185,28,28,.1);  color:#b91c1c; }

  /* 모달 */
  .modal-overlay { display:none; position:fixed; inset:0; background:rgba(15,17,30,.6); z-index:200; backdrop-filter:blur(2px); align-items:center; justify-content:center; }
  .modal-overlay.open { display:flex; }
  .modal { background:var(--bg2); border:1px solid var(--border); border-radius:10px; width:560px; max-width:95vw; max-height:85vh; overflow-y:auto; animation:slideUp .2s ease; }
  @keyframes slideUp { from{transform:translateY(16px);opacity:0} to{transform:translateY(0);opacity:1} }
  .modal-header { display:flex; align-items:center; justify-content:space-between; padding:20px 24px 16px; border-bottom:1px solid var(--border); }
  .modal-title { font-size:15px; font-weight:600; color:var(--text-bright); }
  .modal-close { background:none; border:none; color:var(--text-dim); font-size:20px; cursor:pointer; padding:2px 6px; border-radius:4px; }
  .modal-close:hover { color:var(--text); background:var(--bg3); }
  .modal-body { padding:20px 24px; }
  .detail-section { margin-bottom:20px; }
  .detail-section-title { font-family:'IBM Plex Mono',monospace; font-size:10px; text-transform:uppercase; letter-spacing:.1em; color:var(--text-dim); margin-bottom:10px; padding-bottom:6px; border-bottom:1px solid var(--border); }
  .detail-row { display:flex; gap:12px; margin-bottom:7px; align-items:flex-start; }
  .detail-key { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); min-width:120px; padding-top:1px; }
  .detail-val { font-size:13px; color:var(--text-bright); word-break:break-all; }
  .timeline { position:relative; padding-left:20px; }
  .timeline::before { content:''; position:absolute; left:6px; top:6px; bottom:6px; width:1px; background:var(--border); }
  .tl-item { position:relative; margin-bottom:14px; }
  .tl-item::before { content:''; position:absolute; left:-17px; top:5px; width:7px; height:7px; border-radius:50%; border:1px solid var(--border); background:var(--bg2); }
  .tl-meta { font-family:'IBM Plex Mono',monospace; font-size:11px; color:var(--text-dim); margin-top:2px; }
  .tl-result { font-size:12px; color:var(--text); margin-top:3px; background:#f0f2f8; padding:4px 8px; border-radius:4px; }
  .select-status { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:8px 12px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; width:100%; margin-bottom:8px; outline:none; }
  .select-status:focus { border-color:var(--accent); }
  .input-memo { background:var(--bg3); border:1px solid var(--border); border-radius:6px; padding:8px 12px; color:var(--text); font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; width:100%; margin-bottom:10px; outline:none; resize:vertical; min-height:60px; }
  .input-memo:focus { border-color:var(--accent); }
  .btn-save { background:var(--accent); border:none; border-radius:6px; padding:9px 20px; color:white; font-family:'IBM Plex Sans KR',sans-serif; font-size:13px; font-weight:500; cursor:pointer; transition:opacity .15s; width:100%; }
  .btn-save:hover { opacity:.85; }
  .empty-state, .loading { padding:60px; text-align:center; color:var(--text-dim); font-family:'IBM Plex Mono',monospace; font-size:13px; }
</style>
</head>
<body>

<header>
  <div class="logo">BOA <span>//</span> 업무 대시보드</div>
  <nav class="header-nav">
    <a href="/dashboard" class="nav-link active">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link">구성원</a>
    <a href="/sessions" class="nav-link">대화 로그</a>
  </nav>
  <div class="header-right">
    <div class="live-badge"><div class="live-dot"></div><span id="last-updated">—</span></div>
    <div class="user-info">
      <img class="user-avatar" id="user-avatar" src="" alt="">
      <span class="user-name" id="user-name">로딩중...</span>
      <span class="admin-badge" id="admin-badge" style="display:none">ADMIN</span>
    </div>
    <button class="btn-logout" onclick="location.href='/auth/logout'">로그아웃</button>
  </div>
</header>

<!-- 대기중 알림 배너 -->
<div id="notif-banner">
  <div class="notif-text">
    <span class="notif-icon">⏳</span>
    <span id="notif-text">대기중인 업무가 있습니다.</span>
  </div>
  <button class="btn-notif-close" onclick="closeBanner()">×</button>
</div>

<div class="container">
  <div class="stats-grid">
    <div class="stat-card active" onclick="filterByStatus(null, this)"><div class="stat-label">전체</div><div class="stat-value" id="cnt-all">—</div></div>
    <div class="stat-card" onclick="filterByStatus('처리중', this)"><div class="stat-label">처리중</div><div class="stat-value" id="cnt-처리중">—</div></div>
    <div class="stat-card" onclick="filterByStatus('대기중', this)"><div class="stat-label">대기중</div><div class="stat-value" id="cnt-대기중">—</div></div>
    <div class="stat-card" onclick="filterByStatus('완료', this)"><div class="stat-label">완료</div><div class="stat-value" id="cnt-완료">—</div></div>
    <div class="stat-card" onclick="filterByStatus('오류', this)"><div class="stat-label">오류</div><div class="stat-value" id="cnt-오류">—</div></div>
  </div>

  <div class="filter-bar">
    <input type="text" id="search-input" placeholder="업무명, task_id 검색..." oninput="loadTasks(1)">
    <button class="btn-refresh" onclick="loadTasks(currentTasksPage)">↻ 새로고침</button>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>업무</th>
          <th id="th-user" style="display:none">요청자</th>
          <th>상태</th>
          <th>최근 결과</th>
          <th>생성</th>
          <th>업데이트</th>
        </tr>
      </thead>
      <tbody id="task-tbody"><tr><td colspan="6" class="loading">로딩 중...</td></tr></tbody>
    </table>
  </div>
</div>

<!-- 상세 모달 -->
<div class="modal-overlay" id="modal-overlay" onclick="closeModalOutside(event)">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title" id="modal-title">업무 상세</div>
      <button class="modal-close" onclick="closeModal()">×</button>
    </div>
    <div class="modal-body" id="modal-body"></div>
  </div>
</div>

<script>
let allTasks = [];
let currentFilter = null;
let currentTaskId = null;
let currentUser = null;
let prevWaitingCount = 0;
let agentParamLabels = {};

// ── 초기화 ──
async function init() {
  await loadMe();
  await loadAgents();
  await loadTasks(1);
  requestBrowserNotifPermission();
  setInterval(() => loadTasks(currentTasksPage), 30000);
}

async function loadAgents() {
  const res = await fetch('/api/agents');
  agentParamLabels = await res.json();
}

// ── 유저 정보 로드 ──
async function loadMe() {
  const res = await fetch('/api/me');
  currentUser = await res.json();
  document.getElementById('user-name').textContent = currentUser.name;
  document.getElementById('user-avatar').src = currentUser.picture || '';
  if (currentUser.is_admin) {
    document.getElementById('admin-badge').style.display = 'inline-block';
    document.getElementById('th-user').style.display = '';
  }
}

// ── task 로드 ──
let currentTasksPage = 1;
const tasksPerPage = 50;
let totalTasks = 0;

async function loadTasks(page = 1) {
  currentTasksPage = page;
  const skip = (page - 1) * tasksPerPage;
  const status = currentFilter || '';
  // Not passing search right now as backend doesn't support q, we just rely on the frontend filtering or backend status. 
  // Let's pass status to backend
  let queryUrl = `/api/tasks?skip=${skip}&limit=${tasksPerPage}`;
  if (status) queryUrl += `&status=${status}`;
  
  try {
    const res = await fetch(queryUrl);
    const data = await res.json();
    allTasks = data.items;
    totalTasks = data.total;
    updateStats(); // Note: This will only count items from the current page in the UI unless backend sends total counts per status, but we'll leave it as is for UI consistency.
    renderTable();
    renderTasksPagination();
    checkWaitingAlerts();
    document.getElementById('last-updated').textContent = new Date().toLocaleTimeString('ko-KR');
  } catch(e) {
    document.getElementById('task-tbody').innerHTML = '<tr><td colspan="6" class="empty-state">데이터 로드 실패</td></tr>';
  }
}

function renderTasksPagination() {
  const totalPages = Math.ceil(totalTasks / tasksPerPage);
  const paginationHtml = `
    <div style="display:flex; justify-content:center; padding:10px; gap:5px;">
      <button class="btn-secondary" onclick="loadTasks(${Math.max(1, currentTasksPage - 1)})" ${currentTasksPage === 1 ? 'disabled' : ''}>이전</button>
      <span style="padding: 6px;">${currentTasksPage} / ${totalPages > 0 ? totalPages : 1}</span>
      <button class="btn-secondary" onclick="loadTasks(${Math.min(totalPages, currentTasksPage + 1)})" ${currentTasksPage === totalPages || totalPages === 0 ? 'disabled' : ''}>다음</button>
    </div>
  `;
  // Append after table or inside table container
  const tableWrap = document.querySelector('.table-wrap');
  let pagDiv = document.getElementById('tasks-pagination-controls');
  if(!pagDiv) {
    pagDiv = document.createElement('div');
    pagDiv.id = 'tasks-pagination-controls';
    tableWrap.parentNode.insertBefore(pagDiv, tableWrap.nextSibling);
  }
  pagDiv.innerHTML = paginationHtml;
}

// ── 통계 ──
function updateStats() {
  const statuses = ['처리중','대기중','완료','취소','오류'];
  document.getElementById('cnt-all').textContent = allTasks.length;
  statuses.forEach(s => {
    const el = document.getElementById('cnt-' + s);
    if (el) el.textContent = allTasks.filter(t => t.status === s).length;
  });
}

// ── 대기중 알림 ──
function checkWaitingAlerts() {
  const waitingTasks = allTasks.filter(t => t.status === '대기중');
  const count = waitingTasks.length;

  // 배너
  if (count > 0) {
    document.getElementById('notif-text').textContent =
      `대기중인 업무가 ${count}건 있습니다.`;
    document.getElementById('notif-banner').classList.add('show');
  } else {
    document.getElementById('notif-banner').classList.remove('show');
  }

  // 브라우저 알림 (새로 늘어난 경우에만)
  if (count > prevWaitingCount && prevWaitingCount !== null) {
    const newCount = count - prevWaitingCount;
    sendBrowserNotif(`⏳ 대기중 업무 ${newCount}건 추가됨`, '확인이 필요한 업무가 있습니다.');
  }
  prevWaitingCount = count;
}

// ── 브라우저 알림 ──
function requestBrowserNotifPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}
function sendBrowserNotif(title, body) {
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification(title, { body, icon: '/favicon.ico' });
  }
}

function closeBanner() {
  document.getElementById('notif-banner').classList.remove('show');
}

// ── 날짜 포맷 (GMT+9 보정) ──
function adjustToKST(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  // UTC/GMT 시간에서 9시간을 더함
  date.setHours(date.getHours() + 9);
  return date;
}

// ── 필터 ──
function filterByStatus(status, el) {
  currentFilter = status;
  document.querySelectorAll('.stat-card').forEach(c => c.classList.remove('active'));
  el.classList.add('active');
  loadTasks(1);
}

// ── 테이블 렌더 ──
function renderTable() {
  const search = document.getElementById('search-input').value.toLowerCase();
  let tasks = allTasks;
  // Note: we fetch filtered by status from the backend, so we don't strictly need to filter status here,
  // but keeping it as double safety if allTasks hasn't been fetched yet.
  if (currentFilter) tasks = tasks.filter(t => t.status === currentFilter);
  if (search) tasks = tasks.filter(t =>
    (t.agent_display_name||'').toLowerCase().includes(search) ||
    (t.task_id||'').toLowerCase().includes(search) ||
    (t.user_name||'').toLowerCase().includes(search)
  );

  if (!tasks.length) {
    document.getElementById('task-tbody').innerHTML =
      '<tr><td colspan="6" class="empty-state">조건에 맞는 업무가 없습니다</td></tr>';
    return;
  }

  const isAdmin = currentUser?.is_admin;
  document.getElementById('task-tbody').innerHTML = tasks.map(t => `
    <tr class="${t.status === '대기중' ? 'waiting-row' : ''}" onclick="openModal('${t.task_id}')">
      <td>
        <div style="font-weight:500;color:var(--text-bright)">${t.agent_display_name || t.agent_name}</div>
        <div class="td-mono" style="margin-top:2px">${t.task_id}</div>
      </td>
      ${isAdmin ? `<td><div>${t.user_name||'—'}</div><div class="td-mono">${t.user_id||''}</div></td>` : ''}
      <td><span class="badge badge-${t.status}">${t.status}</span></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-dim);font-size:12px">${t.result||'—'}</td>
      <td class="td-mono">${formatDate(t.created_at)}</td>
      <td class="td-mono">${formatDate(t.updated_at)}</td>
    </tr>
  `).join('');
}

// ── 모달 ──
function openModal(taskId) {
  const task = allTasks.find(t => t.task_id === taskId);
  if (!task) return;
  currentTaskId = taskId;
  document.getElementById('modal-title').textContent = task.agent_display_name || task.agent_name;

  let paramsHtml = '<div style="color:var(--text-dim);font-style:italic">수집된 정보 없음</div>';
  if (task.parameters && Object.keys(task.parameters).length > 0) {
    const labelMap = agentParamLabels[task.agent_name] || {};
    paramsHtml = Object.entries(task.parameters).filter(([key]) => labelMap[key]).map(([key, val]) => {
      const label = labelMap[key] || key;
      const displayVal = formatParamValue(val);
      return `
        <div class="detail-row" style="margin-bottom:6px;align-items:flex-start">
          <div class="detail-key" style="min-width:160px;color:var(--accent)">${label}</div>
          <div class="detail-val" style="color:var(--text)">${displayVal}</div>
        </div>
      `;
    }).join('');
  }

  const history = (task.task_history || []).slice().reverse();
  const isAdmin = currentUser?.is_admin;

  document.getElementById('modal-body').innerHTML = `
    <div class="detail-section">
      <div class="detail-section-title">기본 정보</div>
      <div class="detail-row"><div class="detail-key">Task ID</div><div class="detail-val td-mono">${task.task_id}</div></div>
      ${isAdmin ? `<div class="detail-row"><div class="detail-key">요청자</div><div class="detail-val">${task.user_name} (${task.user_id})</div></div>` : ''}
      <div class="detail-row"><div class="detail-key">업무</div><div class="detail-val">${task.agent_display_name || task.agent_name}</div></div>
      <div class="detail-row"><div class="detail-key">현재 상태</div><div class="detail-val"><span class="badge badge-${task.status}">${task.status}</span></div></div>
      <div class="detail-row"><div class="detail-key">생성 시각</div><div class="detail-val td-mono">${formatDateFull(task.created_at)}</div></div>
      ${task.admin_memo ? `<div class="detail-row"><div class="detail-key">관리자 메모</div><div class="detail-val">${task.admin_memo}</div></div>` : ''}
    </div>
    <div class="detail-section">
      <div class="detail-section-title">수집된 파라미터</div>
      <div style="background:var(--bg3);padding:16px;border-radius:6px;border:1px solid var(--border)">
        ${paramsHtml}
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-section-title">진행 이력</div>
      <div class="timeline">
        ${history.map(h => `
          <div class="tl-item">
            <span class="badge badge-${h.status}">${h.status}</span>
            <div class="tl-meta">${formatDateFull(h.changed_at)} · ${h.changed_by}</div>
            ${h.result ? `<div class="tl-result">${h.result}</div>` : ''}
          </div>
        `).join('')}
      </div>
    </div>
    ${isAdmin ? `
    <div class="detail-section">
      <div class="detail-section-title">상태 수동 변경 (관리자)</div>
      <select class="select-status" id="new-status">
        <option value="처리중">처리중</option>
        <option value="대기중">대기중</option>
        <option value="완료">완료</option>
        <option value="취소">취소</option>
        <option value="오류">오류</option>
      </select>
      <textarea class="input-memo" id="admin-memo" placeholder="관리자 메모 (선택)"></textarea>
      <button class="btn-save" onclick="saveStatus()">상태 변경 저장</button>
    </div>` : ''}
  `;
  if (isAdmin) document.getElementById('new-status').value = task.status;
  document.getElementById('modal-overlay').classList.add('open');
}

async function saveStatus() {
  const status = document.getElementById('new-status').value;
  const memo = document.getElementById('admin-memo').value;
  try {
    const res = await fetch(`/api/tasks/${currentTaskId}/status`, {
      method: 'PATCH',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({status, admin_memo: memo || null})
    });
    if (res.ok) { closeModal(); await loadTasks(currentTasksPage); }
  } catch(e) { alert('저장 실패: ' + e.message); }
}

function closeModal() { document.getElementById('modal-overlay').classList.remove('open'); currentTaskId = null; }
function closeModalOutside(e) { if (e.target === document.getElementById('modal-overlay')) closeModal(); }
function formatDate(iso) { 
  if (!iso) return '—'; 
  const d = adjustToKST(iso);
  return d.toLocaleDateString('ko-KR',{month:'2-digit',day:'2-digit'})+' '+d.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'}); 
}
function formatDateFull(iso) { 
  if (!iso) return '—'; 
  return adjustToKST(iso).toLocaleString('ko-KR'); 
}
function formatParamValue(val) {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'boolean') return val ? '✓ 예' : '✗ 아니오';
  if (Array.isArray(val)) {
    if (val.length === 1) return String(val[0]);  // 1개면 그냥 텍스트
    return val.map(v =>
      `<div style="padding-left:8px">· ${v}</div>`  // 여러개면 목록
    ).join('');
  }
  if (typeof val === 'object') {
    return Object.entries(val).map(([k, v]) =>
      `<div style="padding-left:12px;color:var(--text-dim)">· ${k}: ${v}</div>`
    ).join('');
  }
  // 날짜 포맷 (YYYYMMDD)
  if (typeof val === 'string' && /^\\d{8}$/.test(val)) {
    return `${val.slice(0,4)}-${val.slice(4,6)}-${val.slice(6,8)}`;
  }
  return String(val);
}

init();
</script>
</body>
</html>"""

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    redirect = require_login(request)
    if redirect:
        return redirect
    
    user = get_current_user(request)
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    is_admin = doc is not None

    html_content = DASHBOARD_HTML
    if not is_admin:
        html_content = html_content.replace('<a href="/spaces" class="nav-link">스페이스</a>', '')
        html_content = html_content.replace('<a href="/members" class="nav-link">구성원</a>', '')

    return HTMLResponse(content=html_content)
from src.core.session_logger import get_task_trace

@router.get("/api/tasks/{task_id}/trace")
async def api_get_task_trace(task_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="로그인 필요")

    task = await get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="태스크를 찾을 수 없습니다")
    if task["user_id"] != user["email"] and not await is_admin(user["email"]):
        raise HTTPException(status_code=403, detail="권한이 없습니다")

    events = await get_task_trace(task_id)
    return {"task_id": task_id, "events": events}
