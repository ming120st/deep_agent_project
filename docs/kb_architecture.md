# BOA Knowledge Base Architecture v2 — Topic-Graph RAG

> 설계 버전: v2 (2026-04)
> 이전 설계(Claim-based event-sourced) 대체
> 대상 범위: 수십 ~ 수백 주제 규모의 도메인 지식 운영

---

## 1. 설계 목표 & 핵심 원칙

### 1.1 목표

1. **운영자가 직관적으로 관리 가능한 단위** — 주제(Topic)를 1차 단위로 하여 대시보드에서 읽고 편집할 수 있어야 함.
2. **AI가 관련 지식을 능동적으로 탐색** — 단순 top-K retrieval이 아니라, 주제 설명을 보고 "지금 컨텍스트에 뭐가 부족한지"를 판단해서 연관 주제로 타고 들어감.
3. **자동 인입과 사람 운영의 공존** — AI가 새 내용을 자동으로 분류·추가할 수 있되, 기존 내용과 충돌 시 사람이 반드시 개입할 수 있는 경로 제공.
4. **지식 갭 가시화** — "답을 못 찾음"이 로그로 사라지지 않고 대시보드에 쌓여서 KB가 실제 쓰임새에 맞춰 성장.

### 1.2 비-목표 (v2에서 하지 않는 것)

- 엣지 타입(관계 타입) 분류 — 단순 무방향 연결만.
- 문서 버전 관리의 시간축 복원(git-like history) — `boa_knowledge_versions`에 최근 N개 스냅샷 유지 수준.
- 문서 크기 자동 분할/병합 제안 — 운영하다 필요성 보이면 도입.
- Claim 단위 원자 분해 — 주제 본문은 자연어 문서 그대로.

### 1.3 핵심 원칙

- **주제 설명이 검색의 핵심 자산** — 본문보다 설명+태그가 1차 검색/탐색 판단의 근거. 설명 품질이 KB 품질을 결정.
- **대시보드가 단일 진실 공급원** — 충돌/대기/갭은 모두 대시보드에서 본다. 구글챗은 단방향 알림(링크 포함)만.
- **LangGraph state에 장기 대기 상태를 두지 않는다** — state TTL과 checkpointer 용량 문제로 "담당자 응답을 interrupt로 기다리는" 패턴은 ingestion에 쓰지 않음.

---

## 2. 데이터 모델

### 2.1 컬렉션 개요

| 컬렉션 | 역할 |
|---|---|
| `boa_knowledge` | 주제(Topic) 본문 + 메타 + 관계 |
| `boa_knowledge_versions` | 주제 변경 스냅샷 (최근 N개) |
| `boa_knowledge_conflicts` | 인입 충돌 대기열 |
| `boa_knowledge_gaps` | KB gap queue (답 못 찾은 질문) |
| `boa_knowledge_ingest_log` | 인입 이벤트 로그 (BQ 연동) |

### 2.2 `boa_knowledge` 스키마

```jsonc
{
  "_id": "topic_esim_cancel_process",      // slug 기반 ID
  "title": "이심 취소처리 방법",
  "description": "이심 상품의 주문 취소를 처리하는 절차. 결제수단별/상태별 분기 포함.",  // 검색용 핵심 자산
  "tags": ["esim", "cancel", "cs-operation"],
  "domain": "internal",                     // "internal" | "general" — 6항 하이브리드 정책
  "content": "...(본문, 자연어 마크다운)...",
  "related_topic_ids": [
    "topic_esim_cancel_policy",
    "topic_payment_cancel_methods"
  ],
  "author_type": "auto" | "human",
  "owner_member_id": "member_xxx",          // 담당자 (충돌 알림 수신자)
  "status": "active" | "deprecated" | "draft",
  "created_at": "2026-04-23T...",
  "updated_at": "2026-04-23T...",
  "created_by": "member_xxx" | "auto_ingest",
  "updated_by": "member_xxx" | "auto_ingest"
}
```

**설계 결정 메모**

- `related_topic_ids`는 단방향 저장이지만 **쓰기 시 양방향 유지**. A→B 추가 시 B의 `related_topic_ids`에도 A 추가. 일관성 유지를 위해 mongo 트랜잭션 또는 `knowledge_relation_manager.py`에 쓰기 진입점 단일화.
- `description`은 80~200자 권장. 검색 성능 직결.
- `domain` 필드는 §6 하이브리드 지식 사용 정책의 핵심 플래그.
- `owner_member_id`가 비어있으면 기본값으로 스페이스 관리자에게 알림 발송.

