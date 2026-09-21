"""
Meta Ads 분석 결과를 MongoDB에 저장하고, Excel/비교 분석용으로 다시 읽어오는 DB 접근 전용 클래스
"""

from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
from core.database.mongo import (
    mongo_db,
)

KST = ZoneInfo(
    "Asia/Seoul"
)

class MetaAdsAnalysisRepository:

    COLLECTION_NAME = "meta_ads_campaign_analysis"

    @property
    def collection(self):
        return mongo_db[
            self.COLLECTION_NAME
        ]

    async def save_snapshot(
        self,
        data: dict,
    ) -> None:
        campaign = data["campaign"]

        now = datetime.now(
            KST
        )

        await self.collection.update_one(
            {
                "campaign.campaign_id": (
                    campaign["campaign_id"]
                ),
                "analysis_date": (
                    data["analysis_date"]
                ),
                "analysis_version": (
                    data["analysis_version"]
                ),
            },
            {
                "$set": {
                    **data,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

    async def get_snapshot(
        self,
        campaign_id: str,
        analysis_date: str,
    ) -> dict | None:
        return await self.collection.find_one(
            {
                "campaign.campaign_id": (
                    campaign_id
                ),
                "analysis_date": (
                    analysis_date
                ),
            },
            {
                "_id": 0,
            },
        )

    async def get_history(
        self,
        campaign_id: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        cursor = (
            self.collection
            .find(
                {
                    "campaign.campaign_id": (
                        campaign_id
                    ),
                    "analysis_date": {
                        "$gte": start_date,
                        "$lte": end_date,
                    },
                },
                {
                    "_id": 0,
                },
            )
            .sort(
                "analysis_date",
                1,
            )
        )

        return await cursor.to_list(
            length=None
        )


meta_ads_analysis_repository = MetaAdsAnalysisRepository()