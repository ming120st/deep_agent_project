"""
구성원 관리 라우터 (members_router.py)

페이지 전체 및 API 모두 boa_admins 전용.
"""
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any

from src.core.mongo_client import db, boa_admins_collection

router = APIRouter()
boa_members_collection = db["boa_members"]


# ── 관리자 체크 ───────────────────────────────────────────────────────────────
async def require_admin(request: Request) -> dict:
    """관리자가 아니면 403. 로그인 안 했으면 로그인 페이지로."""
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

@router.get("/api/members")
async def get_members(request: Request):
    await require_admin(request)
    cursor  = boa_members_collection.find({}, {"_id": 0})
    members = await cursor.to_list(length=None)
    for m in members:
        for key in ("created_at", "updated_at"):
            if m.get(key):
                m[key] = m[key].isoformat()
    return members


class SettingsUpdate(BaseModel):
    email: str
    key:   str
    value: Any


@router.patch("/api/members/settings")
async def update_member_settings(request: Request, body: SettingsUpdate):
    await require_admin(request)
    result = await boa_members_collection.update_one(
        {"email": body.email},
        {"$set": {
            f"settings.{body.key}": body.value,
            "updated_at": datetime.utcnow(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="구성원을 찾을 수 없습니다.")
    return {"ok": True, "email": body.email, "key": body.key, "value": body.value}


# ══════════════════════════════════════════════════════════════════════════════
# 페이지 — 관리자만 접근
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/members", response_class=HTMLResponse)
async def members_page(request: Request):
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
  <title>구성원 관리 — BOA</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3e;
      --text: #e2e8f0; --muted: #64748b; --accent: #6366f1;
      --green: #22c55e; --red: #ef4444;
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
    .stat-card .value.green { color: var(--green); }
    .toolbar { display: flex; gap: 12px; margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 14px; font-family: inherit; }
    .search-bar:focus { outline: none; border-color: var(--accent); }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 12px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02); }
    td { padding: 14px 20px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .space-chip { font-size: 11px; color: var(--muted); background: rgba(255,255,255,0.05); padding: 2px 8px; border-radius: 4px; font-family: monospace; }
    .no-space { font-size: 12px; color: var(--red); }
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
  <h1>👥 구성원 관리</h1>
  <a href="/dashboard" class="back">← 대시보드</a>
</div>
<div class="stats">
  <div class="stat-card"><div class="label">전체 구성원</div><div class="value" id="totalCount">—</div></div>
  <div class="stat-card"><div class="label">일일 리포트 수신</div><div class="value green" id="reportCount">—</div></div>
</div>
<div class="toolbar">
  <input class="search-bar" type="text" id="searchInput" placeholder="이름 또는 이메일 검색...">
</div>
<table>
  <thead>
    <tr><th>이름</th><th>이메일</th><th>Space</th><th>등록일</th><th>일일 리포트</th></tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>
<script>
  let all = [];
  async function load() {
    const res = await fetch('/api/members');
    all = await res.json();
    render(all); stats(all);
  }
  function stats(m) {
    document.getElementById('totalCount').textContent  = m.length;
    document.getElementById('reportCount').textContent = m.filter(x => x.settings?.report_daily).length;
  }
  function render(members) {
    const tbody = document.getElementById('tbody');
    if (!members.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">등록된 구성원이 없습니다</td></tr>'; return; }
    tbody.innerHTML = members.map(m => `
      <tr>
        <td>${m.name || '—'}</td>
        <td>${m.email}</td>
        <td>${m.space_name ? `<span class="space-chip">${m.space_name}</span>` : '<span class="no-space">미등록</span>'}</td>
        <td>${m.created_at ? m.created_at.slice(0,10) : '—'}</td>
        <td>
          <label class="toggle">
            <input type="checkbox" ${m.settings?.report_daily ? 'checked' : ''}
              onchange="toggle('${m.email}', 'report_daily', this.checked)">
            <span class="slider"></span>
          </label>
        </td>
      </tr>`).join('');
  }
  async function toggle(email, key, value) {
    const res = await fetch('/api/members/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email, key, value })
    });
    if (!res.ok) { alert('변경 실패'); await load(); return; }
    const m = all.find(x => x.email === email);
    if (m) { m.settings = m.settings || {}; m.settings[key] = value; }
    stats(all);
  }
  document.getElementById('searchInput').addEventListener('input', e => {
    const q = e.target.value.toLowerCase();
    render(all.filter(m => (m.name||'').toLowerCase().includes(q) || m.email.toLowerCase().includes(q)));
  });
  load();
</script>
</body>
</html>""")