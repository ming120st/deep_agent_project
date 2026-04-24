# P2 Build Spec — TopicGraphRetriever

> **상위 설계**: `docs/kb_architecture.md` §3, §6, §8
> **Phase**: P2 (TopicGraphRetriever 구현 + execute_rag 교체)
> **선행 조건**: P1 완료 및 검증 (knowledge_v2_router + knowledge_relation_manager 동작 확인)
> **작업 완료의 의미**: 이 스펙의 §8 검증 시나리오가 전부 통과한다.

---

## 0. 작업 시작 전 필수 확인

1. `CLAUDE.md`를 읽었는가?
2. `docs/kb_architecture.md` §3(RAG 파이프라인), §6(하이브리드 정책), §8(인프라 변경)을 읽었는가?
3. `src/core/rag_engine.py`와 `src/workflow/main_graph/graph.py`의 `execute_rag` 함수를 `view`로 읽었는가?
4. P1이 완료되어 `boa_knowledge`에 `schema_version: 2`, `description`, `related_topic_ids` 필드가 있는 Topic이 존재하는가?

---

## 1. 범위 (Scope)

### 1.1 포함 (P2에서 하는 것)

- [ ] `src/core/rag_engine.py` 임베딩 대상 변경: 본문 → `description + tags`, 인덱스 키 `doc_id` → `_id`
- [ ] `src/core/topic_graph_retriever.py` 신규 작성 — §3 traverse 파이프라인
- [ ] `src/core/gap_recorder.py` 신규 작성 — gap 기록 + 중복 병합
- [ ] `src/workflow/main_graph/graph.py` `execute_rag` 함수 교체 — TopicGraphRetriever 호출로
- [ ] `src/main.py` lifespan에서 FAISS 인덱스 초기화 시 v2 기준으로 재구축

### 1.2 제외 (P2에서 하지 않는 것)

- **인입 파이프라인 금지** — `topic_ingest_pipeline.py`는 P4 대상.
- **대시보드 RAG 추적 뷰 금지** — RAGTrace 저장은 하되 대시보드 UI는 추후.
- **HyDE 재구현 금지** — 일단 제거. description 기반 직접 검색으로 충분한지 검증 후 결정.
- **session_logger RAGTrace 통합 금지** — 구조 잡아두되 실제 연동은 P2 이후.

---

## 2. 절대 건드리지 말 것 (Do-not-touch)

- `src/core/rag_engine.py`의 Azure Blob sync 로직, FAISS 인스턴스 동기화, `init_rag_engine()` 시그니처 — 내부 임베딩 대상만 바꾼다.
- `src/workflow/main_graph/graph.py`에서 `execute_rag` 함수 **이름과 시그니처** — LangGraph 노드로 등록되어 있으므로 이름 변경 금지. 함수 내부만 교체.
- `src/core/knowledge_relation_manager.py` — P1 산출물, 수정 금지.
- `src/dashboard/knowledge_v2_router.py` — P1 산출물, 수정 금지.

---

## 3. 신규/변경 파일 목록

| 경로 | 상태 | 내용 |
|------|------|------|
| `src/core/rag_engine.py` | 변경 | 임베딩 대상 교체 (§5.1) |
| `src/core/topic_graph_retriever.py` | **신규** | traverse 파이프라인 (§5.2) |
| `src/core/gap_recorder.py` | **신규** | gap 기록 + 중복 병합 (§5.3) |
| `src/workflow/main_graph/graph.py` | 변경 | `execute_rag` 내부 교체 (§5.4) |

---

## 4. 핵심 설계 결정

### 4.1 TopicGraphRetriever는 async 클래스

LangGraph 서브그래프가 아닌 독립 async 클래스로 구현한다.
`execute_rag`에서 `await retriever.retrieve(query)` 형태로 호출.

```python
class TopicGraphRetriever:
    async def retrieve(self, query: str, session_id: str | None = None) -> RetrieveResult:
        ...
```

