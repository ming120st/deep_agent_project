import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import certifi
import os
import sys

# 프로젝트 최상단(src의 부모) 폴더를 Python 경로에 추가합니다.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv

load_dotenv()

from src.core.utils import encrypt_text

# 앱 코드와 동일하게 DOCUMENTDB_CONNECTION_STRING 환경변수를 사용합니다.
MONGODB_URI = os.getenv("DOCUMENTDB_CONNECTION_STRING")
if not MONGODB_URI:
    print("Error: DOCUMENTDB_CONNECTION_STRING not found in environment variables.")
    exit(1)

# DocumentDB 연결 시 tlsCAFile을 명시해줍니다.
client = AsyncIOMotorClient(MONGODB_URI, tlsCAFile=certifi.where())

# 운영 환경에서는 DB 이름을 직접 지정합니다. (개발 환경에서는 환경변수 사용 가능)
# db = client[os.getenv("MONGODB_DB_NAME", "boa_db")] # 개발 환경
db = client["boa_db"] # 운영 환경 

session_logs_collection = db["session_logs"]

async def migrate_plain_text_to_encrypted():
    """
    기존에 평문으로 저장된 session_logs 컬렉션의 events 배열 내 content를
    AES-256-GCM으로 암호화하여 DB를 업데이트합니다.
    """
    # Cosmos DB 호환성을 위해 필터 조건을 주지 않고 전체 리스트를 확인합니다.
    cursor = session_logs_collection.find()
    updated_sessions = 0
    total_events_updated = 0

    async for session in cursor:
        turns = session.get("turns", [])
        session_id = session.get("session_id")
        needs_update = False
        
        for turn_idx, turn in enumerate(turns):
            events = turn.get("events", [])
            for event_idx, event in enumerate(events):
                content = event.get("content")
                if content is not None:
                    # 엄격한 평문 여부 검사
                    is_plain = False
                    if not isinstance(content, str):
                        is_plain = True
                    else:
                        import base64
                        try:
                            # base64 디코딩을 시도해보고 실제 바이트로 12+16 바이트가 넘는지 확인
                            b = base64.b64decode(content.encode('utf-8'))
                            if len(b) < 28:
                                is_plain = True
                            # 공백이나 특수기호가 남아있다면 평문이라고 취급
                            if any(c in content for c in (' ', '_', '/', '{', '}')):
                                is_plain = True
                        except Exception:
                            is_plain = True
                            
                    if is_plain:
                        try:
                            encrypted_content = encrypt_text(content)
                            session['turns'][turn_idx]['events'][event_idx]['content'] = encrypted_content
                            needs_update = True
                            total_events_updated += 1
                        except Exception as e:
                            print(f"[Migration] Error encrypting content: {e}")
                        
        if needs_update:
            await session_logs_collection.update_one(
                {"_id": session["_id"]},
                {"$set": {"turns": session["turns"]}}
            )
            updated_sessions += 1
            print(f"Updated session: {session_id}")

    print(f"Migration complete. Updated {updated_sessions} sessions, {total_events_updated} events.")

if __name__ == "__main__":
    asyncio.run(migrate_plain_text_to_encrypted())


#python scripts/encrypt_existing_sessions.py