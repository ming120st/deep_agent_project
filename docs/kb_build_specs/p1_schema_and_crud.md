# P1 Build Spec — Schema Extension & Relation Manager

> **상위 설계**: `docs/kb_architecture.md`
> **Phase**: P1 (스키마 additive 확장 + 관계 매니저 + 신규 컬렉션 + 기본 CRUD)
> **목표**: KB v2의 데이터 모델 기반을 구축한다.
> **작업 완료의 의미**: 이 스펙의 §9 검증 시나리오가 전부 통과한다.

---

## 0. 작업 시작 전 필수 확인

1. `CLAUDE.md`를 이미 읽었는가? — 읽지 않았다면 먼저 읽고 와라.
2. `docs/kb_architecture.md` §2(데이터 모델), §5(충돌 감지 원칙), §7(대시보드) 를 읽었는가?

---

## 1. 범위 (Scope)

### 1.1 포함 (P1에서 하는 것)

- [ ] `boa_knowledge` v2 필드 구성
- [ ] 3개 신규 컬렉션 선언: `boa_knowledge_conflicts`, `boa_knowledge_gaps`, `boa_knowledge_ingest_log`
- [ ] `src/core/knowledge_relation_manager.py` 신규 모듈 — 양방향 링크 관리의 **단일 쓰기 진입점**
- [ ] `src/dashboard/knowledge_v2_router.py` v2 전용 라우터
- [ ] 신규 필드에 대한 인덱스 생성 스크립트 (옵션, 주석으로 권장 인덱스 명시)

### 1.2 제외 (P1에서 하지 않는 것)

- **검색/Retrieval 로직 수정 금지** — `rag_engine.py`는 P2 대상.
- **인입 파이프라인 금지** — `topic_ingest_pipeline.py`는 P4 대상.
- **대시보드 UI(HTML) 수정 금지** — P1은 JSON API만 추가. HTML 뷰는 추후 Phase.
- **FAISS 인덱스 구조 변경 금지** — 기존 엔진 그대로 (P2 대상).

---

## 2. 절대 건드리지 말 것 (Do-not-touch)

다음은 P1 범위에서 **수정/제거/리네임 모두 금지**다:

### 2.1 `src/core/mongo_client.py`
- 기존 컬렉션 export 변수 이름 (`boa_knowledge_collection` 등).
- `checkpointer` 초기화 코드.

### 2.2 `src/core/rag_engine.py`
- 전체 파일 수정 금지 (P2 대상).

---

## 3. 신규 파일 / 변경 파일 목록

### 3.1 변경 파일

| 경로 | 변경 내용 |
|------|----------|
| `src/core/mongo_client.py` | 신규 컬렉션 3개 export 추가 |

### 3.2 신규 파일

| 경로 | 역할 |
|------|------|
| `src/core/knowledge_relation_manager.py` | 양방향 링크 관리 단일 진입점 |
| `src/dashboard/knowledge_v2_router.py` | v2 전용 라우터 |

---

## 4. 스키마 상세

### 4.1 `boa_knowledge` 필드

| 필드 | 타입 | 필수 | 기본값 | 설명 |
|------|------|-----|--------|------|
| `description` | string | ✓ | — | 주제 설명 (80~200자 권장). 검색의 핵심 자산. |
| `related_topic_ids` | array\<string> | no | `[]` | 연관 주제 `_id` 리스트. 양방향 유지. |
| `domain_id` | string | no | `None` | 등록된 도메인 레지스트리 ID(`wifi`, `esim` 등). |
| `author_type` | string | no | `"human"` | `"auto"` \| `"human"`. |
| `schema_version` | int | — | `2` | v2 문서 식별용. 항상 `2`로 세팅. |

**주의**:
- `related_topic_ids`의 요소는 `boa_knowledge._id` 값 (`uuid.uuid4().hex` 형태).

### 4.2 `boa_knowledge_conflicts` (신규 컬렉션)

```python
{
  "_id": str,                        # uuid4().hex
  "topic_id": str,                   # boa_knowledge._id 참조
  "detected_at": datetime,
  "source": {
    "type": str,                     # "auto_ingest" | "dashboard_edit"
    "origin_ref": Optional[str]      # ingest_log_id 또는 session_id 등
  },
  "existing_excerpt": str,           # 기존 본문 중 충돌 지점
  "incoming_content": str,           # 새로 들어온 내용
  "conflict_type": str,              # "contradiction" | "divergence" | "supplement"
  "confidence": float,               # 0.0 ~ 1.0
  "llm_reasoning": str,
  "status": str,                     # "pending" | "merged" | "rejected" | "manual_edited" | "stale"
  "notified_at": Optional[datetime],
  "resolved_at": Optional[datetime],
  "resolved_by": Optional[str],      # member_id
  "resolution_note": Optional[str]
}
```

