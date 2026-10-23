
"""
Search Agent의 검색 결과를 MongoDB에 저장하고,
search_id를 통해 재조회하는 DB 접근 전용 클래스.

- SearchResponse 전체 저장
- 검색 실행별 고유 ID 발급
- 검색 결과 조회 및 Pydantic 검증
- 검색 처리 상태 관리

save_search_response()	검색 결과 저장
get_search_record()	저장된 전체 기록 조회
get_search_response()	검색 데이터만 조회
update_search_status()	처리 상태 변경

"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from core.database.mongo import mongo_db
from models.search_model import SearchResponse


SearchStatus = Literal[
    "completed",
    "processing",
    "failed",
]

class YouTubeResult(BaseModel):
    video_id: str
    title: str
    description: str = ""
    channel_title: str = ""
    published_at: str | None = None
    thumbnail_url: str | None = None
    url: str


class SearchRecord(BaseModel):
    """MongoDB 검색 실행 기록."""

    search_id: str
    status: SearchStatus
    search_response: SearchResponse
    youtube_results: list[YouTubeResult] = Field(
        default_factory=list
    )
    created_at: datetime
    updated_at: datetime


class SearchRepository:
    COLLECTION_NAME = "search_results"

    @property
    def collection(self):
        """MongoDB 검색 결과 컬렉션."""
        return mongo_db[self.COLLECTION_NAME]

    async def save_youtube_results(
        self,
        search_id: str,
        youtube_results: list[YouTubeResult],
    ) -> bool:
        """
        기존 search_id에 YouTube 검색 결과를 저장한다.
        """

        result = await self.collection.update_one(
            {"_id": search_id},
            {
                "$set": {
                    "youtube_results": [
                        item.model_dump(mode="json")
                        for item in youtube_results
                    ],
                    "updated_at": datetime.now(
                        timezone.utc
                    ),
                }
            },
        )

        return result.matched_count > 0

    async def save_search_response(
        self,
        search_response: SearchResponse,
    ) -> str:
        """
        SearchResponse를 MongoDB에 저장한다.

        검색 실행마다 고유 search_id를 생성한다.
        저장 성공 시 search_id를 반환한다.
        """

        search_id = f"search_{uuid4().hex}"
        now = datetime.now(timezone.utc)

        document = {
            "_id": search_id,
            "status": "completed",
            "search_response": (
                search_response.model_dump(
                    mode="json"
                )
            ),
            "youtube_results": [],
            "created_at": now,
            "updated_at": now,
        }

        await self.collection.insert_one(
            document
        )

        return search_id

    async def get_search_record(
        self,
        search_id: str,
    ) -> SearchRecord | None:
        """
        search_id로 검색 실행 기록 전체를 조회한다.

        검색 결과가 없으면 None을 반환한다.
        조회된 데이터는 Pydantic 모델로 검증한다.
        """

        document = await self.collection.find_one(
            {"_id": search_id}
        )

        if document is None:
            return None

        return SearchRecord(
            search_id=document["_id"],
            status=document["status"],
            search_response=SearchResponse.model_validate(
                document["search_response"]
            ),
            youtube_results=[
                YouTubeResult.model_validate(item)
                for item in document.get(
                    "youtube_results", []
                )
            ],
            created_at=document["created_at"],
            updated_at=document["updated_at"],
        )
    
    async def get_search_response(
        self,
        search_id: str,
    ) -> SearchResponse | None:
        """
        search_id로 SearchResponse만 조회한다.

        HTML Agent에 검색 데이터를 전달할 때 사용한다.
        """

        record = await self.get_search_record(
            search_id=search_id
        )

        if record is None:
            return None

        return record.search_response

    async def update_search_status(
        self,
        search_id: str,
        status: SearchStatus,
    ) -> bool:
        """
        검색 실행 기록의 처리 상태를 변경한다.

        completed: 검색 완료
        processing: 후속 처리 진행 중
        failed: 처리 실패

        대상 문서가 존재하면 True를 반환한다.
        """

        result = await self.collection.update_one(
            {"_id": search_id},
            {
                "$set": {
                    "status": status,
                    "updated_at": datetime.now(
                        timezone.utc
                    ),
                }
            },
        )

        return result.matched_count > 0


search_repository = SearchRepository()
