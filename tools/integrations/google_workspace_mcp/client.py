"""
MCP 클라이언트 생성 로직
"""

import os

from langchain_mcp_adapters.client import (
    MultiServerMCPClient,
)


def create_google_workspace_mcp_client():
    env = {
        "GOOGLE_OAUTH_CLIENT_ID": os.environ[
            "GOOGLE_OAUTH_CLIENT_ID"
        ],
        "GOOGLE_OAUTH_CLIENT_SECRET": os.environ[
            "GOOGLE_OAUTH_CLIENT_SECRET"
        ],
    }

    return MultiServerMCPClient(
        {
            "google_workspace": {
                "command": os.environ["UVX_PATH"],
                "args": [
                    "workspace-mcp",
                    "--tools",
                    "drive",
                    "gmail",
                ],
                "transport": "stdio",
                "env": env,
            }
        }
    )
