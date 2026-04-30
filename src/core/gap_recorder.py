"""
KB v2 Gap Recorder — 지식베이스 미답변 질의 기록.

P5: 임베딩 유사도 기반 병합 + ai_suggestion LLM 자동 생성.
"""
import uuid
import logging
import asyncio
import os
from datetime import datetime, timezone

import numpy as np

from src.core.mongo_client import boa_knowledge_gaps_collection

logger = logging.getLogger(__name__)
SIMILARITY_THRESHOLD = float(os.getenv("GAP_SIMILARITY_THRESHOLD", "0.92"))


async def _get_query_embedding(query: str) -> "np.ndarray | None":
    """
    사용자의 질문을 임베딩 벡터로 변환한다.
    """
    try:
        from src.core.rag_engine import get_engine
        emb = await get_engine().embed_query(query) # embed_query: 문장 > 임베딩 벡터 (DIM,) 반환
        return emb[0]  # (1, DIM) → (DIM,) 1차원으로. 
    except Exception as e:
        logger.warning("[GapRecorder] 임베딩 생성 실패: %s", e)
        return None


async def _find_similar_gap(query_emb: np.ndarray) -> "str | None": # 쿼리와 유사한 기존 gap_id 반환. 없으면 None
    """
    이 함수는 사용자 쿼리에 적합한 문서를 찾는게 아니라 아직 해결되지 않은 기존 gap 중에서 사용자 쿼리와 의미적으로 유사한 것이 있는지 찾는 함수다.
    open/in_progress gap 중 query_embedding이 있는 것들과 코사인 유사도 비교.
    SIMILARITY_THRESHOLD 이상이면 해당 gap_id 반환.
    벡터는 L2 정규화되어 있으므로 내적 = 코사인 유사도.
    """
    cursor = boa_knowledge_gaps_collection.find(
        {
            "status": {"$in": ["open", "in_progress"]}, # 아직 해결되지 않은 gap만 비교
            "query_embedding": {"$ne": None}, # 임베딩이 없는 gap은 비교에서 제외
        },
        {"_id": 1, "query_embedding": 1}, # 가져올 필드: gap_id와 임베딩 벡터 (query는 디버깅 용도로 나중에 추가 가능)
    )
    docs = await cursor.to_list(length=None)
    if not docs:
        return None

    best_id = None
    best_score = 0.0
    for doc in docs:
        stored_emb = np.array(doc["query_embedding"], dtype=np.float32)
        score = float(np.dot(query_emb, stored_emb)) # 사용자쿼리 임베딩과 저장된 임베딩의 내적 계산 (코사인 유사도)
        if score > best_score:
            best_score = score
            best_id = doc["_id"]

    if best_score >= SIMILARITY_THRESHOLD:
        logger.info("[GapRecorder] 유사 gap 발견 (score=%.3f): %s", best_score, best_id)
        return best_id
    return None


async def _generate_suggestion(query: str, searched_topics: list[dict]) -> "str | None":
    """
    LLM이 gap에 대한 KB 보완 제안 생성.
    백그라운드에서 호출되므로 실패해도 예외 전파하지 않음.
    """
    try:
        from src.core.llm_config import LLM_LIGHT

        topic_context = ""
        if searched_topics:
            ids = [t.get("topic_id", "") for t in searched_topics[:3]]
            from src.core.mongo_client import boa_knowledge_collection
            cursor = boa_knowledge_collection.find(
                {"_id": {"$in": ids}},
                {"title": 1, "description": 1},
            )
            topics = await cursor.to_list(length=None)
            if topics:
                topic_context = "\n".join(
                    f"- {t.get('title', '')}: {t.get('description', '')}"
                    for t in topics
                )

        prompt = f"""사용자가 다음 질문을 했지만 지식베이스에서 답을 찾지 못했다.

질문: {query}

탐색한 관련 주제:
{topic_context or "없음"}

지식베이스를 어떻게 보완하면 이 질문에 답할 수 있을지 한 줄로 제안하라.
예: "'eSIM 법인 계정 VAT 처리' 주제 신설 권장" 또는 "'eSIM 취소 처리' 주제에 환불 절차 내용 추가 권장"
제안 한 줄만 반환하라."""

        response = await LLM_LIGHT.ainvoke(prompt)
        content = response.content
        if isinstance(content, list):
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return str(content).strip()
    except Exception as e:
        logger.warning("[GapRecorder] suggestion 생성 실패: %s", e)
        return None


