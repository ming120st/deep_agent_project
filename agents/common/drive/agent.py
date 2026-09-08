from pathlib import Path

from core.llm.model_factory import LLM_LIGHT
from registry.agent_registry import register_agent
from tools.integrations.google_workspace_mcp.tools import (
    drive_search,
    drive_create_file,
    gmail_search,
    gmail_recent_messages,
    gmail_send,
)

BASE_DIR = Path(__file__).parent

SYSTEM_PROMPT = (
    BASE_DIR
    / "prompts"
    / "system.md"
).read_text(
    encoding="utf-8",
)


def register_drive_agent() -> None:
    register_agent(
        name="drive",
        description=(
            "Google Drive 파일 작업과 Gmail 메일 검색 및 조회가 필요할 때 사용한다."
        ),
        system_prompt=SYSTEM_PROMPT,
        model=LLM_LIGHT,
        tools=[
            drive_search,
            drive_create_file,
            gmail_search,
            gmail_recent_messages,
            gmail_send,
        ],
    )