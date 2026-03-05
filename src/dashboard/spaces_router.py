"""
스페이스 관리 라우터 (spaces_router.py)

[settings 스키마 동적 렌더링]
  SPACE_SETTINGS_SCHEMA 리스트에 항목 추가하면
  → /api/spaces/schema API로 프론트에 내려감
  → UI가 자동으로 토글 컬럼 추가
  → HTML 수정 불필요

새 설정 추가 방법:
  SPACE_SETTINGS_SCHEMA에 한 줄 추가
  {"key": "new_setting", "label": "새 설정 이름"}
"""
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any

from src.core.mongo_client import boa_spaces_collection, boa_admins_collection

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# 설정 스키마 — 여기에만 추가하면 UI 자동 반영
# ══════════════════════════════════════════════════════════════════════════════
SPACE_SETTINGS_SCHEMA = [
    {"key": "report_daily",  "label": "일일 리포트"},
    {"key": "announcement",  "label": "공지 발송"},   # 예시: 추가 시 주석 해제
    # {"key": "report_weekly", "label": "주간 리포트"}, # 예시: 추가 시 주석 해제
]


# ── 관리자 체크 ───────────────────────────────────────────────────────────────
async def require_admin(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401)
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    if not doc:
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user


# ══════════════════════════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/api/spaces/schema")
async def get_spaces_schema(request: Request):
    """설정 스키마 반환 — 프론트에서 동적 렌더링에 사용"""
    await require_admin(request)
    return SPACE_SETTINGS_SCHEMA


@router.get("/api/spaces")
async def get_spaces(request: Request):
    await require_admin(request)
    cursor = boa_spaces_collection.find({}, {"_id": 0})
    spaces = await cursor.to_list(length=None)
    for s in spaces:
        for key in ("created_at", "updated_at"):
            if s.get(key):
                s[key] = s[key].isoformat()
    return spaces


class SpaceSettingsUpdate(BaseModel):
    space_name: str
    key:        str
    value:      Any


