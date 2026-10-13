from __future__ import annotations
import json
from typing import Any
from core.tracing.logger import (
    get_logger,
)
from models.meta_ads_analysis import CampaignMetrics, CampaignInfo
logger = get_logger(
    "meta_ads_campaign"
)
from langchain_core.runnables import (
    RunnableConfig,
)
from langchain_core.tools import tool
from tools.integrations.meta_ads_mcp.tools.common import (
    invoke_meta_mcp_tool,    
)
from core.prompt.prompt_service import (
    prompt_service,
)
from core.state.meta_ads_analysis_repository import (
    meta_ads_analysis_repository,
)

DEFAULT_CAMPAIGN_PERFORMANCE_FIELDS = [
    "amount_spent",
    "impressions",
    "reach",
    "clicks",
    "link_click",
    "ctr",
    "cpc",
    "cpm",
    "frequency",
    "app_custom_event_fb_mobile_purchase",
    "omni_purchase",
    "omni_purchase_values",
    "cost_per_omni_purchase",
    "purchase_roas",
    "average_purchases_conversion_value"
]

def _parse_ad_entities(
    result: Any,
) -> list[dict]:
    if not result:
        return []

    try:
        data = json.loads(
            result[0]["text"]
        )

        entities = data.get(
            "ad_entities",
            [],
        )

        if isinstance(
            entities,
            str,
        ):
            entities = json.loads(
                entities
            )

        return entities

    except (
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise RuntimeError(
            "Meta MCP ad_entities 응답을 "
            "파싱할 수 없습니다."
        ) from exc



@tool
async def get_meta_campaigns(
    config: RunnableConfig,
) -> Any:
    """
    고정 Meta 광고 계정의 캠페인 목록을 조회한다.
    """

    return await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload={
            "level": "campaign",
        },
        config=config,
    )

@tool
async def get_meta_campaign_skill(
    campaign_id: str,
) -> str:
    """
    특정 Meta Ads 캠페인에 등록된 분석 Skill을 조회한다.

    캠페인 분석 전에 호출하여
    해당 캠페인의 분석 기준을 확인한다.

    조회 순서:
    Redis Cache → MongoDB
    """

    return await prompt_service.get_skill(
        agent="meta_ads",
        skill_name=campaign_id,
    )

@tool
async def get_meta_campaign_performance(
    campaign_id: str,
    config: RunnableConfig,
    date_preset: str | None = None,
    time_range: dict[str, str] | None = None,
) -> list[dict]:
    """
    특정 Meta Ads 캠페인의 기간 전체 성과를 조회한다.

    기간 비교 시에는 누적 기간을 차감하지 않고,
    time_range로 각 기간을 별도 조회한다.
    """

    payload = {
        "level": "campaign",
        "fields": DEFAULT_CAMPAIGN_PERFORMANCE_FIELDS,
        "object_ids": [
            campaign_id,
        ],
        "date_preset": date_preset,
        "time_range": (
            json.dumps(time_range)
            if time_range
            else None
        ),
    }

    raw_result = await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload=payload,
        config=config,
    )

    return _parse_ad_entities(
        raw_result
    )


