# BOA 신규 에이전트 등록 체크리스트

> 새 에이전트가 생겼을 때 코드에서 하드코딩으로 손대야 하는 파일과 위치를 순서대로 정리한 문서.  
> 빠뜨리면 라우팅이 안 되거나 메시지가 Google Chat에 전송되지 않으므로 위에서부터 순서대로 확인할 것.

---

## 타입별 필수 작업 요약

| 타입 | 필수 파일 수정 | MongoDB 등록 |
|------|-------------|-------------|
| **chat** | 5곳 | 앱 재시작 시 자동 sync |
| **schedule** | 3곳 | 앱 재시작 시 자동 sync |
| **trigger** | 3곳 | 앱 재시작 시 자동 sync |

---

## chat 에이전트 (5곳)

### 1. `src/agent/registry.py` — AGENT_REGISTRY 등록

가장 중요한 파일. 여기 없으면 아무 것도 동작하지 않는다.

**① 상단 import 추가**

```python
from src.agent.{domain}.subagents.chat.{agent_name}.graph import {agent_name}_graph
```

예시:
```python
from src.agent.esim.subagents.chat.esim_early_termination.graph import esim_early_termination_graph
```

**② AGENT_REGISTRY dict에 항목 추가**

```python
AGENT_REGISTRY: dict = {
    # ... 기존 에이전트들 ...

    "{agent_name}": {
        "type":              "chat",
        "display_name":      "화면에 표시될 이름",
        "description":       "action_classifier LLM에게 전달되는 설명. 언제 이 에이전트를 써야 하는지 명확히 작성.",
        "description_short": "도움말 카드에 표시되는 짧은 설명",
        "visible":           True,   # False면 도움말 카드에 노출 안 됨
        "usage_guide":       usage_guide.{agent_name},   # usage_guide.py에 추가한 변수명
        "enabled":           True,
        "graph":             {agent_name}_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "업무 진행 의사 여부. 취소 의사 표현 시 false.",
                "required":    True,
            },
            # 에이전트에 필요한 파라미터 추가
            "{param_name}": {
                "type":        "str | int | bool | list[str]",
                "description": "parameter_extractor LLM이 추출 기준으로 삼는 설명. 구체적으로 작성.",
                "required":    True,  # 또는 False
            },
        },
    },
}
```

> **parameters 작성 팁**  
> - `description`은 LLM이 대화에서 값을 추출하는 기준이 된다. 애매하면 LLM이 엉뚱한 값을 뽑는다.  
> - `intent_confirmed`는 모든 chat 에이전트에 반드시 포함. 취소 분기의 트리거다.  
> - `bigquery_agent`처럼 LangGraph 서브그래프가 아닌 단일 노드 함수는 `"parameters"` 키를 생략해도 된다.

---

### 2. `src/agent/usage_guide.py` — 도움말 텍스트 추가

도움말 카드(`/도움말`, `showAgentHelp` 버튼)에 표시되는 텍스트.  
registry.py의 `usage_guide.{agent_name}`이 여기서 참조된다.

```python
# 파일 끝에 추가
{agent_name} = """
*{display_name} 에이전트 사용 가이드*

1. 빠른 요청 예시
{업무 의사 표현} + {필요한 파라미터}
```
{예시 메시지}
```

2. 상세설명
- 이 에이전트는 ...을 처리합니다.
- 주의사항이나 제한사항을 여기 작성.
"""
```

> schedule·trigger 타입도 `usage_guide.py`에 추가해야 한다 (visible=False여도 등록은 해야 참조 오류가 나지 않음).

---

### 3. `src/agent/{domain}/graph.py` — 도메인 그래프에 서브에이전트 등록

**기존 도메인(esim·wifi 등)에 추가하는 경우**

```python
# 상단 import 추가
from src.agent.{domain}.subagents.chat.{agent_name}.graph import {agent_name}_graph

# ESIM_AGENT_NODES (또는 해당 도메인의 노드 딕셔너리)에 추가
{DOMAIN}_AGENT_NODES = {
    # ... 기존 항목 ...
    "{agent_name}": "{agent_name}",
}

# route_{domain}_domain 함수의 Literal 타입 힌트에 추가
def route_{domain}_domain(state: GraphState) -> Literal[
    # ... 기존 항목 ...
    "{agent_name}",
    "end",
]:
    ...

# workflow에 노드 추가
workflow.add_node("{agent_name}", {agent_name}_graph)

# conditional_edges 매핑에 추가
workflow.add_conditional_edges(
    START,
    route_{domain}_domain,
    {
        # ... 기존 항목 ...
        "{agent_name}": "{agent_name}",
        "end": END,
    },
)

# edge 추가
workflow.add_edge("{agent_name}", END)
```

