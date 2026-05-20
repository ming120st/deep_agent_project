"""
구성원 관리 라우터 (members_router.py)

순수 인벤토리: BOA와 DM 중인 구성원 목록 조회만 제공.
에이전트별 발송 대상은 각 에이전트에서 직접 관리.
"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from src.core.mongo_client import boa_members_collection, boa_admins_collection

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


# ══════════════════════════════════════════════════════════════════════════════
# 페이지
# ══════════════════════════════════════════════════════════════════════════════

FORBIDDEN_HTML = """
<html><body style="font-family:sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;background:#f5f6fa;color:#1e2436;">
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
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #f5f6fa; --surface: #ffffff; --border: #e2e7f0;
      --text: #1e2436; --muted: #7b8499; --accent: #4f46e5;
      --green: #22c55e; --red: #ef4444; --blue: #3b82f6;
    }
    body { font-family: 'IBM Plex Sans KR', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; font-size: 15px; }
    .top-nav { background: #ffffff; border-bottom: 1px solid #e5e7eb; padding: 0 28px; height: 56px; display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100; }
    .logo { font-family: 'IBM Plex Mono', monospace; font-weight: 600; font-size: 16px; color: #1e2436; letter-spacing: .05em; }
    .logo span { color: #3b82f6; }
    .nav-links { display: flex; align-items: center; gap: 4px; }
    .nav-link { padding: 6px 14px; border-radius: 6px; font-size: 14px; color: var(--muted); text-decoration: none; transition: all .15s; }
    .nav-link:hover { color: var(--text); background: #f0f2f8; }
    .nav-link.active { color: #4f46e5; background: #ede9fe; }
    .btn-logout { background: none; border: 1px solid #e2e7f0; border-radius: 6px; padding: 5px 12px; color: var(--muted); font-size: 12px; cursor: pointer; text-decoration: none; transition: all .15s; }
    .btn-logout:hover { border-color: #ef4444; color: #ef4444; }
    .page-body { padding: 32px 24px; }
    .header { margin-bottom: 28px; }
    .header h1 { font-size: 20px; font-weight: 600; }
    .stats { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
    .stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 24px; min-width: 140px; }
    .stat-card .label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
    .stat-card .value { font-size: 24px; font-weight: 600; font-family: 'IBM Plex Mono', monospace; }
    .value.green { color: var(--green); }
    .value.red   { color: var(--red); }
    .toolbar { margin-bottom: 20px; }
    .search-bar { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; width: 300px; color: var(--text); font-size: 15px; font-family: inherit; outline: none; transition: border-color .15s; }
    .search-bar:focus { border-color: var(--accent); }
    .search-bar::placeholder { color: var(--muted); }
    table { width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }
    th { text-align: left; padding: 14px 20px; font-size: 12px; color: var(--muted); font-weight: 500; border-bottom: 1px solid var(--border); background: rgba(0,0,0,0.02); white-space: nowrap; text-transform: uppercase; letter-spacing: .06em; font-family: 'IBM Plex Mono', monospace; }
    td { padding: 14px 20px; font-size: 15px; border-bottom: 1px solid rgba(0,0,0,0.04); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: rgba(0,0,0,0.02); }
    .name-cell { font-weight: 500; color: #1a2035; }
    .email-cell { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--muted); }
    .dept-badge { display: inline-block; background: #1e293b; border: 1px solid #334155; padding: 2px 9px; border-radius: 4px; font-size: 12px; color: #94a3b8; }
    .dept-none  { display: inline-block; background: #fef2f2; border: 1px solid #fecaca; padding: 2px 9px; border-radius: 4px; font-size: 12px; color: #dc2626; }
    .space-chip { font-size: 11px; color: var(--muted); background: rgba(0,0,0,0.04); padding: 2px 8px; border-radius: 4px; font-family: 'IBM Plex Mono', monospace; }
    .no-space   { font-size: 12px; color: var(--red); }
    .date-cell  { font-family: 'IBM Plex Mono', monospace; font-size: 12px; color: var(--muted); }
    .empty   { text-align: center; padding: 60px; color: var(--muted); }
    .loading { text-align: center; padding: 60px; color: var(--muted); }
  </style>
</head>
<body>

<nav class="top-nav">
  <div class="logo">BOA <span>//</span> 구성원 관리</div>
  <div class="nav-links">
    <a href="/reports" class="nav-link">리포트</a>
    <a href="/dashboard" class="nav-link">업무 대시보드</a>
    <a href="/knowledge" class="nav-link">지식베이스</a>
    <a href="/agents" class="nav-link">에이전트</a>
    <a href="/spaces" class="nav-link">스페이스</a>
    <a href="/members" class="nav-link active">구성원</a>
    <a href="/sessions" class="nav-link">대화 로그</a>
  </div>
  <a href="/auth/logout" class="btn-logout">로그아웃</a>
</nav>

<div class="page-body">
  <div class="header">
    <h1>👥 구성원 현황</h1>
  </div>

  <div class="stats">
    <div class="stat-card"><div class="label">전체</div><div class="value" id="cntTotal">—</div></div>
    <div class="stat-card"><div class="label">부서 설정 완료</div><div class="value green" id="cntDept">—</div></div>
    <div class="stat-card"><div class="label">부서 미설정</div><div class="value red" id="cntNoDept">—</div></div>
  </div>

  <div class="toolbar">
    <input class="search-bar" type="text" id="searchInput" placeholder="이름 또는 이메일 검색...">
  </div>

  <table>
    <thead>
      <tr>
        <th>이름</th>
        <th>부서</th>
        <th>이메일</th>
        <th>DM Space</th>
        <th>등록일</th>
        <th>최종 활동</th>
      </tr>
    </thead>
    <tbody id="tbody">
      <tr><td colspan="6" class="loading">로딩 중...</td></tr>
    </tbody>
  </table>
</div>

<script>
  let all = [];

  async function load() {
    const res = await fetch('/api/members');
    if (!res.ok) {
      document.getElementById('tbody').innerHTML = '<tr><td colspan="6" class="empty">로드 실패</td></tr>';
      return;
    }
    all = await res.json();
    updateStats();
    render(all);
  }

  function updateStats() {
    const withDept = all.filter(m => m.department).length;
    document.getElementById('cntTotal').textContent  = all.length;
    document.getElementById('cntDept').textContent   = withDept;
    document.getElementById('cntNoDept').textContent = all.length - withDept;
  }

  function render(members) {
    const tbody = document.getElementById('tbody');
    if (!members.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">등록된 구성원이 없습니다</td></tr>';
      return;
    }
    tbody.innerHTML = members.map(m => {
      const deptHtml = m.department
        ? `<span class="dept-badge">${m.department}</span>`
        : `<span class="dept-none">미설정</span>`;
      const spaceHtml = m.space_name
        ? `<span class="space-chip">${m.space_name}</span>`
        : `<span class="no-space">미등록</span>`;
      return `
        <tr>
          <td class="name-cell">${m.name || '—'}</td>
          <td>${deptHtml}</td>
          <td class="email-cell">${m.email}</td>
          <td>${spaceHtml}</td>
          <td class="date-cell">${m.created_at ? m.created_at.slice(0,10) : '—'}</td>
          <td class="date-cell">${m.updated_at ? m.updated_at.slice(0,10) : '—'}</td>
        </tr>`;
    }).join('');
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
</body>
</html>""")