```python
@dataclass
class RetrieveResult:
    context_xml: str            # <knowledge_base_context>...</knowledge_base_context>
    has_context: bool
    gap_flag: bool              # True면 gap_recorder 호출 필요
    visited_topic_ids: list[str]
    total_hops: int
    missing_aspects: list[str]  # sufficiency_judge에서 채움
```

### 4.2 rag_engine.py 임베딩 대상 교체

| 항목 | 기존 | 변경 |
|------|------|------|
| 임베딩 텍스트 | 본문 (`content`) | `description + " " + " ".join(tags)` |
| 인덱스 키 | `doc_id` | `_id` (Topic ID) |
| 필터 | 없음 | `schema_version: 2`인 문서만 색인 |

`search_knowledge()` 반환 형식 변경:
```python
# 기존
{"results": [{"doc_id": ..., "title": ..., "score": ..., "content_preview": ...}]}

# 변경
{"results": [{"topic_id": ..., "title": ..., "description": ..., "score": ...}]}
```

### 4.3 traverse 파라미터

환경변수로 조정 가능. 기본값:
- `MAX_DEPTH = 3`
- `MAX_TOPICS = 7`
- `MIN_CANDIDATE_SCORE = 0.3` — 이 이상 없으면 즉시 gap 플래그

---

## 5. 파일별 구현 명세

### 5.1 `src/core/rag_engine.py` 변경

**변경 위치만 명시. 나머지 건드리지 않는다.**

#### (A) 인덱스 구축 함수에서 임베딩 텍스트 교체

기존 코드에서 `content`를 임베딩하는 부분을 찾아 아래로 교체:

```python
# 변경 전 (본문 임베딩)
text_to_embed = doc.get("content", "")

# 변경 후 (description + tags 임베딩)
description = doc.get("description") or doc.get("title", "")
tags = doc.get("tags") or []
text_to_embed = description + (" " + " ".join(tags) if tags else "")
```

#### (B) MongoDB 쿼리에 schema_version 필터 추가

인덱스 구축 시 전체 `boa_knowledge`를 읽는 쿼리에 필터 추가:
```python
# 변경 전
cursor = boa_knowledge_collection.find({})

# 변경 후
cursor = boa_knowledge_collection.find({"schema_version": 2})
```

#### (C) 인덱스 키 교체

FAISS id ↔ doc 매핑에서 `doc_id` → `_id` 교체:
```python
# 변경 전
doc_id = doc["doc_id"]

# 변경 후
doc_id = doc["_id"]
```

#### (D) search_knowledge() 반환값 교체

```python
# 변경 전
results.append({"doc_id": ..., "title": ..., "score": ..., "content_preview": ...})

# 변경 후
results.append({"topic_id": ..., "title": ..., "description": doc.get("description", ""), "score": ...})
```

---

### 5.2 `src/core/topic_graph_retriever.py` (신규)

전체 구조:

```python
import os, logging
from dataclasses import dataclass, field
from typing import Literal

from src.core.mongo_client import boa_knowledge_collection
from src.core.rag_engine import search_knowledge
from src.core.llm_config import LLM_LIGHT

MAX_DEPTH  = int(os.getenv("RAG_MAX_DEPTH", "3"))
MAX_TOPICS = int(os.getenv("RAG_MAX_TOPICS", "7"))
MIN_SCORE  = float(os.getenv("RAG_MIN_SCORE", "0.3"))

logger = logging.getLogger(__name__)


@dataclass
class RetrieveResult:
    context_xml: str
    has_context: bool
    gap_flag: bool
    visited_topic_ids: list[str] = field(default_factory=list)
    total_hops: int = 0
    missing_aspects: list[str] = field(default_factory=list)


class TopicGraphRetriever:
    async def retrieve(self, query: str, session_id: str | None = None) -> RetrieveResult:
        ...
```

#### 내부 메서드 구현 명세

