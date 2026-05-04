import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from src.core.kb_notifier import notify_conflict, notify_new_topic
from src.core.knowledge_relation_manager import set_relations
from src.core.llm_config import LLM_LIGHT
from src.core.mongo_client import (
    boa_knowledge_collection,
    boa_knowledge_conflicts_collection,
    boa_knowledge_ingest_log_collection,
    boa_knowledge_versions_collection,
)
from src.core.rag_engine import get_engine, search_knowledge
from src.core.domain_primer import DOMAIN_PRIMER

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


def _parse_first_json_block(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    patterns = [r"\[\s*\{.*\}\s*\]", r"\{.*\}"]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group())
        except Exception:
            continue
    raise ValueError("No JSON block found")


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


async def _scope_topics(content: str) -> list[dict]:
    prompt = f"""다음 [입력 내용]을 분석하여 몇 개의 '독립된 주제(Topic)'로 분할할지 판단하라.

[분할 및 병합 원칙]
1. 독립된 주제의 정의: 각각 완전히 다른 상황이나 맥락에서 참고되는 지식의 단위.
2. 병합 원칙 (중요): 특정 정책 및 기능에 수반되는 '예외 상황', '주의사항', '제약 조건' 등은 절대 별도의 주제로 쪼개지 말고, 메인 주제(하나의 문맥) 안에 포함시켜라.
   - 잘못된 예: 'eSIM 등록 방법' / 'eSIM 등록 시 사용기간 주의사항' (X) -> 쪼개면 안 됨
   - 올바른 예: '다중 eSIM 등록 방법 및 주의사항' (O) -> 하나로 병합
3. 적절한 포괄성: 너무 세부적인 조건으로 분할하지 마라.
   - 잘못된 예: '와이파이도시락 택배수령 시 취소 정책' (X)
   - 올바른 예: '와이파이도시락 취소 정책' (O) -> 세부 조건은 본문에 포함

[도메인 핵심 지식]
{DOMAIN_PRIMER}

[입력 내용]
---
{content}
---

도출된 각 주제별로 분할된 텍스트(scoped_content)와 임시 제목(tentative_title)을 작성하여 JSON 배열 형식으로만 응답하라.
응답 포맷:
[{{
    "scoped_content": "...",
    "tentative_title": "..."
}}]"""

    try:
        response = await LLM_LIGHT.ainvoke(prompt)
        print(f"[디버그] _scope_topics LLM 원본 응답:\n{response.content}\n")
        parsed = _parse_first_json_block(_extract_text(response.content))
        if isinstance(parsed, list) and parsed:
            normalized = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                scoped_content = str(item.get("scoped_content", "")).strip()
                tentative_title = str(item.get("tentative_title", "")).strip()
                if scoped_content:
                    normalized.append(
                        {
                            "scoped_content": scoped_content,
                            "tentative_title": tentative_title or "자동 분류 결과",
                        }
                    )
            if normalized:
                return normalized
    except Exception as exc:
        logger.warning("[TopicIngest] _scope_topics failed: %s", exc)

    return [{"scoped_content": content, "tentative_title": "자동 분류 실패"}]


async def _match_existing_topic(scoped_content: str) -> dict | None:
    result = await search_knowledge(scoped_content, top_k=10)
    top_results = result.get("results", [])
    if not top_results:
        return None

    top = top_results[0]
    score = float(top.get("score", 0.0))
    payload = {"topic_id": top.get("topic_id"), "confidence": score}
    if score >= 0.8:
        return payload
    if score < 0.5:
        return None
    payload["low_confidence"] = True
    return payload


