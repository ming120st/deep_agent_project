"""
구성원 관리 (members_manager.py)

BOA와 DM을 시작한 개별 사용자를 boa_members 컬렉션에 자동 등록합니다.
main.py /chat 핸들러에서 space_type이 DM일 때 호출됩니다.

[boa_members 문서 구조]
{
  "name": "홍길동",
  "email": "user@email.com",
  "space_name": "spaces/XXXXXXXXXX",
  "department": "AI팀",
  "settings": {
    "report_daily": False,
  },
  "group_ids": [],
  "created_at": ...,
  "updated_at": ...
}
"""
from datetime import datetime
from src.core.mongo_client import boa_members_collection
from src.core.workspace_client import get_user_org_unit


async def upsert_member(user_id: str, user_name: str, space_name: str) -> None:
    """
    BOA에 DM(space_type="DM")이 올 때마다 호출.
    처음이면 생성 (settings 기본값), 있으면 name/space_name만 갱신.
    settings는 덮어쓰지 않음 — 대시보드에서 설정한 값 유지.

    department 미설정 시 Google Workspace Admin SDK로 조직단위(OU)를 자동 조회해
    department에 저장합니다. 조회에 실패하면 None 유지 (카드 표시 없음).
    """
    if not user_id or not space_name:
        return

    try:
        await boa_members_collection.update_one(
            {"email": user_id},
            {
                "$set": {
                    "name":       user_name,
                    "email":      user_id,
                    "space_name": space_name,
                    "updated_at": datetime.utcnow(),
                },
                "$setOnInsert": {
                    "department": None,
                    "settings": {
                        "report_daily": False,
                    },
                    "group_ids":  [],
                    "created_at": datetime.utcnow(),
                }
            },
            upsert=True
        )
        print(f"[Members] upsert: {user_id} → {space_name}")

        doc = await boa_members_collection.find_one(
            {"email": user_id},
            {"department": 1}
        )
        if doc and doc.get("department"):
            return  # 이미 department 설정됨

        # department 미설정 → Workspace Admin SDK로 자동 조회
        print(f"[Members] department 미설정 → Workspace OU 자동 조회: {user_id}")
        org_unit = await get_user_org_unit(user_id)
        if org_unit:
            await update_member_department(user_id, org_unit)
            print(f"[Members] department 자동 설정: {user_id} → {org_unit}")
        else:
            print(f"[Members] Workspace OU 조회 실패 — department 미설정 유지: {user_id}")

    except Exception as e:
        print(f"[Members] 오류: {e}")


async def update_member_department(user_id: str, department: str) -> bool:
    """
    구글챗 카드 폼 제출로 선택된 부서를 MongoDB에 저장.

    Returns:
        True: 성공 / False: 실패
    """
    try:
        result = await boa_members_collection.update_one(
            {"email": user_id},
            {"$set": {
                "department": department,
                "updated_at": datetime.utcnow(),
            }}
        )
        if result.matched_count == 0:
            print(f"[Members] department 업데이트 실패 — 구성원 없음: {user_id}")
            return False
        print(f"[Members] department 업데이트: {user_id} → {department}")
        return True
    except Exception as e:
        print(f"[Members] department 오류: {e}")
        return False