**(a) `_select_candidates(query) -> list[tuple[str, float]]`**

`search_knowledge(query, top_k=20)` 호출 → score ≥ MIN_SCORE인 것만 필터 → `(topic_id, score)` 리스트 반환.
결과가 비면 즉시 `gap_flag=True`로 빈 `RetrieveResult` 반환.

**(b) `_load_topic(topic_id) -> dict | None`**

`boa_knowledge_collection.find_one({"_id": topic_id})` — 없으면 `None` 반환, 로그만 남기고 스킵.

**(c) `_extract_snippet(query, topic) -> str | None`**

LLM 호출. 프롬프트:
```
주제: {topic["title"]}
본문:
---
{topic["content"]}
---
질문: {query}

위 본문에서 질문에 답하는 데 필요한 부분만 발췌하라.
관련 내용이 전혀 없으면 "없음"만 반환하라.
발췌 시 원문 그대로 인용하되 필요한 문장만 포함하라.
```
반환값이 "없음"이면 `None` 반환.

**(d) `_judge_sufficiency(query, snippets) -> dict`**

LLM 호출. 프롬프트:
```
질문: {query}

지금까지 수집된 지식:
---
{snippets_text}
---

위 내용만으로 질문에 충분히 답할 수 있는가?
JSON으로만 답하라:
{"verdict": "sufficient" | "continue" | "exhausted", "missing_aspects": ["..."]}
```
JSON 파싱. 파싱 실패 시 `{"verdict": "continue", "missing_aspects": []}` 반환.

**(e) `_select_next_hops(topic, missing_aspects, visited) -> list[tuple[str, float]]`**

`topic["related_topic_ids"]`에서 `visited`에 없는 것들의 `{title, description, tags}` 로드.
후보가 없으면 빈 리스트 반환.
후보가 있으면 LLM 호출:
```
부족한 정보: {missing_aspects}

아래 연관 주제 중 부족한 정보를 채울 수 있는 주제를 최대 2개 선택하라.
JSON으로만 답하라: [{"topic_id": "...", "priority": 0.0~1.0}, ...]

연관 주제 목록:
{candidates_json}
```
파싱 실패 시 빈 리스트 반환.

**(f) `_assemble_context(snippets) -> str`**

```python
def _assemble_context(self, snippets: list[dict]) -> str:
    # snippets: [{"topic_id": ..., "title": ..., "snippet": ...}, ...]
    parts = []
    for s in snippets:
        parts.append(
            f'<topic id="{s["topic_id"]}" title="{s["title"]}">\n{s["snippet"]}\n</topic>'
        )
    inner = "\n".join(parts)
    return f"<knowledge_base_context>\n{inner}\n</knowledge_base_context>"
```

#### traverse 루프 전체 흐름

```python
async def retrieve(self, query, session_id=None) -> RetrieveResult:
    # 1. 후보 선정
    candidate_queue = await self._select_candidates(query)
    if not candidate_queue:
        return RetrieveResult(context_xml="", has_context=False, gap_flag=True)

    visited: set[str] = set()
    collected_snippets: list[dict] = []
    depth = 0
    missing_aspects: list[str] = []

    while candidate_queue and depth < MAX_DEPTH and len(visited) < MAX_TOPICS:
        # 우선순위 높은 것 하나 꺼냄
        candidate_queue.sort(key=lambda x: x[1], reverse=True)
        topic_id, _ = candidate_queue.pop(0)

        if topic_id in visited:
            continue
        visited.add(topic_id)
        depth += 1

        # 본문 로드
        topic = await self._load_topic(topic_id)
        if not topic:
            continue

        # 스니펫 추출
        snippet = await self._extract_snippet(query, topic)
        if snippet:
            collected_snippets.append({
                "topic_id": topic_id,
                "title": topic.get("title", ""),
                "snippet": snippet,
                "domain": topic.get("domain"),
            })

        # 충분성 판단
        verdict_result = await self._judge_sufficiency(query, collected_snippets)
        verdict = verdict_result.get("verdict", "continue")
        missing_aspects = verdict_result.get("missing_aspects", [])

        if verdict == "sufficient":
            break

        # 다음 hop 선정
        if verdict == "continue":
            next_hops = await self._select_next_hops(topic, missing_aspects, visited)
            candidate_queue.extend(next_hops)

    gap_flag = (
        not collected_snippets or
        verdict_result.get("verdict") == "exhausted"
    )

    context_xml = self._assemble_context(collected_snippets) if collected_snippets else ""

    return RetrieveResult(
        context_xml=context_xml,
        has_context=bool(collected_snippets),
        gap_flag=gap_flag,
        visited_topic_ids=list(visited),
        total_hops=depth,
        missing_aspects=missing_aspects,
    )
```

