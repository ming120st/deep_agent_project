from __future__ import annotations
import os
from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)

META_MCP_URL = os.environ.get("META_MCP_URL")

def create_meta_ads_mcp_client() -> MultiServerMCPClient:
    access_token = os.environ.get(
        "META_ACCESS_TOKEN"
    )

    if not access_token:
        raise RuntimeError(
            "META_ACCESS_TOKEN 환경변수가 설정되지 않았습니다."
        )

    return MultiServerMCPClient(
        {
            "meta_ads": {
                "url": META_MCP_URL,
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