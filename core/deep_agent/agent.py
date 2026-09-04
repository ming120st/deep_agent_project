import agents.registry

from deepagents import (
    create_deep_agent as create_official_deep_agent,
)
from langchain.agents.middleware import TodoListMiddleware

from registry.agent_registry import get_subagents
from core.llm.model_factory import LLM_LIGHT
from core.prompt.dependencies import prompt_service
from core.deep_agent.state import MainDeepAgentState
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from core.tracing.logger import get_logger


logger = get_logger("deep_agent")


async def create_deep_agent():
    system_prompt = await prompt_service.render(
        "deep_agent.main.system",
    )

    subagents = get_subagents()

    logger.info(
        "[SUBAGENT_REGISTRY] count=%s names=%s",
        len(subagents),
        [
            item.get("name")
            for item in subagents
        ],
    )

    return create_official_deep_agent(
        model=LLM_LIGHT,
        subagents=subagents,
        middleware=[
            TodoListMiddleware(),
        ],
        system_prompt=system_prompt,
        state_schema=MainDeepAgentState,
        checkpointer=InMemorySaver(),
        name="deep_agent",
    )