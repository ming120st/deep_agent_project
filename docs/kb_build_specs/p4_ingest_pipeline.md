# P4 Build Spec — 인입 파이프라인 + Conflicts 대시보드

> **상위 설계**: `docs/kb_architecture.md` §4, §5
> **Phase**: P4 (자동 인입 파이프라인 + 충돌 관리 UI)
> **선행 조건**: P1, P2, P3 완료
> **작업 완료의 의미**: 파일 업로드 시 Topic이 자동 생성/병합/충돌 등록되고, 대시보드 Conflicts 탭에서 처리할 수 있다.

---

## 0. 작업 시작 전 필수 확인

1. `CLAUDE.md`를 읽었는가?
2. `docs/kb_architecture.md` §4(인입 파이프라인), §5(충돌 감지 & 해소)를 읽었는가?
3. `src/core/google_chat.py`의 `send_dm()` 시그니처를 확인했는가?
4. `src/core/mongo_client.py`에서 `boa_members_collection`, `boa_knowledge_conflicts_collection`, `boa_knowledge_ingest_log_collection`이 선언되어 있는지 확인했는가?
5. `src/dashboard/knowledge_v2_router.py`의 현재 KNOWLEDGE_HTML에서 Conflicts 탭 스텁 위치를 확인했는가?

---

## 1. 범위 (Scope)

### 1.1 포함 (P4에서 하는 것)

- [ ] `src/core/topic_ingest_pipeline.py` 신규 작성 — A~E 노드 파이프라인
- [ ] `src/core/kb_notifier.py` 신규 작성 — 담당자 Google Chat DM 알림
- [ ] `src/dashboard/knowledge_v2_router.py` — 파일 업로드 인입 엔드포인트 추가
- [ ] `src/dashboard/knowledge_v2_router.py` — Conflicts 탭 API 엔드포인트 추가
- [ ] `src/dashboard/knowledge_v2_router.py` — KNOWLEDGE_HTML Conflicts 탭 실제 구현

### 1.2 제외 (P4에서 하지 않는 것)

- **대화 추출 트리거 금지** — BOA 대화에서 자동 추출하는 파이프라인은 추후.
- **예약 크롤 금지** — Notion/위키 연동은 추후.
- **Gaps 탭 실제 구현 금지** — P5 대상.
- **Ingest Log 탭 금지** — P7 대상.
- **BQ 미러링 금지** — P7 대상.
- **장기 pending 리마인더 스케줄 금지** — 아키텍처 §5.3의 7일/30일 리마인더는 추후.

---

## 2. 절대 건드리지 말 것 (Do-not-touch)

- `src/core/topic_graph_retriever.py` — P2 산출물.
- `src/core/gap_recorder.py` — P2 산출물.
- `src/core/rag_engine.py` — P2에서 수정됨, 건드리지 않음.
- `src/workflow/main_graph/graph.py` — 건드리지 않음.
- `src/dashboard/knowledge_v2_router.py`의 기존 API 엔드포인트 및 Topics 탭 HTML.

---

## 3. 파일 목록

| 경로 | 상태 | 내용 |
|------|------|------|
| `src/core/topic_ingest_pipeline.py` | **신규** | A~E 인입 파이프라인 |
| `src/core/kb_notifier.py` | **신규** | Google Chat DM 알림 |
| `src/dashboard/knowledge_v2_router.py` | 변경 | 업로드 엔드포인트 + Conflicts API + Conflicts UI |

---

## 4. 핵심 설계 결정

### 4.1 owner_member_id는 email

`boa_knowledge.owner_member_id` 필드는 `boa_members.email`과 동일한 값으로 저장한다.
알림 발송 시 `boa_members_collection.find_one({"email": owner_member_id})`로 `space_name` 조회.
`owner_member_id`가 없거나 `space_name`을 못 찾으면 알림 스킵 + 로그만 남긴다.

### 4.2 파이프라인은 동기 async 함수 체인

LangGraph 그래프 아님. `run_ingest_pipeline(content, source_type, source_ref)` 단일 진입점을 async 함수로 구현. 내부에서 A→E 함수를 순차 호출.

### 4.3 파일 업로드는 텍스트 추출 후 파이프라인 호출

업로드 엔드포인트에서 파일 텍스트 추출 → `run_ingest_pipeline()` 호출. 파이프라인 자체는 텍스트만 받으므로 파일 형식과 분리됨.

---

## 5. 파일별 구현 명세

### 5.1 `src/core/kb_notifier.py` (신규)