**에러 처리 원칙**:
- 각 LLM 호출은 개별 try/except로 감싼다. LLM 호출 실패 시 해당 스텝을 skip하고 루프를 계속한다.
- 로깅은 `logger` 사용. `print()` 금지.

---

### 5.3 `src/core/gap_recorder.py` (신규)

```python
import uuid, logging
from datetime import datetime
from src.core.mongo_client import boa_knowledge_gaps_collection

logger = logging.getLogger(__name__)

async def record_gap(
    query: str,
    session_id: str | None,
    user_member_id: str | None,
    searched_topics: list[dict],   # [{"topic_id": ..., "score": ..., "read": bool}, ...]
    ai_verdict: str,               # "no_match" | "insufficient_coverage"
    ai_suggestion: str | None = None,
) -> str:
    """
    gap을 기록한다. 유사 gap(동일 쿼리 exact match 우선, 추후 임베딩 유사도)이
    있으면 duplicate_count + last_occurred_at만 갱신하고 기존 _id 반환.
    신규면 insert 후 새 _id 반환.
    """
```

**중복 병합 로직 (P2 단순 버전)**:
- `query` exact match로 기존 gap 조회
- 있으면 `$inc: {duplicate_count: 1}`, `$set: {last_occurred_at: now}` 후 기존 `_id` 반환
- 없으면 신규 insert (`status: "open"`, `duplicate_count: 1`)

임베딩 유사도 기반 병합은 P5에서 추가한다. P2에서는 exact match만.

---

### 5.4 `src/workflow/main_graph/graph.py` — `execute_rag` 교체

**함수 이름과 시그니처는 그대로. 내부만 교체.**