**새 도메인을 만드는 경우**

`src/agent/{new_domain}/graph.py`를 새로 생성하고, 위 패턴대로 작성한 뒤 컴파일 변수 이름을 `{new_domain}_domain_graph`로 맞춰야 한다.  
`src/workflow/main_graph/graph.py`의 `build_main_graph()`가 `agent/` 하위 디렉토리를 자동 스캔하므로 **파일만 만들면 main graph에는 자동 등록된다.**

```
# 자동 스캔 조건
src/agent/{domain_name}/graph.py  존재
graph.py 안에 {domain_name}_domain_graph 변수 존재
```

---

### 4. `src/agent/subagents/chat/{agent_name}/graph.py` — 에이전트 그래프 작성

새 에이전트의 핵심 파일. 기존 에이전트를 복사해서 수정하는 게 가장 빠르다.

체크리스트:
- [ ] `AGENT_NAME = "{agent_name}"` 상수 정의
- [ ] 진입 라우터 함수 이름: `route_{agent_name}()`
- [ ] 진입 라우터 Literal 타입 힌트에 모든 노드명 + `"end"` 포함
- [ ] `_NODE_MAP`에 `"end": END` 포함
- [ ] 사용자 입력 대기 노드는 반드시 `workflow.add_edge("{node}", END)`
- [ ] 자동 진행 노드는 `workflow.add_conditional_edges()`로 분기
- [ ] 컴파일 변수명: `{agent_name}_graph = workflow.compile()`

**진입 라우터 작성 원칙**

```python
def route_{agent_name}(state: GraphState):
    params = state.collected_parameters.get(AGENT_NAME, {})

    if state.error_message:
        return "end"

    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 각 단계를 collected_parameters의 키 존재 여부로 판단
    # "키 없음 = 아직 안 된 단계"
    if not params.get("필수파라미터"):
        return "request_필수파라미터"

    if "중간결과키" not in params:
        return "fetch_중간결과"

    # HITL 대기 중 판단 (목록 표시 후 사용자 응답 대기)
    if params.get("list_shown") and "selected_item" not in params:
        return "validate_selection"

    if "최종결과키" not in params:
        return "execute_action"

    return "end"
```

---

### 5. `src/agent/subagents/chat/{agent_name}/node.py` — 노드 함수 작성

체크리스트:
- [ ] `AGENT_NAME = "{agent_name}"` 상수 정의 (graph.py와 동일하게)
- [ ] `_get_params()`, `_set_param()` 헬퍼 함수 정의
- [ ] 모든 노드 함수는 `async def node_name(state: GraphState) -> GraphState:` 시그니처
- [ ] 사용자 입력 대기 노드: `state.add_ai_message()` + `set_waiting()` 호출
- [ ] 업무 완료 노드: `set_completed()` + `state.current_agent = None`
- [ ] 업무 취소/실패 노드: `set_cancelled()` + `state.current_agent = None`
- [ ] 자동 진행 완료를 알리는 플래그: `_set_param(state, "결과키", 값)`
- [ ] 마지막에 반드시 `return state`

**노드 종료 처리 패턴**

```python
# 사용자 입력 대기 노드
async def request_something(state: GraphState) -> GraphState:
    state.add_ai_message("...")
    await set_waiting(state, result="입력 요청 설명")
    return state

# 자동 진행 노드 (성공)
async def fetch_something(state: GraphState) -> GraphState:
    # ...처리...
    _set_param(state, "result_key", result_value)  # ← 완료 플래그
    return state

# 자동 진행 노드 (실패 → 업무 종료)
async def fetch_something(state: GraphState) -> GraphState:
    # ...실패...
    state.add_ai_message("오류 메시지")
    await set_cancelled(state, result="실패 이유")
    state.current_agent = None
    return state

# 업무 완료 노드
async def execute_action(state: GraphState) -> GraphState:
    # ...처리...
    state.add_ai_message("완료 메시지")
    _set_param(state, "completed", True)  # 재진입 시 "end" 분기용
    await set_completed(state, result="완료 설명")
    state.current_agent = None
    return state
```

---

## schedule 에이전트 (3곳)

### 1. `src/agent/registry.py` — SCHEDULE_REGISTRY 등록

