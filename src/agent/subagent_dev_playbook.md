# BOA 서브에이전트 개발 가이드

## 1. 개요

BOA 서브에이전트는 세 가지 타입으로 구분된다.

| 타입 | 트리거 | 설명 | 등록 위치 |
|------|--------|------|----------|
| **chat** | Google Chat 메시지 | 사용자의 메시지를 받아 멀티턴 대화로 업무를 처리 | `AGENT_REGISTRY` |
| **schedule** | cron | 지정한 시간에 자동으로 실행 (일일 리포트 등) | `SCHEDULE_REGISTRY` |
| **trigger** | interval | 일정 주기마다 조건을 체크하여 실행 | `TRIGGER_REGISTRY` |

아키텍처(main graph, orchestrator, state, task_manager 등)는 기존 인프라를 그대로 사용한다.
**서브에이전트 개발자가 작성하는 파일은 타입별로 다르다.**

| 타입 | 작성 파일 |
|------|----------|
| chat | `graph.py` + `node.py` |
| schedule | 실행 함수 파일 1개 (`runner.py` 등) |
| trigger | 실행 함수 파일 1개 (`check_xxx.py` 등) |

이 문서는 **세 가지 타입 모두**를 다룬다. chat 타입이 가장 복잡하므로 분량이 많고, schedule/trigger는 별도 섹션으로 정리한다.

### 핵심 원칙 (chat 타입)
- **서브에이전트는 판단하지 않는다.** 파라미터 수집 → 실행 → 결과 전달이 전부다.
- **라우터가 모든 분기를 담당한다.** 노드 안에서 if/else로 다음 노드를 결정하지 않는다.
- **노드 안에서 메시지를 직접 Google Chat으로 보내지 않는다.** `state.add_ai_message()`로 추가하면 `send_response` 노드가 자동으로 전송한다. (단, `interrupt()` 전 즉시 전송이 필요한 경우는 예외 — 7장 참고)
- **업무 종료 노드에서는 반드시 `state.current_agent = None`을 설정한다.**
- **이중 실행 방지 플래그를 사용한다.** 자동 진행 노드는 실행 후 state에 완료 플래그를 저장한다.

---

## 2. 파일 구조

```
src/agent/
├── chat/
│   └── {agent_name}/
│       ├── graph.py        # 그래프 구성 (노드 등록, 엣지, 라우터)
│       └── node.py         # 노드 함수 구현
│
├── schedule/
│   └── {schedule_name}/
│       └── runner.py       # 실행 함수 (async def run() 하나만 외부 노출)
│
└── trigger/
    └── check_{name}.py     # 실행 함수 (async def {name}() 하나만 외부 노출)
```

### 신규 chat 에이전트 추가 체크리스트
1. `src/agent/chat/{agent_name}/` 폴더 생성
2. `node.py` 작성
3. `graph.py` 작성
4. `src/agent/registry.py`의 `AGENT_REGISTRY`에 등록
5. WORKFLOW.md의 reason 키값과 `_error_message()` 매핑 일치 여부 확인 (DeviceJob 연동 시만)

### 신규 schedule 에이전트 추가 체크리스트
1. `src/agent/schedule/{schedule_name}/runner.py` 작성
2. `src/agent/registry.py`의 `SCHEDULE_REGISTRY`에 등록

### 신규 trigger 에이전트 추가 체크리스트
1. `src/agent/trigger/check_{name}.py` 작성
2. `src/agent/registry.py`의 `TRIGGER_REGISTRY`에 등록

---

## 3. graph.py 패턴

그래프는 **진입점 라우터 하나**로 시작한다. 라우터가 state를 보고 다음 노드를 결정한다.

### 기본 구조

