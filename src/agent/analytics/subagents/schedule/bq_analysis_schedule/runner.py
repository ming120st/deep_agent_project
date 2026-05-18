"""
src/agent/schedule/bq_analysis_schedule/runner.py

BigQuery 분석 스케줄 실행기.

[설계 원칙]
  chat/bigquery_agent 코드를 복제하지 않고 재사용.
  GraphState 의존성 없이 (query, space_name) 만으로 분석 → DM 전송.

[재사용 구조]
  bigquery_agent/tools.py   → BIGQUERY_TOOLS          (동일 tools 재사용)
  bigquery_agent/prompts.py → BIGQUERY_SYSTEM_PROMPT  (동일 프롬프트 재사용)
  create_react_agent 로직   → 그대로 사용
"""
import asyncio
import logging
from datetime import date

from langgraph.prebuilt import create_react_agent

from src.agent.analytics.subagents.chat.bigquery_agent.tools import BIGQUERY_TOOLS
from src.agent.analytics.subagents.chat.bigquery_agent.prompts import BIGQUERY_SYSTEM_PROMPT
from src.core.llm_config import LLM_LIGHT
from src.core.google_chat import send_message

logger = logging.getLogger(__name__)


def _extract_text(content) -> str:
    """LangGraph 메시지 content → 문자열 변환."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


async def run_scheduled_bq_analysis(
    query: str,
    space_name: str,
    schedule_id: str = "",
) -> None:
    """
    스케줄로 호출되는 BigQuery 분석 실행기.

    APScheduler가 직접 호출 (kwargs 전달 방식):
      scheduler.add_job(
          run_scheduled_bq_analysis,
          kwargs={"query": ..., "space_name": ..., "schedule_id": ...},
      )

    Args:
        query:       사용자가 등록한 분석 요청 (자연어)
        space_name:  결과를 전송할 Google Chat space
        schedule_id: 로깅용 스케줄 ID
    """
    log_tag = f"[BqSchedule:{schedule_id or 'unknown'}]"
    logger.info(f"{log_tag} 분석 시작 — query: {query[:50]}")

    prompt = BIGQUERY_SYSTEM_PROMPT.replace("{today}", date.today().isoformat())

    try:
        agent = create_react_agent(
            model=LLM_LIGHT,
            tools=BIGQUERY_TOOLS,
            prompt=prompt,
        )

        result = await asyncio.wait_for(
            agent.ainvoke({"messages": [{"role": "user", "content": query}]}),
            timeout=120,
        )

        report = _extract_text(result["messages"][-1].content)

        short_query = query[:40] + ("..." if len(query) > 40 else "")
        header = f"📊 *[자동 분석 리포트]*\n쿼리: _{short_query}_\n\n"
        await send_message(space_name, header + report)

        logger.info(f"{log_tag} 완료")

    except asyncio.TimeoutError:
        logger.error(f"{log_tag} 타임아웃 (120s 초과)")
        await send_message(space_name, "⏱️ 스케줄 분석이 시간 초과로 중단되었습니다. (120초)")

    except Exception as e:
        logger.error(f"{log_tag} 오류: {e}", exc_info=True)
        await send_message(space_name, f"❌ 스케줄 분석 중 오류가 발생했습니다.\n`{e}`")
