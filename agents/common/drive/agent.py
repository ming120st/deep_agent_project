from pathlib import Path

from core.llm.model_factory import LLM_LIGHT
from registry.agent_registry import register_agent
from tools.integrations.google_workspace_mcp.runtime import (
    google_workspace_runtime,
)


BASE_DIR = Path(__file__).parent

SYSTEM_PROMPT = (
    BASE_DIR
    / "prompts"
    / "system.md"
).read_text(
    encoding="utf-8"
)


def register_drive_agent():
    register_agent(
        name="google_workspace",
        description=(
            "Google Drive와 Gmail 관련 업무가 "
            "필요할 때 사용한다."
        ),
        system_prompt=SYSTEM_PROMPT,
        model=LLM_LIGHT,
        tools=(
            google_workspace_runtime
            .build_agent_tools()
        ),
    )