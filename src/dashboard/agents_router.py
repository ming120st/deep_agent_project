"""
src/dashboard/agents_router.py

[엔드포인트]
  GET    /agents                     → HTML 에이전트 관리 페이지
  GET    /api/boa-agents             → 전체 에이전트 목록 (boa_agents + boa_bq_schedules 통합)
  PATCH  /api/boa-agents/{agent_id}  → enabled 토글 (레지스트리 & bq_schedule 양쪽 처리)
  DELETE /api/boa-agents/{agent_id}  → bq_schedule 에이전트 삭제 (deletable=True만 허용)

[통합 목록 구조]
  레지스트리 에이전트 → deletable: false, 대시보드에서 삭제 불가
  BQ 분석 스케줄      → deletable: true,  display_name=agent_name, description=query
"""
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from src.core.mongo_client import boa_agents_collection, boa_admins_collection
from src.agent.registry import ALL_REGISTRIES
from src.agent.schedule.bq_analysis_schedule.schedule_manager import (
    bq_schedules_collection,
    toggle_bq_schedule,
    delete_bq_schedule,
    INTERVAL_LABELS,
)

router = APIRouter()


# ── 관리자 체크 ───────────────────────────────────────────────────────────────
async def require_admin(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401)
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    if not doc:
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user


# ── 스케줄러 참조 ─────────────────────────────────────────────────────────────
def get_scheduler():
    from src.main import scheduler
    return scheduler


# ── 스키마 ────────────────────────────────────────────────────────────────────
class ToggleRequest(BaseModel):
    enabled: bool