### 2.3 `boa_knowledge_conflicts` 스키마

```jsonc
{
  "_id": "conflict_xxx",
  "topic_id": "topic_esim_cancel_process",
  "detected_at": "...",
  "source": {
    "type": "auto_ingest" | "dashboard_edit",
    "origin_ref": "session_log_id | ingest_log_id"
  },
  "existing_excerpt": "...(기존 본문 중 관련 부분)...",
  "incoming_content": "...(새로 인입된 내용)...",
  "conflict_type": "contradiction" | "divergence" | "supplement",
  "confidence": 0.82,                       // LLM 판단 신뢰도
  "llm_reasoning": "...(왜 충돌이라고 판단했는지)...",
  "status": "pending" | "merged" | "rejected" | "manual_edited",
  "notified_at": "...",
  "resolved_at": null,
  "resolved_by": null,
  "resolution_note": null
}
```

### 2.4 `boa_knowledge_gaps` 스키마

```jsonc
{
  "_id": "gap_xxx",
  "query": "법인 계정 이심 재발송 시 VAT 처리 어떻게 돼?",
  "session_id": "session_xxx",              // 어느 대화에서 발생했는지 추적
  "user_member_id": "member_xxx",
  "searched_topics": [                      // 탐색 경로 감사용
    {"topic_id": "topic_esim_resend", "score": 0.71, "read": true},
    {"topic_id": "topic_corporate_billing", "score": 0.55, "read": false}
  ],
  "ai_verdict": "insufficient_coverage",    // "no_match" | "insufficient_coverage"
  "ai_suggestion": "법인 계정 VAT 관련 주제 신설 또는 'topic_corporate_billing' 확장 권장",
  "created_at": "...",
  "status": "open" | "in_progress" | "resolved" | "wontfix",
  "resolved_topic_id": null,                // 어느 주제 생성/수정으로 해결됐는지
  "assigned_to": null
}
```

**집계 규칙**: 동일/유사 쿼리가 반복되면 `duplicate_count` 증가. 임베딩 유사도 0.9 이상이면 기존 gap에 병합. 대시보드에서 발생 빈도 기준 정렬 → 우선순위 있는 갭부터 채움.

### 2.5 `boa_knowledge_ingest_log` 스키마

```jsonc
{
  "_id": "ingest_xxx",
  "source_type": "manual" | "conversation" | "document_upload" | "scheduled_crawl",
  "source_ref": "...",
  "raw_content": "...",
  "decision": "created_new_topic" | "updated_existing" | "flagged_conflict" | "duplicate_skipped",
  "target_topic_id": "...",
  "llm_cost_usd": 0.003,
  "created_at": "..."
}
```

BQ 미러링으로 장기 분석(가장 많이 생성되는 주제 유형, LLM 비용 등) 수행. 기존 `bq_logger.py` 패턴 준용.

---

## 3. 검색 & 응답 파이프라인 (RAG Workflow)

LangGraph 서브그래프로 구현. 노드 단위로 관찰 가능하게.

### 3.1 전체 플로우

```
[query]
   │
   ▼
(1) topic_candidate_selector       ← 하이브리드: 벡터 top-N → LLM rerank top-K
   │
   ▼
(2) traverse_loop (LangGraph Send/Reduce 패턴)
   │  ├─ (2a) read_topic_content
   │  ├─ (2b) extract_relevant_snippet
   │  ├─ (2c) sufficiency_judge
   │  └─ (2d) next_hop_selector  ─┐
   │                               │ (depth < MAX_DEPTH && budget < MAX_TOPICS)
   │  ◀────────────────────────────┘
   ▼
(3) context_assembler              ← 스니펫들을 프롬프트용 블록으로 조립
   │
   ▼
(4) answer_generator                ← §6 하이브리드 지식 사용 정책 적용
   │
   ▼
(5) gap_recorder (conditional)      ← 찾을 수 없을 때만 실행
```

### 3.2 노드별 명세

#### (1) `topic_candidate_selector`

- **입력**: `query`, `query_embedding`
- **처리**:
  1. FAISS 벡터 검색 (주제의 `description` + `tags`를 임베딩 대상으로 저장) → top-20 후보
  2. 후보의 `{title, description, tags}`를 프롬프트에 주입하여 LLM이 top-5 선정 + 각각 score(0~1) 부여
