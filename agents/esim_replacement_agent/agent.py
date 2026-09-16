from core.prompt.prompt_service import prompt_service
from registry.agent_registry import register_agent
from deepagents import create_deep_agent, CompiledSubAgent
from deepagents.backends.filesystem import FilesystemBackend
from core.llm.model_service import model_service
from agents.esim_replacement_agent.subagents.product_search.agent import (
    build_product_search_subagent,
)

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

    product_search_subagent = (
        await build_product_search_subagent()
    )

    compiled_agent = create_deep_agent(
        model=main_model,
        system_prompt=system_prompt,
        tools=[
            esim_fetch_order,
            esim_resolve_reissue_product,
            esim_get_product_detail,
            esim_build_confirmation,
            esim_send,
            esim_register_consultation,
        ],
        skills=main_skills,
        subagents=[
            product_search_subagent,
        ],
        backend=FilesystemBackend(),
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