```python
# src/agent/chat/{agent_name}/graph.py

from typing import Literal
from langgraph.graph import StateGraph, START, END
from src.workflow.graph_infra.state import GraphState
from src.agent.chat.{agent_name}.node import (
    ask_something,
    do_something,
    handle_result,
    cancel_inquiry,
)

AGENT_NAME = "{agent_name}"


def route_{agent_name}(state: GraphState) -> Literal[
    "ask_something",
    "do_something",
    "handle_result",
    "cancel_inquiry",
    "end",
]:
    params   = state.collected_parameters.get(AGENT_NAME, {})
    response = state.response

    # 에러 발생 시 즉시 종료
    if state.error_message:
        return "end"

    # 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 파라미터 수집 단계 (순서대로)
    if not params.get("param_a"):
        return "ask_something"

    # 자동 실행 단계
    if "result_data" not in response:
        return "do_something"

    # 결과 처리
    if not params.get("result_sent"):
        return "handle_result"

    return "end"


# ── 그래프 구성 ───────────────────────────────────────────────────────────────

_NODE_MAP = {
    "ask_something":  "ask_something",
    "do_something":   "do_something",
    "handle_result":  "handle_result",
    "cancel_inquiry": "cancel_inquiry",
    "end":            END,
}

workflow = StateGraph(GraphState)

workflow.add_node("ask_something",  ask_something)
workflow.add_node("do_something",   do_something)
workflow.add_node("handle_result",  handle_result)
workflow.add_node("cancel_inquiry", cancel_inquiry)

# 진입점: 라우터 → 노드
workflow.add_conditional_edges(START, route_{agent_name}, _NODE_MAP)

# 사용자 입력 대기 노드 → END (다음 메시지에서 라우터가 재진입)
workflow.add_edge("ask_something",  END)
workflow.add_edge("cancel_inquiry", END)

# 자동 진행 노드 → 라우터 재진입 (사용자 입력 없이 연속 실행)
workflow.add_conditional_edges("do_something", route_{agent_name}, _NODE_MAP)

# 결과 노드 → END
workflow.add_edge("handle_result", END)

{agent_name}_graph = workflow.compile()
```

### 엣지 설계 규칙

| 노드 유형 | 엣지 연결 | 이유 |
|----------|----------|------|
| 사용자 입력 대기 노드 | → END | 다음 사용자 메시지가 오면 라우터가 다시 진입 |
| 자동 진행 노드 | → 라우터 재진입 | 사용자 입력 없이 다음 단계로 연속 실행 |
| 업무 종료 노드 (완료/취소/오류) | → END | 멀티턴 종료 |

---

## 4. node.py 패턴

### 기본 구조

```python
# src/agent/chat/{agent_name}/node.py

from src.workflow.graph_infra.state import GraphState
from src.core.task_manager import set_waiting, set_completed, set_cancelled

AGENT_NAME = "{agent_name}"


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _get_params(state: GraphState) -> dict:
    """이 에이전트의 파라미터만 꺼낸다."""
    return state.collected_parameters.get(AGENT_NAME, {})


def _set_param(state: GraphState, key: str, value) -> None:
    """이 에이전트의 파라미터 하나를 저장한다."""
    cp = dict(state.collected_parameters)
    if AGENT_NAME not in cp:
        cp[AGENT_NAME] = {}
    cp[AGENT_NAME] = {**cp[AGENT_NAME], key: value}
    state.collected_parameters = cp
```

### ① 사용자 입력 대기 노드

파라미터 하나를 요청하고 종료. 다음 사용자 메시지가 오면 `parameter_extractor`가 파라미터를 추출해서 `collected_parameters`에 저장한다.

```python
async def ask_something(state: GraphState) -> GraphState:
    state.add_ai_message(
        "필요한 정보를 입력해주세요."
    )
    await set_waiting(state, result="정보 입력 요청")
    return state
```

### ② 자동 진행 노드 (API 호출 등)

사용자 입력 없이 자동으로 실행. 결과를 `state.response`에 저장한다.
성공/실패 여부를 `state.response`에 담고 메시지 생성은 handle_result에 맡긴다.

```python
async def do_something(state: GraphState) -> GraphState:
    params = _get_params(state)

    try:
        result = await some_api_call(params.get("param_a"))
        state.response.update({
            "result_data": result,
            "success": True,
        })
    except Exception as e:
        state.response.update({
            "success": False,
            "reason": str(e),
        })

    return state
```

