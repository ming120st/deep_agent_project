"""
의도 분류 노드 (Action Classifier Node)
사용자 메시지와 대화 이력을 바탕으로 에이전트의 다음 행동을 결정합니다.
"""
from pydantic import BaseModel
from typing import Literal, Optional, Dict, Any

from src.workflow.graph_infra.state import GraphState
from src.core.llm_config import llm_light
from src.prompts.action_classifier_prompt import classify_agent_action_prompt
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import Runnable
import json
from src.agent.agent_config import get_current_agent_spec, AGENT_DESCRIPTIONS

# ── 출력 스키마 ───────────────────────────────────────────────────────────────
class actionResult(BaseModel):
    action: Literal["parameter", "new_agent", "rag", "light_response"]
    agent_name: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None

# ── 프롬프트 ──────────────────────────────────────────────────────────────────
# pydantic 파서
action_parser = PydanticOutputParser(pydantic_object=actionResult)

# pydantic output format instructions 프롬프트
format_instructions = action_parser.get_format_instructions()

# 행동분류기 prompt
action_classify_prompt = ChatPromptTemplate.from_template(
    """
    {classify_agent_action_prompt}
    
    <format_instructions>
    {format_instructions}
    </format_instructions>
    
    <messages>
    {messages}
    </messages>

    <user_query>
    {userQuery}
    </user_query>
    
    <current_agent>
    {current_agent}
    </current_agent>

    <collected_parameters>
    {collected_parameters}
    </collected_parameters>

    <current_agent_spec>
    {current_agent_spec_str}
    </current_agent_spec>

    <agent_list>
    {agent_list_summary_str}
    </agent_list>
"""
)

# 행동분류기 chain
action_classify_chain : Runnable = (
    action_classify_prompt
    | llm_light
    | action_parser
)

# ── 메인 노드 ─────────────────────────────────────────────────────────────────

async def classify_agent_action(state: GraphState):
    # 행동분류 프롬프트에 넣을 변수 준비
    user_query = state.get_last_user_message() or ""
    messages = json.dumps(state.messages, indent=2, ensure_ascii=False) # messages는 JSON 포맷의 문자열로 프롬프트에 전달
    current_agent = state.current_agent or "None"
    
    # 현재 에이전트의 파라미터만 추출
    current_agent_params = state.collected_parameters.get(current_agent, {}) if current_agent != "None" else {}
    collected_parameters_str = json.dumps(current_agent_params, indent=2, ensure_ascii=False)
    
    current_agent_spec = get_current_agent_spec(current_agent) if current_agent != "None" else {}
    current_agent_spec_str = json.dumps(current_agent_spec, indent=2, ensure_ascii=False)
    agent_list_summary_str = json.dumps(AGENT_DESCRIPTIONS, indent=2, ensure_ascii=False)

    result = await action_classify_chain.ainvoke({
        "classify_agent_action_prompt": classify_agent_action_prompt,
        "format_instructions": format_instructions,
        "messages": messages,
        "userQuery": user_query,
        "current_agent": current_agent,
        "collected_parameters": collected_parameters_str,
        "current_agent_spec_str": current_agent_spec_str,
        "agent_list_summary_str": agent_list_summary_str,
    })
    state.action = result.dict() # result는 action_response.py의 actionResponse객체다.
    print(f"이번 발화의 의도: {result}")
    # state.action에는 딕셔너리로 바꿔서 넣지만 result 반환할때는 actionResponse 객체 반환하는게 제일 깔끔하다. 이후 노드에서 state.action에서 필요한 정보 꺼내서 쓰면 된다.
    return {"action": state.action}