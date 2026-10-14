from __future__ import annotations

from langchain_core.tools import (
    tool,
)

from core.state.meta_ads_analysis_repository import (
    meta_ads_analysis_repository,
)

from models.meta_ads_analysis import (
    CampaignAnalysisSnapshot,
)

@tool
async def save_campaign_analysis_snapshot(
    snapshot: CampaignAnalysisSnapshot,
) -> dict:
    """
    완료된 Meta Ads 캠페인 분석 결과를 저장한다.

    일별 원본 성과 데이터는
    sync_meta_campaign_daily_performance에서 별도로 저장한다.

    분석 완료 후 최종 응답 전에 호출한다.

    analysis의 funnel, segment, diagnosis,
    limitations, actions는 performance 하위가 아니라
    analysis의 직접 하위 필드로 전달한다.
    """

    data = snapshot.model_dump(
        exclude_none=True,
    )

    await (
        meta_ads_analysis_repository
        .update_analysis_to_daily_snapshot(
            data
        )
    )

    return {
        "status": "saved",
        "campaign_id": (
            snapshot
            .campaign
            .campaign_id
        ),
        "analysis_date": (
            snapshot.analysis_date
        ),
    }

@tool
async def get_campaign_analysis_snapshot(
    campaign_id: str,
    analysis_date: str,
) -> dict:
    """
    특정 날짜의 저장된 Meta Ads 캠페인 분석 Snapshot을 조회한다.

    주로 현재 성과를 전일과 비교할 때 사용한다.
    """

    result = await (
        meta_ads_analysis_repository
        .get_snapshot(
            campaign_id=campaign_id,
            analysis_date=analysis_date,
        )
    )

    if result is None:
        return {
            "status": "not_found",
        }

    return {
        "status": "found",
        "snapshot": result,
    }