### ③ 결과 처리 노드

`state.response`의 값을 읽어 사용자에게 메시지를 만든다.
성공/실패 분기는 여기서 처리한다.

```python
async def handle_result(state: GraphState) -> GraphState:
    result = state.response

    if not result.get("success"):
        reason = result.get("reason", "UNKNOWN")
        state.add_ai_message(f"⚠️ 처리 중 오류가 발생했습니다.\n사유: {reason}")
        await set_cancelled(state, result=f"처리 실패: {reason}")
    else:
        data = result.get("result_data")
        state.add_ai_message(f"✅ 처리가 완료되었습니다.\n결과: {data}")
        await set_completed(state, result="처리 완료")
        _set_param(state, "result_sent", True)  # 이중 실행 방지

    state.current_agent = None
    return state
```

### ④ 취소 노드

```python
async def cancel_inquiry(state: GraphState) -> GraphState:
    state.add_ai_message("업무가 취소되었습니다. 다른 문의가 있으시면 말씀해주세요.")
    await set_cancelled(state, result="사용자 취소 의사")
    state.current_agent = None
    return state
```

---

## 5. 라우터 설계 원칙

라우터는 **state를 읽어서 다음 노드 이름을 반환하는 순수 함수**다. 부작용이 없어야 한다.

### 라우터 작성 순서 (반드시 이 순서로)

```python
def route_{agent_name}(state: GraphState):
    params   = state.collected_parameters.get(AGENT_NAME, {})
    response = state.response

    # 1. 에러/종료 조건 먼저
    if state.error_message:
        return "end"

    # 2. 취소 의사
    if params.get("intent_confirmed") is False:
        return "cancel_inquiry"

    # 3. 파라미터 수집 (순서대로 - 앞 파라미터가 없으면 뒤로 진행 안 됨)
    if not params.get("param_a"):
        return "ask_param_a"

    if not params.get("param_b"):
        return "ask_param_b"

    # 4. 자동 실행 단계 (response에 결과가 없으면 실행)
    if "result_data" not in response:
        return "do_something"

    # 5. 이중 실행 방지 플래그로 결과 전송 여부 판단
    if not params.get("result_sent"):
        return "handle_result"

    # 6. 모든 단계 완료
    return "end"
```

### 이중 실행 방지 패턴

자동 진행 노드와 결과 노드는 재진입 시 다시 실행되지 않도록 **완료 플래그**를 state에 저장한다.

```python
# 라우터에서 체크
if not params.get("email_sent"):
    return "send_email"

# 노드에서 플래그 저장
async def send_email(state: GraphState) -> GraphState:
    ...
    _set_param(state, "email_sent", True)  # ← 재진입 방지
    return state
```

---

## 6. task_manager 연동

모든 노드는 상태 변경 시 `task_manager` 함수를 호출한다. `state.task_id`가 없으면 자동으로 스킵된다.

| 함수 | 호출 시점 |
|------|----------|
| `set_waiting(state, result="...")` | 사용자 입력 대기 노드 끝 |
| `set_completed(state, result="...")` | 업무 성공 완료 시 |
| `set_cancelled(state, result="...")` | 취소 또는 실패 시 |

```python
# 대기
await set_waiting(state, result="주문번호 입력 요청")

# 완료
await set_completed(state, result=f"처리 완료: {iccid}")

# 취소/실패
await set_cancelled(state, result="사용자 취소 의사")
```

---

## 7. DeviceJob 연동 패턴 (로컬 PC 위임)

로컬 PC의 device_agent에 작업을 위임해야 할 때 사용한다.
`interrupt()`를 사용하며, **재실행 방지를 위한 job_id 저장이 필수**다.

### 동작 원리

