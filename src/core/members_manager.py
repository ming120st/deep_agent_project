"""
구성원 관리 (members_manager.py)

BOA와 DM을 시작한 개별 사용자를 boa_members 컬렉션에 자동 등록합니다.
main.py /chat 핸들러에서 space_type이 DM일 때 호출됩니다.

[boa_members 문서 구조]
{
  "name": "홍길동",
  "email": "user@email.com",
  "space_name": "spaces/XXXXXXXXXX",
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

async def upsert_member(user_id: str, user_name: str, space_name: str) -> None:
    """
    BOA에 DM(space_type="DM")이 올 때마다 호출.
    처음이면 생성 (settings 기본값), 있으면 name/space_name만 갱신.
    settings는 덮어쓰지 않음 — 대시보드에서 설정한 값 유지.
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
                    "settings": {
                        "report_daily": False,   # 기본값: 리포트 미발송
                    },
                    "group_ids":  [],
                    "created_at": datetime.utcnow(),
                }
            },
            upsert=True
        )
        print(f"[Members] upsert: {user_id} → {space_name}")
    except Exception as e:
        print(f"[Members] 오류: {e}")
