# Prompt 조회 결과를 사용해
# 최종 프롬프트 문자열을 생성하는 서비스

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from pathlib import Path
import shutil
from typing import Any

from jinja2 import (
    Environment,
    StrictUndefined,
)

from core.prompt.prompt_repository import (
    PromptRepository,
    prompt_repository,
)

from core.database.redis import (
    prompt_cache_get,
    prompt_cache_put,
)


class PromptService:
    def __init__(
        self,
        repository: PromptRepository,
    ):
        # Prompt DB 조회 로직을 담당하는 Repository
        self.repository = repository

        # DB에 저장된 Jinja Template Prompt를
        # 최종 문자열로 렌더링하기 위한 설정
        self.jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
        )

    # =========================================================
    # Prompt
    # =========================================================

    async def get(
        self,
        key: str,
    ) -> str:
        """
        활성화된 최신 Prompt를 조회하고
        content 문자열만 반환한다.
        """

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
        """
        활성화된 최신 Prompt를 조회하고
        Jinja 변수까지 치환한 최종 문자열을 반환한다.
        """

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
    

    # =========================================================
    # Skill
    # =========================================================

    async def _get_skill_payload(
        self,
        agent: str,
        skill_name: str,
    ) -> dict[str, Any]:

        cached = None

        # 1. Redis Cache 조회
        # Redis 장애가 나도 MongoDB 조회는 계속 진행
        try:
            cached = await prompt_cache_get(
                agent=agent,
                identifier=skill_name,
            )

        except Exception as exc:
            logger.warning(
                "Redis skill cache read failed. "
                "Continue without cache. "
                "agent=%s skill_name=%s error=%s",
                agent,
                skill_name,
                exc,
            )

        # 2. MongoDB에서 현재 활성 version 조회
        db_version = await (
            self.repository
            .get_active_skill_version(
                agent=agent,
                skill_name=skill_name,
            )
        )

        if db_version is None:
            raise ValueError(
                "Active skill not found: "
                f"{agent}/{skill_name}"
            )

        # 3. Redis Cache가 최신이면 사용
        if (
            isinstance(cached, dict)
            and cached.get("version") == db_version
            and cached.get("content") is not None
        ):
            return cached

        # 4. Cache miss / Redis 장애 / version 불일치
        # MongoDB에서 최신 Skill 조회
        prompt = await (
            self.repository
            .get_active_skill(
                agent=agent,
                skill_name=skill_name,
            )
        )

        if prompt is None:
            raise ValueError(
                "Active skill not found: "
                f"{agent}/{skill_name}"
            )

        payload = {
            "agent": prompt.agent,
            "skill_name": prompt.skill_name,
            "name": prompt.name,
            "description": prompt.description,
            "version": prompt.version,
            "content": prompt.content,
        }

        # 5. Redis 갱신
        # 실패해도 MongoDB 결과는 반환
        try:
            await prompt_cache_put(
                agent=agent,
                identifier=skill_name,
                value=payload,
            )

        except Exception as exc:
            logger.warning(
                "Redis skill cache write failed. "
                "Continue without cache. "
                "agent=%s skill_name=%s error=%s",
                agent,
                skill_name,
                exc,
            )

        return payload

    async def get_skill(
        self,
        agent: str,
        skill_name: str,
    ) -> str:
        """
        특정 Agent의 Skill 본문을 조회한다.

        실제 조회는 Redis Cache를 우선 사용하며,
        Cache가 없거나 Version이 오래된 경우
        MongoDB에서 최신 Skill을 조회한다.
        """

        payload = await self._get_skill_payload(
            agent=agent,
            skill_name=skill_name,
        )

        return payload["content"]

    async def get_skill_metadata(
        self,
        agent: str,
        skill_name: str,
    ) -> dict[str, Any]:
        """
        특정 Skill의 Metadata만 반환한다.

        get_skill()과 동일한 Redis Cache Payload를 사용하므로
        Metadata 조회를 위해 MongoDB를 별도로 다시 조회하지 않는다.
        """

        payload = await self._get_skill_payload(
            agent=agent,
            skill_name=skill_name,
        )

        return {
            "agent": payload["agent"],
            "skill_name": payload["skill_name"],
            "name": payload["name"],
            "description": payload["description"],
            "version": payload["version"],            
        }

    async def get_active_skills_by_agent(
        self,
        agent: str,
    ):
        """
        특정 Agent에 등록된 활성 Skill 목록을 조회한다.

        Skill 목록 탐색용 메서드이며,
        개별 Skill 본문 조회는 get_skill()을 사용한다.
        """

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

        base_dir = (
            Path(".runtime")
            / "skills"
            / agent
        )

        base_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        skills = await (
            self.get_active_skills_by_agent(
                agent=agent,
            )
        )

        if not skills:
            return []

        active_skill_names: set[str] = set()

        for skill in skills:
            if not skill.skill_name:
                continue

            skill_name = skill.skill_name
            active_skill_names.add(
                skill_name
            )

            skill_dir = (
                base_dir
                / skill_name
            )

            skill_file = (
                skill_dir
                / "SKILL.md"
            )

            version_file = (
                skill_dir
                / ".version"
            )

            payload = await (
                self._get_skill_payload(
                    agent=agent,
                    skill_name=skill_name,
                )
            )

            current_version = str(
                payload["version"]
            )

            local_version = None

            if version_file.exists():
                local_version = (
                    version_file
                    .read_text(
                        encoding="utf-8",
                    )
                    .strip()
                )

            if (
                not skill_file.exists()
                or local_version
                != current_version
            ):
                skill_dir.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                skill_file.write_text(
                    payload["content"],
                    encoding="utf-8",
                )

                version_file.write_text(
                    current_version,
                    encoding="utf-8",
                )

        for child in base_dir.iterdir():
            if (
                child.is_dir()
                and child.name
                not in active_skill_names
            ):
                shutil.rmtree(
                    child
                )

        # 여기 수정
        return [
            f".runtime/skills/{agent}"
        ]

    async def get_prompt_version(
        self,
        key: str,
    ) -> int:

        version = await (
            self.repository
            .get_active_prompt_version(
                key
            )
        )

        if version is None:
            raise ValueError(
                f"Active prompt not found: {key}"
            )

        return version
        
# 애플리케이션 전역 PromptService
prompt_service = PromptService(
    prompt_repository
)