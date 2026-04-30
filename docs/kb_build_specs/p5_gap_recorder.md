# P5 Build Spec — Gap Recorder 고도화 + Gaps 대시보드

> **상위 설계**: `docs/kb_architecture.md` §3.2(5), §7.2
> **Phase**: P5
> **선행 조건**: P1~P4 완료
> **작업 완료의 의미**: 유사 쿼리가 같은 gap으로 병합되고, 대시보드 Gaps 탭에서 목록 확인 및 처리가 가능하다.

---

## 0. 작업 시작 전 필수 확인

1. `CLAUDE.md`를 읽었는가?
2. `src/core/gap_recorder.py`를 `view`로 읽었는가?
3. `src/dashboard/knowledge_v2_router.py`의 Gaps 탭 스텁 위치를 확인했는가?
4. `src/core/rag_engine.py`의 `get_engine().embed_query()` 시그니처를 확인했는가?

---

## 1. 범위 (Scope)

### 1.1 포함 (P5에서 하는 것)

- [ ] `src/core/gap_recorder.py` 고도화
  - gap 저장 시 `query_embedding` 생성 및 저장
  - exact match → 임베딩 유사도 기반 중복 병합으로 교체
  - `ai_suggestion` LLM 자동 생성
- [ ] `src/dashboard/knowledge_v2_router.py`
  - Gaps 목록/상태변경 API 엔드포인트 추가
  - KNOWLEDGE_HTML Gaps 탭 실제 구현

### 1.2 제외 (P5에서 하지 않는 것)

- **gap에서 바로 토픽 생성 자동화 금지** — 담당자가 대시보드에서 수동으로 하는 것으로 충분.
- **BQ 미러링 금지** — P7 대상.

---

## 2. 절대 건드리지 말 것 (Do-not-touch)

- `src/core/rag_engine.py` — 읽기만 한다.
- `src/workflow/main_graph/graph.py` — 건드리지 않는다.
- `src/dashboard/knowledge_v2_router.py`의 기존 API 엔드포인트 및 Topics/Conflicts 탭 HTML.

---

## 3. 파일 목록

| 경로 | 상태 | 내용 |
|------|------|------|
| `src/core/gap_recorder.py` | 변경 | 임베딩 기반 병합 + ai_suggestion 생성 |
| `src/dashboard/knowledge_v2_router.py` | 변경 | Gaps API + Gaps UI |

---

## 4. 핵심 설계 결정

### 4.1 임베딩 유사도 병합 임계값

`SIMILARITY_THRESHOLD = 0.92`

0.92 이상이면 같은 gap으로 병합한다. 이 값은 환경변수 `GAP_SIMILARITY_THRESHOLD`로 조정 가능.

### 4.2 임베딩 생성은 best-effort

임베딩 API 실패 시 예외 전파하지 않고 `query_embedding=None`으로 저장. 병합은 exact match로 fallback.

### 4.3 ai_suggestion은 비동기 best-effort

gap insert 후 `asyncio.create_task()`로 백그라운드 생성. 실패해도 gap 저장은 유지.

---

## 5. 파일별 구현 명세

### 5.1 `src/core/gap_recorder.py` 변경

#### 전체 구조

```python
import uuid, logging, asyncio, os
from datetime import datetime, timezone
import numpy as np
from src.core.mongo_client import boa_knowledge_gaps_collection

logger = logging.getLogger(__name__)
SIMILARITY_THRESHOLD = float(os.getenv("GAP_SIMILARITY_THRESHOLD", "0.92"))
```

#### `_get_query_embedding(query) -> np.ndarray | None`

```python
async def _get_query_embedding(query: str) -> "np.ndarray | None":
    try:
        from src.core.rag_engine import get_engine
        emb = await get_engine().embed_query(query)
        return emb[0]  # (DIM,) 1차원으로
    except Exception as e:
        logger.warning("[GapRecorder] 임베딩 생성 실패: %s", e)
        return None
```

#### `_find_similar_gap(query_emb) -> str | None`