```python
# 상단 import 추가
from src.agent.{domain}.subagents.schedule.{agent_name}.runner import run_{agent_name}

SCHEDULE_REGISTRY: dict = {
    "{agent_name}": {
        "type":             "schedule",
        "display_name":     "표시 이름",
        "description":      "설명",
        "description_short": "짧은 설명",
        "visible":          True,
        "usage_guide":      usage_guide.{agent_name},
        "enabled":          True,
        "cron":             {"hour": 8, "minute": 0},  # APScheduler cron 파라미터
        "func":             run_{agent_name},
        "target":           "발송 대상 설명",
    },
}
```

### 2. `src/agent/usage_guide.py` — 도움말 텍스트 추가

chat 에이전트와 동일.

### 3. `src/agent/{domain}/subagents/schedule/{agent_name}/runner.py` — 실행 함수 작성

```python
async def run_{agent_name}():
    # 스케줄 시간에 실행될 실제 로직
    ...
```

---

## trigger 에이전트 (3곳)

### 1. `src/agent/registry.py` — TRIGGER_REGISTRY 등록

```python
# 상단 import 추가
from src.{모듈경로} import {trigger_func}

TRIGGER_REGISTRY: dict = {
    "{agent_name}": {
        "type":             "trigger",
        "display_name":     "표시 이름",
        "description":      "설명",
        "description_short": "짧은 설명",
        "visible":          False,   # 트리거는 보통 False
        "usage_guide":      usage_guide.{agent_name},
        "enabled":          True,
        "interval":         {"minutes": 10},  # APScheduler interval 파라미터
        "func":             {trigger_func},
        "target":           "사용자 인터랙션 없음",
    },
}
```

### 2. `src/agent/usage_guide.py` — 도움말 텍스트 추가

### 3. 실행 함수 파일 — 트리거 로직 작성

```python
async def {trigger_func}():
    # 주기마다 실행될 실제 로직
    ...
```

---

## MongoDB 자동 sync 동작 방식

앱 재시작(`lifespan`) 시 `sync_registry_to_mongo()`가 자동으로 호출되어 아래 두 컬렉션을 갱신한다.

| 컬렉션 | 역할 |
|--------|------|
| `boa_domain_registry` | 도메인 목록 (esim, wifi, ...) |
| `boa_agent_registry` | 에이전트 목록 및 enabled 상태 |

**주의:** `enabled` 필드는 DB 값을 우선으로 보존한다. 코드에서 `True`로 써도 DB에 `False`가 있으면 `False`가 유지된다. 새 에이전트를 처음 배포할 때는 DB에 문서가 없으므로 코드의 `"enabled": True`가 그대로 적용된다.

`action_classifier`가 사용하는 라우팅 컨텍스트는 `boa_domain_registry` + `boa_agent_registry`에서 동적으로 읽으며 **5분 캐시**된다. 배포 직후 반영이 안 되면 캐시 만료(5분) 후 자연히 반영된다.

---

## 자주 빠뜨리는 실수 TOP 5

| # | 실수 | 증상 |
|---|------|------|
| 1 | `domain/graph.py`에 에이전트 미등록 | `[ResponseSender] 전송할 AI 메시지 없음` |
| 2 | 진입 라우터 `_NODE_MAP`에 `"end": END` 누락 | LangGraph 컴파일 오류 |
| 3 | 완료 노드에서 `state.current_agent = None` 누락 | 업무 끝났는데 다음 발화에서 같은 에이전트 재진입 |
| 4 | 자동 진행 노드에서 완료 플래그(`_set_param`) 미설정 | 재진입 시 같은 노드 무한 재실행 |
| 5 | `usage_guide.py`에 변수 추가 안 하고 `registry.py`에서 참조 | `AttributeError` on startup |

---

## 파일 경로 치트시트

```
src/
├── agent/
│   ├── registry.py                          ← ✅ AGENT_REGISTRY 등록
│   ├── usage_guide.py                       ← ✅ 도움말 텍스트 추가
│   ├── sync_registry.py                     (건드리지 않음, 자동 실행)
│   └── {domain}/
│       ├── graph.py                         ← ✅ 도메인 그래프에 서브에이전트 등록
│       └── subagents/
│           └── chat/
│               └── {agent_name}/
│                   ├── graph.py             ← ✅ 에이전트 그래프 작성
│                   └── node.py              ← ✅ 노드 함수 작성
└── workflow/
    └── main_graph/
        └── graph.py                         (건드리지 않음, domain/ 자동 스캔)
```

---

## 관련 문서

- `src/agent/subagent_dev_playbook.md` — 노드·그래프 작성 상세 패턴
- `src/agent/subgraph_convention.md` — 서브그래프 컨벤션
- `src/core/세션로깅가이드.md` — session_logger 적용 가이드
