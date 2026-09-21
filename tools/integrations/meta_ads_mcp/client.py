from __future__ import annotations

import os

from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)


def _get_required_env(
    key: str,
) -> str:
    value = os.getenv(key)

    if not value:
        raise RuntimeError(
            f"{key} 환경변수가 설정되지 않았습니다."
        )

    return value


def get_meta_ad_account_id() -> str:
    ad_account_id = _get_required_env(
        "META_AD_ACCOUNT_ID"
    )

    if ad_account_id.startswith(
        "act_"
    ):
        ad_account_id = (
            ad_account_id[4:]
        )

    return ad_account_id


def create_meta_ads_mcp_client(
) -> MultiServerMCPClient:
    meta_mcp_url = _get_required_env(
        "META_MCP_URL"
    )

    access_token = _get_required_env(
        "META_ACCESS_TOKEN"
    )

    get_meta_ad_account_id()

    return MultiServerMCPClient(
        {
            "meta_ads": {
                "url": meta_mcp_url,
                "transport": "http",
                "headers": {
                    "Authorization": (
                        f"Bearer {access_token}"
                    ),
                },
            }
        },
        handle_tool_errors=True,
    )