```python
import logging
from src.core.mongo_client import boa_members_collection
from src.core.google_chat import send_dm

logger = logging.getLogger(__name__)

DASHBOARD_BASE_URL = "https://boa.widemobile.com"  # 환경변수 BOA_DASHBOARD_URL로 오버라이드 가능

async def notify_conflict(topic_id: str, topic_title: str, conflict_id: str,
                           owner_member_id: str | None, confidence: float) -> None:
    """
    충돌 감지 시 담당자에게 Google Chat DM 발송.
    링크만 전달. 내용은 싣지 않는다.
    owner_member_id가 없거나 space 못 찾으면 스킵.
    """

async def notify_new_topic(topic_id: str, topic_title: str,
                            owner_member_id: str | None) -> None:
    """
    신규 Topic 자동 생성 시 담당자에게 알림 (선택적).
    kb_architecture.md §4.4: 기본 일 1회 배치 — P4에서는 즉시 발송으로 단순 구현,
    배치 전환은 추후.
    """
```

**알림 메시지 포맷** (`notify_conflict`):

```
[BOA] 지식베이스 확인 요청

주제: {topic_title}
사유: 기존 내용과 다른 설명이 자동 인입됨
자동 판단 신뢰도: {confidence:.0%}

대시보드에서 확인 → {DASHBOARD_BASE_URL}/knowledge#conflicts
```

**구현 가이드라인**:
- `boa_members_collection.find_one({"email": owner_member_id}, {"space_name": 1})`로 space 조회
- `send_dm(space_name, message)` 호출
- 실패해도 예외 전파하지 않음 — `logger.warning`만.

---

### 5.2 `src/core/topic_ingest_pipeline.py` (신규)

#### 진입점

```python
async def run_ingest_pipeline(
    content: str,
    source_type: str,       # "document_upload" | "conversation" | "manual"
    source_ref: str | None, # 파일명, session_id 등
    actor: str,             # 실행 주체 이메일 또는 "auto_ingest"
) -> dict:
    """
    반환: {
        "decision": "created_new_topic" | "updated_existing" |
                    "flagged_conflict" | "duplicate_skipped",
        "target_topic_ids": list[str],
        "ingest_log_id": str,
    }
    """
```

#### (A) `_scope_topics(content) -> list[dict]`

LLM 호출:
```
다음 내용이 몇 개의 독립된 주제에 해당하는지 판단하라.
독립된 주제 = 각각 다른 상황/맥락에서 참조될 수 있는 단위.
JSON으로만 답하라:
[{"scoped_content": "...", "tentative_title": "..."}, ...]

내용:
---
{content}
---
```
파싱 실패 시 `[{"scoped_content": content, "tentative_title": "자동 분류 실패"}]` 반환 (파이프라인 계속).

#### (B) `_match_existing_topic(scoped_content) -> dict | None`

`search_knowledge(scoped_content, top_k=10)` 호출 → top 1 결과.
score ≥ 0.8이면 `{"topic_id": ..., "confidence": score}` 반환.
score < 0.5이면 `None` 반환 (no match).
0.5 ≤ score < 0.8이면 `{"topic_id": ..., "confidence": score, "low_confidence": True}` 반환.

#### (C) `_compare_content(existing_topic, incoming_content) -> dict`

LLM 호출:
```
기존 주제 본문:
---
{existing_topic["content"]}
---
새로 들어온 내용:
---
{incoming_content}
---
판정하라: identical / supplement / conflict
conflict라면 충돌 지점을 명시하라.
JSON으로만 답하라:
{"verdict": "identical"|"supplement"|"conflict",
 "overlap_region": "...",
 "conflict_region": "...",
 "confidence": 0.0~1.0}
```
파싱 실패 시 `{"verdict": "conflict", "confidence": 0.5}` 반환 (안전하게 사람 검토로).

**Threshold 적용**:
- `identical` && conf > 0.85 → `"duplicate_skipped"`
- `supplement` && conf > 0.8 → auto_merge (`_auto_merge` 호출)
- `supplement` && conf ≤ 0.8 → `_register_conflict` 호출
- `conflict` → `_register_conflict` 호출

#### (D) `_register_conflict(topic, incoming_content, compare_result, source_type, source_ref, actor) -> str`

`boa_knowledge_conflicts`에 레코드 생성 → `kb_notifier.notify_conflict()` 호출 → conflict_id 반환.

```python
doc = {
    "_id": uuid4().hex,
    "topic_id": topic["_id"],
    "detected_at": datetime.utcnow(),
    "source": {"type": source_type, "origin_ref": source_ref},
    "existing_excerpt": topic.get("content", "")[:500],  # 앞 500자
    "incoming_content": incoming_content,
    "conflict_type": "conflict" if compare_result["verdict"] == "conflict" else "supplement",
    "confidence": compare_result.get("confidence", 0.5),
    "llm_reasoning": compare_result.get("conflict_region", ""),
    "status": "pending",
    "notified_at": None,
    "resolved_at": None,
    "resolved_by": None,
    "resolution_note": None,
}
```

알림 후 `notified_at` 갱신.

