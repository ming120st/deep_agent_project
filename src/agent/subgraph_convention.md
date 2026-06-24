# 서브그래프 구현 규격 가이드 (Chat 서브에이전트 기준)

이 문서는 `src/agent/esim/subagents/chat/esim_fault_replacement`의 `graph.py`, `node.py`를 기준으로,
새로운 chat 서브그래프를 만들 때 반드시 맞춰야 하는 공통 규격을 정리한 문서입니다.

적용 범위:
- `src/agent/*/subagents/chat/*/graph.py`
- `src/agent/*/subagents/chat/*/node.py`

---

## 1. 결론: 이것들은 사실상 필수

### 1) 상태/노드 시그니처 규격
- 모든 노드는 `async def ... (state: GraphState) -> GraphState` 시그니처를 사용.
- `GraphState`는 `src/workflow/graph_infra/state.py`의 것을 사용.
- 에이전트별 파라미터는 반드시 `state.collected_parameters[AGENT_NAME]` 하위에서만 관리.

권장 헬퍼:
```python
AGENT_NAME = "my_agent"

def _get_params(state: GraphState) -> dict:
    return state.collected_parameters.get(AGENT_NAME, {})

def _set_param(state: GraphState, key: str, value) -> None:
    if AGENT_NAME not in state.collected_parameters:
        state.collected_parameters[AGENT_NAME] = {}
    state.collected_parameters[AGENT_NAME][key] = value
```

### 2) task 상태 업데이트 규격
- chat 업무 노드에서는 아래 import를 기본 채택.
```python
from src.core.task_manager import set_cancelled, set_completed, set_waiting
```
- 사용자 입력 대기 시 `set_waiting(...)`.
- 정상 종료 시 `set_completed(...)`.
- 취소/실패 종료 시 `set_cancelled(...)`.
- `state.task_id`가 없으면 no-op이므로, 넣어도 안전함.

즉, 질문하신 3개 함수는 **chat 서브그래프에서는 사실상 필수 표준**으로 보는 게 맞습니다.

### 3) 세션 이벤트 로깅 규격
- 아래 import를 기본 채택.
```python
from src.core.session_logger import add_event, session_event, build_llm_meta, EventType
```
- 사용자 대기 지점: `add_event(..., EventType.HITL_WAIT, ...)`
- 재개 지점(interrupt resume): `add_event(..., EventType.HITL_RESUME, ...)`
- 완료/취소/오류: `EventType.COMPLETE`, `EventType.CANCEL`, `EventType.ERROR`
- 외부 API/LLM 호출: `async with session_event(...): ...`
- LLM 호출 시 `ev.llm = build_llm_meta(...)`로 비용/토큰 기록

즉, 위 4개도 **운영 추적/비용 정산을 위해 사실상 필수 표준**입니다.

### 4) 종료 규격
- 업무 종료 노드(완료/취소/치명 실패)에서는 반드시:
```python
state.current_agent = None
```
- 이걸 누락하면 멀티턴이 잘못 이어질 수 있음.

### 5) 그래프 진입 라우터 규격
- `START`에서 단일 라우터 함수로 진입.
- 라우터에서 최소 아래 분기는 항상 처리:
  - `state.error_message`면 종료
  - `intent_confirmed is False`면 취소 노드
  - 필수 파라미터 누락 시 파라미터 요청 노드
- 노드 내부에서 다음 노드를 직접 판단하지 말고, 라우터/조건부 엣지에서 판단.

### 6) 사용자 메시지 출력 규격
- 노드에서 직접 Google Chat API를 호출하지 말고 `state.add_ai_message(...)`만 사용.
- (예외) `interrupt()` 직전에 즉시 전송이 반드시 필요한 경우만 별도 패턴 사용.

---

## 2. 그래프 연결 규격 (실전 패턴)

### 필수 패턴
- `workflow = StateGraph(GraphState)`
- `workflow.add_conditional_edges(START, route_xxx, _NODE_MAP)`
- 사용자 입력 대기 노드 -> `END`
- 자동 처리 노드 -> 조건부 라우팅으로 다음 자동 노드 연결
- 종료 노드(완료/취소/중단) -> `END`
- 마지막 export 이름은 `{agent_name}_graph = workflow.compile()` 형태 권장

