"""
KB v2 TopicGraphRetriever — §3 traverse 파이프라인.

MAX_DEPTH / MAX_TOPICS / MIN_SCORE 는 환경변수로 조정 가능.
"""
import os
import json
import logging
import re
from dataclasses import dataclass, field

from src.core.mongo_client import boa_knowledge_collection
from src.core.rag_engine import search_knowledge
from src.core.llm_config import LLM_LIGHT
from src.core.session_logger import session_event, EventType, build_llm_meta

MAX_DEPTH  = int(os.getenv("RAG_MAX_DEPTH",  "3"))
MAX_TOPICS = int(os.getenv("RAG_MAX_TOPICS", "7"))
MIN_SCORE  = float(os.getenv("RAG_MIN_SCORE", "0.3"))

logger = logging.getLogger(__name__)


@dataclass
class RetrieveResult:
    context_xml: str
    has_context: bool
    gap_flag: bool
    visited_topic_ids: list[str] = field(default_factory=list)
    visited_docs: list[dict] = field(default_factory=list)
    total_hops: int = 0
    missing_aspects: list[str] = field(default_factory=list)
    context_domains: list[str] = field(default_factory=list)


class TopicGraphRetriever:

    # ── 후보 선정 ──────────────────────────────────────────────────────────────

    async def _select_candidates(self, query: str) -> list[tuple[str, float]]:
        """FAISS top-20 후보 중 MIN_SCORE 이상만 (topic_id, score) 리스트로 반환."""
        try:
            result = await search_knowledge(query, top_k=20)
        except Exception as exc:
            logger.error("[TopicGraphRetriever] search_knowledge 실패: %s", exc)
            return []

        candidates = [
            (r["topic_id"], r["score"])
            for r in result.get("results", [])
            if r.get("score", 0.0) >= MIN_SCORE and r.get("topic_id")
        ]
        return candidates

    # ── 주제 본문 로드 ─────────────────────────────────────────────────────────

    async def _load_topic(self, topic_id: str) -> dict | None:
        try:
            doc = await boa_knowledge_collection.find_one({"_id": topic_id})
            if doc is None:
                logger.warning("[TopicGraphRetriever] topic_id 없음: %s", topic_id)
            return doc
        except Exception as exc:
            logger.error("[TopicGraphRetriever] _load_topic 실패 %s: %s", topic_id, exc)
            return None

    # ── 스니펫 추출 ────────────────────────────────────────────────────────────

    async def _extract_snippet(self, query: str, topic: dict, session_id: str | None = None) -> str | None:
        prompt = f"""주제: {topic.get("title", "")}
본문:
---
{topic.get("content", "")}
---
질문: {query}

위 본문에서 질문에 답하는 데 필요한 부분만 발췌하라.
관련 내용이 전혀 없으면 "없음"만 반환하라.
발췌 시 원문 그대로 인용하되 필요한 문장만 포함하라."""

        try:
            if session_id:
                async with session_event(session_id, EventType.LLM_EXTRACT, f"문서 발췌: {topic.get('title', '')}") as ev:
                    response = await LLM_LIGHT.ainvoke(prompt)
                    text = _extract_text(response.content).strip()
                    ev.content = text[:200]
                    ev.llm = build_llm_meta(LLM_LIGHT.model, getattr(response, "usage_metadata", {}))
                    return None if text == "없음" else text
            else:
                response = await LLM_LIGHT.ainvoke(prompt)
                text = _extract_text(response.content).strip()
                return None if text == "없음" else text
        except Exception as exc:
            logger.error("[TopicGraphRetriever] _extract_snippet 실패 %s: %s", topic.get("_id"), exc)
            return None

    # ── 충분성 판단 ────────────────────────────────────────────────────────────

    async def _judge_sufficiency(self, query: str, snippets: list[dict], session_id: str | None = None) -> dict:
        snippets_text = "\n\n".join(
            f"[{s['title']}]\n{s['snippet']}" for s in snippets
        ) or "(없음)"

        prompt = f"""질문: {query}

지금까지 수집된 지식:
---
{snippets_text}
---

위 내용만으로 질문에 충분히 답할 수 있는가?
JSON으로만 답하라:
{{"verdict": "sufficient" | "continue" | "exhausted", "missing_aspects": ["..."]}}"""

        try:
            if session_id:
                async with session_event(session_id, EventType.LLM_GENERIC, "답변 충분성 판단") as ev:
                    response = await LLM_LIGHT.ainvoke(prompt)
                    text = _extract_text(response.content).strip()
                    ev.content = text[:200]
                    ev.llm = build_llm_meta(LLM_LIGHT.model, getattr(response, "usage_metadata", {}))
                    match = re.search(r'\{.*\}', text, re.DOTALL)
                    if match:
                        return json.loads(match.group())
            else:
                response = await LLM_LIGHT.ainvoke(prompt)
                text = _extract_text(response.content).strip()
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group())
        except Exception as exc:
            logger.error("[TopicGraphRetriever] _judge_sufficiency 실패: %s", exc)

        return {"verdict": "continue", "missing_aspects": []}

    # ── 다음 hop 선정 ──────────────────────────────────────────────────────────

    async def _select_next_hops(
        self,
        topic: dict,
        missing_aspects: list[str],
        visited: set[str],
        session_id: str | None = None
    ) -> list[tuple[str, float]]:
        rel_ids = [
            rid for rid in (topic.get("related_topic_ids") or [])
            if rid not in visited
        ]
        if not rel_ids:
            return []

        cursor = boa_knowledge_collection.find(
            {"_id": {"$in": rel_ids}},
            {"_id": 1, "title": 1, "description": 1, "tags": 1},
        )
        candidates_docs = await cursor.to_list(length=None)
        if not candidates_docs:
            return []

        candidates_json = json.dumps(
            [
                {
                    "topic_id": str(d["_id"]),
                    "title": d.get("title", ""),
                    "description": d.get("description", ""),
                    "tags": d.get("tags", []),
                }
                for d in candidates_docs
            ],
            ensure_ascii=False,
        )

        prompt = f"""부족한 정보: {missing_aspects}

아래 연관 주제 중 부족한 정보를 채울 수 있는 주제를 최대 2개 선택하라.
JSON으로만 답하라: [{{"topic_id": "...", "priority": 0.0~1.0}}, ...]

연관 주제 목록:
{candidates_json}"""

        try:
            if session_id:
                async with session_event(session_id, EventType.LLM_GENERIC, f"연관 문서 탐색: {topic.get('title', '')}") as ev:
                    response = await LLM_LIGHT.ainvoke(prompt)
                    text = _extract_text(response.content).strip()
                    ev.content = text[:200]
                    ev.llm = build_llm_meta(LLM_LIGHT.model, getattr(response, "usage_metadata", {}))
                    match = re.search(r'\[.*\]', text, re.DOTALL)
                    if match:
                        parsed = json.loads(match.group())
                        return [(item["topic_id"], float(item.get("priority", 0.5))) for item in parsed]
            else:
                response = await LLM_LIGHT.ainvoke(prompt)
                text = _extract_text(response.content).strip()
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    parsed = json.loads(match.group())
                    return [(item["topic_id"], float(item.get("priority", 0.5))) for item in parsed]
        except Exception as exc:
            logger.error("[TopicGraphRetriever] _select_next_hops 실패: %s", exc)

        return []

    # ── 컨텍스트 조립 ──────────────────────────────────────────────────────────

    def _assemble_context(self, snippets: list[dict]) -> str:
        parts = [
            f'<topic id="{s["topic_id"]}" title="{s["title"]}">\n{s["snippet"]}\n</topic>'
            for s in snippets
        ]
        inner = "\n".join(parts)
        return f"<knowledge_base_context>\n{inner}\n</knowledge_base_context>"

    # ── 메인 retrieve ──────────────────────────────────────────────────────────

    async def retrieve(self, query: str, session_id: str | None = None) -> RetrieveResult:
        print(f"\n[{'='*50}]")
        print(f"[RAG Debug] 0. 사용자 쿼리 수신: '{query}'")
        
        # 1. 후보 선정
        candidate_queue = await self._select_candidates(query)
        if not candidate_queue:
            print("[RAG Debug] 1. MIN_SCORE 이상 후보 없음 → 지식 갭(Gap) 처리")
            logger.info("[TopicGraphRetriever] MIN_SCORE 이상 후보 없음 → gap")
            return RetrieveResult(context_xml="", has_context=False, gap_flag=True)

        candidate_ids = [q[0] for q in candidate_queue]
        cursor = boa_knowledge_collection.find({"_id": {"$in": candidate_ids}}, {"title": 1})
        title_map = {doc["_id"]: doc.get("title", "제목없음") for doc in await cursor.to_list(length=None)}
        
        printed_candidates = [(title_map.get(q[0], q[0]), round(q[1], 4)) for q in candidate_queue]
        print(f"[RAG Debug] 1. 초기 벡터 검색된 후보 문서 & 점수: {printed_candidates}")

        visited: set[str] = set()
        visited_docs: list[dict] = []
        collected_snippets: list[dict] = []
        depth = 0
        missing_aspects: list[str] = []
        verdict_result: dict = {"verdict": "continue", "missing_aspects": []}

        while candidate_queue and depth < MAX_DEPTH and len(visited) < MAX_TOPICS:
            # 우선순위 높은 것 꺼냄
            candidate_queue.sort(key=lambda x: x[1], reverse=True)
            topic_id, score = candidate_queue.pop(0)

            if topic_id in visited:
                continue
            visited.add(topic_id)
            depth += 1

            # 본문 로드
            topic = await self._load_topic(topic_id)
            if not topic:
                continue
                
            topic_title = topic.get("title", "제목 없음")
            visited_docs.append({"topic_id": topic_id, "title": topic_title, "score": score})
            print(f"\n[RAG Debug] 2. >> 현재 검토 중인 문서: '{topic_title}' (탐색 깊이: {depth})")

            # 스니펫 추출
            try:
                snippet = await self._extract_snippet(query, topic, session_id=session_id)
            except Exception as exc:
                logger.error("[TopicGraphRetriever] snippet 추출 예외 %s: %s", topic_id, exc)
                snippet = None

            if snippet:
                print(f"[RAG Debug] 3. 추출된 필요 내용(요약):\n    - {snippet[:150]}...")
                collected_snippets.append({
                    "topic_id": topic_id,
                    "title":    topic_title,
                    "snippet":  snippet,
                    "domain":   topic.get("domain"),
                })
            else:
                print(f"[RAG Debug] 3. 추출된 필요 내용 없음 (관련 내용이 본문에 없습니다.)")

            # 충분성 판단
            try:
                verdict_result = await self._judge_sufficiency(query, collected_snippets, session_id=session_id)
            except Exception as exc:
                logger.error("[TopicGraphRetriever] sufficiency 판단 예외: %s", exc)
                verdict_result = {"verdict": "continue", "missing_aspects": []}

            verdict = verdict_result.get("verdict", "continue")
            missing_aspects = verdict_result.get("missing_aspects", [])
            print(f"[RAG Debug] 4. 충분성 판단 결과: '{verdict}'")
            if missing_aspects:
                print(f"               부족한 정보: {missing_aspects}")

            if verdict == "sufficient":
                print("[RAG Debug] 5. 충분하다고 판단되어 탐색을 종료합니다.")
                break

            # 다음 hop 선정
            if verdict == "continue":
                try:
                    next_hops = await self._select_next_hops(topic, missing_aspects, visited, session_id=session_id)
                    if next_hops:
                        hop_ids = [h[0] for h in next_hops]
                        cursor = boa_knowledge_collection.find({"_id": {"$in": hop_ids}}, {"title": 1})
                        hop_title_map = {doc["_id"]: doc.get("title", "제목없음") for doc in await cursor.to_list(length=None)}
                        printed_hops = [(hop_title_map.get(h[0], h[0]), h[1]) for h in next_hops]
                        print(f"[RAG Debug] 5. 부족한 정보를 찾기 위해 연관 문서(Hop)들을 큐에 추가합니다: {printed_hops}")
                    else:
                        print("[RAG Debug] 5. 더 이상 연결된 연관 문서가 없습니다.")
                    candidate_queue.extend(next_hops)
                except Exception as exc:
                    logger.error("[TopicGraphRetriever] next_hops 선정 예외: %s", exc)

        gap_flag = (
            not collected_snippets
            or verdict_result.get("verdict") == "exhausted"
        )
        
        if verdict_result.get("verdict") == "exhausted":
            print("[RAG Debug] 5. 관련 문서를 더 찾을 수 없습니다 (Exhausted).")

        context_xml = self._assemble_context(collected_snippets) if collected_snippets else ""
        context_domains = list({s["domain"] for s in collected_snippets if s.get("domain")})
        
        print("\n[RAG Debug] 6. === RAG 탐색 최종 결과 ===")
        print(f"    - 총 탐색 문서 개수: {depth} 개")
        if collected_snippets:
            print("    - 최종 취합된 스니펫(사용할 지식 목록):")
            for idx, s in enumerate(collected_snippets, 1):
                print(f"      {idx}. [{s['title']}] {s['snippet'][:100]}...")
        else:
            print("    - 최종 수집된 스니펫이 없습니다.")
        print(f"[{'='*50}]\n")

        return RetrieveResult(
            context_xml=context_xml,
            has_context=bool(collected_snippets),
            gap_flag=gap_flag,
            visited_topic_ids=list(visited),
            visited_docs=visited_docs,
            total_hops=depth,
            missing_aspects=missing_aspects,
            context_domains=context_domains,
        )


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)