async def _compare_content(existing_topic: dict, incoming_content: str) -> dict:
    prompt = f"""
    
당신은 지식베이스 관리자입니다. 기존에 등록된 주제와 새로 들어온 내용을 비교하여 다음 기준에 따라 관계를 판단하시오.

[도메인 핵심 지식]
{DOMAIN_PRIMER}

기존 주제: {existing_topic.get("title", "")}
기존 주제 설명: {existing_topic.get("description", "")}
기존 본문:
---
{existing_topic.get("content", "")}
---
새로 들어온 내용:
---
{incoming_content}
---

판정 기준:
- different_topic: 두 내용이 다루는 주제 자체가 다름. 공통 키워드가 있어도 맥락이 다르면 different_topic.
- identical: 표현이 달라도 의미적으로 동일한 정보. 기존 본문에 이미 포함된 내용.
- supplement: 기존 본문에 없는 새로운 정보가 추가됨. 주제는 같되 보완되는 내용.
- conflict: 기존 본문과 사실관계가 모순되거나 상충함.

confidence 기준:
- 1.0: 판정이 명확하고 근거가 확실함
- 0.7~0.9: 판정이 맞을 가능성이 높지만 약간 애매함
- 0.5~0.7: 판단이 어려운 경계 케이스

JSON으로만 응답하라:
{{"verdict": "different_topic"|"identical"|"supplement"|"conflict",
  "is_same_topic": true|false,
  "reasoning": "판정 근거를 한 줄로",
  "overlap_region": "겹치는 내용 요약 (없으면 null)",
  "conflict_region": "충돌 지점 (conflict일 때만)",
  "confidence": 0.0~1.0}}
"""

    try:
        response = await LLM_LIGHT.ainvoke(prompt)
        parsed = _parse_first_json_block(_extract_text(response.content))
        if isinstance(parsed, dict):
            return {
                "verdict": parsed.get("verdict", "conflict"),
                "overlap_region": parsed.get("overlap_region", ""),
                "conflict_region": parsed.get("conflict_region", ""),
                "confidence": float(parsed.get("confidence", 0.5) or 0.5),
            }
    except Exception as exc:
        logger.warning(
            "[TopicIngest] _compare_content failed topic=%s error=%s",
            existing_topic.get("_id"),
            exc,
        )

    return {
        "verdict": "conflict",
        "overlap_region": "",
        "conflict_region": "",
        "confidence": 0.5,
    }


async def _auto_merge(topic: dict, incoming_content: str, actor: str) -> None:
    now = _now()
    original_content = topic.get("content", "")
    new_content = (
        f"{original_content}\n\n---\n*자동 병합 (auto_ingest)*\n\n{incoming_content}"
    )

    await boa_knowledge_versions_collection.insert_one(
        {
            "_id": uuid.uuid4().hex,
            "topic_id": topic["_id"],
            "content": original_content,
            "snapshot_at": now,
            "snapshot_by": "auto_ingest",
        }
    )

    await boa_knowledge_collection.update_one(
        {"_id": topic["_id"]},
        {
            "$set": {
                "content": new_content,
                "updated_at": now,
                "updated_by": "auto_ingest",
            }
        },
    )

    try:
        refreshed = await boa_knowledge_collection.find_one({"_id": topic["_id"]})
        if refreshed:
            await get_engine().upsert_doc(
                doc_id=str(refreshed["_id"]),
                title=refreshed.get("title", ""),
                content=refreshed.get("content", ""),
                status=refreshed.get("status", ""),
                tags=refreshed.get("tags", []),
                description=refreshed.get("description", ""),
                domain=refreshed.get("domain"),
            )
    except Exception as exc:
        logger.warning("[TopicIngest] auto_merge index refresh skipped: %s", exc)