LangGraph는 `Command(resume=...)`으로 그래프를 재개할 때, `interrupt()`가 있던 노드를 **처음부터 다시 실행**한다.
따라서 `interrupt()` 전에 job_id를 state에 저장해두지 않으면 device_agent에 작업이 두 번 전송된다.

```
[1차 실행]
request_device_job 노드 시작
  → _job_id 없음 → UUID 생성 → state에 저장 → device_agent 전송
  → 즉시 Google Chat에 "전달했습니다" 메시지 전송 (send_message 직접 호출)
  → interrupt() → 일시정지

[콜백 도착 → Command(resume=callback)]

[2차 실행 - LangGraph 재실행]
request_device_job 노드 처음부터 시작
  → _job_id 있음 → 전송 블록 스킵 ✅
  → interrupt() → callback 값 반환하고 통과
  → handle_result 실행
```

### 구현 예시

```python
# node.py - request_device_job 노드

import os
import httpx
from langgraph.types import interrupt
from src.core.google_chat import send_message
from src.workflow.device_job.schemas import DeviceJobRequest, DeviceJobCallback
from src.core.device_job_manager import create_device_job

DEVICE_JOB_URL    = os.getenv("DEVICE_JOB_URL", "")
DEVICE_JOB_SECRET = os.getenv("DEVICE_JOB_SECRET", "")


async def request_device_job(state: GraphState) -> GraphState:
    params    = _get_params(state)
    thread_id = state.chat_context.get("user_id", "")

    # ── LangGraph 재실행 방지 ─────────────────────────────────────────────────
    job_id = params.get("_job_id")

    if not job_id:
        req = DeviceJobRequest(
            thread_id=thread_id,
            task="{job_task_name}",     # device_agent JOB_DISPATCH 키와 일치
            parameters={
                "param_a": params.get("param_a", ""),
            },
            context={"user_name": state.chat_context.get("user_name")},
        )
        job_id = req.job_id

        # ★ interrupt() 전에 반드시 저장
        _set_param(state, "_job_id", job_id)

        await create_device_job(job_id, thread_id)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{DEVICE_JOB_URL}/device-job",
                    json=req.model_dump(),
                    headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                    timeout=10.0,
                )
                resp.raise_for_status()
        except Exception as e:
            state.add_ai_message(f"⚠️ PC 에이전트 연결에 실패했습니다.\n({e})")
            await set_cancelled(state, result=f"DeviceJob 전송 실패: {e}")
            state.current_agent = None
            return state

        # interrupt() 전에 직접 전송 (interrupt 이후엔 send_response가 실행되지 않음)
        space_name = state.chat_context.get("space_name", "")
        await send_message(
            space_name,
            "⏳ 작업을 PC에이전트에 전달했습니다.\n완료되면 결과를 알려드릴게요!"
        )
        await set_waiting(state, result=f"DeviceJob 대기 중: {job_id}")

    # interrupt — /device-callback 수신 시 자동 재개
    callback_payload: dict = interrupt({"job_id": job_id, "status": "waiting"})

    # ── 재개 후 이 라인부터 실행 ──────────────────────────────────────────────
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "error":
        # state.response에 담아서 handle_result가 처리
        state.response.update({"success": False, "reason": callback.error_message or "UNKNOWN"})
    else:
        state.response.update(callback.result)

    state.current_agent = None
    return state
```

### handle_result에서 결과 처리

DeviceJob 에이전트의 handle_result는 `state.response`를 읽어 메시지를 생성한다.
reason 키값은 **device_agent의 반환값과 정확히 일치**해야 한다.

