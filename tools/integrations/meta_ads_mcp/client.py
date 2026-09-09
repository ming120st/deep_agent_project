# tools/integrations/meta_ads_mcp/client.py

import os

from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)


def create_meta_ads_mcp_client():
    return MultiServerMCPClient(
        {
            "meta_ads": {
                "url": os.environ[
                    "META_ADS_MCP_URL"
                ],
                "transport": "streamable_http",
                "headers": {
                    "Authorization": (
                        "Bearer "
                        + os.environ[
                            "META_ADS_ACCESS_TOKEN"
                        ]
                    )
                },
            }
        }
    )