def _to_float(
    value: Any,
) -> float | None:
    if value is None:
        return None

    if isinstance(
        value,
        dict,
    ):
        value = value.get(
            "value"
        )

    if isinstance(
        value,
        str,
    ):
        value = value.replace(
            "%",
            "",
        )

    try:
        return float(
            value
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

def _get_common_value(
    items: list[dict],
    field: str,
) -> Any | None:
    """
    여러 객체에서 특정 필드 값이 모두 동일하면
    해당 값을 반환한다.

    서로 다른 값이 존재하면 None을 반환한다.
    """

    values = {
        item.get(field)
        for item in items
        if item.get(field) is not None
    }

    if len(values) == 1:
        return next(
            iter(values)
        )

    return None

def _build_daily_snapshot(
    raw: dict,
    campaign_info: CampaignInfo,
) -> dict | None:
    """
    Meta MCP의 일별 Raw 성과를
    기존 MongoDB Daily Snapshot 구조로 변환한다.

    AOV는 Meta에서 조회된 값을 사용하고,
    CVR만 구매수 / 링크클릭 × 100으로 계산한다.
    """

    analysis_date = (
        raw.get("date_start")
        or raw.get("date")
    )

    if not analysis_date:
        return None

    purchases = _to_float(
        raw.get("omni_purchase")
    )

    link_click = _to_float(
        raw.get("link_click")
    )

    cvr = None

    if (
        purchases is not None
        and link_click is not None
        and link_click > 0
    ):
        cvr = (
            purchases
            / link_click
            * 100
        )

    metrics = CampaignMetrics(
        spend=_to_float(
            raw.get("amount_spent")
        ),
        purchases=purchases,
        cost_per_purchase=_to_float(
            raw.get(
                "cost_per_omni_purchase"
            )
        ),
        purchase_value=_to_float(
            raw.get(
                "omni_purchase_values"
            )
        ),
        purchase_roas=_to_float(
            raw.get(
                "purchase_roas"
            )
        ),
        impressions=_to_float(
            raw.get("impressions")
        ),
        reach=_to_float(
            raw.get("reach")
        ),
        frequency=_to_float(
            raw.get("frequency")
        ),
        clicks=_to_float(
            raw.get("clicks")
        ),
        link_click=link_click,
        ctr=_to_float(
            raw.get("ctr")
        ),
        cpc=_to_float(
            raw.get("cpc")
        ),
        cpm=_to_float(
            raw.get("cpm")
        ),
        cvr=cvr,
        aov=_to_float(
            raw.get(
                "average_purchases_conversion_value"
            )
        ),
    )
    

    return {
        "campaign": campaign_info.model_dump(
            exclude_none=True,
        ),
        "analysis_date": analysis_date,
        "metrics": metrics.model_dump(
            exclude_none=True,
        ),
    }


@tool
async def sync_meta_campaign_daily_performance(
    campaign_id: str,
    time_range: dict[str, str],
    config: RunnableConfig,
) -> dict:
    """
    특정 Meta 캠페인의 일별 성과를 조회하여
    MongoDB에 날짜별 Snapshot으로 동기화한다.

    Excel Dashboard 생성에 필요한 일별 데이터를 준비할 때 사용한다.

    Raw 일별 데이터는 LLM에 반환하지 않고,
    기존 Daily Snapshot 형식으로 정규화한 뒤
    MongoDB에 직접 저장한다.
    """
    campaign_result = await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload={
            "level": "campaign",
            "fields": [
                "objective",
            ],
            "object_ids": [
                campaign_id,
            ],
        },
        config=config,
    )

    campaign_entities = _parse_ad_entities(
        campaign_result
    )

    campaign_entity = (
        campaign_entities[0]
        if campaign_entities
        else {}
    )
    # 광고 세트에서 optimization_goal 조회
    adset_result = await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload={
            "level": "adset",
            "fields": [
                "optimization_goal",
                "promoted_object",
            ],
            "filtering": [
                {
                    "field": "campaign.id",
                    "operator": "IN",
                    "value": [
                        campaign_id,
                    ],
                }
            ],
        },
        config=config,
    )

    adsets = _parse_ad_entities(
        adset_result
    )

    optimization_goal = _get_common_value(
        adsets,
        "optimization_goal",
    )
    conversion_events = {
        promoted_object.get("custom_event_type")
        for adset in adsets
        if isinstance(
            (
                promoted_object := adset.get(
                    "promoted_object"
                )
            ),
            dict,
        )
        and promoted_object.get(
            "custom_event_type"
        )
    }

    conversion_event = (
        next(iter(conversion_events))
        if len(conversion_events) == 1
        else None
    )

    campaign_info = CampaignInfo(
        campaign_id=campaign_id,
        campaign_name=(
            campaign_entity.get("name")
            or campaign_id
        ),
        objective=campaign_entity.get(
            "objective"
        ),
        optimization_goal=(
            optimization_goal
        ),
        conversion_event=(
            conversion_event
        ),
    )
    payload = {
        "level": "campaign",
        "fields": (
            DEFAULT_CAMPAIGN_PERFORMANCE_FIELDS
        ),
        "object_ids": [
            campaign_id,
        ],
        "time_range": json.dumps(
            time_range
        ),
        "time_increment": "1",
    }

    raw_result = await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload=payload,
        config=config,
    )

    raw_snapshots = _parse_ad_entities(
        raw_result
    )

    snapshots = []

    for raw in raw_snapshots:
        snapshot = _build_daily_snapshot(
            raw=raw,
            campaign_info=campaign_info,
        )

        if snapshot is not None:
            snapshots.append(
                snapshot
            )

    saved_count = await (
        meta_ads_analysis_repository
        .save_daily_snapshots(
            campaign_id=campaign_id,
            snapshots=snapshots,            
        )
    )

    return {
        "status": "saved",
        "campaign_id": campaign_id,
        "start_date": (
            time_range.get("since")
        ),
        "end_date": (
            time_range.get("until")
        ),
        "fetched_count": len(
            raw_snapshots
        ),
        "saved_count": saved_count,
    }

@tool
async def get_meta_analysis_targets() -> dict:
    """
    분석 Skill이 등록된 Meta 캠페인 목록을 조회한다.

    Meta Ads Agent에 등록된 활성 Skill을 기준으로
    분석 대상 캠페인을 반환한다.

    정기 분석 또는 등록된 캠페인 분석 시
    전체 Meta 캠페인 목록 대신 이 Tool을 우선 사용한다.
    """

    skills = await (
        prompt_service
        .get_active_skills_by_agent(
            "meta_ads"
        )
    )

    campaign_ids = sorted({
        skill.skill_name
        for skill in skills
        if (
            skill.skill_name
            and skill.skill_name.isdigit()
        )
    })

    return {
        "campaign_ids": campaign_ids,
        "count": len(campaign_ids),
    }
