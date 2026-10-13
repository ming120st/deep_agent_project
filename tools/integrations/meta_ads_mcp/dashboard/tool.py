from __future__ import annotations

import hashlib
import json

from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.types import Command

from core.state.meta_ads_analysis_repository import (
    meta_ads_analysis_repository,
)
from models.meta_ads_analysis import (
    CampaignAnalysis,
)
from tools.integrations.meta_ads_mcp.dashboard.builder import (
    build_meta_ads_dashboard,
)


def _to_command(
    result: dict,
    runtime: ToolRuntime,
) -> Command:
    """
    Dashboard 처리 결과를 ToolMessage로 변환한다.
    """

    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        result,
                        ensure_ascii=False,
                    ),
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )


def _create_analysis_hash(
    analysis: CampaignAnalysis,
) -> str:
    """
    Dashboard 생성에 사용한 분석 결과를
    추적하기 위한 해시를 생성한다.
    """

    payload = json.dumps(
        analysis.model_dump(
            exclude_none=True,
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:16]


@tool
async def create_meta_ads_dashboard(
    campaign_id: str,
    start_date: str,
    end_date: str,
    comparison_start_date: str,
    comparison_end_date: str,
    runtime: ToolRuntime,
) -> Command:
    """
    MongoDB에 저장된 Meta Ads 일별 성과 Snapshot과
    저장된 캠페인 분석 결과를 사용해
    기간 비교 Excel Dashboard를 생성한다.

    - start_date ~ end_date:
      분석 대상 기간

    - comparison_start_date ~ comparison_end_date:
      비교 대상 기간

    Dashboard의 분석 내용은 Agent가 새로 생성하지 않고,
    save_campaign_analysis_snapshot을 통해 저장된
    CampaignAnalysis 결과만 사용한다.

    저장된 분석 결과가 없거나 요청 기간과 일치하지 않으면
    Dashboard를 생성하지 않고 명시적인 상태를 반환한다.

    Dashboard 생성 과정에서는 Meta Ads API를 다시 호출하지 않는다.
    """

    current_snapshots = await (
        meta_ads_analysis_repository
        .get_history(
            campaign_id=campaign_id,
            start_date=start_date,
            end_date=end_date,
        )
    )

    if not current_snapshots:
        return _to_command(
            {
                "status": "no_data",
                "message": (
                    "분석 기간의 일별 Snapshot이 없습니다."
                ),
                "campaign_id": campaign_id,
                "period": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
            },
            runtime,
        )

    comparison_snapshots = await (
        meta_ads_analysis_repository
        .get_history(
            campaign_id=campaign_id,
            start_date=comparison_start_date,
            end_date=comparison_end_date,
        )
    )

    if not comparison_snapshots:
        return _to_command(
            {
                "status": "no_comparison_data",
                "message": (
                    "비교 기간의 일별 Snapshot이 없습니다."
                ),
                "campaign_id": campaign_id,
                "comparison_period": {
                    "start_date": (
                        comparison_start_date
                    ),
                    "end_date": (
                        comparison_end_date
                    ),
                },
            },
            runtime,
        )

    # 분석 결과는 분석 기간의 마지막 날짜 Daily Snapshot에 저장된다.
    analysis_snapshot = await (
        meta_ads_analysis_repository
        .get_snapshot(
            campaign_id=campaign_id,
            analysis_date=end_date,
        )
    )

    if (
        analysis_snapshot is None
        or not analysis_snapshot.get("analysis")
    ):
        return _to_command(
            {
                "status": "no_analysis",
                "message": (
                    "저장된 캠페인 분석 결과가 없습니다. "
                    "먼저 캠페인 분석을 실행하고 저장하세요."
                ),
                "campaign_id": campaign_id,
                "period": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
            },
            runtime,
        )

    stored_analysis = analysis_snapshot[
        "analysis"
    ]

    stored_period = stored_analysis.get(
        "period",
        {},
    )

    stored_comparison_period = (
        stored_analysis.get(
            "comparison_period",
            {},
        )
    )

    if (
        stored_period.get("start_date")
        != start_date
        or stored_period.get("end_date")
        != end_date
        or stored_comparison_period.get("start_date")
        != comparison_start_date
        or stored_comparison_period.get("end_date")
        != comparison_end_date
    ):
        return _to_command(
            {
                "status": "analysis_period_mismatch",
                "message": (
                    "저장된 분석 결과의 기간과 "
                    "Dashboard 요청 기간이 일치하지 않습니다. "
                    "해당 기간의 캠페인 분석을 먼저 실행하세요."
                ),
                "campaign_id": campaign_id,
                "requested_period": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "stored_period": stored_period,
                "requested_comparison_period": {
                    "start_date": (
                        comparison_start_date
                    ),
                    "end_date": (
                        comparison_end_date
                    ),
                },
                "stored_comparison_period": (
                    stored_comparison_period
                ),
            },
            runtime,
        )

    analysis = CampaignAnalysis.model_validate(
        stored_analysis
    )

    analysis_hash = _create_analysis_hash(
        analysis
    )

    file_path = build_meta_ads_dashboard(
        current_snapshots=current_snapshots,
        comparison_snapshots=(
            comparison_snapshots
        ),
        analysis=analysis,
    )

    return _to_command(
        {
            "status": "created",
            "campaign_id": campaign_id,
            "period": {
                "start_date": start_date,
                "end_date": end_date,
                "snapshot_count": len(
                    current_snapshots
                ),
            },
            "comparison_period": {
                "start_date": (
                    comparison_start_date
                ),
                "end_date": (
                    comparison_end_date
                ),
                "snapshot_count": len(
                    comparison_snapshots
                ),
            },
            "analysis_run_date": (
                analysis_snapshot.get(
                    "analysis_run_date"
                )
            ),
            "analysis_hash": analysis_hash,
            "file_path": file_path,
        },
        runtime,
    )