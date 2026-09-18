from __future__ import annotations

import json

from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from langgraph.types import Command

from core.state.meta_ads_analysis_repository import (
    meta_ads_analysis_repository,
)
from tools.integrations.meta_ads_mcp.dashboard.builder import (
    build_meta_ads_dashboard,
)


@tool
async def create_meta_ads_dashboard(
    campaign_id: str,
    start_date: str,
    end_date: str,
    runtime: ToolRuntime,
) -> Command:
    """
    저장된 Meta Ads 캠페인 Snapshot을 조회해
    Excel Dashboard를 생성한다.

    Meta Ads API를 새로 조회하지 않고,
    MongoDB에 저장된 Snapshot만 사용한다.
    """

    snapshots = (
        await meta_ads_analysis_repository
        .get_history(
            campaign_id=campaign_id,
            start_date=start_date,
            end_date=end_date,
        )
    )

    if not snapshots:
        result = {
            "status": "no_data",
            "message": (
                "해당 기간의 Snapshot이 없습니다."
            ),
        }

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

    file_path = build_meta_ads_dashboard(
        snapshots=snapshots,
    )

    result = {
        "status": "created",
        "campaign_id": campaign_id,
        "start_date": start_date,
        "end_date": end_date,
        "snapshot_count": len(
            snapshots
        ),
        "file_path": file_path,
    }

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