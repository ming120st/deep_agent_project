# CLAUDE.md — BOA (Business Operations Agent)

> Claude Code가 이 리포지터리에서 작업할 때 **가장 먼저 읽는 파일**.
> 프로젝트 전역 컨벤션, 파일 구조, 작업 가드레일을 정의한다.

---

## 1. 프로젝트 개요

**BOA (Business Operations Agent)** 는 와이드모바일 사내 업무 자동화를 위한 멀티 에이전트 시스템이다.

- **플랫폼**: Google Chat (사용자 인터페이스) + 웹 대시보드 (운영자 인터페이스)
- **핵심 스택**: Python 3.14 · FastAPI · LangGraph · MongoDB · Azure App Service
- **외부 연동**: Google Workspace (Chat/Calendar/Admin SDK) · BigQuery · Gemini · Claude · Azure Blob · 사내 eSIM/WiFi 시스템
- **동반 시스템**: `device_agent` — Playwright 기반 브라우저 자동화 서버 (BOA에서 ngrok 터널로 호출)

---

## 2. 아키텍처 핵심

### 2.1 진입 흐름

```
Google Chat event
   → src/main.py (FastAPI endpoint)
   → src/orchestrator.py (의도 분류 + 라우팅)
   → src/workflow/main_graph/graph.py (LangGraph 메인 그래프)
   → src/agent/chat/{agent_name}/graph.py (서브에이전트)
```

### 2.2 에이전트 타입 (3종)

| 타입 | 트리거 | 위치 | 레지스트리 |
|------|--------|------|----------|
| **chat** | Google Chat 메시지 | `src/agent/chat/{agent_name}/` | `AGENT_REGISTRY` |
| **schedule** | cron (APScheduler) | `src/agent/schedule/{name}/runner.py` | `SCHEDULE_REGISTRY` |
| **trigger** | interval (APScheduler) | `src/agent/trigger/check_{name}.py` | `TRIGGER_REGISTRY` |

모든 레지스트리는 `src/agent/registry.py`에 정의. 신규 에이전트 추가 시 반드시 여기에 등록.

### 2.3 상태(State) 관리

- **LangGraph state**: `src/workflow/graph_infra/state.py`의 `GraphState` 사용.
- **Checkpointer**: MongoDB (`MongoDBSaver`, TTL 1일) — `src/core/mongo_client.py`에서 초기화.
- **State TTL을 신뢰하지 말 것**: 장기 대기(담당자 며칠 응답 대기 등)에는 LangGraph state 대신 **별도 MongoDB 컬렉션 + 대시보드**를 써라.

### 2.4 관찰성

- **`session_logger.py`**: 턴/이벤트/LLM 비용 기록 → `session_logs` 컬렉션. 대화형 상호작용은 기본 기록.
- **`trace_logger.py`**: 그래프 노드 진입/종료 추적.
- **`bq_logger.py`**: BigQuery 장기 분석 미러링. `insert_row(table_id, row)` — 실패해도 업무 중단 없음.

---

## 3. 디렉터리 지도

