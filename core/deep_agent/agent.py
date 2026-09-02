from deepagents import (
    create_deep_agent as create_official_deep_agent,
)

from core.llm.model_factory import LLM_LIGHT
from core.prompt.dependencies import prompt_service
from core.deep_agent.state import MainDeepAgentState
from core.state.checkpointer import checkpointer
from core.deep_agent.middleware.subagent_filter import (
    SubAgentFilterMiddleware,
)
from registry.agent_registry import (
    get_subagents,
)


async def create_deep_agent():
    system_prompt = await prompt_service.render(
        "deep_agent.main.system",
    )

    return create_official_deep_agent(
        model=LLM_LIGHT,

        subagents=get_subagents(),

        middleware=[
            
            SubAgentFilterMiddleware(),
        ],

        system_prompt=system_prompt,
        state_schema=MainDeepAgentState,
        checkpointer=checkpointer,
        name="deep_agent",
    )