async def _update_suggestion(gap_id: str, query: str, searched_topics: list[dict]) -> None:
    suggestion = await _generate_suggestion(query, searched_topics)
    if suggestion:
        await boa_knowledge_gaps_collection.update_one(
            {"_id": gap_id},
            {"$set": {"ai_suggestion": suggestion}},
        )
        logger.info("[GapRecorder] suggestion 업데이트: %s", gap_id)


# 핵심 함수: 사용자 질문과 탐색 결과를 바탕으로 gap 기록
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
    - 임베딩 유사도(≥SIMILARITY_THRESHOLD)로 기존 gap 병합 시도.
    - 임베딩 실패 시 exact match로 fallback.
    - 신규 gap이면 ai_suggestion을 백그라운드로 생성.

    searched_topics: [{"topic_id": ..., "score": ..., "read": bool}, ...] # 사용자가 탐색한 주제 목록과 점수, 실제로 읽었는지 여부. ai_suggestion 생성 시 참고용으로 활용. 
    ai_verdict: "no_match" | "insufficient_coverage" # 왜 답을 못찾았는지 LLM이 판단한 이유. 디버깅/분석 용도.
    ai_suggestion: str | None = None
    """
    now = datetime.now(timezone.utc)

    try:
        # 1. 사용자 질문 임베딩 생성
        query_emb = await _get_query_embedding(query)

        # 2. 기존에 gap해결 대기중인 질문과 중복된게 있는지 검사: 임베딩 유사도 우선, fallback은 exact match
        existing_id = None
        if query_emb is not None:
            existing_id = await _find_similar_gap(query_emb)

        if existing_id is None:
            existing = await boa_knowledge_gaps_collection.find_one(
                {"query": query, "status": {"$in": ["open", "in_progress"]}},
                {"_id": 1},
            )
            if existing:
                existing_id = existing["_id"]

        # 3. 기존 gap 갱신
        if existing_id:
            await boa_knowledge_gaps_collection.update_one(
                {"_id": existing_id},
                {
                    "$inc": {"duplicate_count": 1},
                    "$set": {"last_occurred_at": now},
                },
            )
            logger.info("[GapRecorder] 중복 gap 갱신: %s", existing_id)
            return existing_id

        # 4. 신규 gap insert
        gap_id = uuid.uuid4().hex
        await boa_knowledge_gaps_collection.insert_one({
            "_id":               gap_id,
            "query":             query,
            "query_embedding":   query_emb.tolist() if query_emb is not None else None,
            "session_id":        session_id,
            "user_member_id":    user_member_id,
            "searched_topics":   searched_topics,
            "ai_verdict":        ai_verdict,
            "ai_suggestion":     ai_suggestion,
            "duplicate_count":   1,
            "created_at":        now,
            "last_occurred_at":  now,
            "status":            "open",
            "resolved_topic_id": None,
            "assigned_to":       None,
        })
        logger.info("[GapRecorder] 신규 gap 생성: %s (query=%s)", gap_id, query[:40])

        # 5. ai_suggestion 백그라운드 생성 (caller가 넘기지 않은 경우에만)
        if ai_suggestion is None:
            asyncio.create_task(_update_suggestion(gap_id, query, searched_topics))

        return gap_id

    except Exception as exc:
        logger.error("[GapRecorder] 기록 실패: %s", exc)
        raise