```python
async def execute_rag(state: GraphState):
    """
    RAG 노드: TopicGraphRetriever로 지식 탐색 → 하이브리드 정책 적용 → LLM 답변 생성
    """
    session_id = state.session_id
    await update_turn_meta(session_id, intent="rag")

    from src.core.topic_graph_retriever import TopicGraphRetriever
    from src.core.gap_recorder import record_gap
    from src.core.llm_config import LLM_LIGHT

    user_query = state.get_last_user_message() or ""

    # ── 1. TopicGraphRetriever 검색 ───────────────────────────────
    retriever = TopicGraphRetriever()
    try:
        result = await retriever.retrieve(query=user_query, session_id=session_id)

        await add_event(session_id, EventType.RAG_SEARCH, "지식베이스 탐색",
                        content=f"{result.total_hops}홉, {len(result.visited_topic_ids)}개 주제",
                        meta={
                            "visited": result.visited_topic_ids,
                            "has_context": result.has_context,
                            "gap_flag": result.gap_flag,
                        })
    except Exception as e:
        logger.error("[RAG] retriever 실패: %s", e)
        result = None

    # ── 2. gap 기록 ───────────────────────────────────────────────
    if result and result.gap_flag:
        try:
            await record_gap(
                query=user_query,
                session_id=session_id,
                user_member_id=state.member_id if hasattr(state, "member_id") else None,
                searched_topics=[{"topic_id": t, "read": True} for t in (result.visited_topic_ids or [])],
                ai_verdict="no_match" if not result.has_context else "insufficient_coverage",
            )
        except Exception as e:
            logger.error("[RAG] gap 기록 실패: %s", e)

    # ── 3. 하이브리드 정책 적용 시스템 프롬프트 결정 ──────────────
    # kb_architecture.md §6: visited topics 중 domain: internal이 있으면 엄격 모드
    has_internal = False
    if result and result.visited_topic_ids:
        from src.core.mongo_client import boa_knowledge_collection
        cursor = boa_knowledge_collection.find(
            {"_id": {"$in": result.visited_topic_ids}, "domain": "internal"},
            {"_id": 1}
        )
        async for _ in cursor:
            has_internal = True
            break

    if result and result.has_context:
        if has_internal:
            system_prompt = """당신은 와이드모바일의 내부 업무 지원 AI(BOA)입니다.
다음 질문은 회사 내부 정책/절차에 관한 것입니다.
반드시 제공된 <knowledge_base_context> 내의 정보에만 근거하여 답하십시오.
KB 컨텍스트에 근거가 없는 부분은 "해당 내용은 현재 지식베이스에 등록되어 있지 않습니다"로 답하고,
일반적 추론이나 유사 사례로 보완하지 마십시오.

{context}"""
        else:
            system_prompt = """당신은 와이드모바일의 내부 업무 지원 AI(BOA)입니다.
아래 지식베이스 내용을 참고하여 질문에 답변하세요.
문서에 없는 내용은 일반 지식을 활용해 보완할 수 있습니다.

{context}"""
        prompt = system_prompt.format(context=result.context_xml)
    else:
        prompt = """당신은 와이드모바일의 내부 업무 지원 AI(BOA)입니다.
지식베이스에서 관련 내용을 찾지 못했습니다.
일반적인 답변을 제공하거나, 정확한 정보를 위해 담당자에게 확인을 권유하세요."""

    # ── 4. LLM 답변 생성 ──────────────────────────────────────────
    from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

    lc_messages = [SystemMessage(content=prompt)]
    for m in state.messages[:-1]:
        if m["role"] == "user":
            lc_messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            lc_messages.append(AIMessage(content=m["content"]))
    lc_messages.append(HumanMessage(content=user_query))

    try:
        async with session_event(session_id, EventType.LLM_RESPONSE, "RAG 답변 생성") as ev:
            response = await LLM_LIGHT.ainvoke(lc_messages)
            answer = _extract_text(response.content)
            ev["meta"] = {"chars": len(answer)}
    except Exception as e:
        logger.error("[RAG] LLM 호출 실패: %s", e)
        answer = "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

    # ── 5. 응답 전송 ──────────────────────────────────────────────
    await send_response(state, answer)
    return state
```

`_extract_text()` 헬퍼가 `graph.py`에 없으면 아래를 파일 상단에 추가:
```python
def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)
```

---

## 6. 작업 순서

순서대로 진행. 각 step 완료 후 보고하고 다음으로.

1. **Step 1**: `src/core/rag_engine.py` 임베딩 대상 교체 (§5.1 A~D)
2. **Step 2**: `src/core/gap_recorder.py` 신규 작성 (§5.3)
3. **Step 3**: `src/core/topic_graph_retriever.py` 신규 작성 (§5.2)
4. **Step 4**: `src/workflow/main_graph/graph.py` `execute_rag` 교체 (§5.4)
5. **Step 5**: §8 검증 시나리오 실행 및 결과 보고

---

## 7. 환경 / 실행 고려사항

