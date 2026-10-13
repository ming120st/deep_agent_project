"""
Meta Ads 분석 결과를 MongoDB에 저장하고,
Excel/비교 분석용으로 다시 읽어오는 DB 접근 전용 클래스
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
    COLLECTION_NAME = (
        "meta_ads_campaign_analysis"
    )

    @property
    def collection(self):
        return mongo_db[
            self.COLLECTION_NAME
        ]

    async def save_daily_snapshots(
        self,
        campaign_id: str,
        snapshots: list[dict],
    ) -> int:
        """
        Meta Ads 일별 성과를 날짜별로 저장한다.

        동일 캠페인 + 날짜가 존재하면
        해당 일자의 metrics를 갱신한다.
        """

        now = datetime.now(
            KST
        )

        saved_count = 0

        for snapshot in snapshots:
            analysis_date = snapshot.get(
                "analysis_date"
            )

            metrics = snapshot.get(
                "metrics"
            )

            campaign = snapshot.get(
                "campaign"
            )

            if (
                not analysis_date
                or metrics is None
                or not campaign
            ):
                continue

            await self.collection.update_one(
                {
                    "campaign.campaign_id": (
                        campaign_id
                    ),
                    "analysis_date": (
                        analysis_date
                    ),
                },
                {
                    "$set": {
                        "campaign": campaign,
                        "analysis_date": (
                            analysis_date
                        ),
                        "metrics": metrics,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "created_at": now,
                    },
                },
                upsert=True,
            )

            saved_count += 1

        return saved_count

    async def update_analysis_to_daily_snapshot(
        self,
        data: dict,
    ) -> None:
        """
        이미 저장된 Daily Snapshot에
        분석 결과를 추가한다.
        """

        campaign = data[
            "campaign"
        ]

        analysis = data[
            "analysis"
        ]

        period = analysis[
            "period"
        ]

        # 실제 광고 성과 기준일
        analysis_date = period[
            "end_date"
        ]

        # 에이전트가 분석한 날짜
        analysis_run_date = data[
            "analysis_date"
        ]

        now = datetime.now(
            KST
        )

        result = await self.collection.update_one(
            {
                "campaign.campaign_id": (
                    campaign["campaign_id"]
                ),
                "analysis_date": (
                    analysis_date
                ),
            },
            {
                "$set": {
                    "campaign": campaign,
                    "analysis": analysis,
                    
                    "analysis_run_date": (
                        analysis_run_date
                    ),
                    "updated_at": now,
                },
            },
            upsert=False,
        )

        if result.matched_count == 0:
            raise RuntimeError(
                "Daily Meta Ads snapshot not found. "
                f"campaign_id={campaign['campaign_id']}, "
                f"analysis_date={analysis_date}"
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

        query = {
            "campaign.campaign_id": (
                campaign_id
            ),
            "analysis_date": {
                "$gte": start_date,
                "$lte": end_date,
            },
        }

        cursor = (
            self.collection
            .find(
                query,
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


meta_ads_analysis_repository = (
    MetaAdsAnalysisRepository()
)