```python
async def handle_result(state: GraphState) -> GraphState:
    result = state.response

    if not result.get("success"):
        reason = result.get("reason", "UNKNOWN")
        state.add_ai_message(_error_message(reason))
        await set_cancelled(state, result=f"처리 실패: {reason}")
    else:
        state.add_ai_message(
            f"✅ 처리가 완료되었습니다!\n"
            f"• 결과: {result.get('some_field', '')}"
        )
        await set_completed(state, result="처리 완료")

    state.current_agent = None
    return state


def _error_message(reason: str) -> str:
    """reason → 사용자 메시지 매핑. device_agent 반환값과 반드시 일치해야 함."""
    messages = {
        "LOGIN_FAILED":                  "⚠️ 시스템 로그인에 실패했습니다.",
        "Device Status: Manual shutdown": "⚠️ 기기가 꺼져 있습니다. 기기를 켠 후 다시 시도해주세요.",
        "NO_SIM":                        "⚠️ 사용 가능한 SIM을 찾을 수 없습니다.",
    }
    return messages.get(reason, f"⚠️ 처리 중 오류가 발생했습니다.\n사유: {reason}\nAI팀에 문의해주세요.")
```

### DeviceJob 그래프 엣지

```python
# graph.py - DeviceJob 에이전트

# request_device_job은 interrupt 후 handle_result로 직행
workflow.add_edge("request_device_job", "handle_result")
workflow.add_edge("handle_result", END)
```

---

## 8. AGENT_REGISTRY 등록

`src/agent/registry.py`에 에이전트를 등록한다.
`description`은 LLM이 어떤 에이전트를 호출할지 판단하는 기준이다. 상세하고 정확하게 작성한다.

```python
# registry.py

from src.agent.chat.{agent_name}.graph import {agent_name}_graph

AGENT_REGISTRY: dict = {
    ...
    "{agent_name}": {
        "type":         "chat",
        "display_name": "에이전트 표시 이름",
        "description":  "이 에이전트가 처리하는 업무를 상세히 설명. "
                        "어떤 요청에 사용되는지 예시를 포함. "
                        "예) 와이파이 회선변경, SIM 교체 요청을 처리하는 에이전트.",
        "enabled":      True,
        "graph":        {agent_name}_graph,
        "parameters": {
            "intent_confirmed": {
                "type":        "bool",
                "description": "업무 진행 의사 여부. 취소 의사 시 false.",
                "required":    True,
            },
            "param_a": {
                "type":        "str",
                "description": "파라미터 설명. LLM이 사용자 메시지에서 추출하는 기준이 됨.",
                "required":    True,
            },
            "param_b": {
                "type":        "str",
                "description": "선택 파라미터 설명.",
                "required":    False,
            },
        },
    },
}
```

### parameters 작성 규칙

- `intent_confirmed`는 **모든 에이전트에 필수**로 포함한다.
- `description`은 LLM이 사용자 메시지에서 값을 추출하는 기준이다. 구체적인 형식과 예시를 포함한다.
- `required: True`인 파라미터가 수집되지 않으면 라우터가 해당 요청 노드로 라우팅한다.
- `required: False`인 파라미터는 사용자가 제공하지 않아도 흐름이 진행된다.

---

## 9. GraphState 주요 필드

에이전트 개발 시 사용하는 state 필드 목록.

| 필드 | 타입 | 설명 |
|------|------|------|
| `state.collected_parameters[AGENT_NAME]` | dict | 이 에이전트의 수집된 파라미터 |
| `state.response` | dict | 자동 실행 노드의 결과 저장소 |
| `state.chat_context["user_id"]` | str | 사용자 이메일 (thread_id로 사용) |
| `state.chat_context["user_name"]` | str | 사용자 표시 이름 |
| `state.chat_context["space_name"]` | str | Google Chat space 식별자 |
| `state.current_agent` | str \| None | 현재 활성 에이전트. 업무 종료 시 None으로 초기화 |
| `state.task_id` | str \| None | 현재 task 식별자 (task_manager 연동) |
| `state.error_message` | str \| None | 에러 발생 시 라우터가 "end"로 분기 |

---

## 10. WORKFLOW.md 작성 규칙

에이전트 폴더에 `WORKFLOW.md`를 작성하면 AI가 코딩 전 이 문서를 읽고 구현한다.
**스텝 흐름과 파라미터 목록을 최대한 구체적으로 작성할수록 코드 품질이 높아진다.**

