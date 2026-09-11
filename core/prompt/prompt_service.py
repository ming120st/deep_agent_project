from typing import Any

from jinja2 import Environment, StrictUndefined
from pymongo.asynchronous.database import AsyncDatabase

from core.database.mongo import mongo_db
from core.prompt.schemas import PromptConfig


class PromptService:

    def __init__(
        self,
        db: AsyncDatabase,
    ):
        self.collection = db["prompts"]

        self.jinja = Environment(
            undefined=StrictUndefined,
            autoescape=False,
        )

    async def get(
        self,
        key: str,
    ) -> str:

        prompt = await self.get_active_prompt(
            key
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

        prompt = await self.get_active_prompt(
            key
        )

        if prompt is None:
            raise ValueError(
                f"Active prompt not found: {key}"
            )

        template = self.jinja.from_string(
            prompt.content
        )

        return template.render(
            **values
        )

    async def get_active_prompt(
        self,
        key: str,
    ) -> PromptConfig | None:

        document = await self.collection.find_one(
            {
                "key": key,
                "enabled": True,
            },
            sort=[
                ("version", -1),
            ],
        )

        if document is None:
            return None

        document.pop(
            "_id",
            None,
        )

        return PromptConfig.model_validate(
            document
        )

    async def get_active_skills_by_agent(
        self,
        agent: str,
    ) -> list[PromptConfig]:

        cursor = (
            self.collection
            .find(
                {
                    "agent": agent,
                    "prompt_type": "skill",
                    "enabled": True,
                }
            )
            .sort(
                "skill_name",
                1,
            )
        )

        documents = await cursor.to_list(
            length=None
        )

        skills: list[PromptConfig] = []

        for document in documents:
            document.pop(
                "_id",
                None,
            )

            skills.append(
                PromptConfig.model_validate(
                    document
                )
            )

        return skills


prompt_service = PromptService(
    mongo_db
)