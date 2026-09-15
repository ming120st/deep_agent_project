from pathlib import Path
from core.prompt.prompt_service import prompt_service
from registry.agent_registry import (
    AGENT_REGISTRY,
    register_agent,
)
from tools.network.esim_product_search_tools import search_products_by_country
from tools.network.esim_replacement_tools import (
    esim_build_confirmation,
    esim_fetch_order,
    esim_get_product_detail,
    esim_register_consultation,
    esim_resolve_reissue_product,
    esim_send,
)
from deepagents import create_deep_agent, CompiledSubAgent
from core.llm.model_service import model_service

BASE_DIR = Path(__file__).parent
SKILLS_DIR = BASE_DIR / "skills"


# ── product_search_subagent ─────────────────────────────────────────────────
# SKILL.md 4.3에서 위임하는 대상. 하위에 더 붙는 에이전트 없이 tool만 사용하는
# 일반 SubAgent(dict)로 구성한다 — name은 SKILL.md 본문이 참조하는 이름과 반드시 일치해야
# 딥에이전트가 정확히 이 서브에이전트로 라우팅한다.

async def build_product_search_subagent() -> dict:
    search_skills = await prompt_service.prepare_skills(
        "esim-search-skill"
    )
    return {
        "name": "product_search_subagent",
        "description": (
            "이심재발급 tool만으로 동일/대체 상품코드를 찾지 못했을 때, 사용자가 상품명/코드로 "
            "직접 상품을 바꿔달라고 요청했을 때, 또는 통신사·5G·무제한 여부 같은 조건으로 상품을 "
            "찾아달라고 요청했을 때 위임된다. 국가·조건 기반 자동 탐색과 사용자 지정 검색을 "
            "모두 처리한다."
        ),
        "tools": [search_products_by_country, esim_get_product_detail],
        "skills": search_skills,
        "system_prompt": "너는 이심 대체 상품 검색 전문가다. 반드시 esim-search 스킬의 SKILL.md를 처음부터 참고해서 업무를 진행해라.",
    }

async def esim_replacement_agent() -> CompiledSubAgent:
    system_prompt = await prompt_service.render(
        "deep_agent.subagent.esim_replacement.system"
    )

    main_model = await model_service.get_model(
        "llm.light"
    )

    main_skills = await prompt_service.prepare_skills(
        "esim-replacement-main-manual"
    )

    product_search_subagent = await build_product_search_subagent()

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
        subagents=[product_search_subagent],
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