```markdown
# {에이전트 이름} 워크플로우

## 목적
한 문장으로 이 에이전트가 처리하는 업무를 설명한다.

## 멀티턴 흐름
사용자와 몇 턴의 대화가 오가는지, 각 턴에서 무엇을 묻고 무엇을 받는지 기술한다.

예)
1. 에이전트: 주문번호를 요청
2. 사용자: 주문번호 입력
3. 에이전트: (자동) 주문 조회 → 상품 목록 표시, 재발송 대상 선택 요청
4. 사용자: 재발송 대상 선택
5. 에이전트: (자동) 재발송 API 호출 → 결과 메시지

## 파라미터 목록
| 파라미터명 | 타입 | 필수 | 설명 | 예시 |
|-----------|------|------|------|------|
| intent_confirmed | bool | 필수 | 업무 진행 의사 | true |
| order_id | str | 필수 | E로 시작하는 주문번호 | E202501010001 |

## 노드 목록
| 노드명 | 유형 | 설명 |
|-------|------|------|
| ask_order_id | 대기 | 주문번호 요청 |
| fetch_order | 자동 | 주문 조회 API 호출 |
| show_product_list | 대기 | 상품 목록 표시 및 선택 요청 |
| resend_esim | 자동+종료 | 재발송 API 호출 후 결과 메시지 |
| cancel_inquiry | 종료 | 업무 취소 |

## API 정보 (자동 진행 노드에서 호출)
- 엔드포인트: POST https://api.example.com/v1/order/resend
- 인증: esim_client 사용 (기존 구현 참고)
- 요청 파라미터: orderDetailId, email, mobile
- 응답: { resultCode: bool, resultMessage: str }

## DeviceJob 정보 (device_agent 위임 시만 작성)
- task 키값: wifi_sim_change
- 파라미터: imei, country_code, expiry_date
- 성공 반환: { success: true, changed_iccid: str, plan_name: str }
- 실패 reason 목록 (BOA _error_message()와 합의된 값):
  - LOGIN_FAILED
  - Device Status: Manual shutdown
  - NO_SIM
  - ALL_ICCID_OCCUPIED
```

---

## 11. schedule 에이전트 패턴

지정한 시간(cron)에 자동으로 실행되는 에이전트다. 사용자 입력이 없고 Google Chat으로 결과를 직접 전송한다.

### 구조 규칙
- `async def run()` 함수 하나만 외부에 노출
- 발송 대상은 `boa_members` / `boa_spaces` 컬렉션에서 조회하거나 고정값으로 설정
- 예외가 발생해도 다른 수신자 발송에 영향을 주지 않도록 try/except로 개별 처리

### 예시

```python
# src/agent/schedule/daily_report/runner.py

from src.core.google_chat import send_message
from src.core.mongo_client import boa_members_collection

async def run() -> None:
    """매일 오전 8시 실행. 리포트 데이터 조회 후 대상자에게 DM 발송."""

    try:
        data = await fetch_report_data()
    except Exception as e:
        print(f"[daily_report] 데이터 조회 실패: {e}")
        return

    # settings.daily_report == True인 멤버에게 발송
    cursor  = boa_members_collection.find({"settings.daily_report": True})
    members = await cursor.to_list(length=None)

    for member in members:
        try:
            await send_message(member["space_name"], _build_message(data))
        except Exception as e:
            print(f"[daily_report] 발송 실패: {member['user_id']} / {e}")


def _build_message(data: dict) -> str:
    return f"📊 일일 리포트\n오늘 판매: {data.get('sales', 0)}건"
```

### SCHEDULE_REGISTRY 등록

```python
SCHEDULE_REGISTRY: dict = {
    "daily_report": {
        "type":         "schedule",
        "display_name": "일일 리포트",
        "description":  "매일 오전 8시 영업 리포트를 구성원에게 발송합니다.",
        "enabled":      True,
        "cron":         {"hour": 8, "minute": 0},   # APScheduler cron 파라미터
        "func":         daily_report_run,
        "target":       "daily_report 설정된 구성원 DM",
    },
}
```

---

