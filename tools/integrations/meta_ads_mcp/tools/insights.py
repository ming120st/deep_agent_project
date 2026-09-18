from __future__ import annotations

from typing import Any

from langchain_core.runnables import (
    RunnableConfig,
)
from langchain_core.tools import tool

from tools.integrations.meta_ads_mcp.tools.common import (
    invoke_meta_mcp_tool,
)


@tool
async def get_meta_performance_trend(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    Meta 광고 성과의 기간별 추이를 조회한다.

    CTR, CPC, CPM, 결과당 비용, ROAS 등의
    상승/하락 추세 분석에 사용한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name=(
            "ads_insights_performance_trend"
        ),
        payload=parameters,
        config=config,
    )


@tool
async def get_meta_anomaly_signal(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    광고 성과에서 비정상적인 변화나
    이상 징후를 조회한다.

    갑작스러운 CTR 하락, CPC 상승,
    ROAS 하락 등의 원인 탐색에 사용한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name=(
            "ads_insights_anomaly_signal"
        ),
        payload=parameters,
        config=config,
    )


@tool
async def get_meta_auction_benchmark(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    Meta 광고 경매 관련 Benchmark를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name=(
            "ads_insights_auction_ranking_benchmarks"
        ),
        payload=parameters,
        config=config,
    )


@tool
async def get_meta_industry_benchmark(
    parameters: dict,
    config: RunnableConfig,
) -> Any:
    """
    업종 기준 광고 성과 Benchmark를 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name=(
            "ads_insights_industry_benchmark"
        ),
        payload=parameters,
        config=config,
    )