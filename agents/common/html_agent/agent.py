from __future__ import annotations

from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    register_agent,
)
from tools.common.html.generate_weekly_html import (
    generate_weekly_html,
)

HTML_AGENT_PROMPT_KEY = (
    "deep_agent.subagent.html_agent.system"
)


async def register_html_agent() -> None:
    """
    HTML 콘텐츠 생성 전문 Agent 등록.

    1. MongoDB에서 System Prompt 조회
    2. HTML 생성 Tool 등록
    3. Agent Registry 등록
    """

    system_prompt = await prompt_service.get(
        HTML_AGENT_PROMPT_KEY
    )

    register_agent(
        name="html_agent",

        description=(
            "웹 검색 결과를 기반으로 인트라넷 게시글의 "
            "콘텐츠와 HTML을 생성하는 전문 Agent입니다. "
            "web_search가 반환한 search_id를 이용해 "
            "MongoDB에 저장된 검색 원본 데이터를 조회합니다. "
            "콘텐츠 작성, HTML 생성 및 게시용 데이터 저장을 "
            "처리하고 job_id를 반환합니다. "
            "실제 인트라넷 게시 작업은 수행하지 않습니다."
        ),

        system_prompt=system_prompt,

        prompt_key=HTML_AGENT_PROMPT_KEY,

        tools=[
            generate_weekly_html,
        ],
    )