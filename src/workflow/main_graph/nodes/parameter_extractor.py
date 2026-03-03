"""
파라미터 추출 노드 (Parameter Extractor Node)
SOP + 경험(experience) + agent_spec을 풀로 주입하여 파라미터를 추출합니다.
action_classifier 이후, update_parameters 이전에 실행됩니다.
"""
from pydantic import BaseModel
from typing import Optional, Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.pydantic import PydanticOutputParser
from langchain_core.runnables import Runnable
import json

from src.workflow.graph_infra.state import GraphState
from src.core.llm_config import llm_light
from src.agent.agent_config import get_current_agent_spec
from src.core.mongo_client import boa_sops_collection, boa_memories_collection

# ── 출력 스키마 ───────────────────────────────────────────────────────────────
class ParameterResult(BaseModel):
    parameters: Optional[Dict[str, Any]] = None
    new_task: Optional[bool] = False  # action_classifier 결과 그대로 전달

# ── 프롬프트 ──────────────────────────────────────────────────────────────────
param_parser = PydanticOutputParser(pydantic_object=ParameterResult)
format_instructions = param_parser.get_format_instructions()

EXTRACT_PROMPT = """
당신은 대화 내역에서 업무 파라미터를 추출하는 AI입니다.

[추출 규칙]
- current_agent_spec에 정의된 파라미터 필드만 추출하세요.
- 사용자의 현재 발화를 최우선으로 고려하고, 누락된 정보는 대화 내역을 참고하여 보완하세요.
- 이미 수집된 파라미터(collected_parameters)는 다시 추출하지 않아도 됩니다. 새로 추가되거나 변경된 것만 추출하세요.
- AI가 "맞나요?", "할까요?" 라고 물었을 때 사용자가 "맞아", "응" 이라고 대답하면 해당 파라미터를 true로 설정하세요.
- 날짜는 YYYYMMDD 형식으로 변환하세요.
- 취소 의사를 표현하면 intent_confirmed: false로 설정하세요.

[SOP 참고]
- SOP가 있다면 업무 절차를 이해하는 데 참고하세요.
- SOP에 명시된 파라미터 형식이나 규칙이 있다면 따르세요.

[경험(experience) 참고]
- 유사한 과거 경험이 있다면 파라미터 추출에 참고하세요.
"""

extract_prompt = ChatPromptTemplate.from_template(
    """
    {extract_prompt}

    <format_instructions>
    {format_instructions}
    </format_instructions>

    <messages>
    {messages}
    </messages>

    <user_query>
    {user_query}
    </user_query>

    <current_agent_spec>
    {agent_spec}
    </current_agent_spec>

    <collected_parameters>
    {collected_parameters}
    </collected_parameters>

    <sop>
    {sop}
    </sop>

    <experience>
    {experience}
    </experience>
    """
)

extract_chain: Runnable = (
    extract_prompt
    | llm_light
    | param_parser
)

# ── 메인 노드 ─────────────────────────────────────────────────────────────────
async def extract_parameters(state: GraphState):
    action_data = state.action or {}
    current_agent = action_data.get("agent_name") or state.current_agent

    if not current_agent:
        print("[ExtractParameters] current_agent 없음, 스킵")
        return {}

    user_query = state.get_last_user_message() or ""
    messages = json.dumps(state.messages, indent=2, ensure_ascii=False)

    agent_spec = get_current_agent_spec(current_agent)
    agent_spec_str = json.dumps(agent_spec, indent=2, ensure_ascii=False)

    current_params = state.collected_parameters.get(current_agent, {})
    collected_parameters_str = json.dumps(current_params, indent=2, ensure_ascii=False)

    # ── SOP 조회 (MongoDB) ────────────────────────────────────────
    sop_doc = await boa_sops_collection.find_one({"agent_name": current_agent})
    sop = sop_doc.get("content", "없음") if sop_doc else "없음"
    if sop_doc:
        print(f"[ExtractParameters] SOP 주입: {current_agent}")

    # ── experience 조회 (MongoDB) ─────────────────────────────────
    # 가장 최근 경험 1건 조회 (추후 유사도 검색으로 고도화 가능)
    exp_doc = await boa_memories_collection.find_one(
        {"agent_name": current_agent},
        sort=[("created_at", -1)]
    )
    experience = exp_doc.get("content", "없음") if exp_doc else "없음"
    if exp_doc:
        print(f"[ExtractParameters] Experience 주입: {current_agent}")

    result = await extract_chain.ainvoke({
        "extract_prompt": EXTRACT_PROMPT,
        "format_instructions": format_instructions,
        "messages": messages,
        "user_query": user_query,
        "agent_spec": agent_spec_str,
        "collected_parameters": collected_parameters_str,
        "sop": sop,
        "experience": experience,
    })

    print(f"[ExtractParameters] {result}")

    # action에 파라미터 결과 머지해서 반환 (update_parameters 노드가 그대로 활용)
    updated_action = {
        **action_data,
        "parameters": result.parameters,
    }
    return {"action": updated_action}