```
src/
├── main.py                       # FastAPI 엔트리포인트, 스케줄러 부팅
├── orchestrator.py               # Chat 이벤트 → 그래프 라우팅
│
├── agent/
│   ├── registry.py               # AGENT_REGISTRY, SCHEDULE_REGISTRY, TRIGGER_REGISTRY
│   ├── subagent_dev_playbook.md  # 서브에이전트 개발 가이드 (반드시 참고)
│   ├── usage_guide.py            # help/feature discovery
│   ├── chat/
│   │   ├── esim_fault_replacement/
│   │   ├── esim_order_cancellation/
│   │   ├── esim_resend/
│   │   ├── wifi_line_change/
│   │   ├── wifi_usage_inquiry/
│   │   └── bigquery_agent/
│   ├── schedule/
│   │   └── bq_analysis_schedule/
│   └── trigger/
│       └── check_stale_docs.py
│
├── core/                         # 공통 인프라
│   ├── mongo_client.py           # Motor + pymongo + checkpointer 초기화
│   ├── llm_config.py             # LLM 클라이언트 공통 설정
│   ├── llm_calls.py              # LLM 호출 래퍼
│   ├── rag_engine.py             # FAISS + Gemini 임베딩 RAG 엔진
│   ├── session_logger.py         # 세션/턴/이벤트 로깅
│   ├── trace_logger.py           # 그래프 노드 추적
│   ├── bq_logger.py              # BigQuery 로그 미러링
│   ├── google_chat.py            # Chat API 래퍼 (send_message 등)
│   ├── workspace_client.py       # Admin SDK (부서 조회 등)
│   ├── bigquery_client.py        # BQ 쿼리 래퍼
│   ├── members_manager.py        # boa_members 관리
│   ├── spaces_manager.py         # boa_spaces 관리
│   ├── task_manager.py           # boa_tasks 관리
│   ├── device_job_manager.py     # device_agent 작업 관리
│   └── internal_esim_client.py   # 사내 eSIM API 래퍼
│
├── dashboard/                    # 운영자 웹 대시보드 (FastAPI 라우터)
│   ├── auth.py                   # 구글 OAuth 로그인
│   ├── task_router.py            # 업무 모니터링
│   ├── sessions_router.py        # 세션 로그 조회
│   ├── knowledge_router.py       # 지식베이스 관리
│   ├── members_router.py         # 사용자 관리
│   ├── spaces_router.py          # 스페이스 관리
│   ├── agents_router.py          # 에이전트 메타 관리
│   └── device_job_router.py      # device_agent 작업 조회
│
├── workflow/
│   ├── main_graph/               # 메인 LangGraph
│   ├── graph_infra/              # state, 공용 노드
│   └── device_job/               # device_agent 연동 그래프
│
└── tools/
    ├── mos_client.py             # MOS 시스템 연동
    └── smtp_mail.py              # 메일 발송
```

---

## 4. 컨벤션 (반드시 준수)

### 4.1 비동기 패턴

- **Motor(비동기 MongoDB)** 를 기본으로 쓴다. Sync pymongo는 checkpointer에만 사용.
- **동기 I/O는 `asyncio.to_thread()`로 감싼다** — FAISS, Blob, BigQuery, 파일 I/O 전부 해당.

```python
# 올바른 예
import asyncio
result = await asyncio.to_thread(sync_function, arg1, arg2)
```

### 4.2 LLM 호출

- **공통 진입점은 `src/core/llm_calls.py`와 `llm_config.py`** — 직접 `anthropic.Anthropic()` 인스턴스화 지양.
- 비용이 기록되어야 하는 호출은 `session_logger`의 event에 `llm_cost` 같이 기록.

### 4.3 MongoDB 컬렉션 명명

- 전부 `boa_` 접두사 (`boa_knowledge`, `boa_tasks`, `boa_members` 등).
- 신규 컬렉션 추가 시 반드시 `src/core/mongo_client.py`에 상수로 선언 후 import.

### 4.4 LangGraph interrupt 패턴

- 사용자 응답 대기(수 분~수십 분 내 반환 예상): `interrupt()` + `Command(resume=...)` 사용.
- 장기 대기(시간~일 단위): state 밖으로 빼내서 MongoDB 컬렉션 + 대시보드로 처리. **checkpointer TTL(1일)을 믿지 말 것.**
- `interrupt()` 호출 직전에 반드시 `job_id` / `task_id` 같은 식별자를 state에 저장 → 재개 시 MongoDB 조회 키로 사용.

### 4.5 서브에이전트 작성

신규 chat 에이전트는 반드시 `src/agent/subagent_dev_playbook.md`를 먼저 읽고 시작한다. 핵심 원칙:
- **판단은 라우터에서**, 노드는 실행만.
- **메시지 직접 발송 금지** — `state.add_ai_message()`만 쓴다 (interrupt 직전 예외).
- **종료 노드에서 `state.current_agent = None` 필수.**
- **이중 실행 방지 플래그**를 노드마다 state에 저장.

### 4.6 DeviceJob 연동

- `device_agent`는 scenario 레벨에서만 호출한다 (atomic action 직접 호출 금지).
- BOA ↔ device_agent 통신: ngrok 터널 + MongoDB `boa_device_jobs` 매핑.
- `interrupt()`로 결과 대기하되 state에 `job_id` 저장해두어야 함 (재개 시 MongoDB lookup).

### 4.7 환경변수

- 모두 `.env`에 정의, `dotenv`로 로드.
- 주요 변수: `MONGODB_URI`, `MONGODB_DB_NAME`, `GEMINI_API_KEY`, `AZURE_STORAGE_CONNECTION_STRING`, `CONTAINER_NAME`, `GOOGLE_SERVICE_ACCOUNT_KEY_PATH`, `BQ_BOA_PROJECT_ID`, `BQ_BOA_DATASET`.

