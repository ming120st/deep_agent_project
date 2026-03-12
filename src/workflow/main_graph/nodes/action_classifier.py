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
from src.agent.registry import AGENT_REGISTRY
from src.agent.registry import get_enabled_agent_descriptions

# 대화이력 남기지 않고 중간과정 노티만 보낼 때 사용
from src.core.google_chat import send_message

# ── 출력 스키마 (경량화 - parameters 제거) ────────────────────────────────────
class ActionResult(BaseModel):
    action: Literal["new_agent", "continue_task", "rag", "light_response"]
    agent_name: Optional[str] = None  # new_agent일 때만 사용
    new_task: Optional[bool] = False

# ── 프롬프트 ──────────────────────────────────────────────────────────────────
action_parser = PydanticOutputParser(pydantic_object=ActionResult)
format_instructions = action_parser.get_format_instructions()

CLASSIFY_PROMPT = """
당신은 사용자의 발화를 분석하여 다음 행동을 결정하는 라우팅 AI입니다.
파라미터 추출은 하지 않습니다. 오직 어떤 행동을 취해야 할지만 분류하세요.

[행동(action) 분류 기준]
1. new_agent: 사용자가 새로운 업무(에이전트)를 시작하려고 할 때.
   - 에이전트 종류는 agent_list를 참고하여 분류하세요.
   - agent_name에 해당 에이전트 이름을 넣으세요.
   - 항상 new_task: true로 설정하세요.

2. continue_task: 현재 진행 중인 업무(에이전트)를 이어가는 모든 경우.
   - 파라미터 제공, AI 질문에 답변, 취소 의사 표현 등 모두 포함.
   - current_agent가 있을 때만 해당.

3. rag: 사내 규정, 매뉴얼, 일반 지식 등에 대한 질문.

4. light_response: 단순 인사, 감정 표현, 위 분류에 해당하지 않는 가벼운 대화.

[new_task 판단 기준]
- action이 new_agent일 때: new_task는 항상 True
- 동일 에이전트 진행 중이지만 이전 업무 완료 후 새 건 요청: True
- 진행 중인 업무 이어가기: False
- rag, light_response: False
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

    <agent_list>
    {agent_list}
    </agent_list>
    """
)

action_classify_chain: Runnable = (
    action_classify_prompt
    | LLM_LIGHT
    | action_parser
)

# ── 메인 노드 ─────────────────────────────────────────────────────────────────
async def classify_agent_action(state: GraphState):

    """ 해보니까 구려서 주석처리함
    # 의도 분석 중임을 알림
    space_name = state.chat_context.get("space_name", "")
    await send_message(space_name, "에이전트가 해야 할 일을 분석 중입니다...")
    """

    start_time = time.time()
    print(f"[Node] Start: classify_agent_action")
    user_query = state.get_last_user_message() or ""
    messages = json.dumps(state.messages, indent=2, ensure_ascii=False)
    current_agent = state.current_agent or "None"

    # MongoDB에서 enabled=True인 chat 에이전트의 이름과 설명을 가져와서 프롬프트에 포함 (action_classifier 프롬프트용)
    if state.current_agent and state.current_agent in AGENT_REGISTRY:
        descriptions = {state.current_agent: AGENT_REGISTRY[state.current_agent]["description"]}
    else:
        descriptions = await get_enabled_agent_descriptions()
    agent_list = json.dumps(descriptions, indent=2, ensure_ascii=False)

    result = await action_classify_chain.ainvoke({
        "classify_prompt": CLASSIFY_PROMPT,
        "format_instructions": format_instructions,
        "messages": messages,
        "user_query": user_query,
        "current_agent": current_agent,
        "agent_list": agent_list,
    })

    end_time = time.time()
    print(f"[Node] End: classify_agent_action | Duration: {end_time - start_time:.2f}s")
    print(f"[ActionClassifier] {result}")
    return {"action": result.dict()}