#### (E) `_create_new_topic(scoped_content, tentative_title, actor) -> str`

LLM 호출:
```
다음 내용으로 새 주제를 만들어라.
- 제목: 명사구, 구체적
- 설명: 80~200자. 이 주제가 무엇을 다루는지 + 언제 참조되는지
- 태그: 3~6개
- domain: "internal" 또는 "general"
- 연관 주제: 아래 후보 중 관련될 만한 것 0~3개의 _id만 선택

기존 주제 후보 (top-20):
{candidates_json}

내용:
---
{scoped_content}
---

JSON으로만 답하라:
{"title": "...", "description": "...", "tags": [...],
 "domain": "internal"|"general", "related_topic_ids": [...]}
```

후보 조회: `search_knowledge(scoped_content, top_k=20)` 결과의 title+description만 추출.

생성 후:
- `boa_knowledge`에 insert (`schema_version: 2`, `status: "draft"`, `author_type: "auto"`)
- `set_relations()` 호출로 양방향 링크
- FAISS 재색인: `rag_engine`의 인덱스 갱신 함수 호출 (있으면). 없으면 스킵 + 로그.
- `kb_notifier.notify_new_topic()` 호출

#### `_auto_merge(topic, incoming_content, actor)`

```python
new_content = topic["content"] + "\n\n---\n*자동 병합 (auto_ingest)*\n\n" + incoming_content
await boa_knowledge_collection.update_one(
    {"_id": topic["_id"]},
    {"$set": {"content": new_content, "updated_at": now, "updated_by": "auto_ingest"}}
)
# boa_knowledge_versions에 스냅샷 저장
```

`boa_knowledge_versions` 스냅샷:
```python
{
    "_id": uuid4().hex,
    "topic_id": topic["_id"],
    "content": topic["content"],  # 병합 전 본문
    "snapshot_at": now,
    "snapshot_by": "auto_ingest",
}
```

#### ingest_log 기록

파이프라인 종료 시 항상 기록:
```python
{
    "_id": uuid4().hex,
    "source_type": source_type,
    "source_ref": source_ref,
    "raw_content": content[:1000],  # 앞 1000자만
    "decision": decision,
    "target_topic_id": target_topic_id,  # 해당 없으면 None
    "llm_cost_usd": 0.0,  # P4에서는 0으로. 추후 실제 비용 계산.
    "created_at": datetime.utcnow(),
}
```

---

### 5.3 `src/dashboard/knowledge_v2_router.py` 추가 엔드포인트

#### 파일 업로드 인입

```python
@router.post("/api/knowledge/v2/ingest/upload", status_code=202)
async def ingest_upload(request: Request, file: UploadFile):
    """
    텍스트 파일(.txt, .md) 또는 기타 업로드 → 파이프라인 실행.
    즉시 반환(202 Accepted), 파이프라인은 백그라운드 실행.
    """
```

**지원 파일 형식 (P4)**:
- `.txt`, `.md`: 그대로 텍스트로 읽음
- 그 외: 400 에러 ("지원하지 않는 파일 형식입니다. .txt, .md만 지원합니다.")

**백그라운드 실행**:
```python
import asyncio
asyncio.create_task(
    run_ingest_pipeline(
        content=text,
        source_type="document_upload",
        source_ref=file.filename,
        actor=user["email"],
    )
)
return {"message": "인입 파이프라인이 시작되었습니다.", "filename": file.filename}
```

#### Conflicts 목록 조회

```python
@router.get("/api/knowledge/v2/conflicts")
async def list_conflicts(
    request: Request,
    status: Optional[str] = None,  # pending | merged | rejected | manual_edited | stale
):
```

`boa_knowledge_conflicts_collection.find(query).sort("detected_at", DESCENDING)` 반환.
각 conflict 문서에 `topic_title` 필드 추가 (boa_knowledge에서 join):
```python
topic = await boa_knowledge_collection.find_one({"_id": c["topic_id"]}, {"title": 1})
c["topic_title"] = topic["title"] if topic else "(삭제된 주제)"
```

#### Conflict 해소

```python
@router.patch("/api/knowledge/v2/conflicts/{conflict_id}/resolve")
async def resolve_conflict(request: Request, conflict_id: str, body: ConflictResolveRequest):
    """
    body: {"action": "merge" | "reject" | "manual_edit", "resolution_note": "...", "manual_content": "..."}
    """
```

**액션별 동작**:

| action | 동작 |
|--------|------|
| `merge` | `incoming_content`를 본문에 append (`_auto_merge` 재사용) → status: `merged` |
| `reject` | 본문 변경 없음 → status: `rejected` |
| `manual_edit` | `body.manual_content`로 본문 교체 → versions 스냅샷 → status: `manual_edited` |

모든 해소: `resolved_at`, `resolved_by`, `resolution_note` 기록.