### 4.8 배포 환경

- 프로덕션: Azure App Service (ACR 이미지 pull).
- 개발: `boa-dev` GCP 프로젝트 + `boa_db_dev` MongoDB (격리됨).
- 로컬 테스트: `.env`에 dev 값 세팅, ngrok 터널 필요.

---

## 5. 진행 중인 대규모 작업

### 5.1 Knowledge Base v2 (Topic-Graph RAG)

설계 원칙과 컬렉션 구조: `kb_architecture.md` 참고
스키마·API 명세: `p1_schema_and_crud.md` 참고

### 작업 시 반드시 지킬 규칙

1. **관계 변경은 반드시 `knowledge_relation_manager` 경유**
   - `boa_knowledge` 컬렉션의 `related_topic_ids`를 라우터에서 직접 `$set`하지 않는다
   - 추가: `add_relation()` / 제거: `remove_relation()` / 전체 교체: `set_relations()`

2. **신규 Topic은 `schema_version: 2`, `status: "draft"` 고정**

3. **인덱스는 `ensure_kb_v2_indexes()`에서 관리** — 다른 곳에서 인덱스 생성하지 않는다

4. **레거시 `knowledge_router.py`는 삭제됨** — import하거나 참조하지 않는다

### 파일 위치
- 라우터: `src/dashboard/knowledge_v2_router.py`
- 관계 관리: `src/core/knowledge_relation_manager.py`
- 컬렉션 선언: `src/core/mongo_client.py`

---

## 6. 작업 가드레일

### 6.1 작업 시작 전 체크리스트

1. **지시받은 문서를 끝까지 읽어라** — build spec이 있다면 거기 있는 "하지 말 것" 목록을 반드시 확인.
2. **해당 영역의 기존 코드를 grep/view로 훑어라** — 특히 명명 컨벤션, import 경로, 컬렉션명은 기존 것에 맞춰라.
3. **계획을 먼저 사용자에게 제시하고 승인받아라** — 파일 목록, 함수 시그니처, 변경 범위.

### 6.2 절대 하지 말 것

- 기존 `boa_*` 컬렉션 스키마를 파괴적으로 변경 (필드 제거, 타입 변경).
- `src/agent/subagent_dev_playbook.md`의 원칙을 무시한 서브에이전트 작성.
- `asyncio.to_thread()` 없이 동기 I/O를 async 함수에서 호출.
- 직접 `print()`로 로깅 — `logging` 또는 `session_logger`/`trace_logger` 경유.
- `main.py` 라우터 import 순서 임의 변경 (의존성 있음).
- `AGENT_REGISTRY`/`SCHEDULE_REGISTRY`/`TRIGGER_REGISTRY` 등록 누락.
- 실제 프로덕션 DB 대상으로 마이그레이션 스크립트 단독 실행 — 반드시 dev에서 검증 후 사용자 승인.

### 6.3 불확실하면 물어라

- 스키마 변경 범위가 애매할 때.
- 기존 컨벤션이 두 가지로 나뉘어 보일 때.
- 외부 API(Google/Azure/BQ) 권한 관련 변경이 필요할 때.
- build spec에 명시되지 않은 파일을 새로 만들어야 할 때.

### 6.4 테스트/검증

- 단위 테스트가 본 프로젝트에 광범위하게 깔려있지는 않다. 주요 로직은 **수동 검증 시나리오**를 build spec에 명시 → 구현 후 해당 시나리오 실행 결과를 보고.
- DB 관련 작업은 반드시 dev 환경(`boa-dev` / `boa_db_dev`)에서 먼저 확인.

---

## 7. 도움이 될 만한 파일

- **이 문서**: `/CLAUDE.md`
- **서브에이전트 개발**: `src/agent/subagent_dev_playbook.md`
- **KB v2 설계**: `docs/kb_architecture.md`
- **KB v2 Phase별 build spec**: `docs/kb_build_specs/`
- **device_agent 워크플로**: `device_agent/WORKFLOW.md` (device_agent 리포 내)

---

## 8. 커뮤니케이션 규칙

- **한국어로 답변한다** (사용자 모국어).
- **불필요한 요약/반복 금지** — 코드 변경 요약은 diff가 말한다.
- **가정을 하지 말고 명시** — "아마도 X일 것 같은데 Y로 진행합니다" 형태로 분명히.
- **파일 경로는 항상 프로젝트 루트 기준 상대경로**로 명시 (예: `src/core/rag_engine.py`).