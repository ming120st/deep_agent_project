# MongoDB prompts 컬렉션에서 Prompt/Skill 데이터를 조회하는 Repository

from __future__ import annotations
from pymongo.asynchronous.database import AsyncDatabase
from core.database.mongo import mongo_db
from core.prompt.schemas import PromptConfig


class PromptRepository:
    def __init__(
        self,
        db: AsyncDatabase,
    ):
        # MongoDB의 prompts 컬렉션을 사용하도록 설정
        self.collection = db["prompts"]

    async def get_active_prompt(
        self,
        key: str,
    ) -> PromptConfig | None:
        # key에 해당하는 활성 Prompt 중 가장 최신 버전을 DB에서 조회

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
        # 특정 Agent에 등록된 활성 Skill Prompt들을 DB에서 조회

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

        return [
            # MongoDB 문서를 PromptConfig 모델로 변환 및 검증
            PromptConfig.model_validate(
                {
                    key: value
                    for key, value in document.items()
                    if key != "_id"
                }
            )
            for document in documents
        ]


prompt_repository = PromptRepository(
    mongo_db
)