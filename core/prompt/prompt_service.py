# Repository에서 프롬프트를 가져와 변수 치환 등 실제 사용 로직을 처리

from typing import Any
from jinja2 import Environment, StrictUndefined
from core.prompt.prompt_repository import PromptRepository

class PromptService:

    def __init__(
        self,
        repository: PromptRepository,
    ):
        self.repository = repository

        self.jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
        )

    async def get(
        self,
        key: str,
    ) -> str:

        prompt = await self.repository.get_active_prompt(key)

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

        prompt = await self.repository.get_active_prompt(key)

        if prompt is None:
            raise ValueError(
                f"Active prompt not found: {key}"
            )

        render_values = dict(values)

        # 변수 검증 및 기본값 처리
        for variable in prompt.variables:

            if variable.name in render_values:
                continue

            if variable.default is not None:
                render_values[variable.name] = variable.default
                continue

            if variable.required:
                raise ValueError(
                    "Required prompt variable missing: "
                    f"{key}.{variable.name}"
                )

            render_values[variable.name] = ""

        template = self.jinja.from_string(
            prompt.content
        )

        return template.render(**render_values)