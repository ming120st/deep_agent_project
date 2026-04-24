"""
KB v2 Gap Recorder — 지식베이스 미답변 질의 기록.

P2: exact match 중복 병합.
P5: 임베딩 유사도 기반 병합으로 고도화 예정.
"""
import uuid
import logging
from datetime import datetime, timezone

from src.core.mongo_client import boa_knowledge_gaps_collection

logger = logging.getLogger(__name__)


async def record_gap(
    query: str,
    session_id: str | None,
    user_member_id: str | None,
    searched_topics: list[dict],
    ai_verdict: str,
    ai_suggestion: str | None = None,
) -> str:
    """
    gap을 기록한다.
    - query exact match로 기존 gap 조회.
    - 있으면 duplicate_count +1, last_occurred_at 갱신 후 기존 _id 반환.
    - 없으면 신규 insert 후 새 _id 반환.

    searched_topics: [{"topic_id": ..., "score": ..., "read": bool}, ...]
    ai_verdict: "no_match" | "insufficient_coverage"
    """
    now = datetime.now(timezone.utc)

    try:
        existing = await boa_knowledge_gaps_collection.find_one(
            {"query": query, "status": {"$in": ["open", "in_progress"]}},
            {"_id": 1},
        )

        if existing:
            await boa_knowledge_gaps_collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$inc": {"duplicate_count": 1},
                    "$set": {"last_occurred_at": now},
                },
            )
            logger.info("[GapRecorder] 중복 gap 갱신: %s (query=%s)", existing["_id"], query[:40])
            return existing["_id"]

        gap_id = uuid.uuid4().hex
        await boa_knowledge_gaps_collection.insert_one({
            "_id":             gap_id,
            "query":           query,
            "query_embedding": None,
            "session_id":      session_id,
            "user_member_id":  user_member_id,
            "searched_topics": searched_topics,
            "ai_verdict":      ai_verdict,
            "ai_suggestion":   ai_suggestion,
            "duplicate_count": 1,
            "created_at":      now,
            "last_occurred_at": now,
            "status":          "open",
            "resolved_topic_id": None,
            "assigned_to":     None,
        })
        logger.info("[GapRecorder] 신규 gap 생성: %s (query=%s)", gap_id, query[:40])
        return gap_id

    except Exception as exc:
        logger.error("[GapRecorder] 기록 실패: %s", exc)
        raise
