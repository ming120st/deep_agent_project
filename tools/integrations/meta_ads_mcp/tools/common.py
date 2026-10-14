"""
Meta MCP 공통 호출 Wrapper

payload 정규화
→ Tool schema 확인
→ ad_account_id 자동 주입
→ client_conversation_id 자동 주입
→ 지원하지 않는 필드 제거
→ MCP Tool 호출
"""

from __future__ import annotations
from typing import Any

from langchain_core.runnables import (
    RunnableConfig,
)

from core.tracing.logger import (
    get_logger,
)
from tools.integrations.meta_ads_mcp.client import (
    get_meta_ad_account_id,
)
from tools.integrations.meta_ads_mcp.context import (
    get_meta_client_conversation_id,
)
from tools.integrations.meta_ads_mcp.runtime import (
    meta_ads_runtime,
)


logger = get_logger(
    "meta_ads_mcp"
)


def normalize_meta_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Meta MCP에 전달할 payload를 정규화한다.

    None 값은 제거하고,
    filtering의 value는 MCP 요구 형식에 맞게 list로 변환한다.
    """

    payload = {
        key: value
        for key, value in payload.items()
        if value is not None
    }

    filtering = payload.get(
        "filtering"
    )

    if isinstance(
        filtering,
        list,
    ):
        normalized = []

        for item in filtering:
            item = dict(
                item
            )

            value = item.get(
                "value"
            )

            if (
                value is not None
                and not isinstance(
                    value,
                    list,
                )
            ):
                item["value"] = [
                    value
                ]

            normalized.append(
                item
            )

        payload["filtering"] = (
            normalized
        )

    return payload


async def invoke_meta_mcp_tool(
    tool_name: str,
    payload: dict[str, Any] | None,
    config: RunnableConfig,
) -> Any:
    """
    Meta MCP Tool을 공통 방식으로 호출한다.

    Tool schema를 기준으로 필요한 공통 값을 자동 주입하고,
    지원하지 않는 payload 필드는 제거한 뒤 MCP Tool을 실행한다.
    """

    mcp_tool = meta_ads_runtime.get_tool(
        tool_name
    )

    payload = normalize_meta_payload(
        dict(
            payload or {}
        )
    )

    args_schema = (
        mcp_tool.args_schema
    )

    if isinstance(
        args_schema,
        dict,
    ):
        schema = args_schema

    else:
        schema = (
            args_schema.model_json_schema()
            if args_schema
            else {}
        )

    properties = schema.get(
        "properties",
        {},
    )

    # 광고 계정 ID를 지원하는 Tool이면
    # 환경변수의 기본 계정 ID를 자동으로 주입한다.
    if (
        "ad_account_id" in properties
        and not payload.get(
            "ad_account_id"
        )
    ):
        payload[
            "ad_account_id"
        ] = get_meta_ad_account_id()

    # conversation ID를 지원하는 Tool이면
    # 현재 Deep Agent 세션에 연결된 ID를 자동으로 주입한다.
    if (
        "client_conversation_id" in properties
        and not payload.get(
            "client_conversation_id"
        )
    ):
        payload[
            "client_conversation_id"
        ] = (
            await get_meta_client_conversation_id(
                config
            )
        )

    # MCP Tool schema에 존재하는 필드만 전달한다.
    filtered_payload = {
        key: value
        for key, value in payload.items()
        if key in properties
    }

    dropped_keys = {
        key
        for key in payload
        if key not in properties
    }

    if dropped_keys:
        logger.warning(
            "[META_MCP_PAYLOAD_DROPPED] "
            "tool=%s keys=%s",
            tool_name,
            sorted(
                dropped_keys
            ),
        )

    logger.info(
        "[META_MCP_CALL] "
        "tool=%s payload=%r",
        tool_name,
        filtered_payload,
    )

    result = await mcp_tool.ainvoke(
        filtered_payload
    )

    logger.info(
        "[META_MCP_RESULT] "
        "tool=%s type=%s result=%r",
        tool_name,
        type(
            result
        ).__name__,
        result,
    )

    return result