- **출력**: `candidates: [{topic_id, score, reason}, ...]`
- **비용 최적화**: 쿼리 임베딩 캐시(세션 단위). 재진입 시 벡터 검색 생략.

#### (2) `traverse_loop` (핵심)

Greedy iterative 전략. 매 hop마다 "충분한가?"를 판단.

**상태 스키마**:
```python
class TraverseState(TypedDict):
    query: str
    visited_topic_ids: set[str]
    candidate_queue: list[tuple[str, float]]  # (topic_id, priority_score)
    collected_snippets: list[SnippetBlock]
    depth: int
    sufficiency_verdict: Literal["continue", "sufficient", "exhausted"]
```

**MAX_DEPTH = 3, MAX_TOPICS = 7** (환경변수로 조정 가능).

**각 iteration**:

- **(2a) `read_topic_content`**: 우선순위 큐에서 하나 꺼내 본문 로드. `visited`에 추가.
- **(2b) `extract_relevant_snippet`**: 본문 전체를 프롬프트에 넣지 않고, LLM에게 "이 주제 본문에서 `query`에 답하는 데 필요한 부분만 발췌" 지시. 결과는 `SnippetBlock{topic_id, title, snippet, source_excerpt_range}`.
  - 만약 본문에 관련 내용 전무하면 `snippet = None`, 해당 주제는 컨텍스트에서 제외.
- **(2c) `sufficiency_judge`**: 지금까지 `collected_snippets`만으로 `query`에 답할 수 있는가? LLM이 `{verdict, missing_aspects}` 반환.
  - `sufficient` → 루프 탈출.
  - `continue` → (2d)로.
  - `exhausted`(후보 큐 비었고 새로 탐색할 연관 주제도 없음) → 루프 탈출, gap 플래그.
- **(2d) `next_hop_selector`**: 현재 읽은 주제의 `related_topic_ids` 중 아직 방문하지 않은 것들의 `{title, description, tags}`를 LLM에 제시, "`missing_aspects` 채우는 데 가장 도움이 될 주제 1~2개 선정 + priority". 선정된 것을 `candidate_queue`에 삽입.

**종료 조건**:
- `sufficiency_verdict == "sufficient"` OR
- `depth >= MAX_DEPTH` OR
- `len(visited) >= MAX_TOPICS` OR
- `candidate_queue`가 비었고 확장할 연관 주제도 없음

#### (3) `context_assembler`

스니펫들을 XML 블록으로 조립:

```xml
<knowledge_base_context>
  <topic id="topic_esim_cancel_process" title="이심 취소처리 방법">
    (발췌된 스니펫)
  </topic>
  <topic id="topic_esim_cancel_policy" title="이심 취소 정책">
    ...
  </topic>
</knowledge_base_context>
```

#### (4) `answer_generator`

§6의 하이브리드 정책 적용:
- 컨텍스트에 `domain: internal` 주제가 섞여 있으면 → 시스템 프롬프트에 "내부 정책 질문은 제공된 KB에만 근거. 모르면 모른다고 답변" 강화.
- 전부 `general`이거나 KB 컨텍스트가 비어있으면 → 사전지식 허용.

#### (5) `gap_recorder`

다음 조건 중 하나라도 해당 시 `boa_knowledge_gaps`에 기록:
- 루프 종료 시 `sufficiency_verdict == "exhausted"`
- 최종 답변 생성 시 LLM이 "모름" 플래그 반환
- `topic_candidate_selector`가 score 0.3 이상 후보를 하나도 못 찾음

기록 시 유사 gap과 임베딩 비교하여 중복 병합.

### 3.3 관찰성

- 각 노드 진입/종료를 `trace_logger` + `session_logger`에 기록.
- 최종적으로 `RAGTrace` 레코드 생성: `{query, candidates, visited_topics, skipped_topics, final_verdict, total_llm_cost}`. 대시보드의 "RAG 추적" 뷰에서 조회 가능.

---

## 4. 문서 인입 파이프라인

### 4.1 인입 트리거

1. **대시보드 수동 입력**: 담당자가 직접 주제 생성/편집 — 충돌 감지 스킵, 즉시 저장.
2. **대화 추출**: RAG investigator sub-agent가 직원 인터뷰로 얻은 내용 — 자동 인입.
3. **문서 업로드**: 담당자가 파일 업로드 → LLM이 청크 단위로 분해 후 주제 단위로 재구성 → 자동 인입.
4. **예약 크롤**: 내부 위키/Notion 연동 (추후 확장).