```python
async def _find_similar_gap(query_emb: "np.ndarray") -> "str | None":
    """
    open/in_progress gap 중 query_embedding이 있는 것들과 코사인 유사도 비교.
    SIMILARITY_THRESHOLD 이상이면 해당 gap_id 반환.
    """
    cursor = boa_knowledge_gaps_collection.find(
        {
            "status": {"$in": ["open", "in_progress"]},
            "query_embedding": {"$ne": None},
        },
        {"_id": 1, "query_embedding": 1},
    )
    docs = await cursor.to_list(length=None)
    if not docs:
        return None

    # 코사인 유사도 계산 (이미 L2 정규화된 벡터라면 내적 = 코사인 유사도)
    best_id = None
    best_score = 0.0
    for doc in docs:
        stored_emb = np.array(doc["query_embedding"], dtype=np.float32)
        score = float(np.dot(query_emb, stored_emb))
        if score > best_score:
            best_score = score
            best_id = doc["_id"]

    if best_score >= SIMILARITY_THRESHOLD:
        logger.info("[GapRecorder] 유사 gap 발견 (score=%.3f): %s", best_score, best_id)
        return best_id
    return None
```

#### `_generate_suggestion(query, searched_topics) -> str | None`

```python
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
                {"title": 1, "description": 1}
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
```

#### `record_gap()` 전체 교체

```python
async def record_gap(
    query: str,
    session_id: str | None,
    user_member_id: str | None,
    searched_topics: list[dict],
    ai_verdict: str,
    ai_suggestion: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)

    try:
        # 1. 임베딩 생성
        query_emb = await _get_query_embedding(query)

        # 2. 중복 검사: 임베딩 유사도 우선, fallback은 exact match
        existing_id = None
        if query_emb is not None:
            existing_id = await _find_similar_gap(query_emb)

        if existing_id is None:
            # exact match fallback
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
            "_id":              gap_id,
            "query":            query,
            "query_embedding":  query_emb.tolist() if query_emb is not None else None,
            "session_id":       session_id,
            "user_member_id":   user_member_id,
            "searched_topics":  searched_topics,
            "ai_verdict":       ai_verdict,
            "ai_suggestion":    ai_suggestion,
            "duplicate_count":  1,
            "created_at":       now,
            "last_occurred_at": now,
            "status":           "open",
            "resolved_topic_id": None,
            "assigned_to":      None,
        })
        logger.info("[GapRecorder] 신규 gap 생성: %s (query=%s)", gap_id, query[:40])

        # 5. ai_suggestion 백그라운드 생성 (ai_suggestion이 없을 때만)
        if ai_suggestion is None:
            asyncio.create_task(_update_suggestion(gap_id, query, searched_topics))

        return gap_id

    except Exception as exc:
        logger.error("[GapRecorder] 기록 실패: %s", exc)
        raise


async def _update_suggestion(gap_id: str, query: str, searched_topics: list[dict]) -> None:
    suggestion = await _generate_suggestion(query, searched_topics)
    if suggestion:
        await boa_knowledge_gaps_collection.update_one(
            {"_id": gap_id},
            {"$set": {"ai_suggestion": suggestion}},
        )
        logger.info("[GapRecorder] suggestion 업데이트: %s", gap_id)
```

---

### 5.2 `src/dashboard/knowledge_v2_router.py` — Gaps API 추가

#### Gap 목록 조회

```python
@api_router.get("/gaps")
async def list_gaps(
    request: Request,
    status: Optional[str] = None,  # open | in_progress | resolved | wontfix
    limit: int = 50,
):
    _require_user(request)
    query: dict = {}
    if status:
        query["status"] = status

    cursor = boa_knowledge_gaps_collection.find(
        query,
        {"query_embedding": 0},  # 임베딩 벡터는 응답에서 제외
    ).sort("duplicate_count", -1).limit(limit)

    gaps = await cursor.to_list(length=None)
    for gap in gaps:
        gap["id"] = str(gap.pop("_id"))
        for field in ("created_at", "last_occurred_at"):
            if isinstance(gap.get(field), datetime):
                gap[field] = gap[field].isoformat()
    return gaps
```

#### Gap 상태 변경

```python
class GapStatusRequest(BaseModel):
    status: str               # in_progress | resolved | wontfix | open
    resolved_topic_id: Optional[str] = None  # resolved 시 어떤 토픽으로 해결했는지

@api_router.patch("/gaps/{gap_id}/status")
async def update_gap_status(request: Request, gap_id: str, body: GapStatusRequest):
    _require_user(request)
    update = {"status": body.status}
    if body.resolved_topic_id:
        update["resolved_topic_id"] = body.resolved_topic_id
    await boa_knowledge_gaps_collection.update_one(
        {"_id": gap_id}, {"$set": update}
    )
    return {"ok": True}
```

