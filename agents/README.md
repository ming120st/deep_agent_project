# SubAgent 추가 가이드

## 1. Agent 폴더 생성

예 : agents/<agent_name>/
---

## 2. Agent 코드 작성

예 : agents/<agent_name>/agent.py

Agent 실행 로직, 모델 호출, Tool 연결 등을 작성합니다.

---

## 3. Agent 등록 정보 작성

예 : agents/registry.py

Agent ID, 이름, 설명, Tool, 모델, Prompt 관련 등록 정보를 작성합니다.

---

## 4. Prompt 추가

기본 경로

예 : agents/<agent_name>/prompts/system.md

현재 Agent가 DB Prompt를 사용하도록 구현되어 있다면
`system.md` 대신 DB에 Prompt를 등록하고 `agent.py`에서 조회합니다.

---

## 5. Tool이 필요하면 추가

공용 Tool: tools/common/<tool_name>/

외부 서비스 / MCP 연동: tools/integrations/<integration_name>/

---

## 최종 구조 예시

```text
agents/
└─ marketing/
   ├─ prompts/
   │  └─ system.md
   ├─ agent.py
   └─ registry.py
```

필요한 경우 Tool 추가:

```text
tools/
├─ common/
└─ integrations/
```

## 추가 순서

```text
agents/<agent_name>/
        ↓
agent.py 작성
        ↓
registry.py 작성
        ↓
prompts/system.md 추가
        ↓
필요 시 tools/common/ 또는 tools/integrations/ 추가
        ↓
서버 실행 후 Agent 등록 확인
```
