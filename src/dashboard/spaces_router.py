"""
스페이스 관리 라우터 (spaces_router.py)

페이지 전체 및 API 모두 boa_admins 전용.

GET   /spaces                  → 스페이스 관리 페이지 (HTML)
GET   /api/spaces              → 스페이스 목록
PATCH /api/spaces/settings     → settings 토글 (관리자 전용)
"""
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any

from src.core.mongo_client import db, boa_admins_collection

router = APIRouter()
boa_spaces_collection = db["boa_spaces"]


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
# 페이지 — 관리자만 접근
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/spaces", response_class=HTMLResponse)
async def spaces_page(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse("/auth/login")
    doc = await boa_admins_collection.find_one({"email": user["email"]})
    if not doc:
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;display:flex;align-items:center;
            justify-content:center;height:100vh;background:#0f1117;color:#e2e8f0;">
            <div style="text-align:center">
              <div style="font-size:48px">🔒</div>
              <h2 style="margin:16px 0 8px">접근 권한 없음</h2>
              <p style="color:#64748b">관리자만 접근할 수 있는 페이지입니다.</p>
              <a href="/dashboard" style="color:#6366f1;margin-top:16px;display:inline-block">← 대시보드로</a>
            </div></body></html>
        """, status_code=403)

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
    body { font-family: 'IBM Plex Sans KR', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; padding: 32px 24px; }
    .header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 28px; }
    .header h1 { font-size: 20px; font-weight: 600; }
    .back { color: var(--muted); text-decoration: none; font-size: 14px; }
    .back:hover { color: var(--text); }
    .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 24px; min-width: 140px; }
    .stat-card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
    .stat-card .value { font-size: 24px; font-weight: 600; }
    .stat-card .value.green  { color: var(--green); }
    .stat-card .value.yellow { color: var(--yellow); }
    .toolbar { display: flex; gap: 12px; margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 14px; font-family: inherit; }
    .search-bar:focus { outline: none; border-color: var(--accent); }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 12px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02); }
    td { padding: 14px 20px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; }
    .badge.group { background: rgba(99,102,241,0.15); color: #818cf8; }
    .badge.space { background: rgba(245,158,11,0.15); color: var(--yellow); }
    .badge.room  { background: rgba(245,158,11,0.15); color: var(--yellow); }
    .space-chip { font-size: 11px; color: var(--muted); background: rgba(255,255,255,0.05); padding: 2px 8px; border-radius: 4px; font-family: monospace; }
    .toggle { position: relative; display: inline-block; width: 40px; height: 22px; }
    .toggle input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; cursor: pointer; inset: 0; background: var(--border); border-radius: 22px; transition: .2s; }
    .slider:before { position: absolute; content: ""; width: 16px; height: 16px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: .2s; }
    input:checked + .slider { background: var(--accent); }
    input:checked + .slider:before { transform: translateX(18px); }
    .empty { text-align: center; padding: 60px; color: var(--muted); }
  </style>
</head>
<body>
<div class="header">
  <h1>💬 스페이스 관리</h1>
  <a href="/dashboard" class="back">← 대시보드</a>
</div>
<div class="stats">
  <div class="stat-card"><div class="label">전체 등록</div><div class="value" id="totalCount">—</div></div>
  <div class="stat-card"><div class="label">GROUP_CHAT</div><div class="value green" id="groupCount">—</div></div>
  <div class="stat-card"><div class="label">ROOM (스페이스)</div><div class="value yellow" id="roomCount">—</div></div>
  <div class="stat-card"><div class="label">일일 리포트 수신</div><div class="value green" id="reportCount">—</div></div>
</div>
<div class="toolbar">
  <input class="search-bar" type="text" id="searchInput" placeholder="이름 또는 space_name 검색...">
</div>
<table>
  <thead>
    <tr><th>이름</th><th>유형</th><th>Space Name</th><th>등록일</th><th>일일 리포트</th></tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
<script>
  let all = [];
  async function load() {
    const res = await fetch('/api/spaces');
    all = await res.json();
    render(all); stats(all);
  }
  function stats(s) {
    document.getElementById('totalCount').textContent  = s.length;
    document.getElementById('groupCount').textContent  = s.filter(x => x.space_type === 'GROUP_CHAT').length;
    document.getElementById('roomCount').textContent   = s.filter(x => x.space_type === 'ROOM' || x.space_type === 'SPACE').length;
    document.getElementById('reportCount').textContent = s.filter(x => x.settings?.report_daily).length;
  }
  function typeBadge(t) {
    const cls = t === 'GROUP_CHAT' ? 'group' : 'room';
    return `<span class="badge ${cls}">${t}</span>`;
  }
  function render(spaces) {
    const tbody = document.getElementById('tbody');
    if (!spaces.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">등록된 스페이스가 없습니다</td></tr>'; return; }
    tbody.innerHTML = spaces.map(s => `
      <tr>
        <td>${s.display_name || '—'}</td>
        <td>${typeBadge(s.space_type)}</td>
        <td><span class="space-chip">${s.space_name}</span></td>
        <td>${s.created_at ? s.created_at.slice(0,10) : '—'}</td>
        <td>
          <label class="toggle">
            <input type="checkbox" ${s.settings?.report_daily ? 'checked' : ''}
              onchange="toggle('${s.space_name}', 'report_daily', this.checked)">
            <span class="slider"></span>
          </label>
        </td>
      </tr>`).join('');
  }
  async function toggle(space_name, key, value) {
    const res = await fetch('/api/spaces/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ space_name, key, value })
    });
    if (!res.ok) { alert('변경 실패'); await load(); return; }
    const s = all.find(x => x.space_name === space_name);
    if (s) { s.settings = s.settings || {}; s.settings[key] = value; }
    stats(all);
  }
  document.getElementById('searchInput').addEventListener('input', e => {
    const q = e.target.value.toLowerCase();
    render(all.filter(s =>
      (s.display_name||'').toLowerCase().includes(q) ||
      s.space_name.toLowerCase().includes(q)
    ));
  });
  load();
</script>
</body>
</html>""")