# ── bq_schedule → 에이전트 포맷 변환 ──────────────────────────────────────────
def _bq_schedule_to_agent(doc: dict) -> dict:
    interval_label = INTERVAL_LABELS.get(doc.get("interval", "daily"), "매일")
    hour   = doc.get("hour", 8)
    minute = doc.get("minute", 0)
    time_str = f"{hour:02d}:{minute:02d}" if doc.get("interval") != "hourly" else "매시간"
    created = doc.get("created_at")
    return {
        "agent_id":     doc["schedule_id"],
        "display_name": doc.get("agent_name", doc["schedule_id"]),
        "type":         "schedule",
        "description":  doc.get("query", ""),
        "enabled":      doc.get("enabled", True),
        "deletable":    True,
        "cron_label":   f"{interval_label} {time_str}",
        "created_by":   doc.get("user_id", ""),
        "created_at":   created.isoformat() if created else None,
        "updated_at":   doc.get("updated_at", created).isoformat() if doc.get("updated_at", created) else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/boa-agents")
async def list_agents(request: Request):
    await require_admin(request)

    # 1. 레지스트리 에이전트 (boa_agents 컬렉션)
    cursor = boa_agents_collection.find({}, {"_id": 0})
    registry_agents = await cursor.to_list(length=None)
    for a in registry_agents:
        for key in ("created_at", "updated_at"):
            if a.get(key):
                a[key] = a[key].isoformat()
        a["deletable"] = False

    # 2. BQ 분석 스케줄 (boa_bq_schedules 컬렉션) — schedule 탭에 통합
    bq_cursor = bq_schedules_collection.find({}, {"_id": 0})
    bq_docs   = await bq_cursor.to_list(length=None)
    bq_agents = [_bq_schedule_to_agent(doc) for doc in bq_docs]

    # 타입별 그룹핑
    result = {"chat": [], "schedule": [], "trigger": []}
    for agent in registry_agents:
        t = agent.get("type", "chat")
        if t in result:
            result[t].append(agent)
    result["schedule"].extend(bq_agents)

    return result


@router.patch("/api/boa-agents/{agent_id}")
async def toggle_agent(agent_id: str, body: ToggleRequest, request: Request):
    await require_admin(request)

    # Case 1. 레지스트리 에이전트
    if agent_id in ALL_REGISTRIES:
        await boa_agents_collection.update_one(
            {"agent_id": agent_id},
            {"$set": {"enabled": body.enabled, "updated_at": datetime.utcnow()}}
        )
        meta = ALL_REGISTRIES[agent_id]
        if meta["type"] in ("schedule", "trigger"):
            job = get_scheduler().get_job(agent_id)
            if job:
                job.resume() if body.enabled else job.pause()
        print(f"[Agents] {agent_id} → {'활성화' if body.enabled else '비활성화'}")
        return {"agent_id": agent_id, "enabled": body.enabled}

    # Case 2. BQ 분석 스케줄
    doc = await bq_schedules_collection.find_one({"schedule_id": agent_id})
    if doc:
        ok = await toggle_bq_schedule(get_scheduler(), agent_id, body.enabled)
        if ok:
            return {"agent_id": agent_id, "enabled": body.enabled}

    raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")


@router.delete("/api/boa-agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    await require_admin(request)

    if agent_id in ALL_REGISTRIES:
        raise HTTPException(status_code=403, detail="코드에 등록된 에이전트는 삭제할 수 없습니다.")

    deleted = await delete_bq_schedule(get_scheduler(), agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")

    print(f"[Agents] 삭제: {agent_id}")
    return {"agent_id": agent_id, "deleted": True}


# ══════════════════════════════════════════════════════════════════════════════
# 페이지
# ══════════════════════════════════════════════════════════════════════════════

FORBIDDEN_HTML = """
<html><body style="font-family:sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;background:#0f1117;color:#e2e8f0;">
<div style="text-align:center">
  <div style="font-size:48px">🔒</div>
  <h2 style="margin:16px 0 8px">접근 권한 없음</h2>
  <p style="color:#64748b">관리자만 접근할 수 있는 페이지입니다.</p>
  <a href="/dashboard" style="color:#6366f1;margin-top:16px;display:inline-block">← 대시보드로</a>
</div></body></html>
"""

@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/auth/login")
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    if not doc:
        return HTMLResponse(FORBIDDEN_HTML, status_code=403)

    return HTMLResponse(AGENTS_HTML)


AGENTS_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>에이전트 관리 — BOA</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3e;
      --text: #e2e8f0; --muted: #64748b; --accent: #6366f1;
      --green: #22c55e; --red: #ef4444; --yellow: #f59e0b; --blue: #3b82f6;
    }
    body { font-family: 'IBM Plex Sans KR', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }

    .top-nav { background: #13161b; border-bottom: 1px solid #252931; padding: 0 28px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
    .logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 16px; color: #eef1f6; letter-spacing: .05em; }
    .logo span { color: #3b82f6; }
    .nav-links { display: flex; align-items: center; gap: 4px; }
    .nav-link { padding: 6px 14px; border-radius: 6px; font-size: 13px; color: var(--muted); text-decoration: none; transition: all .15s; }
    .nav-link:hover { color: var(--text); background: #1a1e25; }
    .nav-link.active { color: #3b82f6; background: #1d3d6e; }
    .nav-right { display: flex; align-items: center; gap: 12px; }
    .btn-logout { background: none; border: 1px solid #252931; border-radius: 6px; padding: 5px 12px; color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: none; transition: all .15s; }
    .btn-logout:hover { border-color: #ef4444; color: #ef4444; }

    .page-body { padding: 32px 24px; }
    .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 28px; }
    .header h1 { font-size: 20px; font-weight: 600; }

    .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 24px; min-width: 130px; }
    .stat-card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
    .stat-card .value { font-size: 24px; font-weight: 600; font-family: 'IBM Plex Mono', monospace; }
    .value.green  { color: var(--green); }
    .value.accent { color: var(--accent); }
    .value.yellow { color: var(--yellow); }
    .value.blue   { color: var(--blue); }

    .tabs { display: flex; gap: 4px; margin-bottom: 20px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 6px; width: fit-content; }
    .tab { padding: 8px 20px; border-radius: 7px; font-size: 13px; font-weight: 500; cursor: pointer; color: var(--muted); transition: all .15s; border: none; background: none; font-family: 'IBM Plex Sans KR', sans-serif; }
    .tab:hover { color: var(--text); }
    .tab.active { background: var(--accent); color: white; }

    .toolbar { margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 14px; font-family: inherit; outline: none; transition: border-color .15s; }
    .search-bar:focus { border-color: var(--accent); }
    .search-bar::placeholder { color: var(--muted); }

    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 11px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02); white-space: nowrap; text-transform: uppercase; letter-spacing: .06em; font-family: 'IBM Plex Mono', monospace; }
    td { padding: 14px 20px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }

    .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; font-family: 'IBM Plex Mono', monospace; white-space: nowrap; }
    .badge-chat     { background: rgba(99,102,241,0.15); color: #818cf8; }
    .badge-schedule { background: rgba(245,158,11,0.15); color: var(--yellow); }
    .badge-trigger  { background: rgba(34,197,94,0.15);  color: var(--green); }
    .badge-user     { background: rgba(59,130,246,0.12); color: var(--blue); margin-left: 6px; font-size: 10px; }

    .agent-name { font-weight: 500; color: #eef1f6; }
    .agent-id   { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--muted); margin-top: 3px; }
    .desc       { font-size: 12px; color: var(--muted); line-height: 1.5; max-width: 340px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .cron-label { font-family: 'IBM Plex Mono', monospace; font-size: 11px; color: var(--yellow); margin-top: 4px; }
    .td-mono    { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--muted); }

    .toggle-cell { display: flex; align-items: center; gap: 8px; }
    .status-dot  { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; transition: all .2s; }
    .dot-on  { background: var(--green); box-shadow: 0 0 6px var(--green); }
    .dot-off { background: var(--muted); }
    .toggle { position: relative; display: inline-block; width: 40px; height: 22px; }
    .toggle input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; cursor: pointer; inset: 0; background: var(--border); border-radius: 22px; transition: .2s; }
    .slider:before { position: absolute; content: ""; width: 16px; height: 16px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: .2s; }
    input:checked + .slider { background: var(--accent); }
    input:checked + .slider:before { transform: translateX(18px); }

    .btn-delete { background: none; border: 1px solid transparent; border-radius: 6px; padding: 5px 12px; color: var(--muted); font-size: 12px; cursor: pointer; font-family: 'IBM Plex Sans KR', sans-serif; transition: all .15s; }
    .btn-delete:hover { border-color: var(--red); color: var(--red); background: rgba(239,68,68,0.08); }
    .btn-delete:disabled { opacity: 0.3; cursor: not-allowed; }
    .action-placeholder { color: var(--border); font-size: 13px; }

    /* 삭제 확인 모달 */
    .modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.65); z-index: 200; align-items: center; justify-content: center; }
    .modal-backdrop.open { display: flex; }
    .modal { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 28px 32px; width: 420px; max-width: 90vw; }
    .modal h3 { font-size: 16px; font-weight: 600; margin-bottom: 12px; }
    .modal p  { font-size: 13px; color: var(--muted); line-height: 1.7; margin-bottom: 24px; }
    .modal-name { color: var(--text); font-weight: 600; }
    .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
    .btn-cancel { background: none; border: 1px solid var(--border); border-radius: 8px; padding: 8px 18px; color: var(--muted); font-size: 13px; cursor: pointer; font-family: inherit; transition: all .15s; }
    .btn-cancel:hover { border-color: var(--text); color: var(--text); }
    .btn-confirm-del { background: var(--red); border: none; border-radius: 8px; padding: 8px 18px; color: white; font-size: 13px; cursor: pointer; font-family: inherit; transition: opacity .15s; }
    .btn-confirm-del:hover { opacity: 0.85; }
    .btn-confirm-del:disabled { opacity: 0.5; cursor: not-allowed; }

    .empty   { text-align: center; padding: 60px; color: var(--muted); }
    .loading { text-align: center; padding: 60px; color: var(--muted); }
  </style>
</head>
<body>

<nav class="top-nav">
  <div class="logo">BOA <span>//</span> 에이전트 관리</div>
  <div class="nav-links">
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link active">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link">구성원</a>
  </div>
  <div class="nav-right">
    <a href="/auth/logout" class="btn-logout">로그아웃</a>
  </div>
</nav>

<div class="page-body">
<div class="header">
  <h1>🤖 에이전트 관리</h1>
</div>

<div class="stats">
  <div class="stat-card"><div class="label">전체</div><div class="value" id="cnt-total">—</div></div>
  <div class="stat-card"><div class="label">활성화</div><div class="value green" id="cnt-enabled">—</div></div>
  <div class="stat-card"><div class="label">대화형</div><div class="value accent" id="cnt-chat">—</div></div>
  <div class="stat-card"><div class="label">스케줄</div><div class="value yellow" id="cnt-schedule">—</div></div>
  <div class="stat-card"><div class="label">트리거</div><div class="value blue" id="cnt-trigger">—</div></div>
</div>

<div class="tabs">
  <button class="tab active" onclick="switchTab('all', this)">전체</button>
  <button class="tab" onclick="switchTab('chat', this)">💬 대화형</button>
  <button class="tab" onclick="switchTab('schedule', this)">⏰ 스케줄</button>
  <button class="tab" onclick="switchTab('trigger', this)">⚡ 트리거</button>
</div>

<div class="toolbar">
  <input class="search-bar" type="text" id="searchInput" placeholder="에이전트 이름 또는 ID 검색...">
</div>

<table>
  <thead>
    <tr>
      <th>에이전트</th>
      <th>타입</th>
      <th>설명 / 쿼리</th>
      <th>업데이트</th>
      <th>활성화</th>
      <th>액션</th>
    </tr>
  </thead>
  <tbody id="tbody">
    <tr><td colspan="6" class="loading">로딩 중...</td></tr>
  </tbody>
</table>
</div>

<!-- 삭제 확인 모달 -->
<div class="modal-backdrop" id="deleteModal">
  <div class="modal">
    <h3>🗑️ 에이전트 삭제</h3>
    <p>
      <span class="modal-name" id="modal-agent-name"></span> 스케줄을 삭제하시겠습니까?<br>
      삭제 후에는 예약된 분석이 더 이상 실행되지 않습니다.
    </p>
    <div class="modal-actions">
      <button class="btn-cancel" onclick="closeModal()">취소</button>
      <button class="btn-confirm-del" id="btn-confirm-del" onclick="confirmDelete()">삭제</button>
    </div>
  </div>
</div>

<script>
  let data = { chat: [], schedule: [], trigger: [] };
  let currentTab = 'all';
  let pendingDeleteId = null;

  async function load() {
    const res = await fetch('/api/boa-agents');
    if (!res.ok) {
      document.getElementById('tbody').innerHTML = '<tr><td colspan="6" class="empty">데이터 로드 실패</td></tr>';
      return;
    }
    data = await res.json();
    updateStats();
    render();
  }

  function updateStats() {
    const all = [...data.chat, ...data.schedule, ...data.trigger];
    document.getElementById('cnt-total').textContent    = all.length;
    document.getElementById('cnt-enabled').textContent  = all.filter(a => a.enabled).length;
    document.getElementById('cnt-chat').textContent     = data.chat.length;
    document.getElementById('cnt-schedule').textContent = data.schedule.length;
    document.getElementById('cnt-trigger').textContent  = data.trigger.length;
  }

  function getAgents() {
    if (currentTab === 'all') return [...data.chat, ...data.schedule, ...data.trigger];
    return data[currentTab] || [];
  }

  function typeBadge(a) {
    const map = {
      chat:     ['badge-chat',     '💬 대화형'],
      schedule: ['badge-schedule', '⏰ 스케줄'],
      trigger:  ['badge-trigger',  '⚡ 트리거'],
    };
    const [cls, label] = map[a.type] || ['badge-chat', a.type];
    const userBadge = a.deletable ? '<span class="badge badge-user">사용자 등록</span>' : '';
    return `<span class="badge ${cls}">${label}</span>${userBadge}`;
  }

  function escHtml(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function render() {
    const q = document.getElementById('searchInput').value.toLowerCase();
    let agents = getAgents();
    if (q) agents = agents.filter(a =>
      (a.display_name || '').toLowerCase().includes(q) ||
      (a.agent_id || '').toLowerCase().includes(q)
    );

    const tbody = document.getElementById('tbody');
    if (!agents.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">등록된 에이전트가 없습니다</td></tr>';
      return;
    }

    tbody.innerHTML = agents.map(a => {
      const cronLine = a.cron_label
        ? `<div class="cron-label">🕐 ${escHtml(a.cron_label)}</div>` : '';
      const dateStr = (a.updated_at || a.created_at || '').slice(0, 10) || '—';
      const actionCell = a.deletable
        ? `<button class="btn-delete"
             onclick="openDeleteModal('${escHtml(a.agent_id)}','${escHtml(a.display_name)}')">
             🗑 삭제
           </button>`
        : `<span class="action-placeholder">—</span>`;

      return `
        <tr id="row-${escHtml(a.agent_id)}">
          <td>
            <div class="agent-name">${escHtml(a.display_name || a.agent_id)}</div>
            <div class="agent-id">${escHtml(a.agent_id)}</div>
          </td>
          <td>${typeBadge(a)}</td>
          <td>
            <div class="desc" title="${escHtml(a.description)}">${escHtml(a.description) || '—'}</div>
            ${cronLine}
          </td>
          <td class="td-mono">${dateStr}</td>
          <td>
            <div class="toggle-cell">
              <span class="status-dot ${a.enabled ? 'dot-on' : 'dot-off'}" id="dot-${escHtml(a.agent_id)}"></span>
              <label class="toggle">
                <input type="checkbox" ${a.enabled ? 'checked' : ''}
                  onchange="toggleAgent('${escHtml(a.agent_id)}', this.checked, this)">
                <span class="slider"></span>
              </label>
            </div>
          </td>
          <td>${actionCell}</td>
        </tr>
      `;
    }).join('');
  }

  async function toggleAgent(agent_id, enabled, checkbox) {
    const res = await fetch(`/api/boa-agents/${agent_id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) {
      alert('변경 실패');
      checkbox.checked = !enabled;
      return;
    }
    for (const type of ['chat', 'schedule', 'trigger']) {
      const a = data[type].find(x => x.agent_id === agent_id);
      if (a) { a.enabled = enabled; break; }
    }
    const dot = document.getElementById(`dot-${agent_id}`);
    if (dot) dot.className = `status-dot ${enabled ? 'dot-on' : 'dot-off'}`;
    updateStats();
  }

  // ── 삭제 모달 ───────────────────────────────────────────────────────────────
  function openDeleteModal(agent_id, display_name) {
    pendingDeleteId = agent_id;
    document.getElementById('modal-agent-name').textContent = display_name;
    document.getElementById('deleteModal').classList.add('open');
  }

  function closeModal() {
    pendingDeleteId = null;
    document.getElementById('deleteModal').classList.remove('open');
  }

  async function confirmDelete() {
    if (!pendingDeleteId) return;
    const agent_id = pendingDeleteId;
    const btn = document.getElementById('btn-confirm-del');
    btn.disabled = true;
    btn.textContent = '삭제 중...';

    const res = await fetch(`/api/boa-agents/${agent_id}`, { method: 'DELETE' });
    btn.disabled = false;
    btn.textContent = '삭제';
    closeModal();

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert('삭제 실패: ' + (err.detail || '알 수 없는 오류'));
      return;
    }

    // 로컬 데이터에서 제거 후 재렌더
    for (const type of ['chat', 'schedule', 'trigger']) {
      data[type] = data[type].filter(x => x.agent_id !== agent_id);
    }
    updateStats();
    render();
  }

  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

  function switchTab(tab, el) {
    currentTab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
    render();
  }

  document.getElementById('searchInput').addEventListener('input', render);
  load();
</script>
</body>
</html>"""