**권장 인덱스** (주석으로 명시, 자동 생성은 선택):
- `topic_id` (단일)
- `status, detected_at` (복합, 대시보드 pending 리스트 조회용)

### 4.3 `boa_knowledge_gaps` (신규 컬렉션)

```python
{
  "_id": str,                        # uuid4().hex
  "query": str,
  "query_embedding": Optional[list[float]],  # 유사 gap 병합용 (P5에서 채워짐, P1에서는 None 허용)
  "session_id": Optional[str],
  "user_member_id": Optional[str],
  "searched_topics": list[dict],     # [{"topic_id": str, "score": float, "read": bool}, ...]
  "ai_verdict": str,                 # "no_match" | "insufficient_coverage"
  "ai_suggestion": Optional[str],
  "duplicate_count": int,            # default 1
  "created_at": datetime,
  "last_occurred_at": datetime,
  "status": str,                     # "open" | "in_progress" | "resolved" | "wontfix"
  "resolved_topic_id": Optional[str],
  "assigned_to": Optional[str]
}
```

**권장 인덱스**:
- `status, duplicate_count` (복합, 빈도순 open gap 조회)
- `last_occurred_at` (단일)

### 4.4 `boa_knowledge_ingest_log` (신규 컬렉션)

```python
{
  "_id": str,                        # uuid4().hex
  "source_type": str,                # "manual" | "conversation" | "document_upload" | "scheduled_crawl"
  "source_ref": Optional[str],
  "raw_content": str,
  "decision": str,                   # "created_new_topic" | "updated_existing" | "flagged_conflict" | "duplicate_skipped"
  "target_topic_id": Optional[str],
  "llm_cost_usd": float,             # default 0.0
  "created_at": datetime
}
```

**권장 인덱스**:
- `created_at` (단일, 최근순 조회)
- `decision` (단일)

---

## 5. 파일별 구현 명세

### 5.1 `src/core/mongo_client.py` 변경

**변경 위치**: `src/core/mongo_client.py` 하단 컬렉션 선언 블록 뒤에 3줄 추가.

```python
boa_knowledge_conflicts_collection = db["boa_knowledge_conflicts"]
boa_knowledge_gaps_collection       = db["boa_knowledge_gaps"]
boa_knowledge_ingest_log_collection = db["boa_knowledge_ingest_log"]
```

**금지**: checkpointer 관련 라인, `sync_client`, `session_logs_collection` 선언 등은 그대로 둔다.

### 5.2 `src/core/knowledge_relation_manager.py` (신규)

**목적**: `related_topic_ids`의 양방향 일관성을 유지하는 유일한 쓰기 진입점.

**외부 공개 API** (이것들만 export, 내부 헬퍼는 언더스코어 접두):

```python
from typing import Optional

async def add_relation(topic_id_a: str, topic_id_b: str, actor: str) -> None:
    """
    두 주제를 양방향으로 연결한다.
    - 이미 연결되어 있으면 no-op.
    - 자기 자신 연결 금지 (a == b면 ValueError).
    - 존재하지 않는 topic_id면 ValueError.
    - 둘 중 하나만 업데이트되고 실패하는 상황을 방지하기 위해 사전 검증 후 두 번 update_one 수행.
      (본 phase에서는 트랜잭션 없이 구현하되 실패 로그 남김.)
    - actor: member_id 또는 "auto_ingest" 등, updated_by 로깅용.
    """

async def remove_relation(topic_id_a: str, topic_id_b: str, actor: str) -> None:
    """양방향 연결 제거. 연결되어 있지 않으면 no-op."""

async def set_relations(topic_id: str, related_topic_ids: list[str], actor: str) -> None:
    """
    주제의 관련 주제 리스트를 통째로 교체.
    - 현재 related_topic_ids와 비교하여 추가된 것은 add_relation, 제거된 것은 remove_relation 호출.
    - 원자성 보장은 하지 않지만 중간 실패 시 어느 주제의 어느 방향까지 성공했는지 로그.
    """

async def get_related_topics(topic_id: str) -> list[dict]:
    """
    주제의 연관 주제를 {_id, title, description, tags, domain}만 추려서 반환.
    본문(content)은 포함하지 않는다 (경량 조회).
    """

async def check_bidirectional_consistency() -> dict:
    """
    전체 boa_knowledge를 스캔하여 단방향 링크(A→B 있는데 B→A 없음)를 탐지.
    반환 형식:
      {
        "checked": 120,
        "inconsistencies": [
          {"from": "...", "to": "...", "direction": "missing_reverse"},
          ...
        ]
      }
    자동 복구는 하지 않는다. 복구는 별도 함수 또는 운영자 승인 하에 진행.
    """
```