### 4.2 자동 인입 플로우 (2, 3, 4번 공통)

```
[incoming_content]
   │
   ▼
(A) topic_scoping              ← 이 내용이 몇 개 주제에 해당하는가? (1:1, 1:N 분기)
   │
   ▼  (for each scoped chunk)
(B) existing_topic_matcher     ← 하이브리드 검색 (벡터 top-N → LLM 매칭)
   │
   ├─ match found & high confidence (>0.8)
   │     │
   │     ▼
   │  (C) content_compare      ← 기존 본문과 비교: identical / supplement / conflict
   │     │
   │     ├─ identical    → skip (log)
   │     ├─ supplement   → auto_merge (log + versions snapshot)
   │     └─ conflict     → (D) conflict_register
   │
   ├─ match found & low confidence (0.5 ~ 0.8)
   │     │
   │     ▼
   │  (D) conflict_register    ← 사람 판단 필요: "이 주제에 추가할지?" pending
   │
   └─ no match
         │
         ▼
      (E) new_topic_creator    ← 제목/설명/태그/domain 자동 생성 + 연관 주제 자동 링크
```

### 4.3 노드별 명세

#### (A) `topic_scoping`

```
prompt: "다음 내용이 몇 개의 독립된 주제에 해당하는지 판단하라.
        독립된 주제 = 각각 다른 상황/맥락에서 참조될 수 있는 단위."
output: [{scoped_content, tentative_title}, ...]
```

예: 회의록 하나가 "이심 재발송 정책 변경"과 "법인 계정 청구 처리"를 모두 포함 → 2개로 쪼갬.

#### (B) `existing_topic_matcher`

벡터 top-10 + LLM rerank → `{matched_topic_id, confidence}` 또는 `None`.

#### (C) `content_compare`

```
prompt: "기존 주제 본문:
        ---
        {existing_content}
        ---
        새로 들어온 내용:
        ---
        {incoming_content}
        ---
        판정하라: identical(완전히 같은 정보) / supplement(기존에 없던 보완) / conflict(모순).
        conflict라면 충돌 지점을 명시."
output: {verdict, overlap_region, conflict_region, confidence}
```

**Threshold**:
- `verdict == "identical"` && conf > 0.85 → skip
- `verdict == "supplement"` && conf > 0.8 → auto_merge (본문에 append, `updated_by: auto_ingest`)
- `verdict == "supplement"` && conf 0.5~0.8 → conflict queue (사람 확인)
- `verdict == "conflict"` (항상) → conflict queue

#### (D) `conflict_register`

`boa_knowledge_conflicts`에 레코드 생성 → `notify_owner` (구글챗 푸시):

```
[BOA] 지식베이스 확인 요청

주제: 이심 취소처리 방법
사유: 기존 내용과 다른 설명이 자동 인입됨 (conflict)
자동 판단 신뢰도: 82%

대시보드에서 확인 → https://boa.example.com/dashboard/knowledge/conflicts/conflict_xxx
```

메시지 본문에 내용을 싣지 않고, **대시보드 링크만 전달**. 담당자가 대시보드에서:
- 기존 vs 신규 diff 뷰
- 선택지: [자동 병합 반영] / [기각 — 기존 유지] / [직접 수정 후 저장]

#### (E) `new_topic_creator`

```
prompt: "다음 내용에 대한 새 주제를 만든다.
        - 제목 (명사구, 구체적)
        - 설명 (80~200자, 이 주제가 무엇을 다루는지 + 언제 참조되는지)
        - 태그 (3~6개)
        - domain (internal | general)
        - 기존 주제 리스트 중 연관될 만한 주제 0~5개 선정
        기존 주제 후보: [top-20 candidates]"
output: {title, description, tags, domain, related_topic_ids}
```

생성 후:
- 주제 저장
- `related_topic_ids` 양방향 링크 갱신
- FAISS 인덱스에 설명+태그 임베딩 추가
- Blob에 인덱스 sync
- 담당자에게 "새 주제가 자동 생성되었습니다 — 확인하시겠습니까?" 알림 (선택적, 빈도 조절)

### 4.4 알림 배치 전략

