# 프롬프트 관련 공용 객체나 의존성을 쉽게 가져와 쓸 수 있게 연결

from core.database.mongo import mongo_db
from core.prompt.prompt_repository import PromptRepository
from core.prompt.prompt_service import PromptService


prompt_repository = PromptRepository(
    mongo_db
)

prompt_service = PromptService(
    prompt_repository
)