```python
class ConflictResolveRequest(BaseModel):
    action: str  # "merge" | "reject" | "manual_edit"
    resolution_note: Optional[str] = None
    manual_content: Optional[str] = None  # manual_edit 시만
```

---

### 5.4 Conflicts 탭 UI (KNOWLEDGE_HTML 교체)

P3에서 만든 빈 상태 스텁을 실제 UI로 교체.

**레이아웃**:

```
[pending 필터] [merged] [rejected] [manual_edited] [stale]   (탭 형태)

┌─ 충돌 목록 ────────────────────────────────────────────────┐
│ TOPIC          │ 충돌유형   │ 신뢰도 │ 감지일시  │ 액션    │
│ eSIM 취소처리  │ conflict  │  82%  │ 04-23    │ [처리▼] │
└────────────────────────────────────────────────────────────┘
```

**행 클릭 시 상세 패널** (모달 또는 하단 확장):

```
┌─ 기존 본문 ──────────────────────────────────────────────┐
│ (existing_excerpt)                                       │
├─ 새로 들어온 내용 ───────────────────────────────────────┤
│ (incoming_content)                                       │
├─ AI 판단 ────────────────────────────────────────────────┤
│ 충돌유형: conflict  신뢰도: 82%                           │
│ (llm_reasoning)                                         │
├──────────────────────────────────────────────────────────┤
│ [자동 병합 반영]  [기각 — 기존 유지]  [직접 수정 후 저장] │
└──────────────────────────────────────────────────────────┘
```

**[직접 수정 후 저장]**: 본문 textarea 편집창 오픈 → 수정 → 저장 시 `action: "manual_edit"` 호출.

**빈 상태**: pending 충돌이 없으면 "현재 처리 대기 중인 충돌이 없습니다." 표시.

**파일 업로드 버튼**: Topics 탭 우상단에 `[📄 파일로 인입]` 버튼 추가. 클릭 시 파일 선택 → `POST /api/knowledge/v2/ingest/upload` → 완료 메시지 토스트.

---

## 6. 작업 순서

1. **Step 1**: `src/core/kb_notifier.py` 작성 (§5.1)
2. **Step 2**: `src/core/topic_ingest_pipeline.py` 작성 (§5.2)
3. **Step 3**: `knowledge_v2_router.py`에 업로드 엔드포인트 + Conflicts API 추가 (§5.3)
4. **Step 4**: KNOWLEDGE_HTML Conflicts 탭 + 파일 업로드 버튼 구현 (§5.4)
5. **Step 5**: §7 검증 시나리오 실행 및 결과 보고

---

## 7. 검증 시나리오

**dev 환경**에서 수행.

### 시나리오 A: 신규 Topic 자동 생성

1. `schema_version: 2` Topic이 없는 내용의 `.txt` 파일 업로드
2. `boa_knowledge_ingest_log` 확인 → `decision: "created_new_topic"` 레코드 존재
3. `boa_knowledge` 확인 → 새 Topic 생성, `status: "draft"`, `author_type: "auto"`

### 시나리오 B: 중복 스킵

4. 방금 생성된 Topic의 내용과 동일한 파일 재업로드
5. `boa_knowledge_ingest_log` → `decision: "duplicate_skipped"`
6. Topic 추가 생성 없음

### 시나리오 C: 충돌 등록

7. 기존 Topic 내용과 모순되는 내용의 파일 업로드
8. `boa_knowledge_conflicts` → `status: "pending"` 레코드 생성
9. `/knowledge` Conflicts 탭 → 해당 충돌 표시

### 시나리오 D: 충돌 해소

10. Conflicts 탭에서 충돌 행 클릭 → 상세 패널 오픈
11. [기각 — 기존 유지] 클릭 → `status: "rejected"` 변경, 목록에서 사라짐 (pending 필터 기준)
12. 다른 충돌 → [자동 병합 반영] → `status: "merged"`, 해당 Topic 본문에 내용 추가됨

### 시나리오 E: 알림 (선택 검증)

13. `owner_member_id`가 설정된 Topic에 충돌 발생 → 해당 멤버 Google Chat DM 수신 확인
14. `owner_member_id` 없는 Topic 충돌 → 알림 없이 정상 처리

---

## 8. 완료 보고 형식

```
## P4 구현 완료 보고

### 변경 파일
- src/core/kb_notifier.py (신규, XX줄)
- src/core/topic_ingest_pipeline.py (신규, XX줄)
- src/dashboard/knowledge_v2_router.py (엔드포인트 추가 + Conflicts UI)

### 검증 결과
- 시나리오 A: ✅ / 상세
- 시나리오 B: ✅ / 상세
- 시나리오 C: ✅ / 상세
- 시나리오 D: ✅ / 상세
- 시나리오 E: ✅ / 상세

### 이슈/의사결정 사항

### 다음 단계 제안
```
