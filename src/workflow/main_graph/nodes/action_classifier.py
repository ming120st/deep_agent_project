"""
의도 분류 노드 (Action Classifier Node) - 경량화 버전
라우팅만 담당. 파라미터 추출은 extract_parameters 노드에서 처리.
"""
import time
from pydantic import BaseModel
from typing import Literal, Optional
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import Runnable
import json

from src.workflow.graph_infra.state import GraphState
from src.core.llm_config import LLM_LIGHT
from src.core.router import build_routing_context

# 대화이력 남기지 않고 중간과정 노티만 보낼 때 사용
from src.core.google_chat import send_message

# ── 출력 스키마 (경량화 - parameters 제거) ────────────────────────────────────
from pydantic import Field

class ActionResult(BaseModel):
    reason: str = Field(description="해당 action으로 분류한 구체적인 이유")
    action: Literal["new_agent", "continue_task", "rag", "context_response", "help_request", "ambiguous"]
    domain_id: Optional[str] = None
    agent_name: Optional[str] = None  # new_agent일 때만 사용
    new_task: Optional[bool] = False

# ── 프롬프트 ──────────────────────────────────────────────────────────────────
action_parser = PydanticOutputParser(pydantic_object=ActionResult)
format_instructions = action_parser.get_format_instructions()

CLASSIFY_PROMPT = """
당신은 사용자의 발화를 분석하여 다음 행동을 결정하는 라우팅 AI입니다.
파라미터 추출은 하지 않습니다. 오직 어떤 행동을 취해야 할지만 분류하세요.
동일하거나 유사한 사용자 발화가 반복되었다고 하더라도 사용자의 의도 분석을 다시 진행한 뒤 분류하세요.
행동을 결정하기 전에, 반드시 'reason' 필드에 해당 action으로 분류한 구체적인 이유를 먼저 작성하세요. 특히 ambiguous로 분류할 경우, 사용자의 어떤 의도가 불분명한지 상세히 적어주세요.

[행동(action) 분류 기준]
1. new_agent: 사용자가 새로운 업무(에이전트)를 시작하려고 할 때.
   - 도메인과 에이전트 종류는 routing_context를 참고하여 분류하세요.
   - agent_name에 해당 에이전트 이름을 넣으세요.
   - 항상 new_task: true로 설정하세요.

2. continue_task: 현재 진행 중인 업무(에이전트)를 이어가는 모든 경우.
   - 파라미터 제공, AI 질문에 답변, 취소 의사 표현 등 모두 포함.
   - current_agent가 있을 때만 해당.

3. rag: 사용자가 업무 실행을 요청한 것이 아니라, 제품/서비스/정책/절차/오류 해결 방법을 질문하는 경우.
   - CS 상담 지식베이스, 매뉴얼, FAQ, 처리 절차에서 답을 찾아야 하는 질문은 rag로 분류한다.
   - ex) 사내 규정, 매뉴얼, 일반 지식 등에 대한 질문.

4. context_response: 컨텍스트에 답이 있거나 외부 지식 검색 없이도 현재 대화 맥락만으로 답변 가능한 경우.
   - ex) 단순 인사, 감정 표현 등 가벼운 대화.
   - AI의 업무 수행 결과나 분석 결과가 컨텍스트에 있고, 추가 정보 없이 답변 가능한 경우도 포함.

5. help_request: 사용자가 에이전트 사용방법을 요청하는 경우.
   - "너 뭐 할 수 있어?", "도움말 보여줘", "사용법 알려줘" 등 전체 기능 문의 → agent_name은 null
   - "이심 재발급 사용방법", "회선변경 어떻게 해?" 등 특정 에이전트 문의 → agent_name에 해당 에이전트 키 입력

6. ambiguous: 사용자의 의도가 명확하지 않거나 위 다른 분류에 속하지 않는 경우. 

[new_task 판단 기준]
- action이 new_agent일 때: new_task는 항상 True
- **중요**: action이 continue_task 라 하더라도, 기존 파라미터(현재 진행중인 상태)와 다른 "새로운 파라미터 값(예: 다른 전화번호, 다른 기기번호, 다른 주문번호 등)"이 주어지거나 "이전과 다른 내용으로 다시 해줘"와 같은 요청이 온다면 **반드시 new_task를 True로 설정**하세요.
- 동일 에이전트 진행 중이지만 새로운 파라미터로 변경 요청: True
- 진행 중인 업무 이어가기 (파라미터 변경 없이 계속 진행): False
- rag, context_response, help_request, ambiguous: False

[domain_id / agent_name]
- new_agent로 분류할 때는 routing_context에 있는 domain_id와 agent_id를 함께 반환하세요.
- continue_task로 분류할 때는 current_agent를 기준으로 같은 domain_id와 agent_name을 유지하세요.
- 특정 도메인 에이전트가 필요 없는 action에서는 domain_id와 agent_name을 null로 반환하세요.
"""

action_classify_prompt = ChatPromptTemplate.from_template(
    """
    {classify_prompt}

    <format_instructions>
    {format_instructions}
    </format_instructions>

    <messages>
    {messages}
    </messages>

    <user_query>
    {user_query}
    </user_query>

    <current_agent>
    {current_agent}
    </current_agent>

    <routing_context>
    {routing_context}
    </routing_context>
    """
)

action_classify_chain: Runnable = (
    action_classify_prompt
    | LLM_LIGHT
    | action_parser
)

from src.core.session_logger import session_event, add_event, EventType, build_llm_meta

# ── 메인 노드 ─────────────────────────────────────────────────────────────────
async def classify_agent_action(state: GraphState):
    session_id = state.session_id

    # 사용자 메시지 기록 (턴의 첫 이벤트)
    await add_event(session_id, EventType.USER_MESSAGE, "사용자 메시지 수신",
                    content=state.get_last_user_message())

    start_time = time.time()
    print(f"[Node] Start: classify_agent_action")
    user_query = state.get_last_user_message() or ""
    messages = json.dumps(state.messages, indent=2, ensure_ascii=False)
    current_agent = state.current_agent or "None"

    # MongoDB에서 enabled=True인 chat 에이전트의 이름과 설명을 가져와서 프롬프트에 포함 (action_classifier 프롬프트용)
    routing_context = await build_routing_context()

    # 의도 분류 LLM 호출
    async with session_event(session_id, EventType.LLM_INTENT, "의도 분류") as ev:
        llm_response = await (action_classify_prompt | LLM_LIGHT).ainvoke({
            "classify_prompt": CLASSIFY_PROMPT,
            "format_instructions": format_instructions,
            "messages": messages,
            "user_query": user_query,
            "current_agent": current_agent,
            "routing_context": routing_context,
        })
        result = action_parser.invoke(llm_response)
        
        ev.content = f"{result.action}:{result.domain_id}:{result.agent_name}"
        ev.llm = build_llm_meta("gemini-2.0-flash", getattr(llm_response, "usage_metadata", {}))

    end_time = time.time()
    print(f"[Node] End: classify_agent_action | Duration: {end_time - start_time:.2f}s")
    print(f"[ActionClassifier] {result}")

    action_dict = result.dict()

    return {"action": action_dict}
