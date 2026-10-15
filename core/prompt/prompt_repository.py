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
            PromptConfig.model_validate(
                document
            )
            for document in documents
        ]

    async def get_active_skill(
        self,
        agent: str,
        skill_name: str,
    ) -> PromptConfig | None:
        """
        특정 Agent의 특정 Skill을 조회한다.

        조건:
        - agent 일치
        - prompt_type = "skill"
        - skill_name 일치
        - enabled = True

        동일 Skill이 여러 버전 존재할 수 있으므로
        가장 최신 version을 1건 조회한다.
        """

        document = await self.collection.find_one(
            {
                "agent": agent,
                "prompt_type": "skill",
                "skill_name": skill_name,
                "enabled": True,
            },
            sort=[
                ("version", -1),
            ],
        )

        if document is None:
            return None

        return PromptConfig.model_validate(
            document
        )
    
    async def get_active_skill_version(
        self,
        agent: str,
        skill_name: str,
    ) -> int | None:

        document = await self.collection.find_one(
            {
                "agent": agent,
                "prompt_type": "skill",
                "skill_name": skill_name,
                "enabled": True,
            },
            projection={
                "_id": 0,
                "version": 1,
            },
            sort=[
                ("version", -1),
            ],
        )

        if document is None:
            return None

        return document.get("version")
    
    async def get_active_prompt_version(
        self,
        key: str,
    ) -> int | None:

        document = await self.collection.find_one(
            {
                "key": key,
                "enabled": True,
            },
            projection={
                "_id": 0,
                "version": 1,
            },
            sort=[
                ("version", -1),
            ],
        )

        if document is None:
            return None

        return document.get(
            "version"
        )
prompt_repository = PromptRepository(
    mongo_db
)