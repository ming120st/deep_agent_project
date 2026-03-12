"""
스페이스 관리 라우터 (spaces_router.py)

순수 인벤토리: BOA가 참여 중인 스페이스 목록 조회만 제공.
에이전트별 발송 대상은 각 에이전트(boa_bq_schedules 등)에서 직접 관리.
"""
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core.mongo_client import boa_spaces_collection, boa_admins_collection

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
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3e;
      --text: #e2e8f0; --muted: #64748b; --accent: #6366f1;
      --green: #22c55e; --yellow: #f59e0b; --blue: #3b82f6;
    }
    body { font-family: 'IBM Plex Sans KR', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
    .top-nav { background: #13161b; border-bottom: 1px solid #252931; padding: 0 28px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
    .logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 16px; color: #eef1f6; letter-spacing: .05em; }
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
    .stat-card .value { font-size: 24px; font-weight: 600; font-family: 'IBM Plex Mono', monospace; }
    .value.green  { color: var(--green); }
    .value.yellow { color: var(--yellow); }
    .value.blue   { color: var(--blue); }
    .toolbar { margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 14px; font-family: inherit; outline: none; transition: border-color .15s; }
    .search-bar:focus { border-color: var(--accent); }
    .search-bar::placeholder { color: var(--muted); }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 11px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(255,255,255,0.02); white-space: nowrap; text-transform: uppercase; letter-spacing: .06em; font-family: 'IBM Plex Mono', monospace; }
    td { padding: 14px 20px; font-size: 14px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 500; font-family: 'IBM Plex Mono', monospace; }
    .badge-group { background: rgba(99,102,241,0.15); color: #818cf8; }
    .badge-room  { background: rgba(245,158,11,0.15); color: var(--yellow); }
    .space-name-chip { font-size: 11px; color: var(--muted); background: rgba(255,255,255,0.05); padding: 2px 8px; border-radius: 4px; font-family: 'IBM Plex Mono', monospace; }
    .empty   { text-align: center; padding: 60px; color: var(--muted); }
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
    <h1>💬 스페이스 현황</h1>
  </div>

  <div class="stats">
    <div class="stat-card"><div class="label">전체</div><div class="value" id="cntTotal">—</div></div>
    <div class="stat-card"><div class="label">GROUP_CHAT</div><div class="value green" id="cntGroup">—</div></div>
    <div class="stat-card"><div class="label">ROOM / SPACE</div><div class="value yellow" id="cntRoom">—</div></div>
  </div>

  <div class="toolbar">
    <input class="search-bar" type="text" id="searchInput" placeholder="이름 또는 space_name 검색...">
  </div>

  <table>
    <thead>
      <tr>
        <th>이름</th>
        <th>유형</th>
        <th>Space Name</th>
        <th>등록일</th>
        <th>최종 활동</th>
      </tr>
    </thead>
    <tbody id="tbody">
      <tr><td colspan="5" class="loading">로딩 중...</td></tr>
    </tbody>
  </table>
</div>

<script>
  let all = [];

  async function load() {
    const res = await fetch('/api/spaces');
    if (!res.ok) {
      document.getElementById('tbody').innerHTML = '<tr><td colspan="5" class="empty">로드 실패</td></tr>';
      return;
    }
    all = await res.json();
    updateStats();
    render(all);
  }

  function updateStats() {
    document.getElementById('cntTotal').textContent = all.length;
    document.getElementById('cntGroup').textContent = all.filter(s => s.space_type === 'GROUP_CHAT').length;
    document.getElementById('cntRoom').textContent  = all.filter(s => s.space_type === 'ROOM' || s.space_type === 'SPACE').length;
  }

  function typeBadge(t) {
    const cls = t === 'GROUP_CHAT' ? 'badge-group' : 'badge-room';
    return `<span class="badge ${cls}">${t}</span>`;
  }

  function render(spaces) {
    const tbody = document.getElementById('tbody');
    if (!spaces.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">등록된 스페이스가 없습니다</td></tr>';
      return;
    }
    tbody.innerHTML = spaces.map(s => `
      <tr>
        <td style="font-weight:500;color:#eef1f6">${s.display_name || '—'}</td>
        <td>${typeBadge(s.space_type)}</td>
        <td><span class="space-name-chip">${s.space_name}</span></td>
        <td style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted)">${s.created_at ? s.created_at.slice(0,10) : '—'}</td>
        <td style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--muted)">${s.updated_at ? s.updated_at.slice(0,10) : '—'}</td>
      </tr>
    `).join('');
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
</body>
</html>""")