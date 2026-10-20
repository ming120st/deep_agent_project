
"""
HTML Agent가 생성한 ArticleContent를
MongoDB에 저장하고 재조회하는 DB 접근 클래스.

- 게시용 콘텐츠 저장
- post_key 기반 조회
- search_id와 원본 검색 결과 연결
- 기존 콘텐츠 수정 시 업데이트
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

from core.database.mongo import mongo_db
from models.article_content import ArticleContent


class ArticleRecord(BaseModel):
    """MongoDB에 저장된 게시 콘텐츠 기록."""

    post_key: str
    search_id: str
    article: ArticleContent
    created_at: datetime
    updated_at: datetime


class ArticleRepository:
    COLLECTION_NAME = "intranet_articles"

    @property
    def collection(self):
        """MongoDB 게시 콘텐츠 컬렉션."""
        return mongo_db[self.COLLECTION_NAME]

    async def save_article(
        self,
        search_id: str,
        post_key: str,
        article: ArticleContent,
    ) -> None:
        """
        ArticleContent를 MongoDB에 저장한다.

        동일한 post_key가 이미 존재하면
        기존 데이터를 갱신한다.
        """

        now = datetime.now(timezone.utc)

        await self.collection.update_one(
            {"_id": post_key},
            {
                "$set": {
                    "search_id": search_id,
                    "article": article.model_dump(
                        mode="json"
                    ),
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

    async def get_article(
        self,
        post_key: str,
    ) -> ArticleRecord | None:
        """
        post_key로 저장된 게시 콘텐츠를 조회한다.
        """

        document = await self.collection.find_one(
            {"_id": post_key}
        )

        if document is None:
            return None

        return ArticleRecord(
            post_key=document["_id"],
            search_id=document["search_id"],
            article=ArticleContent.model_validate(
                document["article"]
            ),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        )


article_repository = ArticleRepository()