### 레지스트리 연동
- `src/agent/registry.py`에서 graph import 후 `AGENT_REGISTRY[agent_name]["graph"]`에 등록.
- 변수명/agent_id 불일치가 가장 흔한 실수이므로 반드시 동일하게 맞출 것.

---

## 3. interrupt/재진입(idempotency) 규격

`esim_fault_replacement`에서 확인된 핵심 패턴:

- `interrupt()` 전에 재개에 필요한 키를 state에 저장
  - 예: `_job_id`, `_job_sent_at`, `task_id`
- 재진입 시 중복 전송 방지 플래그 사용
  - 예: `_callback_processed`, `_job_id` 존재 체크
- 콜백/재개 이후에는 `HITL_RESUME` 이벤트 기록

권장 최소 형태:
```python
if not params.get("_request_sent"):
    _set_param(state, "_request_sent", True)
    await add_event(..., EventType.HITL_WAIT, ...)
    await set_waiting(state, result="외부 작업 대기")

payload = interrupt({"status": "waiting"})

if params.get("_callback_processed"):
    return state
_set_param(state, "_callback_processed", True)
await add_event(..., EventType.HITL_RESUME, ...)
```

---

## 4. LLM/API 호출 규격

### LLM
```python
async with session_event(state.session_id, EventType.LLM_GENERIC, "설명", task_id=state.task_id) as ev:
    llm_resp = await LLM_LIGHT.ainvoke([...])
    ev.content = str(llm_resp.content)[:200]
    ev.llm = build_llm_meta(LLM_LIGHT.model, llm_resp.usage_metadata)
```

### 외부 API
```python
async with session_event(state.session_id, EventType.API_CALL, "설명", task_id=state.task_id) as ev:
    response = await client_call(...)
    ev.content = "요약 정보"
```

실패 시 규격:
- 사용자 안내 메시지 추가
- `set_cancelled(...)` 또는 `set_completed(...)`로 상태 확정
- 종료 노드라면 `state.current_agent = None`

---

## 5. 구현 체크리스트 (복붙용)

- [ ] `AGENT_NAME` 상수 선언
- [ ] `_get_params`, `_set_param` 구현
- [ ] 모든 노드 시그니처 `async def node(state: GraphState) -> GraphState`
- [ ] `set_waiting/set_completed/set_cancelled` 적용
- [ ] `add_event/session_event/build_llm_meta/EventType` 적용
- [ ] 대기 노드에서 `HITL_WAIT` + `set_waiting`
- [ ] resume 지점에서 `HITL_RESUME`
- [ ] 완료/취소 노드에서 `state.current_agent = None`
- [ ] `START` 단일 라우터 + 조건부 엣지 구성
- [ ] 사용자 입력 대기 노드는 `END`로 종료
- [ ] graph compile export명과 registry 등록명 일치 확인

---

## 6. 참고: 필수/권장 구분

필수(운영 안정성 관점):
- `GraphState` 기반 노드 시그니처
- task 상태 업데이트 3종 (`set_waiting`, `set_completed`, `set_cancelled`)
- session 이벤트 로깅 (`add_event`, `session_event`, `EventType`)
- 완료/취소 시 `state.current_agent = None`
- START 단일 라우터 + END 경계

권장(품질/유지보수 관점):
- `_get_params`, `_set_param` 헬퍼 통일
- LLM 비용 메타(`build_llm_meta`) 기록
- interrupt 재진입 idempotency 플래그
- 라우터의 Literal 반환 타입 명시

---

## 7. 주의사항 (이 리포지터리 공통 규칙 반영)

- async 노드에서 동기 I/O를 직접 호출하지 말고 `asyncio.to_thread(...)` 또는 executor로 분리.
- 컬렉션/상태 키 이름은 기존 규격(`boa_*`, `collected_parameters[AGENT_NAME]`)을 유지.
- 기존 public 동작(메시지 흐름, task 상태, 로그 이벤트 타입)을 깨는 변경은 금지.