async def _register_conflict(
    topic: dict,
    incoming_content: str,
    compare_result: dict,
    source_type: str,
    source_ref: str | None,
    actor: str,
    match_confidence: float | None = None,
) -> str:
    conflict_id = uuid.uuid4().hex
    now = _now()
    doc = {
        "_id": conflict_id,
        "topic_id": topic["_id"],
        "detected_at": now,
        "source": {"type": source_type, "origin_ref": source_ref},
        "existing_excerpt": (topic.get("content", "") or "")[:500],
        "incoming_content": incoming_content,
        "conflict_type": (
            "conflict"
            if compare_result.get("verdict") == "conflict"
            else "supplement"
        ),
        "confidence": float(compare_result.get("confidence", 0.5) or 0.5),
        "match_confidence": float(match_confidence) if match_confidence is not None else None,
        "llm_reasoning": compare_result.get("conflict_region") or compare_result.get("overlap_region", ""),
        "status": "pending",
        "notified_at": None,
        "resolved_at": None,
        "resolved_by": None,
        "resolution_note": None,
        "created_by": actor,
    }
    await boa_knowledge_conflicts_collection.insert_one(doc)

    await notify_conflict(
        topic_id=str(topic["_id"]),
        topic_title=topic.get("title", ""),
        conflict_id=conflict_id,
        owner_member_id=topic.get("owner_member_id"),
        confidence=doc["confidence"],
    )
    await boa_knowledge_conflicts_collection.update_one(
        {"_id": conflict_id},
        {"$set": {"notified_at": _now()}},
    )
    return conflict_id


def _fallback_topic_payload(
    scoped_content: str,
    tentative_title: str,
    candidates: list[dict],
) -> dict:
    base_title = tentative_title.strip() or "자동 생성 주제"
    tags = re.findall(r"[A-Za-z0-9_-]{3,}", scoped_content.lower())[:4]
    tags = _dedupe_keep_order(tags)
    related_ids = _dedupe_keep_order(
        [candidate.get("topic_id") for candidate in candidates[:3] if candidate.get("topic_id")]
    )
    domain = "internal" if re.search(r"esim|wifi|환불|취소|정책|사내|cs", scoped_content, re.I) else "general"
    return {
        "title": base_title,
        "description": scoped_content.strip().replace("\n", " ")[:180] or base_title,
        "tags": tags[:6],
        "domain": domain,
        "related_topic_ids": related_ids[:3],
    }