**구현 가이드라인**:
- 모든 mongo 접근은 `boa_knowledge_collection` (Motor, async) 사용.
- `updated_at`, `updated_by` 필드는 링크 변경 시 갱신.
- 로깅은 `logging.getLogger(__name__)` 사용. `print()` 금지.
- 내부 헬퍼 `_assert_topic_exists(topic_id: str)` 만들어 사전 검증 통일.
- 에러 케이스는 `ValueError`로 명확히 raise, 예외는 상위 호출자가 처리하도록.

### 5.3 `src/dashboard/knowledge_v2_router.py` (신규)

**엔드포인트**:

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/knowledge/v2/topics` | v2 필드가 세팅된 주제 리스트 (`schema_version: 2` 필터) |
| `GET` | `/api/knowledge/v2/topics/{topic_id}` | 단일 주제 조회 (v2 필드 포함) |
| `POST` | `/api/knowledge/v2/topics` | v2 포맷으로 신규 주제 생성 |
| `PATCH` | `/api/knowledge/v2/topics/{topic_id}` | v2 필드만 부분 업데이트 (description, domain, tags, related_topic_ids) |
| `GET` | `/api/knowledge/v2/topics/{topic_id}/related` | 연관 주제 경량 조회 |
| `POST` | `/api/knowledge/v2/topics/{topic_id}/relations` | 단일 관계 추가 (body: `{"related_topic_id": "..."}`) |
| `DELETE` | `/api/knowledge/v2/topics/{topic_id}/relations/{related_id}` | 단일 관계 제거 |
| `GET` | `/api/knowledge/v2/admin/consistency-check` | 양방향 링크 정합성 체크 결과 반환 (admin only) |

**요청/응답 모델** (Pydantic):

```python
class TopicV2CreateRequest(BaseModel):
    title: str
    description: str                              # 80~200자 권장, 필수
    content: str
    tags: list[str] = []
    domain_id: Optional[str] = None               # "wifi" | "esim" 등 
    related_topic_ids: list[str] = []

class TopicV2PatchRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None
    domain_id: Optional[str] = None
    related_topic_ids: Optional[list[str]] = None

class RelationAddRequest(BaseModel):
    related_topic_id: str
```

**동작 가이드라인**:
- 모든 엔드포인트는 `require_login` 적용 (`auth.py` 패턴 재사용).
- `POST /topics` 시:
  - `_id`는 `uuid.uuid4().hex`.
  - `schema_version: 2`, `author_type: "human"`, `created_by`, `updated_by`에 현재 로그인 사용자 세팅.
  - `created_at`, `updated_at`은 `datetime.utcnow()`.
  - `related_topic_ids`가 비어있지 않으면, 저장 후 `knowledge_relation_manager.set_relations()` 호출로 양방향 링크 처리.
- `PATCH /topics/{id}` 시:
  - `related_topic_ids`가 포함되어 있으면 **반드시** `knowledge_relation_manager.set_relations()` 경유. 직접 update_one 금지.
  - 기타 필드는 `$set`으로 단순 업데이트.
  - `updated_at`, `updated_by` 갱신.
- `POST/DELETE /relations`: `knowledge_relation_manager.add_relation` / `remove_relation` 호출.
- 응답 직렬화: `_id` → `id`, datetime → isoformat.

**admin 확인**: consistency-check 엔드포인트는 `is_admin()` 확인.

### 5.4 `src/main.py` 변경

**변경 위치**: router import/register 블록.

```python
# import 추가
from src.dashboard.knowledge_v2_router import router as knowledge_v2_router

