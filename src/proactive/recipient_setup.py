"""
수신자 등록 가이드 (recipient_setup.py)

boa_report_recipients 컬렉션에 리포트 수신자를 등록합니다.
BOA 대시보드에 관리 UI를 붙이기 전까지는 이 스크립트로 직접 등록하세요.
"""
import asyncio
from src.core.mongo_client import db

recipients_collection = db["boa_report_recipients"]


# ── 수신자 등록 ───────────────────────────────────────────────────────────────
async def add_recipient(name: str, email: str, space_name: str) -> str:
    """
    수신자를 등록합니다.

    Args:
        name:       이름 (예: "홍길동")
        email:      이메일 (예: "hong@widemobile.com")
        space_name: Google Chat DM space 이름
                    → 사용자가 BOA에 메시지를 보내면 /chat 핸들러의
                      chat_context["space_name"]으로 확인 가능

    Returns:
        삽입된 문서의 _id
    """
    from datetime import datetime

    doc = {
        "name":       name,
        "email":      email,
        "space_name": space_name,
        "active":     True,
        "created_at": datetime.utcnow(),
    }
    result = await recipients_collection.insert_one(doc)
    print(f"등록 완료: {name} ({email}) → {space_name}")
    return str(result.inserted_id)


async def deactivate_recipient(email: str) -> None:
    """수신자 비활성화 (삭제 대신 active=False)"""
    await recipients_collection.update_one(
        {"email": email},
        {"$set": {"active": False}}
    )
    print(f"비활성화: {email}")


async def list_recipients() -> None:
    """등록된 수신자 목록 출력"""
    cursor = recipients_collection.find()
    docs   = await cursor.to_list(length=None)
    print(f"\n{'─'*50}")
    print(f"{'이름':<12} {'이메일':<30} {'활성'}")
    print(f"{'─'*50}")
    for doc in docs:
        status = "✓" if doc.get("active") else "✗"
        print(f"{doc['name']:<12} {doc['email']:<30} {status}")
    print(f"{'─'*50}\n")


# ── space_name 확인 방법 안내 ─────────────────────────────────────────────────
"""
[space_name 확인 방법]

방법 1. 사용자가 BOA에 아무 메시지나 보내게 한 후
        /chat 핸들러 로그에서 chat_context["space_name"] 확인
        → "spaces/XXXXXXXXXX" 형식

방법 2. /chat 핸들러에 아래 코드 임시 추가:
        print(f"[SpaceName] {user_id}: {space_name}")

방법 3. Google Chat API로 직접 조회:
        chat_service.spaces().findDirectMessage(name="users/{email}").execute()
"""


# ── 샘플 등록 실행 ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def main():
        # 샘플 데이터 — 실제 값으로 교체하세요
        await add_recipient(
            name="이충덕",
            email="cdlee78@widemobile.com",
            space_name="spaces/3pkb6SAAAAE",
        )
        await list_recipients()

    asyncio.run(main())