"""
구성원 관리 라우터 (members_router.py)

[settings 스키마 동적 렌더링]
  MEMBER_SETTINGS_SCHEMA 리스트에 항목 추가하면
  → /api/members/schema API로 프론트에 내려감
  → UI가 자동으로 토글 컬럼 추가
  → HTML 수정 불필요

새 설정 추가 방법:
  MEMBER_SETTINGS_SCHEMA에 한 줄 추가
  {"key": "new_setting", "label": "새 설정 이름"}
"""
import json
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Any

from src.core.mongo_client import boa_members_collection, boa_admins_collection

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# 설정 스키마 — 여기에만 추가하면 UI 자동 반영
# ══════════════════════════════════════════════════════════════════════════════
MEMBER_SETTINGS_SCHEMA = [
    {"key": "report_daily",  "label": "일일 리포트"},
    {"key": "alert_waiting", "label": "대기 알림"},   # 예시: 추가 시 주석 해제
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

@router.get("/api/members/schema")
async def get_members_schema(request: Request):
    """설정 스키마 반환 — 프론트에서 동적 렌더링에 사용"""
    await require_admin(request)
    return MEMBER_SETTINGS_SCHEMA


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

    # 스키마에 없는 key는 거부
    valid_keys = {s["key"] for s in MEMBER_SETTINGS_SCHEMA}
    if body.key not in valid_keys:
        raise HTTPException(status_code=400, detail=f"유효하지 않은 설정 키: {body.key}")

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

@router.get("/members", response_class=HTMLResponse)
async def members_page(request: Request):
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
  <title>구성원 관리 — BOA</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3e;
      --text: #e2e8f0; --muted: #64748b; --accent: #6366f1;
      --green: #22c55e; --red: #ef4444;
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
    .stat-card .value.green { color: var(--green); }
    .toolbar { margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 14px; font-family: inherit; }
    .search-bar:focus { outline: none; border-color: var(--accent); }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 12px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02); white-space: nowrap; }
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
    .loading { text-align: center; padding: 60px; color: var(--muted); }
  </style>
</head>
<body>
<nav class="top-nav">
  <div class="logo">BOA <span>//</span> 구성원 관리</div>
  <div class="nav-links">
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link active">구성원</a>
  </div>
  <a href="/auth/logout" class="btn-logout">로그아웃</a>
</nav>
<div class="page-body">
<div class="header">
  <h1>👥 구성원 관리</h1>
</div>
<div class="stats" id="stats">
  <div class="stat-card"><div class="label">전체 구성원</div><div class="value" id="totalCount">—</div></div>
</div>
<div class="toolbar">
  <input class="search-bar" type="text" id="searchInput" placeholder="이름 또는 이메일 검색...">
</div>
<table>
  <thead><tr id="thead-row"></tr></thead>
  <tbody id="tbody"><tr><td class="loading">로딩 중...</td></tr></tbody>
</table>

<script>
  let all = [], schema = [];

  async function load() {
    // 스키마와 데이터 병렬 로드
    [schema, all] = await Promise.all([
      fetch('/api/members/schema').then(r => r.json()),
      fetch('/api/members').then(r => r.json()),
    ]);
    buildHeader();
    buildStats();
    render(all);
  }

  function buildHeader() {
    const tr = document.getElementById('thead-row');
    const base = ['이름', '이메일', 'Space', '등록일'];
    const schemaLabels = schema.map(s => s.label);
    [...base, ...schemaLabels].forEach(label => {
      const th = document.createElement('th');
      th.textContent = label;
      tr.appendChild(th);
    });
  }

  function buildStats() {
    const statsEl = document.getElementById('stats');
    // 전체 카드는 이미 있음, 설정별 카드 동적 추가
    schema.forEach(s => {
      const count = all.filter(m => m.settings?.[s.key]).length;
      const card = document.createElement('div');
      card.className = 'stat-card';
      card.id = `stat-${s.key}`;
      card.innerHTML = `<div class="label">${s.label}</div><div class="value green">${count}</div>`;
      statsEl.appendChild(card);
    });
    document.getElementById('totalCount').textContent = all.length;
  }

  function updateStats() {
    document.getElementById('totalCount').textContent = all.length;
    schema.forEach(s => {
      const el = document.querySelector(`#stat-${s.key} .value`);
      if (el) el.textContent = all.filter(m => m.settings?.[s.key]).length;
    });
  }

  function render(members) {
    const tbody = document.getElementById('tbody');
    const colSpan = 4 + schema.length;
    if (!members.length) {
      tbody.innerHTML = `<tr><td colspan="${colSpan}" class="empty">등록된 구성원이 없습니다</td></tr>`;
      return;
    }
    tbody.innerHTML = members.map(m => {
      const spaceCell = m.space_name
        ? `<span class="space-chip">${m.space_name}</span>`
        : '<span class="no-space">미등록</span>';
      const date = m.created_at ? m.created_at.slice(0, 10) : '—';
      // 스키마 기반으로 토글 셀 동적 생성
      const toggleCells = schema.map(s => `
        <td>
          <label class="toggle">
            <input type="checkbox" ${m.settings?.[s.key] ? 'checked' : ''}
              onchange="toggleSetting('${m.email}', '${s.key}', this.checked)">
            <span class="slider"></span>
          </label>
        </td>`).join('');
      return `
        <tr>
          <td>${m.name || '—'}</td>
          <td>${m.email}</td>
          <td>${spaceCell}</td>
          <td>${date}</td>
          ${toggleCells}
        </tr>`;
    }).join('');
  }

  async function toggleSetting(email, key, value) {
    const res = await fetch('/api/members/settings', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email, key, value })
    });
    if (!res.ok) { alert('변경 실패'); await load(); return; }
    const m = all.find(x => x.email === email);
    if (m) { m.settings = m.settings || {}; m.settings[key] = value; }
    updateStats();
  }

  document.getElementById('searchInput').addEventListener('input', e => {
    const q = e.target.value.toLowerCase();
    render(all.filter(m =>
      (m.name || '').toLowerCase().includes(q) ||
      m.email.toLowerCase().includes(q)
    ));
  });

  load();
</script>
</div>
</body>
</html>""")