## 12. trigger 에이전트 패턴

일정 주기(interval)마다 조건을 체크하여 조건이 충족되면 알림을 보내는 에이전트다.

### 구조 규칙
- `async def {name}()` 함수 하나만 외부에 노출
- **조건 먼저 체크 → 충족된 경우에만 발송.** 아무 조건도 없으면 return
- 주기가 짧은 trigger는 실행 시간이 짧아야 함 (DB 조회 최소화)

### 예시

```python
# src/agent/trigger/check_stale_docs.py

from datetime import datetime, timedelta
from src.core.google_chat import send_message
from src.core.mongo_client import boa_knowledge_collection, boa_members_collection

async def check_stale_docs() -> None:
    """24시간마다 실행. 검토 기한이 지난 문서 담당자에게 알림."""

    threshold  = datetime.utcnow() - timedelta(days=30)
    cursor     = boa_knowledge_collection.find({"review_due": {"$lt": threshold}, "reviewed": False})
    stale_docs = await cursor.to_list(length=None)

    if not stale_docs:
        return  # 조건 미충족 → 종료

    for doc in stale_docs:
        member = await boa_members_collection.find_one({"user_id": doc.get("owner_id")})
        if not member:
            continue
        try:
            await send_message(
                member["space_name"],
                f"📋 지식베이스 문서 검토가 필요합니다.\n문서: {doc['title']}"
            )
        except Exception as e:
            print(f"[check_stale_docs] 발송 실패: {doc.get('owner_id')} / {e}")
```

### TRIGGER_REGISTRY 등록

```python
TRIGGER_REGISTRY: dict = {
    "stale_docs": {
        "type":         "trigger",
        "display_name": "지식베이스 검토 요청",
        "description":  "검토 기한이 지난 문서를 매일 확인하여 담당자에게 검토를 요청합니다.",
        "enabled":      True,
        "interval":     {"hours": 24},   # APScheduler interval 파라미터
        "func":         check_stale_docs,
        "target":       "문서 담당자 DM",
    },
}
```

---

## 13. 자주 발생하는 실수

### ① handle_result를 통하지 않고 노드 중간에 메시지 추가 후 return

```python
# ❌ 잘못된 패턴
async def request_device_job(state):
    ...
    if callback.status == "error":
        state.add_ai_message("⚠️ 오류 발생")   # 메시지 추가
        return state                             # return

    # handle_result가 실행되어 state.response = {} → reason = "UNKNOWN"
```

```python
# ✅ 올바른 패턴
async def request_device_job(state):
    ...
    if callback.status == "error":
        state.response.update({"success": False, "reason": callback.error_message})
        # return 하지 않고 handle_result가 메시지 처리
    return state
```

### ② interrupt() 전에 _job_id를 저장하지 않음 → device_agent에 두 번 전송

LangGraph는 `Command(resume=...)`으로 재개 시 노드를 **처음부터** 다시 실행한다.
`_job_id`를 저장하지 않으면 재실행 시 새 UUID가 생성되어 device_agent에 두 번 전송된다.

### ③ 업무 종료 노드에서 state.current_agent = None 누락

`current_agent`가 남아있으면 다음 사용자 메시지가 새 업무인데도 이 에이전트로 라우팅된다.
완료/취소/실패 노드 마지막에 **반드시** `state.current_agent = None`을 설정한다.

### ④ 이중 실행 방지 플래그 누락

자동 진행 노드가 라우터 재진입 후 다시 실행된다.
API를 두 번 호출하거나 이메일이 두 번 발송되는 문제가 생긴다.
실행 후 `_set_param(state, "xxx_sent", True)` 플래그를 저장하고 라우터에서 체크한다.

### ⑤ parameters의 description을 불명확하게 작성

`description`은 `parameter_extractor` LLM이 사용자 메시지에서 값을 추출하는 기준이다.
"주문번호" 대신 "E로 시작하는 인트라넷 주문번호 (예: E202501010001)"처럼 형식과 예시를 명확히 작성한다.