**Alert fatigue 방지**:
- 같은 `topic_id`에 대한 conflict가 1시간 내 재발생 → 같은 알림 스레드 업데이트 (새 메시지 X)
- `new_topic_creator` 알림은 기본 **일 1회 배치** (저녁에 "오늘 생성된 새 주제 N개" 요약). 실시간 알림 필요 시 담당자 설정으로 토글.
- conflict 알림만 즉시 발송 (담당자 개입 필요하므로).

---

## 5. 충돌 감지 & 해소

### 5.1 원칙

- **LangGraph state에 담당자 응답 대기 상태를 두지 않는다** — checkpoint TTL(기본 수일) 만료 위험, state 비대화, 동시 여러 conflict 상태 관리 복잡도 때문.
- **대시보드가 해소 UI의 단일 창구** — Google Chat은 링크만.
- **비동기 해소를 허용** — 담당자가 며칠 뒤 처리해도 파이프라인 영향 없음. 그 사이 다른 인입은 정상 처리되며, 같은 주제에 겹치면 같은 conflict 레코드에 누적.

### 5.2 해소 액션별 동작

| 액션 | 동작 |
|---|---|
| **자동 병합 반영** | `incoming_content`를 본문에 append → `boa_knowledge_versions` 스냅샷 → FAISS 재색인 → conflict `status: merged` |
| **기각** | 본문 변경 없음 → conflict `status: rejected`, `resolution_note` 기록 |
| **직접 수정 후 저장** | 본문 에디터 오픈 → 저장 시 스냅샷 + FAISS 재색인 → conflict `status: manual_edited` |

모든 해소는 `updated_by`에 `member_id` 기록, audit trail 유지.

### 5.3 장기 pending 처리

- `status: pending`이 7일 이상 → 주 1회 리마인더 알림 (담당자 + 백업 담당자)
- 30일 이상 → `status: stale`로 자동 전환, 대시보드 별도 탭에 표시. 담당자가 "일괄 정리" 가능.

---

## 6. 하이브리드 지식 사용 정책

### 6.1 분기 기준

주제의 `domain` 필드로 결정:

| 값 | 의미 | 답변 정책 |
|---|---|---|
| `internal` | 회사 특화 (내부 정책/절차/상품코드/시스템) | **반드시 KB만** — 사전지식 금지, 없으면 gap 기록 |
| `general` | 일반 지식 (기술 원리, 범용 개념) | KB + 사전지식 혼용 허용 |

### 6.2 런타임 적용

`answer_generator` 노드가 `collected_snippets` 중 하나라도 `domain: internal`이면 시스템 프롬프트에 강한 제약 추가:

```
다음 질문은 회사 내부 정책/절차에 관한 것이다.
반드시 제공된 <knowledge_base_context> 내의 정보에만 근거하여 답하라.
KB 컨텍스트에 근거가 없는 부분은 "해당 내용은 현재 지식베이스에 등록되어 있지 않습니다"로 답하고,
절대 일반적 추론이나 유사 사례로 보완하지 마라.
```

쿼리 자체의 도메인 판정은 candidate 주제의 `domain`으로 결정 (별도 분류기 불필요).

### 6.3 경계 케이스

쿼리에 internal + general이 섞여 있을 때 (예: "eSIM 원리와 우리 회사 재발송 절차") — 엄격 모드 우선. 내부 관련 부분은 KB만, 일반 원리는 자연스럽게 답변하되 섹션 분리.

---

## 7. 대시보드 요구사항

### 7.1 엔드포인트

**`knowledge_v2_router.py`**에 다음 엔드포인트 추가:

- `GET /dashboard/knowledge/topics` — 주제 리스트 (검색/필터: tag, domain, status, owner)
- `GET /dashboard/knowledge/topics/{id}` — 상세 (본문 + 연관 주제 + 최근 인입/변경 이력)
- `PATCH /dashboard/knowledge/topics/{id}` — 편집
- `POST /dashboard/knowledge/topics` — 수동 생성
- `DELETE /dashboard/knowledge/topics/{id}` — 삭제 (연관 주제의 역링크도 정리)
- `GET /dashboard/knowledge/topics/{id}/graph` — 주제 중심 1~2-hop 그래프 시각화 데이터

### 7.2 신규 뷰

**Conflicts 탭**
- pending / merged / rejected / stale 필터
- diff 뷰어 (기존 vs 신규)
- 액션 버튼 3종