async def _create_new_topic(scoped_content: str, tentative_title: str, actor: str) -> str:
    candidates_result = await search_knowledge(scoped_content, top_k=20)
    candidates = candidates_result.get("results", [])
    candidates_json = json.dumps(
        [
            {
                "topic_id": item.get("topic_id"),
                "title": item.get("title", ""),
                "description": item.get("description", ""),
            }
            for item in candidates
        ],
        ensure_ascii=False,
    )

    prompt = f"""다음 [입력 내용]을 바탕으로 지식베이스에 등록할 형태의 '새 주제(Topic)' 데이터를 생성하라.

[주제 생성 원칙]
1. 적절한 범위: 너무 포괄적이지도, 너무 세부적이지도 않은 메인 주제 단위로 묶어라.
   - 예: '와이파이도시락 택배수령 시 취소 정책' (X) -> '와이파이도시락 취소 정책' (O)
2. 병합 원칙 (중요): 특정 기능 설명과 그에 수반되는 주의사항/제약사항/예외조건 등은 절대 별도로 분리하지 말고, 본문(content) 안에 온전히 포함되도록 유지하라.

[출력 필드 작성 가이드]
- title: 명사구 형태의 구체적인 제목 (예: 다중 eSIM 등록 및 주의사항)
- description: 80~200자 분량. 이 지식을 '무엇을 다루고, 언제 참고해야 하는지' 명확히 요약. 주의사항이 포함된 경우 그 사실도 간략히 언급.
- tags: 검색에 용이한 관련 키워드 3~6개 추출
- domain: "internal" (문서 내용 근거가 필수인 사내/업무 지식) 또는 "general" (일반 지식 활용이 가능한 경우)
- related_topic_ids: 아래 [기존 주제 후보] 중 현재 주제와 관련 있거나 보완이 될 수 있는 주제의 topic_id를 0~3개 선택하여 배열로 작성 (전혀 없으면 빈 배열)

[도메인 핵심 지식]
{DOMAIN_PRIMER}

[기존 주제 후보 (top-20)]
{candidates_json}

[입력 내용]
---
{scoped_content}
---

오직 아래 JSON 포맷으로만 응답하라.
{{
    "title": "...",
    "description": "...",
    "tags": ["...", "..."],
    "domain": "internal",
    "related_topic_ids": ["...", "..."]
}}"""

    payload = None
    try:
        response = await LLM_LIGHT.ainvoke(prompt)
        parsed = _parse_first_json_block(_extract_text(response.content))
        if isinstance(parsed, dict):
            payload = parsed
    except Exception as exc:
        logger.warning("[TopicIngest] _create_new_topic LLM failed: %s", exc)

    if not isinstance(payload, dict):
        payload = _fallback_topic_payload(scoped_content, tentative_title, candidates)

    now = _now()
    topic_id = uuid.uuid4().hex
    related_ids = _dedupe_keep_order(
        [str(rel_id) for rel_id in payload.get("related_topic_ids", []) if rel_id]
    )

    doc = {
        "_id": topic_id,
        "title": str(payload.get("title", tentative_title or "자동 생성 주제")).strip() or "자동 생성 주제",
        "description": str(payload.get("description", "")).strip()[:400],
        "content": scoped_content,
        "tags": _dedupe_keep_order([str(tag).strip() for tag in payload.get("tags", []) if str(tag).strip()])[:6],
        "domain": payload.get("domain") if payload.get("domain") in {"internal", "general"} else None,
        "related_topic_ids": [],
        "schema_version": 2,
        "author_type": "auto",
        "status": "draft",
        "owner_member_id": actor if actor != "auto_ingest" else None,
        "created_at": now,
        "updated_at": now,
        "created_by": "auto_ingest",
        "updated_by": "auto_ingest",
    }
    await boa_knowledge_collection.insert_one(doc)

    if related_ids:
        valid_related = []
        for rel_id in related_ids:
            exists = await boa_knowledge_collection.find_one({"_id": rel_id}, {"_id": 1})
            if exists:
                valid_related.append(rel_id)
        if valid_related:
            await set_relations(topic_id, valid_related, actor="auto_ingest")

    try:
        await get_engine().upsert_doc(
            doc_id=topic_id,
            title=doc["title"],
            content=doc["content"],
            status=doc["status"],
            tags=doc["tags"],
            description=doc["description"],
            domain=doc["domain"],
        )
    except Exception as exc:
        logger.warning("[TopicIngest] index upsert skipped topic=%s error=%s", topic_id, exc)

    await notify_new_topic(
        topic_id=topic_id,
        topic_title=doc["title"],
        owner_member_id=doc["owner_member_id"],
    )
    return topic_id


def _summarize_decision(decisions: list[str]) -> str:
    if "flagged_conflict" in decisions:
        return "flagged_conflict"
    if "updated_existing" in decisions:
        return "updated_existing"
    if "created_new_topic" in decisions:
        return "created_new_topic"
    return "duplicate_skipped"




