
from pathlib import Path

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.state import StateBackend
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware

from core.prompt.prompt_service import prompt_service
from core.tracing.logging_middleware import ToolLoggingMiddleware
from registry.agent_registry import register_agent
from tools.computer_use.browser import SKILL_ROOT
from tools.computer_use.tool import (
    start_browser,
    browser_action,
    get_browser_state,
    close_browser,
)


MARKET_AGENT_PROMPT_KEY = "deep_agent.subagent.market.system"


async def register_market_agent() -> None:
    base_prompt = await prompt_service.get(MARKET_AGENT_PROMPT_KEY)
    runtime_sources = await prompt_service.prepare_skills(agent="market")

    routes = {
        "/marketplaces/": FilesystemBackend(root_dir=SKILL_ROOT),
    }

    sources = ["/marketplaces/"]

    for index, source in enumerate(runtime_sources):
        prefix = f"/runtime-{index}/"
        routes[prefix] = FilesystemBackend(root_dir=Path(source).resolve())
        sources.append(prefix)

    backend = CompositeBackend(
        default=StateBackend(),
        routes=routes,
    )

    register_agent(
        name="market",
        description="마켓 관리자 로그인, 주문 조회 및 파일 다운로드를 수행하는 에이전트.",

        system_prompt=(
            f"{base_prompt}\n\n"
            "작업 전에 공통 Skill과 대상 마켓 Skill을 읽고 해당 절차를 따른다.\n"
            "브라우저 작업은 제공된 공통 도구로 실행한다.\n"
            "로그인 정보는 서버에서 처리하며 사용자에게 비밀번호를 요청하지 않는다.\n"
            "로그인 성공 여부는 인증 검증 결과로 판단한다.\n"
            "주문 변경, 취소, 환불 등 상태 변경 작업은 별도 승인 없이 수행하지 않는다."
        ),

        prompt_key=MARKET_AGENT_PROMPT_KEY,
        skills=sources,

        tools=[
            start_browser,
            browser_action,
            get_browser_state,
            close_browser,
        ],

        middleware=[
            FilesystemMiddleware(
                backend=backend,
                tools=["read_file"],
            ),
            SkillsMiddleware(
                backend=backend,
                sources=sources,
            ),
            ToolLoggingMiddleware(),
        ],
    )
