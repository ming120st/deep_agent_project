"""
src/dashboard/agents_router.py

[엔드포인트]
  GET    /agents                     → HTML 에이전트 관리 페이지 (로그인 필수)
  GET    /api/boa-agents             → 에이전트 목록 (권한에 따라 필터링)
  PATCH  /api/boa-agents/{agent_id}  → enabled 토글
  DELETE /api/boa-agents/{agent_id}  → bq_schedule 에이전트 삭제

[권한 정책]
  관리자(boa_admins):
    - 전체 에이전트 조회
    - 모든 에이전트 토글
    - 모든 사용자 생성 에이전트 삭제

  일반 멤버:
    - 공통 에이전트(deletable=False) + 자신이 만든 에이전트만 조회
    - 자신이 만든 에이전트만 토글
    - 자신이 만든 에이전트만 삭제
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


# ── 인증 헬퍼 ─────────────────────────────────────────────────────────────────

async def require_login(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


async def check_is_admin(email: str) -> bool:
    doc = await boa_admins_collection.find_one({"email": email})
    return doc is not None


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
        "agent_id":        doc["schedule_id"],
        "display_name":    doc.get("agent_name", doc["schedule_id"]),
        "type":            "schedule",
        "description":     doc.get("query", ""),
        "enabled":         doc.get("enabled", True),
        "deletable":       True,
        "cron_label":      f"{interval_label} {time_str}",
        "created_by":      doc.get("user_id", ""),
        "created_by_name": doc.get("user_name", ""),
        "space_names":     [doc.get("space_name", "")] if doc.get("space_name") else [],
        "created_at":      created.isoformat() if created else None,
        "updated_at":      doc.get("updated_at", created).isoformat() if doc.get("updated_at", created) else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/boa-agents")
async def list_agents(request: Request):
    user     = await require_login(request)
    email    = user["email"]
    is_admin = await check_is_admin(email)

    # 1. 레지스트리 에이전트 (boa_agents 컬렉션) — 공통, 모두에게 표시
    cursor = boa_agents_collection.find({}, {"_id": 0})
    registry_agents = await cursor.to_list(length=None)
    for a in registry_agents:
        for key in ("created_at", "updated_at"):
            if a.get(key):
                a[key] = a[key].isoformat()
        a["deletable"] = False
        # schedule/trigger: target 필드를 space_names 표시용으로 사용
        if not a.get("space_names"):
            a["space_names"] = [a["target"]] if a.get("target") else []

    # 2. BQ 분석 스케줄 — 관리자는 전체, 멤버는 자신이 만든 것만
    if is_admin:
        bq_cursor = bq_schedules_collection.find({}, {"_id": 0})
    else:
        bq_cursor = bq_schedules_collection.find({"user_id": email}, {"_id": 0})

    bq_docs   = await bq_cursor.to_list(length=None)
    bq_agents = [_bq_schedule_to_agent(doc) for doc in bq_docs]

    # 타입별 그룹핑 + 메타 정보
    result = {
        "chat": [], "schedule": [], "trigger": [],
        "_meta": {"is_admin": is_admin, "user_id": email},
    }
    for agent in registry_agents:
        t = agent.get("type", "chat")
        if t in result:
            result[t].append(agent)
    result["schedule"].extend(bq_agents)

    return result


@router.patch("/api/boa-agents/{agent_id}")
async def toggle_agent(agent_id: str, body: ToggleRequest, request: Request):
    user     = await require_login(request)
    email    = user["email"]
    is_admin = await check_is_admin(email)

    # Case 1. 레지스트리 에이전트 (공통) — 관리자만 토글 가능
    if agent_id in ALL_REGISTRIES:
        if not is_admin:
            raise HTTPException(status_code=403, detail="공통 에이전트는 관리자만 변경할 수 있습니다.")
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
    if not doc:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")

    # 권한 확인: 관리자 또는 본인
    if not is_admin and doc.get("user_id") != email:
        raise HTTPException(status_code=403, detail="본인이 만든 에이전트만 변경할 수 있습니다.")

    ok = await toggle_bq_schedule(get_scheduler(), agent_id, body.enabled)
    if ok:
        return {"agent_id": agent_id, "enabled": body.enabled}

    raise HTTPException(status_code=500, detail="토글 처리 중 오류가 발생했습니다.")


@router.delete("/api/boa-agents/{agent_id}")
async def delete_agent(agent_id: str, request: Request):
    user     = await require_login(request)
    email    = user["email"]
    is_admin = await check_is_admin(email)

    if agent_id in ALL_REGISTRIES:
        raise HTTPException(status_code=403, detail="코드에 등록된 에이전트는 삭제할 수 없습니다.")

    doc = await bq_schedules_collection.find_one({"schedule_id": agent_id})
    if not doc:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")

    # 권한 확인: 관리자 또는 본인
    if not is_admin and doc.get("user_id") != email:
        raise HTTPException(status_code=403, detail="본인이 만든 에이전트만 삭제할 수 있습니다.")

    deleted = await delete_bq_schedule(get_scheduler(), agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")

    print(f"[Agents] 삭제: {agent_id}")
    return {"agent_id": agent_id, "deleted": True}


# ══════════════════════════════════════════════════════════════════════════════
# 페이지
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/auth/login")
    # 관리자/멤버 모두 접근 가능 — 데이터는 API에서 권한별 필터링
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
    .header { display: flex; align-items: center; gap: 14px; margin-bottom: 28px; }
    .header h1 { font-size: 20px; font-weight: 600; }
    .role-badge { font-size: 11px; padding: 3px 10px; border-radius: 20px; font-family: 'IBM Plex Mono', monospace; }
    .role-admin  { background: rgba(99,102,241,0.18); color: #818cf8; }
    .role-member { background: rgba(100,116,139,0.18); color: var(--muted); }

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

    /* 타입 배지 */
    .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; font-family: 'IBM Plex Mono', monospace; white-space: nowrap; }
    .badge-chat     { background: rgba(99,102,241,0.15); color: #818cf8; }
    .badge-schedule { background: rgba(245,158,11,0.15); color: var(--yellow); }
    .badge-trigger  { background: rgba(34,197,94,0.15);  color: var(--green); }

    /* 등록자 칩 */
    .badge-creator {
      display: inline-flex; align-items: center; gap: 5px;
      background: rgba(59,130,246,0.12); color: var(--blue);
      border-radius: 20px; padding: 3px 10px; margin-left: 6px;
      font-size: 10px; font-family: 'IBM Plex Mono', monospace; white-space: nowrap;
    }
    .creator-name { font-weight: 600; color: #93c5fd; }
    .creator-id   { color: #60a5fa; opacity: 0.7; }

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
    input:disabled + .slider { opacity: 0.3; cursor: not-allowed; }

    .btn-delete { background: none; border: 1px solid transparent; border-radius: 6px; padding: 5px 12px; color: var(--muted); font-size: 12px; cursor: pointer; font-family: 'IBM Plex Sans KR', sans-serif; transition: all .15s; }
    .btn-delete:hover { border-color: var(--red); color: var(--red); background: rgba(239,68,68,0.08); }
    .action-placeholder { color: var(--border); font-size: 13px; }
    .space-chip { display: inline-block; background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.25); color: #60a5fa; border-radius: 4px; padding: 2px 7px; font-size: 10px; font-family: 'IBM Plex Mono', monospace; margin: 1px 2px; }

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
  <span class="role-badge" id="roleBadge" style="display:none"></span>
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
      <th>발송 대상</th>
      <th>업데이트</th>
      <th>활성화</th>
      <th>액션</th>
    </tr>
  </thead>
  <tbody id="tbody">
    <tr><td colspan="7" class="loading">로딩 중...</td></tr>
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
  let isAdmin = false;
  let currentUserId = '';

  // ── 데이터 로드 ────────────────────────────────────────────────────────────
  async function load() {
    const res = await fetch('/api/boa-agents');
    if (!res.ok) {
      document.getElementById('tbody').innerHTML =
        '<tr><td colspan="7" class="empty">데이터 로드 실패</td></tr>';
      return;
    }
    const json = await res.json();

    // 메타 정보 (권한, 현재 유저)
    const meta  = json._meta || {};
    isAdmin      = meta.is_admin   || false;
    currentUserId = meta.user_id   || '';

    // 역할 배지 표시
    const roleBadge = document.getElementById('roleBadge');
    roleBadge.style.display = '';
    if (isAdmin) {
      roleBadge.textContent = '👑 관리자';
      roleBadge.className   = 'role-badge role-admin';
    } else {
      roleBadge.textContent = '👤 멤버';
      roleBadge.className   = 'role-badge role-member';
    }

    data = { chat: json.chat || [], schedule: json.schedule || [], trigger: json.trigger || [] };
    updateStats();
    render();
  }

  // ── 통계 ──────────────────────────────────────────────────────────────────
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

  // ── 권한 판단 ─────────────────────────────────────────────────────────────
  // 공통(deletable=false): 관리자만 토글
  // 사용자 생성(deletable=true): 관리자 or 본인
  function canToggle(a) {
    if (!a.deletable) return isAdmin;
    return isAdmin || a.created_by === currentUserId;
  }
  function canDelete(a) {
    if (!a.deletable) return false;
    return isAdmin || a.created_by === currentUserId;
  }

  // ── 등록자 칩 ─────────────────────────────────────────────────────────────
  function creatorChip(a) {
    if (!a.deletable || !a.created_by) return '';
    const name    = escHtml(a.created_by_name || '');
    const idShort = escHtml(a.created_by.split('@')[0]);
    const nameEl  = name ? `<span class="creator-name">${name}</span>` : '';
    return `<span class="badge-creator">${nameEl}<span class="creator-id">${idShort}</span></span>`;
  }

  function typeBadge(a) {
    const map = {
      chat:     ['badge-chat',     '💬 대화형'],
      schedule: ['badge-schedule', '⏰ 스케줄'],
      trigger:  ['badge-trigger',  '⚡ 트리거'],
    };
    const [cls, label] = map[a.type] || ['badge-chat', a.type];
    return `<span class="badge ${cls}">${label}</span>${creatorChip(a)}`;
  }

  // ── 발송 대상 셀 ─────────────────────────────────────────────────────────────
  function spaceNamesHtml(a) {
    // chat 타입은 표시 안 함
    if (a.type === 'chat') return '<span class="action-placeholder">—</span>';
    const names = a.space_names || [];
    if (!names.length || (names.length === 1 && !names[0]))
      return '<span class="action-placeholder">—</span>';
    return names.map(n => {
      // spaces/XXX 형태면 짧게, 아니면 그대로
      const label = n.startsWith('spaces/') ? n.slice(7, 7+12) + (n.length > 19 ? '…' : '') : n;
      return `<span class="space-chip" title="${escHtml(n)}">${escHtml(label)}</span>`;
    }).join(' ');
  }

  function escHtml(s) {
    return String(s || '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  // ── 렌더 ──────────────────────────────────────────────────────────────────
  function render() {
    const q = document.getElementById('searchInput').value.toLowerCase();
    let agents = getAgents();
    if (q) agents = agents.filter(a =>
      (a.display_name || '').toLowerCase().includes(q) ||
      (a.agent_id || '').toLowerCase().includes(q)
    );

    const tbody = document.getElementById('tbody');
    if (!agents.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">등록된 에이전트가 없습니다</td></tr>';
      return;
    }

    tbody.innerHTML = agents.map(a => {
      const cronLine = a.cron_label
        ? `<div class="cron-label">🕐 ${escHtml(a.cron_label)}</div>` : '';
      const dateStr = (a.updated_at || a.created_at || '').slice(0, 10) || '—';

      // 토글 — 권한 없으면 disabled
      const disabled = canToggle(a) ? '' : 'disabled';
      const toggleHtml = `
        <div class="toggle-cell">
          <span class="status-dot ${a.enabled ? 'dot-on' : 'dot-off'}" id="dot-${escHtml(a.agent_id)}"></span>
          <label class="toggle">
            <input type="checkbox" ${a.enabled ? 'checked' : ''} ${disabled}
              onchange="toggleAgent('${escHtml(a.agent_id)}', this.checked, this)">
            <span class="slider"></span>
          </label>
        </div>`;

      // 삭제 버튼 — 권한 없으면 placeholder
      const actionCell = canDelete(a)
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
          <td>${spaceNamesHtml(a)}</td>
          <td class="td-mono">${dateStr}</td>
          <td>${toggleHtml}</td>
          <td>${actionCell}</td>
        </tr>
      `;
    }).join('');
  }

  // ── 토글 ──────────────────────────────────────────────────────────────────
  async function toggleAgent(agent_id, enabled, checkbox) {
    const res = await fetch(`/api/boa-agents/${agent_id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert('변경 실패: ' + (err.detail || '알 수 없는 오류'));
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

  // ── 삭제 모달 ─────────────────────────────────────────────────────────────
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