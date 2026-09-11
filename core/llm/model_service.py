from pymongo.asynchronous.database import AsyncDatabase

from langchain_openai import ChatOpenAI
from langchain_google_genai import (
    ChatGoogleGenerativeAI,
    GoogleGenerativeAIEmbeddings,
)

from core.database.mongo import mongo_db


class ModelService:

    def __init__(
        self,
        db: AsyncDatabase,
    ):
        self.collection = db["llm_models"]
        self._models = {}

    async def load(
        self,
    ):
        cursor = self.collection.find(
            {
                "enabled": True,
            }
        )

        documents = await cursor.to_list(
            length=None
        )

        # 재로드 시 기존 캐시 제거
        self._models.clear()

        for document in documents:
            key = document["key"]

            model_type = document["type"]
            provider = document["provider"]
            model_name = document["model"]

            if model_type == "chat":
                model = self._create_chat_model(
                    provider=provider,
                    model_name=model_name,
                    document=document,
                )

            elif model_type == "embedding":
                model = self._create_embedding_model(
                    provider=provider,
                    model_name=model_name,
                )

            else:
                continue

            self._models[key] = model

    def get(
        self,
        key: str,
    ):
        model = self._models.get(
            key
        )

        if model is None:
            raise ValueError(
                f"Loaded model not found: {key}"
            )

        return model

    @property
    def LLM_LIGHT(
        self,
    ):
        return self.get(
            "llm.light"
        )

    @property
    def LLM_HEAVY(
        self,
    ):
        return self.get(
            "llm.heavy"
        )

    @property
    def EMBEDDING(
        self,
    ):
        return self.get(
            "embedding.default"
        )

    def _create_chat_model(
        self,
        provider: str,
        model_name: str,
        document: dict,
    ):
        kwargs = {
            "model": model_name,
            "temperature": document.get(
                "temperature",
                0,
            ),
        }

        request_timeout = document.get(
            "request_timeout"
        )

        if request_timeout is not None:
            kwargs["request_timeout"] = (
                request_timeout
            )

        max_retries = document.get(
            "max_retries"
        )

        if max_retries is not None:
            kwargs["max_retries"] = (
                max_retries
            )

        if provider == "openai":
            return ChatOpenAI(
                **kwargs
            )

        if provider == "google":
            return ChatGoogleGenerativeAI(
                **kwargs
            )

        raise ValueError(
            f"Unsupported chat provider: "
            f"{provider}"
        )

    def _create_embedding_model(
        self,
        provider: str,
        model_name: str,
    ):
        if provider == "google":
            return GoogleGenerativeAIEmbeddings(
                model=model_name
            )

        raise ValueError(
            f"Unsupported embedding provider: "
            f"{provider}"
        )

    def clear_cache(
        self,
        key: str | None = None,
    ):
        if key:
            self._models.pop(
                key,
                None,
            )
            return

        self._models.clear()


model_service = ModelService(
    mongo_db
)