- **FAISS 인덱스 재구축**: rag_engine 교체 후 기존 인덱스(본문 기반)와 새 코드(description 기반)가 불일치한다. dev 환경에서 `init_rag_engine(force_rebuild=True)` 또는 Blob의 기존 인덱스를 삭제하고 재구축 필요. 방법은 `rag_engine.py`의 초기화 코드를 확인 후 판단.
- **dev DB**: `boa_db_dev` 환경에서 schema_version: 2 Topic이 최소 3개 이상 있어야 검증 가능. 없으면 먼저 `POST /api/knowledge/v2/topics`로 테스트 데이터 생성.
- **LLM 비용**: traverse 루프에서 topic당 최대 3회 LLM 호출 (snippet 추출, 충분성 판단, next hop 선정). MAX_TOPICS=7이면 최대 21회. dev에서는 MAX_TOPICS=3으로 낮춰 테스트.

---

## 8. 검증 시나리오

**dev 환경** (`boa_db_dev`)에서 수행. Topic 3개 이상 사전 생성 필요.

### 시나리오 A: 기본 검색 동작

1. `boa_db_dev`에 서로 연관된 Topic 3개 생성 (P1 API로):
   - Topic A: `title="eSIM 재발급 절차"`, `domain="internal"`, related: [B]
   - Topic B: `title="eSIM 주문 상태 조회"`, `domain="internal"`, related: [A, C]
   - Topic C: `title="고객 환불 정책"`, `domain="internal"`, related: [B]
2. Google Chat 또는 직접 `execute_rag` 호출로 "eSIM 재발급 어떻게 하나요?" 질의
3. 확인:
   - `session_logger`에 RAG_SEARCH 이벤트가 기록되는가
   - `visited_topic_ids`에 Topic A가 포함되는가
   - Google Chat 답변에 eSIM 재발급 관련 내용이 있는가

### 시나리오 B: 그래프 탐색 (multi-hop)

4. "eSIM 재발급 후 환불 받을 수 있나요?" 질의 — A, B, C를 거쳐야 답이 나오는 질문
5. 확인:
   - `total_hops` > 1인가
   - 2개 이상 Topic이 `visited_topic_ids`에 있는가

### 시나리오 C: gap 기록

6. KB에 없는 내용으로 질의: "법인 계정 VAT 처리 방법"
7. 확인:
   - `boa_knowledge_gaps` 컬렉션에 레코드가 생겼는가
   - `ai_verdict`가 `"no_match"` 또는 `"insufficient_coverage"`인가
8. 동일 질의 재전송
9. 확인:
   - 신규 gap 생성이 아닌 기존 gap의 `duplicate_count`가 2로 증가했는가

### 시나리오 D: 하이브리드 정책

10. `domain: "internal"` Topic이 검색된 질의 → 답변에 "해당 내용은 현재 지식베이스에 등록되어 있지 않습니다" 패턴이 작동하는지 확인 (KB에 없는 부분 질의 시)
11. `domain: "general"` Topic만 있는 질의 → 모른다고 하지 않고 일반 지식으로 보완하는지 확인

### 시나리오 E: 기존 동작 무영향

12. `action_classifier`가 `rag`로 분류하지 않는 일반 질의 → 기존 에이전트 라우팅 정상 동작 확인
13. `execute_rag` 이외의 노드(`execute_light_response` 등)는 변경 없이 정상 동작 확인

---

## 9. 완료 보고 형식

```
## P2 구현 완료 보고

### 변경 파일
- src/core/rag_engine.py (임베딩 대상 교체, 변경 줄 수)
- src/core/topic_graph_retriever.py (신규, XX줄)
- src/core/gap_recorder.py (신규, XX줄)
- src/workflow/main_graph/graph.py (execute_rag 교체, 변경 줄 수)

### 검증 결과
- 시나리오 A: ✅ / 상세
- 시나리오 B: ✅ / 상세
- 시나리오 C: ✅ / 상세
- 시나리오 D: ✅ / 상세
- 시나리오 E: ✅ / 상세

### 이슈/의사결정 사항
- (스펙과 다르게 처리한 부분 및 이유)

### 다음 단계 제안
- (P3/P4 진행 전 확인이 필요한 사항)
```