# 핵심 함수
async def run_ingest_pipeline(
    content: str,
    source_type: str,
    source_ref: str | None,
    actor: str,
) -> dict:
    """
    Run the P4 topic ingest pipeline.
    """
    print(f"\n=================== [디버그] 인입 파이프라인 시작 ===================")
    print(f"[디버그] 원본 통데이터({source_type}):\n{content}\n")
    scoped_items = await _scope_topics(content)
    
    print(f"[디버그] 주제 분리 완료 (총 {len(scoped_items)}개)")
    for i, item in enumerate(scoped_items, 1):
        print(f"  [{i}번째 주제] 가제목: {item.get('tentative_title')}")
        print(f"  [{i}번째 내용] 본문: {item.get('scoped_content')}\n")

    decisions: list[str] = []
    target_topic_ids: list[str] = []

    for item in scoped_items:
        scoped_content = str(item.get("scoped_content", "")).strip()
        tentative_title = str(item.get("tentative_title", "")).strip()
        if not scoped_content:
            continue

        match = await _match_existing_topic(scoped_content)
        print(f"[디버그] 매칭 결과: {match}")
        if not match:
            print(f"[디버그] 매칭 없음 -> 새 토픽 생성으로 넘어감")
            topic_id = await _create_new_topic(scoped_content, tentative_title, actor)
            decisions.append("created_new_topic")
            target_topic_ids.append(topic_id)
            continue

        topic = await boa_knowledge_collection.find_one({"_id": match["topic_id"]})
        if not topic:
            print(f"[디버그] DB에서 토픽 못 찾음 -> 새 토픽 생성으로 넘어감")
            topic_id = await _create_new_topic(scoped_content, tentative_title, actor)
            decisions.append("created_new_topic")
            target_topic_ids.append(topic_id)
            continue

        print(f"topic: {topic.get('title')}, scoped_content: {scoped_content}")
        compare_result = await _compare_content(topic, scoped_content)
        print(f"Comparison result for topic_id={topic['_id']}: {compare_result}")
        verdict = compare_result.get("verdict")
        compare_confidence = float(compare_result.get("confidence", 0.0) or 0.0)
        match_confidence = float(match.get("confidence", 0.0) or 0.0)

        # 1. 아예 다른 주제 → 새 토픽 생성 (FAISS가 잘못 매칭한 경우)
        if verdict == "different_topic":
            print(f"[디버그] LLM 판정: different_topic -> 새 토픽 생성")
            topic_id = await _create_new_topic(scoped_content, tentative_title, actor)
            decisions.append("created_new_topic")
            target_topic_ids.append(topic_id)
            continue

        # 2. 동일 내용 → 중복 스킵 (FAISS confidence 무관, LLM 판정 우선)
        if verdict == "identical":
            print(f"[디버그] LLM 판정: identical -> 중복 스킵 (match_conf={match_confidence:.3f}, llm_conf={compare_confidence:.3f})")
            decisions.append("duplicate_skipped")
            target_topic_ids.append(str(topic["_id"]))
            continue

        # 3. 보완 내용 → 자동 병합 (LLM 판정이 충분히 확실한 경우)
        if verdict == "supplement" and compare_confidence >= 0.7:
            print(f"[디버그] LLM 판정: supplement (llm_conf={compare_confidence:.3f}) -> 자동 병합")
            await _auto_merge(topic, scoped_content, actor)
            decisions.append("updated_existing")
            target_topic_ids.append(str(topic["_id"]))
            continue

        # 4. conflict이거나 supplement이지만 LLM 확신 낮음 → 충돌 등록
        print(f"[디버그] LLM 판정: {verdict} (llm_conf={compare_confidence:.3f}, match_conf={match_confidence:.3f}) -> 충돌 등록")
        await _register_conflict(
            topic=topic,
            incoming_content=scoped_content,
            compare_result=compare_result,
            source_type=source_type,
            source_ref=source_ref,
            actor=actor,
            match_confidence=match_confidence,
        )
        decisions.append("flagged_conflict")
        target_topic_ids.append(str(topic["_id"]))

    decision = _summarize_decision(decisions or ["duplicate_skipped"])
    unique_targets = _dedupe_keep_order(target_topic_ids)
    ingest_log_id = uuid.uuid4().hex

    await boa_knowledge_ingest_log_collection.insert_one(
        {
            "_id": ingest_log_id,
            "source_type": source_type,
            "source_ref": source_ref,
            "raw_content": content[:1000],
            "decision": decision,
            "target_topic_id": unique_targets[0] if unique_targets else None,
            "llm_cost_usd": 0.0,
            "created_at": _now(),
        }
    )

    return {
        "decision": decision,
        "target_topic_ids": unique_targets,
        "ingest_log_id": ingest_log_id,
    }