**Gaps 탭**
- 빈도순 정렬
- 유사 gap 자동 클러스터링
- "새 주제 만들기" 바로가기 (gap 내용을 new_topic_creator의 input으로 prefill)
- "기존 주제 확장" (타겟 주제 선택 후 gap 내용을 해당 주제 편집창으로 prefill)
- status 관리: open → in_progress → resolved/wontfix

**Ingest Log 탭**
- 최근 자동 인입 내역
- 필터: source_type, decision
- 비용 집계 (일별 LLM 비용)

**Graph 탭**
- 전체 주제 관계 그래프 (vis.js 또는 cytoscape)
- orphan topic(연관 주제 0개) 하이라이트
- 클러스터 단위 색상 (tag 기준)

### 7.3 그래프 일관성 유틸리티

백그라운드 잡(`TRIGGER_REGISTRY`에 등록, 일 1회):
- 양방향 링크 정합성 체크 (A→B는 있는데 B→A는 없음 → 자동 복구)
- orphan 주제 리스트업
- FAISS 인덱스 vs Mongo 싱크 드리프트 체크

---

## 8. 인프라 변경 요약

### 8.1 `rag_engine.py` 개편

- 임베딩 대상 변경: **주제의 `description` + `tags`** (본문 아님). 본문은 FAISS에 넣지 않고 Mongo에서 그때그때 로드.
  - 이유: 본문이 길어져도 검색 품질 영향 없음. 설명을 잘 쓰면 검색 품질 향상. 인덱스 재구축 비용↓.
- `search_knowledge()` 대체: `TopicGraphRetriever` 클래스 신설, §3의 traverse_loop을 메서드로 제공.
- 기존 HyDE는 유지하되 description 기반 매칭이라 효과 반감 가능 — 필요성 재평가.

### 8.2 새 모듈

```
src/core/
  ├─ topic_graph_retriever.py   # §3 파이프라인
  ├─ topic_ingest_pipeline.py   # §4 파이프라인
  ├─ knowledge_relation_manager.py  # 양방향 링크 유지 진입점
  └─ gap_recorder.py             # §3.2 (5) + 유사 gap 병합

src/dashboard/
  └─ knowledge_v2_router.py

src/agent/trigger/
  └─ kb_consistency_check.py    # §7.3 백그라운드 잡
```

### 8.3 Agent/Workflow 통합

- 기존 `AGENT_REGISTRY`의 RAG-의존 에이전트들 → `TopicGraphRetriever` 호출로 교체.
- `session_logger`에 RAGTrace 레코드 추가.
- `bq_logger`에 `knowledge_ingest_log` 미러링.

---

## 9. 구현 로드맵 (제안)

| Phase | 범위 | 의존성 |
|---|---|---|
| **P1** | 스키마 확장 + `knowledge_relation_manager` + 대시보드 기본 CRUD | - |
| **P2** | `TopicGraphRetriever` 구현 (§3) + session_logger 통합 | P1 |
| **P3** | 대시보드 UI — Topics/Conflicts/Gaps/Graph 뷰 | P1, P2 |
| **P4** | `topic_ingest_pipeline` + conflicts 대시보드 (§4, §5) | P1, P2 |
| **P5** | Gap recorder + Gaps 대시보드 (§3.2(5), §7.2) | P2 |
| **P6** | Graph 뷰어 + consistency jobs (§7) | P1 |
| **P7** | Ingest log BQ 미러링 + 비용 대시보드 | P4 |

각 phase는 feature flag 뒤에서 점진 배포.

---

## 11. 열린 이슈 (v2 범위 밖, 추후 결정)

1. **본문 크기 관리** — 자동 분할/병합 제안 도입 여부. 운영 후 평균 주제 크기 모니터링하여 결정.
2. **관계 타입 도입** — 운영 중 "전제조건 vs 참조" 구분이 retrieval 품질에 영향 주는지 관찰 후 결정.
3. **다국어 주제** — 현재는 한국어 전제. 영문/중문 고객 대응 필요 시 주제별 다국어 필드 확장.
4. **KB 간 cross-reference** — BOA 외부 시스템(Notion 등)의 문서와 링크할지, 아니면 KB 내부에 복제할지.
5. **RAG investigator와의 결합** — investigator가 gap을 타겟으로 직원 인터뷰를 자동 시작하게 할 것인가. 할 거면 gap 레코드에 `investigator_session_id` 필드 추가.