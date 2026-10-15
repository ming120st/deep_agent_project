from core.prompt.prompt_service import prompt_service
from registry.agent_registry import register_agent
from tools.integrations.google_workspace_mcp.runtime import (
    google_workspace_runtime,
)

GOOGLE_WORKSPACE_PROMPT_KEY = (
    "deep_agent.subagent.google_workspace.system"
)
async def register_drive_agent() -> None:
    system_prompt = await prompt_service.render(
        GOOGLE_WORKSPACE_PROMPT_KEY,
    )

    register_agent(
        name="google_workspace",
        description=(
            "Google Drive와 Gmail 관련 업무가 "
            "필요할 때 사용한다."
        ),
        system_prompt=system_prompt,
        prompt_key=GOOGLE_WORKSPACE_PROMPT_KEY,
        tools=(
            google_workspace_runtime
            .build_agent_tools()
        ),
    )