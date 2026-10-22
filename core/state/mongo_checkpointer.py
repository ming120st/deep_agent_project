from langgraph.checkpoint.mongodb import (
    MongoDBSaver,
)

from core.database.mongo import (
    MONGODB_URI,
    MONGODB_DB_NAME,
)


# 1시간
CHECKPOINT_TTL_SECONDS = 3600


def create_mongo_checkpointer():
    """
    BOA Deep Agent MongoDB Checkpointer 생성.

    - 기존 MongoDB 서버 사용
    - Checkpoint TTL 7일
    - Agent 재생성 시 상태 유지
    - Checkpoint 및 Pending Writes 저장
    """

    return MongoDBSaver.from_conn_string(
        conn_string=MONGODB_URI,
        db_name=MONGODB_DB_NAME,
        checkpoint_collection_name=(
            "boa_checkpoints"
        ),
        writes_collection_name=(
            "boa_checkpoint_writes"
        ),
        ttl=CHECKPOINT_TTL_SECONDS,
    )