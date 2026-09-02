# 프롬프트를 DB나 저장소에서 직접 조회·저장하는 데이터 접근 계층

from pymongo.asynchronous.database import AsyncDatabase
from core.prompt.schemas import PromptConfig

class PromptRepository:

    def __init__(
        self,
        db: AsyncDatabase,
    ):
        self.collection = db["prompts"]

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
    
        print("PROMPT KEY:", key)
        print("PROMPT DOCUMENT:", document)
    
        if document is None:
            return None
    
        document.pop("_id", None)
    
        return PromptConfig.model_validate(document)