"""
core/workspace_client.py

Google Workspace Admin SDK Directory API 클라이언트.
사용자의 조직단위(OU) 정보를 조회합니다.

[필요 환경변수]
  GOOGLE_SERVICE_ACCOUNT_KEY_PATH  - 기존 서비스 계정 키 JSON 문자열 (재사용)
  GOOGLE_ADMIN_EMAIL               - 도메인 위임(impersonation)에 사용할 관리자 이메일

[사용 권한]
  사용자 관리 관리자 → 사용자 프로필 및 조직 구조 보기
  사용자 관리 관리자 → 조직단위 보기
"""
import asyncio
import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build


_ADMIN_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/admin.directory.orgunit.readonly",
]

_admin_service = None


def _get_admin_service():
    """Admin SDK 서비스 인스턴스 반환 (lazy singleton)"""
    global _admin_service
    if _admin_service is not None:
        return _admin_service

    try:
        service_account_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH"))
    except (TypeError, json.JSONDecodeError) as e:
        raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_KEY_PATH 환경변수 에러: {e}")

    admin_email = os.getenv("GOOGLE_ADMIN_EMAIL")
    if not admin_email:
        raise ValueError("GOOGLE_ADMIN_EMAIL 환경변수가 설정되지 않았습니다.")

    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=_ADMIN_SCOPES,
    ).with_subject(admin_email)  # 관리자 계정으로 위임

    _admin_service = build("admin", "directory_v1", credentials=credentials)
    return _admin_service


def _fetch_user_org_unit_sync(email: str) -> str | None:
    """
    동기 함수 (googleapiclient는 동기 I/O).
    asyncio.to_thread 로 호출.

    orgUnitPath 예시:
      "/"           → 최상위 (부서 미지정 취급)
      "/AI팀"       → "AI팀"
      "/본사/AI팀"  → "AI팀" (마지막 세그먼트 사용)
    """
    try:
        service = _get_admin_service()
        result = (
            service.users()
            .get(userKey=email, fields="orgUnitPath")
            .execute()
        )
        org_unit_path: str = result.get("orgUnitPath", "")

        if not org_unit_path or org_unit_path == "/":
            print(f"[Workspace] 최상위 OU 또는 미설정: {email}")
            return None

        # 마지막 세그먼트를 팀/부서명으로 사용
        department = org_unit_path.strip("/").split("/")[-1]
        print(f"[Workspace] OU 조회 성공: {email} → {department} ({org_unit_path})")
        return department

    except Exception as e:
        print(f"[Workspace] OU 조회 실패: {email} → {e}")
        return None


async def get_user_org_unit(email: str) -> str | None:
    """
    Google Workspace에서 사용자의 조직단위(OU) 이름을 비동기로 반환합니다.

    Returns:
        조직단위 이름 (예: "AI팀") 또는 None (조회 실패 / 최상위 OU)
    """
    return await asyncio.to_thread(_fetch_user_org_unit_sync, email)