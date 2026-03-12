"""
src/agent/chat/bigquery_agent/agent.py

BigQuery DeepAgent 노드.
create_react_agent 기반 ReAct 루프로 BigQuery를 자율 탐색·분석합니다.

[흐름]
  main_graph → bigquery_agent_node(state)
    → create_react_agent (ReAct 루프)
        Thought → Action(tool) → Observation → ... → Final Answer
    → state.add_ai_message(report)
    → main_graph → send_response → Google Chat 전송
"""
import logging
from datetime import date

from langgraph.prebuilt import create_react_agent

from src.workflow.graph_infra.state import GraphState
from src.core.llm_config import LLM_LIGHT, LLM_HEAVY
from src.agent.chat.bigquery_agent.tools import BIGQUERY_TOOLS
from src.agent.chat.bigquery_agent.prompts import BIGQUERY_SYSTEM_PROMPT
from src.core.task_manager import set_completed, set_cancelled
import asyncio

logger = logging.getLogger(__name__)


def _extract_text(content) -> str:
    """
    LangGraph 메시지의 content를 문자열로 변환.
    Claude API는 content를 list로 반환할 수 있음:
      [{'type': 'text', 'text': '...'}, ...]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


async def bigquery_agent_node(state: GraphState) -> GraphState:
    """
    BigQuery DeepAgent 노드.
    main_graph: update_parameters → bigquery_agent_node → send_response
    """
    # 오늘 날짜를 system prompt에 반영
    prompt = BIGQUERY_SYSTEM_PROMPT.replace("{today}", date.today().isoformat())
    user_query = state.get_last_user_message() or ""

    try:
        agent = create_react_agent(
            model=LLM_LIGHT,
            tools=BIGQUERY_TOOLS,
            prompt=prompt,
        )

        result = await asyncio.wait_for(
            agent.ainvoke({
                "messages": [{"role": "user", "content": user_query}]
            }),
            timeout=120
        )

        raw_content = result["messages"][-1].content
        report = _extract_text(raw_content)

        state.add_ai_message(report)
        await set_completed(state, result="BigQuery 분석 완료")

    except Exception as e:
        logger.error(f"[bigquery_agent_node] {e}", exc_info=True)
        state.add_ai_message(f"❌ BigQuery 분석 중 오류가 발생했습니다.\n{e}")
        state.error_message = str(e)
        await set_cancelled(state, result=f"BigQuery 분석 실패: {e}")

    state.current_agent = None
    return state