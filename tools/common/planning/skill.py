from langchain_core.tools import (
    tool,
)

from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    get_planning_skill_refs,
)


@tool
async def get_planning_skills() -> dict:
    """
    현재 활성화된 Agent별 Planning Skill 목록을 조회한다.

    Main Agent가 전문 업무를 계획하기 전에 호출하여
    해당 업무에 적용할 Planning Skill이 존재하는지 확인한다.

    Skill 전체 본문은 반환하지 않고,
    Skill 선택에 필요한 Metadata만 반환한다.
    """

    refs = await get_planning_skill_refs()

    result = []

    for ref in refs:
        metadata = (
            await prompt_service.get_skill_metadata(
                agent=ref["agent"],
                skill_name=ref["skill_name"],
            )
        )

        if metadata is None:
            continue

        result.append(
            {
                "agent": metadata["agent"],
                "skill_name": (
                    metadata["skill_name"]
                ),
                "name": metadata["name"],
                "description": (
                    metadata["description"]
                ),
                "version": metadata["version"],
            }
        )

    return {
        "skills": result,
        "count": len(result),
    }


@tool
async def get_planning_skill(
    agent: str,
    skill_name: str,
) -> str:
    """
    특정 Planning Skill의 본문을 조회한다.

    Main Agent가 Todo를 작성하기 전에 호출하여
    해당 업무의 계획 규칙, 재계획 조건,
    완료 기준을 확인한다.
    """

    return await prompt_service.get_skill(
        agent=agent,
        skill_name=skill_name,
    )