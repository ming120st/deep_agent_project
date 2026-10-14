from langchain.tools import tool

from core.prompt.prompt_service import (
    prompt_service,
)


@tool
async def get_bigquery_skills() -> dict:
    """
    BigQuery Agent에 등록된 활성 Skill 목록을 조회한다.

    각 Skill의 이름, 설명, 버전 정보를 반환하며
    사용 가능한 분석 Skill을 확인할 때 사용한다.
    """
    skills = await (
        prompt_service
        .get_active_skills_by_agent(
            "bigquery"
        )
    )

    result = []

    for skill in skills:
        if not skill.skill_name:
            continue

        result.append(
            {
                "skill_name": skill.skill_name,
                "name": skill.name,
                "description": skill.description,
                "version": skill.version,
            }
        )

    return {
        "skills": result,
        "count": len(result),
    }


@tool
async def get_bigquery_skill(
    skill_name: str,
) -> str:
    """
    지정한 BigQuery Skill의 상세 내용을 조회한다.

    Args:
        skill_name:
            조회할 BigQuery Skill 이름.

    Returns:
        해당 Skill의 프롬프트 또는 분석 가이드 본문.
    """
    return await prompt_service.get_skill(
        agent="bigquery",
        skill_name=skill_name,
    )