from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.runnables import (
    RunnableConfig,
)
from langchain_core.tools import tool

from tools.integrations.meta_ads_mcp.tools.common import (
    invoke_meta_mcp_tool,
    
)


def _parse_numeric_value(
    value: Any,
) -> float | None:
    if value is None:
        return None

    text = re.sub(
        r"[^\d.\-]",
        "",
        str(value),
    )

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def _parse_campaign_performance(
    result: Any,
) -> dict:
    if result is None:
        return {}

    if not isinstance(
        result,
        list,
    ):
        raise RuntimeError(
            "Meta MCP 응답 형식이 예상과 다릅니다. "
            f"type={type(result).__name__}, "
            f"value={result!r}"
        )

    if not result:
        return {}

    first = result[0]

    if not isinstance(
        first,
        dict,
    ):
        raise RuntimeError(
            "Meta MCP 응답의 첫 번째 요소가 dict가 아닙니다. "
            f"type={type(first).__name__}, "
            f"value={first!r}"
        )

    text = first.get(
        "text"
    )

    if not text:
        return {}

    if isinstance(
        text,
        dict,
    ):
        outer = text

    elif isinstance(
        text,
        str,
    ):
        text = text.strip()

        if not text:
            return {}

        try:
            outer = json.loads(
                text
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Meta MCP text 응답이 JSON이 아닙니다. "
                f"value={text!r}"
            ) from exc

    else:
        raise RuntimeError(
            "Meta MCP text 타입이 예상과 다릅니다. "
            f"type={type(text).__name__}, "
            f"value={text!r}"
        )

    if not isinstance(
        outer,
        dict,
    ):
        raise RuntimeError(
            "Meta MCP JSON 응답이 dict가 아닙니다. "
            f"type={type(outer).__name__}, "
            f"value={outer!r}"
        )

    ad_entities_raw = outer.get(
        "ad_entities"
    )

    if not ad_entities_raw:
        return {}

    if isinstance(
        ad_entities_raw,
        str,
    ):
        try:
            entities = json.loads(
                ad_entities_raw
            )
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Meta MCP ad_entities 응답이 "
                "JSON 문자열이 아닙니다. "
                f"value={ad_entities_raw!r}"
            ) from exc

    elif isinstance(
        ad_entities_raw,
        list,
    ):
        entities = ad_entities_raw

    else:
        raise RuntimeError(
            "Meta MCP ad_entities 타입이 예상과 다릅니다. "
            f"type={type(ad_entities_raw).__name__}, "
            f"value={ad_entities_raw!r}"
        )

    if not entities:
        return {}

    entity = entities[0]

    if not isinstance(
        entity,
        dict,
    ):
        raise RuntimeError(
            "Meta MCP campaign entity 타입이 예상과 다릅니다. "
            f"type={type(entity).__name__}, "
            f"value={entity!r}"
        )

    return {
        "campaign_id": entity.get(
            "id"
        ),
        "campaign_name": entity.get(
            "name"
        ),
        "metrics": {
            "spend": _parse_numeric_value(
                entity.get(
                    "amount_spent"
                )
            ),
            "purchases": _parse_numeric_value(
                entity.get(
                    "app_custom_event_fb_mobile_purchase"
                )
                or entity.get(
                    "omni_purchase"
                )
            ),
            "cost_per_purchase": _parse_numeric_value(
                entity.get(
                    "cost_per_omni_purchase"
                )
            ),
            "purchase_value": _parse_numeric_value(
                entity.get(
                    "omni_purchase_values"
                )
            ),
            "purchase_roas": _parse_numeric_value(
                entity.get(
                    "purchase_roas"
                )
            ),
            "impressions": _parse_numeric_value(
                entity.get(
                    "impressions"
                )
            ),
            "reach": _parse_numeric_value(
                entity.get(
                    "reach"
                )
            ),
            "clicks": _parse_numeric_value(
                entity.get(
                    "clicks"
                )
            ),
            "ctr": _parse_numeric_value(
                entity.get(
                    "ctr"
                )
            ),
            "cpc": _parse_numeric_value(
                entity.get(
                    "cpc"
                )
            ),
            "cpm": _parse_numeric_value(
                entity.get(
                    "cpm"
                )
            ),
            "frequency": _parse_numeric_value(
                entity.get(
                    "frequency"
                )
            ),
        },
    }


DEFAULT_CAMPAIGN_PERFORMANCE_FIELDS = [
    "amount_spent",
    "impressions",
    "reach",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "frequency",
    "app_custom_event_fb_mobile_purchase",
    "omni_purchase",
    "omni_purchase_values",
    "cost_per_omni_purchase",
    "purchase_roas",
]


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
async def get_meta_campaign_performance(
    campaign_id: str,
    config: RunnableConfig,
    date_preset: str | None = None,
    time_range: str | None = None,
    time_increment: str | None = None,
) -> dict:
    """
    고정 Meta 광고 계정의 특정 캠페인 성과를 조회한다.

    일일 캠페인 분석에 필요한 기본 성과 필드를 조회한다.
    광고 세트/광고 단위 분석은 별도 Tool을 사용한다.
    """

    payload = {
        "level": "campaign",
        "fields": (
            DEFAULT_CAMPAIGN_PERFORMANCE_FIELDS
        ),
        "object_ids": [
            campaign_id,
        ],
        "date_preset": date_preset,
        "time_range": time_range,
        "time_increment": time_increment,
    }

    raw_result = await invoke_meta_mcp_tool(
        tool_name="ads_get_ad_entities",
        payload=payload,
        config=config,
    )

    return _parse_campaign_performance(
        raw_result
    )