---

### 5.3 Gaps 탭 UI (KNOWLEDGE_HTML 교체)

P3에서 만든 빈 상태 스텁을 실제 UI로 교체.

#### 레이아웃

```
[open] [in_progress] [resolved] [wontfix]    (상태 필터 칩)

┌─ Gap 목록 ─────────────────────────────────────────────────┐
│ QUERY            │ 발생횟수 │ AI 제안          │ 마지막발생 │ 처리  │
│ 이심 삭제 방법   │    5     │ 'eSIM 삭제' 추가 │  04-27    │ [처리▼]│
└────────────────────────────────────────────────────────────┘
```

**행 클릭 시 상세 패널** (Conflicts 탭과 동일한 subpanel 패턴):

```
┌─ 질문 ───────────────────────────────────────────────────┐
│ 이심 다 썼는데 어떻게 지워?                               │
├─ AI 제안 ────────────────────────────────────────────────┤
│ 'eSIM 사용완료 후 삭제' 주제에 기기별 삭제 방법 추가 권장 │
├─ 탐색한 주제 ────────────────────────────────────────────┤
│ [eSIM 사용가능 기종] [eSIM 취소 처리 방법]               │
├──────────────────────────────────────────────────────────┤
│ [wontfix]  [처리중으로 변경]  [새 토픽 만들기]           │
└──────────────────────────────────────────────────────────┘
```

**[새 토픽 만들기]**: Topics 탭의 생성 모달을 열되, 제목 필드에 `ai_suggestion` 내용을 prefill.

**빈 상태**: open gap이 없으면 "현재 미해결 지식 갭이 없습니다." 표시.

#### JS 구현 가이드라인

- `fetchGaps()`: `GET /api/knowledge/v2/gaps?status={currentGapStatus}` 호출
- 상태 필터 칩: Conflicts 탭과 동일한 패턴
- `[새 토픽 만들기]`: `openCreateModal()`에 prefill 파라미터 추가

```javascript
function openCreateModalWithPrefill(title) {
  openCreateModal();
  document.getElementById('topicTitleInput').value = title || '';
}
```

---

## 6. 작업 순서

1. **Step 1**: `src/core/gap_recorder.py` 전체 교체 (§5.1)
2. **Step 2**: `knowledge_v2_router.py`에 Gaps API 추가 (§5.2)
3. **Step 3**: KNOWLEDGE_HTML Gaps 탭 구현 (§5.3)
4. **Step 4**: §7 검증 시나리오 실행 및 결과 보고

---

## 7. 검증 시나리오

### 시나리오 A: 유사 쿼리 병합

1. Google Chat에서 "이심 어떻게 지워요?" 질의 → gap 생성 확인
2. "이심 삭제 방법 알려줘" 질의 → 새 gap 생성이 아닌 기존 gap의 `duplicate_count`가 2로 증가했는가
3. "eSIM 삭제하고 싶어요" 질의 → 동일하게 병합되는가

### 시나리오 B: ai_suggestion 생성

4. 신규 gap 생성 직후 MongoDB에서 해당 gap 조회 → `ai_suggestion`이 null
5. 약 5초 후 재조회 → `ai_suggestion`에 LLM 제안이 채워졌는가

### 시나리오 C: Gaps 대시보드

6. `/knowledge` → Gaps 탭 → open gap 목록 표시
7. gap 행 클릭 → 상세 패널 (질문, AI 제안, 탐색 주제 표시)
8. [처리중으로 변경] → status가 `in_progress`로 변경, 필터 전환 시 해당 탭에 표시
9. [새 토픽 만들기] → 생성 모달 오픈, 제목에 ai_suggestion prefill

### 시나리오 D: 임베딩 없는 fallback

10. `GEMINI_API_KEY` 없는 환경에서 gap 기록 → 에러 없이 exact match로 fallback 동작

---

## 8. 완료 보고 형식

```
## P5 구현 완료 보고

### 변경 파일
- src/core/gap_recorder.py (임베딩 병합 + suggestion, XX줄)
- src/dashboard/knowledge_v2_router.py (Gaps API + UI)

### 검증 결과
- 시나리오 A: ✅ / 상세
- 시나리오 B: ✅ / 상세
- 시나리오 C: ✅ / 상세
- 시나리오 D: ✅ / 상세

### 이슈/의사결정 사항

### 다음 단계 제안
```