@router.patch("/api/spaces/settings")
async def update_space_settings(request: Request, body: SpaceSettingsUpdate):
    await require_admin(request)

    # 스키마에 없는 key는 거부
    valid_keys = {s["key"] for s in SPACE_SETTINGS_SCHEMA}
    if body.key not in valid_keys:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 설정 키: {body.key}")

    result = await boa_spaces_collection.update_one(
        {"space_name": body.space_name},
        {"$set": {
            f"settings.{body.key}": body.value,
            "updated_at": datetime.utcnow(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="스페이스를 찾을 수 없습니다.")
    return {"ok": True, "space_name": body.space_name, "key": body.key, "value": body.value}


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

@router.get("/spaces", response_class=HTMLResponse)
async def spaces_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/auth/login")
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    if not doc:
        return HTMLResponse(FORBIDDEN_HTML, status_code=403)

    return HTMLResponse("""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>스페이스 관리 — BOA</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3e;
      --text: #e2e8f0; --muted: #64748b; --accent: #6366f1;
      --green: #22c55e; --yellow: #f59e0b;
    }
    body { font-family: 'IBM Plex Sans KR', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
    /* 상단 네비게이션 바 */
    .top-nav { background: #13161b; border-bottom: 1px solid #252931; padding: 0 28px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
    .logo { font-family: monospace; font-weight: 600; font-size: 16px; color: #eef1f6; letter-spacing: .05em; }
    .logo span { color: #3b82f6; }
    .nav-links { display: flex; align-items: center; gap: 4px; }
    .nav-link { padding: 6px 14px; border-radius: 6px; font-size: 13px; color: var(--muted); text-decoration: none; transition: all .15s; }
    .nav-link:hover { color: var(--text); background: #1a1e25; }
    .nav-link.active { color: #3b82f6; background: #1d3d6e; }
    .btn-logout { background: none; border: 1px solid #252931; border-radius: 6px; padding: 5px 12px; color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: none; transition: all .15s; }
    .btn-logout:hover { border-color: #ef4444; color: #ef4444; }
    .page-body { padding: 32px 24px; }
    .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 28px; }
    .header h1 { font-size: 20px; font-weight: 600; }
    .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 24px; min-width: 140px; }
    .stat-card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
    .stat-card .value { font-size: 24px; font-weight: 600; }
    .stat-card .value.green  { color: var(--green); }
    .stat-card .value.yellow { color: var(--yellow); }
    .toolbar { margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 14px; font-family: inherit; }
    .search-bar:focus { outline: none; border-color: var(--accent); }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 12px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02); white-space: nowrap; }
    td { padding: 14px 20px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; }
    .badge.group  { background: rgba(99,102,241,0.15); color: #818cf8; }
    .badge.room   { background: rgba(245,158,11,0.15); color: var(--yellow); }
    .space-chip { font-size: 11px; color: var(--muted); background: rgba(255,255,255,0.05); padding: 2px 8px; border-radius: 4px; font-family: monospace; }
    .toggle { position: relative; display: inline-block; width: 40px; height: 22px; }
    .toggle input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; cursor: pointer; inset: 0; background: var(--border); border-radius: 22px; transition: .2s; }
    .slider:before { position: absolute; content: ""; width: 16px; height: 16px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: .2s; }
    input:checked + .slider { background: var(--accent); }
    input:checked + .slider:before { transform: translateX(18px); }
    .empty { text-align: center; padding: 60px; color: var(--muted); }
    .loading { text-align: center; padding: 60px; color: var(--muted); }
  </style>
</head>
<body>
<nav class="top-nav">
  <div class="logo">BOA <span>//</span> 스페이스 관리</div>
  <div class="nav-links">
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link active">스페이스</a>
    <a href="/members" class="nav-link">구성원</a>
  </div>
  <a href="/auth/logout" class="btn-logout">로그아웃</a>
</nav>
<div class="page-body">
<div class="header">
  <h1>💬 스페이스 관리</h1>
</div>
<div class="stats" id="stats">
  <div class="stat-card"><div class="label">전체 등록</div><div class="value" id="totalCount">—</div></div>
  <div class="stat-card"><div class="label">GROUP_CHAT</div><div class="value green" id="groupCount">—</div></div>
  <div class="stat-card"><div class="label">ROOM</div><div class="value yellow" id="roomCount">—</div></div>
</div>
<div class="toolbar">
  <input class="search-bar" type="text" id="searchInput" placeholder="이름 또는 space_name 검색...">
</div>
<table>
  <thead><tr id="thead-row"></tr></thead>
  <tbody id="tbody"><tr><td class="loading">로딩 중...</td></tr></tbody>
</table>

<script>
  let all = [], schema = [];

  async function load() {
    [schema, all] = await Promise.all([
      fetch('/api/spaces/schema').then(r => r.json()),
      fetch('/api/spaces').then(r => r.json()),
    ]);
    buildHeader();
    buildStats();
    render(all);
  }

  function buildHeader() {
    const tr = document.getElementById('thead-row');
    const base = ['이름', '유형', 'Space Name', '등록일'];
    const schemaLabels = schema.map(s => s.label);
    [...base, ...schemaLabels].forEach(label => {
      const th = document.createElement('th');
      th.textContent = label;
      tr.appendChild(th);
    });
  }

  function buildStats() {
    const statsEl = document.getElementById('stats');
    // 기본 카운트 업데이트
    document.getElementById('totalCount').textContent = all.length;
    document.getElementById('groupCount').textContent = all.filter(s => s.space_type === 'GROUP_CHAT').length;
    document.getElementById('roomCount').textContent  = all.filter(s => s.space_type === 'ROOM' || s.space_type === 'SPACE').length;
    // 스키마별 카드 동적 추가
    schema.forEach(s => {
      const count = all.filter(x => x.settings?.[s.key]).length;
      const card = document.createElement('div');
      card.className = 'stat-card';
      card.id = `stat-${s.key}`;
      card.innerHTML = `<div class="label">${s.label}</div><div class="value green">${count}</div>`;
      statsEl.appendChild(card);
    });
  }

  function updateStats() {
    document.getElementById('totalCount').textContent = all.length;
    document.getElementById('groupCount').textContent = all.filter(s => s.space_type === 'GROUP_CHAT').length;
    document.getElementById('roomCount').textContent  = all.filter(s => s.space_type === 'ROOM' || s.space_type === 'SPACE').length;
    schema.forEach(s => {
      const el = document.querySelector(`#stat-${s.key} .value`);
      if (el) el.textContent = all.filter(x => x.settings?.[s.key]).length;
    });
  }

  function typeBadge(t) {
    const cls = t === 'GROUP_CHAT' ? 'group' : 'room';
    return `<span class="badge ${cls}">${t}</span>`;
  }

  function render(spaces) {
    const tbody = document.getElementById('tbody');
    const colSpan = 4 + schema.length;
    if (!spaces.length) {
      tbody.innerHTML = `<tr><td colspan="${colSpan}" class="empty">등록된 스페이스가 없습니다</td></tr>`;
      return;
    }
    tbody.innerHTML = spaces.map(s => {
      const toggleCells = schema.map(sc => `
        <td>
          <label class="toggle">
            <input type="checkbox" ${s.settings?.[sc.key] ? 'checked' : ''}
              onchange="toggleSetting('${s.space_name}', '${sc.key}', this.checked)">
            <span class="slider"></span>
          </label>
        </td>`).join('');
      return `
        <tr>
          <td>${s.display_name || '—'}</td>
          <td>${typeBadge(s.space_type)}</td>
          <td><span class="space-chip">${s.space_name}</span></td>
          <td>${s.created_at ? s.created_at.slice(0, 10) : '—'}</td>
          ${toggleCells}
        </tr>`;
    }).join('');
  }

  async function toggleSetting(space_name, key, value) {
    const res = await fetch('/api/spaces/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ space_name, key, value })
    });
    if (!res.ok) { alert('변경 실패'); await load(); return; }
    const s = all.find(x => x.space_name === space_name);
    if (s) { s.settings = s.settings || {}; s.settings[key] = value; }
    updateStats();
  }

  document.getElementById('searchInput').addEventListener('input', e => {
    const q = e.target.value.toLowerCase();
    render(all.filter(s =>
      (s.display_name || '').toLowerCase().includes(q) ||
      s.space_name.toLowerCase().includes(q)
    ));
  });

  load();
</script>
</div>
</body>
</html>""")
