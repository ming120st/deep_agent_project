# SubAgent 추가 가이드

## 1. Agent 폴더 생성
Agent 성격에 맞는 도메인 아래에 생성합니다.

```text
agents/
├─ common/
│  └─ <agent_name>/
├─ hr/
├─ finance/
└─ sales/
```
---

## 2. Agent 코드 작성

경로:

```text
agents/<domain>/<agent_name>/agent.py
```

`agent.py`에서 다음 내용을 구성합니다.

* System Prompt 조회
* Skill 준비
* Tool 연결
* Middleware 설정
* Agent 등록

예:

```python
async def register_sample_agent() -> None:
    system_prompt = await prompt_service.get(
        "deep_agent.subagent.sample_agent.system"
    )

    skills = await prompt_service.prepare_skills(
        "sample_agent"
    )

    register_agent(
        name="sample_agent",
        description="Sample Agent 설명",
        system_prompt=system_prompt,
        tools=tools,
        skills=skills,
        middleware=middleware,
    )
```
---

## 3. Prompt / Skill 등록
현재 프로젝트에서는 Prompt와 Skill을 DB에서 관리합니다.

### System Prompt

DB에 Prompt를 등록한 후 `agent.py`에서 조회합니다.

```python
system_prompt = await prompt_service.render(
    "deep_agent.subagent.<agent_name>.system",
    ...
)
```

### Skill

DB에 Skill을 등록한 후:

```python
skills = await prompt_service.prepare_skills(
    "<agent_name>"
)
```

`prepare_skills()`가 Deep Agent가 읽을 수 있도록 `.runtime/skills/`에 Skill 파일을 생성합니다.

따라서 일반적인 Agent는 디렉터리를 별도로 만들 필요가 없습니다.

---

## 4. Tool 추가

공용 Tool:

```text
tools/common/<tool_name>/
```

외부 서비스 또는 MCP 연동:

```text
tools/integrations/<integration_name>/
```

Agent 전용 Tool은 필요한 Tool만 `agent.py`에서 선택하여 연결합니다.

---

## 5. 전체 Agent 등록에 추가

Agent 등록 초기화 코드에 새 Agent를 추가합니다.

예:

예:

```python
from agents.common.sample_agent.agent import (
    register_sample_agent,
)


async def register_all_agents() -> None:
    ...
    await register_sample_agent()
```

실제 Agent 등록은 공통 Registry를 사용합니다.

```text
registry/
└─ agent_registry.py
```

Agent별로 별도의 `registry.py`를 만들 필요는 없습니다.


실제 등록 데이터는 공통 Registry를 사용합니다.

```text
registry/
└─ agent_registry.py
```

Agent별 `registry.py`를 별도로 만들 필요는 없습니다.

---

## 최종 구조 예시

```text
agents/
└─ common/
   └─ meta_ads/
      └─ agent.py

tools/
├─ common/
└─ integrations/
   └─ meta_ads_mcp/

registry/
└─ agent_registry.py

.runtime/
└─ skills/
   └─ meta_ads/
```

`.runtime/skills/`는 실행 시 생성되는 Runtime 영역이므로 소스코드로 직접 관리하지 않습니다.

---

## SubAgent 추가 순서

```text
agents/<domain>/<agent_name>/ 생성
        ↓
agent.py 작성
        ↓
DB System Prompt 등록
        ↓
필요 시 DB Skill 등록
        ↓
필요 시 Tool 구현
        ↓
register_all_agents()에 등록 함수 추가
        ↓
서버 실행
        ↓
SUBAGENT_REGISTRY 로그에서 등록 확인
```

등록 확인 예:

```text
[SUBAGENT_REGISTRY]
count=...
names=[..., '<agent_name>']
```
