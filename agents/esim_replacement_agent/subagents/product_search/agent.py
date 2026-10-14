from core.prompt.prompt_service import prompt_service

from tools.network.esim_product_search_tools import (
    search_products_by_country,
)
from tools.network.esim_replacement_tools import (
    esim_get_product_detail,
)


async def build_product_search_subagent() -> dict:
    search_skills = await prompt_service.prepare_skills(
        "product_search_subagent"
    )

    return {
        "name": "product_search_subagent",
        "description": (
            "이심재발급 tool만으로 동일/대체 상품코드를 찾지 못했을 때, 사용자가 상품명/코드로 "
            "직접 상품을 바꿔달라고 요청했을 때, 또는 통신사·5G·무제한 여부 같은 조건으로 상품을 "
            "찾아달라고 요청했을 때 위임된다. 국가·조건 기반 자동 탐색과 사용자 지정 검색을 "
            "모두 처리한다."
        ),
        "tools": [
            search_products_by_country,
            esim_get_product_detail,
        ],
        "skills": search_skills,
        "system_prompt": (
            "너는 이심 대체 상품 검색 전문가다. "
            "제공된 Skill의 절차를 우선하여 업무를 수행한다."
            "사용자가 변경을 요청한 조건만 변경하고, 나머지는 기존에 제공된 정보 그대로 유지한다."
            "예) 기존 상품 '일본 5GB 1일 1개' > 사용자가 무제한 상품을 요청하면 '일본 무제한 1일 1개'로 변경한다."
        ),
    }