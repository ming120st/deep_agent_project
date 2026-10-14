from core.prompt.prompt_service import prompt_service
from registry.agent_registry import register_agent
from core.tracing.logging_middleware import ToolLoggingMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from deepagents import create_deep_agent, CompiledSubAgent
from deepagents.backends.filesystem import FilesystemBackend
from core.llm.model_service import model_service
from agents.esim_replacement_agent.subagents.product_search.agent import (
    build_product_search_subagent,
)
from agents.esim_replacement_agent.middleware import (
    EsimOrderPromptMiddleware,
)
import os

from tools.network.esim_replacement_tools import (
    esim_build_confirmation,
    esim_fetch_order,
    esim_get_product_detail,
    esim_register_consultation,
    esim_resolve_reissue_product,
    esim_send,
)


async def esim_replacement_agent() -> CompiledSubAgent:
    system_prompt = await prompt_service.render(
        "deep_agent.subagent.esim_replacement.system"
    )

    main_model = await model_service.get_model(
        "llm.light"
    )

    main_skills = await prompt_service.prepare_skills(
        "esim_replacement"
    )

    # Redis / MongoDB의 최신 Skill을 확인하고
    # 필요한 경우 Runtime SKILL.md 갱신
    skill_sources = await (
        prompt_service.prepare_skills(
            agent="esim_replacement",
        )
    )

    # .runtime 파일을 읽는 Backend
    skill_backend = FilesystemBackend(
        root_dir="."
    )

    product_search_subagent = (
        await build_product_search_subagent()
    )

    compiled_agent = create_deep_agent(
        model=main_model,
        system_prompt=system_prompt,
        middleware=[
            EsimOrderPromptMiddleware(),
            SkillsMiddleware(
                backend=skill_backend,
                sources=skill_sources,

                # Skill을 읽은 후 노출할 Tool
                tools=[
                    esim_send,
                ]
            ),
            ToolLoggingMiddleware(),
        ],
        # 항상 노출되는 Tools 목록
        tools=[
            esim_fetch_order,
            esim_resolve_reissue_product,
            esim_get_product_detail,
            esim_build_confirmation,
            esim_register_consultation,
        ],
        skills=main_skills,
        subagents=[
            product_search_subagent,
        ],
        backend=FilesystemBackend(
            root_dir=os.environ[
                "DEEP_AGENT_ROOT_DIR"
            ],
        ),
    )

    return CompiledSubAgent(
        name="esim_replacement",
        description=(
            "사용자의 이심 재발급 요청이 있을 때 처리한다. "
            "주문번호 조회, 대체 상품 선정/검색, 발송, 상담 등록 전 과정을 담당한다."
        ),
        runnable=compiled_agent,
    )


async def register_esim_replacement_agent() -> None:
    subagent = await esim_replacement_agent()

    register_agent(
        name=subagent["name"],
        description=subagent["description"],
        runnable=subagent["runnable"],
    )