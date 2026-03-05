"""
스페이스 관리 (spaces_manager.py)

BOA가 참여한 단체 공간(GROUP_CHAT, SPACE)을 boa_spaces 컬렉션에 자동 등록합니다.
main.py /chat 핸들러에서 space_type이 DM이 아닐 때 호출됩니다.

[boa_spaces 문서 구조]
{
  "space_name":   "spaces/XXXXXXXXXX",   ← Google Chat space 리소스명
  "display_name": "AI+BM",               ← 채팅방/스페이스 이름
  "space_type":   "GROUP_CHAT" | "SPACE",
  "settings": {
    "announcement": False,               ← 향후: 공지 발송 대상 여부
  },
  "created_at": ...,
  "updated_at": ...
}
"""
from datetime import datetime
from src.core.mongo_client import boa_spaces_collection

MANAGED_SPACE_TYPES = {"GROUP_CHAT", "SPACE", "ROOM"}  # space의 type이 "SPACE"가 아니라 "ROOM"인 경우도 존재하여 둘 다 처리


async def upsert_space(space_name: str, display_name: str, space_type: str) -> None:
    """
    BOA가 메시지를 받은 단체 공간을 자동 등록/갱신합니다.
    처음이면 생성 (settings 기본값), 있으면 display_name만 갱신.
    settings는 덮어쓰지 않음 — 대시보드 설정값 유지.

    main.py에서:
        if space_type in ("GROUP_CHAT", "SPACE"):
            background_tasks.add_task(upsert_space, space_name, display_name, space_type)
    """
    if not space_name or space_type not in MANAGED_SPACE_TYPES:
        print(f"[Spaces] skip: type={space_type}, name={space_name}")
        return

    try:
        await boa_spaces_collection.update_one(
            {"space_name": space_name},
            {
                "$set": {
                    "space_name":   space_name,
                    "display_name": display_name or space_name,
                    "space_type":   space_type,
                    "updated_at":   datetime.utcnow(),
                },
                "$setOnInsert": {
                    "settings": {
                        "announcement": False,   # 향후 공지 발송 등 설정 추가
                    },
                    "created_at": datetime.utcnow(),
                }
            },
            upsert=True
        )
    except Exception as e:
        print(f"[Spaces] 오류: {e}")
    print(f"[Spaces] upsert: {space_type} / {display_name} → {space_name}")