# app.include_router 블록에 1줄 추가
app.include_router(knowledge_v2_router)
```

**금지**: 기존 router import/register 순서 변경.

---

## 6. 작업 순서

순서대로 진행할 것. 각 단계 완료 후 **사용자에게 보고 후 다음 단계로**.

1. **Step 1**: `src/core/mongo_client.py`에 3개 컬렉션 export 추가. (§5.1)
2. **Step 2**: `src/core/knowledge_relation_manager.py` 신규 작성. (§5.2)
3. **Step 3**: `src/dashboard/knowledge_v2_router.py` 작성. (§5.3)
4. **Step 4**: `src/main.py`에 router include 1줄 추가. (§5.4)
5. **Step 5**: §9 수동 검증 시나리오 실행 및 결과 보고.

---

## 7. 환경 / 실행 고려사항

- **인덱스 생성**: 본 phase에서는 자동 실행하지 않는다. 권장 인덱스를 `knowledge_v2_router.py` 파일 상단 주석 또는 별도 `docs/kb_indexes.md`에 기록해두고, 운영자가 수동 실행하도록 안내.
- **dev DB 사용**: 작업 도중 DB 접속이 필요하면 `boa-dev` / `boa_db_dev` 환경변수로 전환. 프로덕션 DB에 테스트 데이터 주입 금지.

---

## 8. 실패 / 엣지 케이스

| 케이스 | 처리 |
|--------|------|
| `POST /v2/topics`에서 `related_topic_ids`에 존재하지 않는 ID 포함 | 400 에러, 해당 ID 목록 반환. 주제 자체는 저장되지 않음. |
| `add_relation`에서 하나의 update는 성공, 반대편은 실패 | 에러 로그 + 성공한 쪽도 롤백 시도 (best-effort). 예외 전파. |
| `PATCH`로 `related_topic_ids`를 `[]`로 넣음 | 기존 관계 전부 제거 (양방향). |
| 자기 자신을 관련 주제로 추가 | 400 에러, `ValueError("self-reference not allowed")`. |

---

## 9. 수동 검증 시나리오

아래 시나리오를 순서대로 수행하고, 각 단계 결과를 보고한다.
**dev 환경** (`boa_db_dev`)에서 수행할 것.

### 시나리오 A: 신규 v2 주제 생성 & 관계 구성

1. `POST /api/knowledge/v2/topics` 으로 주제 3개 생성:
   - Topic X: `title="이심 취소처리 방법"`, `description="..."`, `domain="internal"`, `related_topic_ids=[]`
   - Topic Y: `title="이심 취소 정책"`, `description="..."`, `domain="internal"`, `related_topic_ids=[<X._id>]`
   - Topic Z: `title="결제수단별 취소방식"`, `description="..."`, `domain="internal"`, `related_topic_ids=[<Y._id>]`
2. `GET /api/knowledge/v2/topics/{X._id}` → `related_topic_ids`에 Y가 **자동으로** 들어있는지 확인 (양방향).
3. `GET /api/knowledge/v2/topics/{Y._id}/related` → X, Z 둘 다 경량 객체로 반환되는지 확인.

### 시나리오 B: 관계 수정

4. `PATCH /api/knowledge/v2/topics/{X._id}` body `{"related_topic_ids": []}` → X의 관계 전부 제거.
5. `GET /api/knowledge/v2/topics/{Y._id}` → Y의 `related_topic_ids`에서 X가 사라졌는지 확인 (양방향 제거).

### 시나리오 C: 에러 케이스

6. `POST /api/knowledge/v2/topics` body에 `related_topic_ids: ["존재하지_않는_id"]` → 400 에러.
7. `POST /api/knowledge/v2/topics/{X._id}/relations` body `{"related_topic_id": "{X._id}"}` → 400 에러 (self-reference).

### 시나리오 D: 정합성 체크

8. MongoDB에서 직접 Topic X의 `related_topic_ids`에 임의의 id 하나 추가 (단방향 오염).
9. `GET /api/knowledge/v2/admin/consistency-check` (admin 계정으로) → 해당 단방향 링크가 `inconsistencies`에 리포트되는지 확인.

---

## 10. 완료 보고 형식

작업이 끝나면 다음 형식으로 보고:

```
## P1 구현 완료 보고

### 변경 파일
- src/core/mongo_client.py (3줄 추가)
- src/core/knowledge_relation_manager.py (신규, XX줄)
- src/dashboard/knowledge_v2_router.py (신규, XX줄)
- src/main.py (2줄 추가)

### 검증 결과
- 시나리오 A: ✅ / 상세
- 시나리오 B: ✅ / 상세
- 시나리오 C: ✅ / 상세
- 시나리오 D: ✅ / 상세

### 이슈/의사결정 사항
- (작업 중 발견한 이슈나 스펙과 다르게 처리한 부분)

### 다음 단계 제안
- (P2로 넘어가기 전 확인이 필요한 사항)
```
