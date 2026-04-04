"""
Google OAuth 인증 (auth.py)
/auth/login    → Google 로그인 페이지로 리다이렉트
/auth/callback → Google 인증 완료 후 세션 발급
/auth/logout   → 세션 삭제
"""
import os
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware

router = APIRouter()

# ── OAuth 설정 ─────────────────────────────────────────────────────────────────
oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# ── 허용 도메인 (회사 이메일만 접근 가능) ────────────────────────────────────────
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "widemobile.com")  # .env에서 변경 가능

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────
def get_current_user(request: Request):
    """세션에서 현재 유저 정보 반환. 없으면 None."""
    return request.session.get("user")

def require_login(request: Request):
    """로그인 안 되어 있으면 /auth/login으로 리다이렉트."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/auth/login")
    return None

async def is_admin(email: str) -> bool:
    """어드민 여부 확인 (MongoDB)"""
    from src.core.mongo_client import boa_admins_collection
    doc = await boa_admins_collection.find_one({"email": email})
    return doc is not None

# ── 라우트 ────────────────────────────────────────────────────────────────────
@router.get("/auth/login")
async def login(request: Request):
    redirect_uri = str(request.base_url) + "auth/callback"
    return await oauth.google.authorize_redirect(
        request, 
        redirect_uri,
        prompt="select_account"  # ← 추가: 항상 계정 선택 화면 표시
    )

@router.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")

    if not user_info:
        return RedirectResponse(url="/auth/login")

    email = user_info.get("email", "")

    # 허용 도메인 체크
    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        return RedirectResponse(url="/auth/denied")

    # 세션에 유저 정보 저장
    request.session["user"] = {
        "email": email,
        "name": user_info.get("name", ""),
        "picture": user_info.get("picture", ""),
    }
    return RedirectResponse(url="/dashboard")

@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie("session")  # 쿠키도 명시적으로 삭제
    return response

@router.get("/auth/denied")
async def denied():
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <html><body style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background:#0d0f12;color:#c8d0db">
    <div style="text-align:center">
        <div style="font-size:48px;margin-bottom:16px">🚫</div>
        <h2>접근 권한이 없습니다</h2>
        <p style="color:#5a6272">회사 계정으로만 접근할 수 있습니다.</p>
        <a href="/auth/login" style="color:#3b82f6">다시 시도</a>
    </div>
    </body></html>
    """)