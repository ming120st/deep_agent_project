# Prompt 조회 결과를 사용해 최종 프롬프트 문자열을 생성하는 서비스

from pathlib import Path
from typing import Any
from jinja2 import (
    Environment,
    StrictUndefined,
)
from core.prompt.prompt_repository import (
    PromptRepository,
    prompt_repository,
)
from pathlib import Path

class PromptService:
    def __init__(
        self,
        repository: PromptRepository,
    ):
        # Prompt DB 조회 로직을 담당하는 Repository를 주입
        self.repository = repository

        # DB에 저장된 Jinja 템플릿 프롬프트를 렌더링하기 위한 설정
        self.jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
        )

    async def get(
        self,
        key: str,
    ) -> str:
        # 활성화된 최신 Prompt를 조회하고 content만 반환
        prompt = await (
            self.repository
            .get_active_prompt(
                key
            )
        )

        if prompt is None:
            raise ValueError(
                f"Active prompt not found: {key}"
            )

        return prompt.content

    async def render(
        self,
        key: str,
        **values: Any,
    ) -> str:
        # 활성 Prompt를 조회한 뒤 Jinja 변수까지 치환하여 최종 문자열 반환
        prompt = await (
            self.repository
            .get_active_prompt(
                key
            )
        )

        if prompt is None:
            raise ValueError(
                f"Active prompt not found: {key}"
            )

        template = (
            self.jinja
            .from_string(
                prompt.content
            )
        )

        return template.render(
            **values
        )

    async def get_active_skills_by_agent(
        self,
        agent: str,
    ):
        # 특정 Agent에 연결된 활성 Skill Prompt 목록 조회
        return await (
            self.repository
            .get_active_skills_by_agent(
                agent
            )
        )
    
    # 특정 Agent의 활성 Skill을 DB에서 조회하고
    # Deep Agent가 사용할 수 있도록 런타임 SKILL.md 파일로 생성한다.
    async def prepare_skills(
        self,
        agent: str,
    ) -> list[str]:
        skills = await (
            self.repository
            .get_active_skills_by_agent(
                agent
            )
        )

        if not skills:
            return []

        base_dir = (
            Path(".runtime")
            / "skills"
            / agent
        )

        base_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        for skill in skills:
            if not skill.skill_name:
                continue

            skill_dir = (
                base_dir
                / skill.skill_name
            )

            skill_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            skill_file = (
                skill_dir
                / "SKILL.md"
            )

            skill_file.write_text(
                skill.content,
                encoding="utf-8",
            )

        return [
            str(base_dir)
        ]

# 애플리케이션 전역에서 사용할 PromptService 인스턴스 생성
prompt_service = PromptService(
    prompt_repository
)
