from deepagents import (
    create_deep_agent as create_official_deep_agent,
)
from agents.registry import (
    register_all_agents,
)
from langchain.agents.middleware import TodoListMiddleware
from deepagents.backends.filesystem import FilesystemBackend
from registry.agent_registry import get_subagents
from core.llm.model_factory import LLM_LIGHT
from core.prompt.dependencies import prompt_service
from core.deep_agent.state import MainDeepAgentState
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from core.tracing.logger import get_logger
import os


logger = get_logger("deep_agent")


async def create_deep_agent():
    system_prompt = await prompt_service.render(
        "deep_agent.main.system",
    )
    register_all_agents()
    subagents = get_subagents()

    for agent in subagents:
        print(
            agent["name"],
            agent.get("skills"),
        )

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
        backend=FilesystemBackend(
            root_dir=os.environ["DEEP_AGENT_ROOT_DIR"],
        ),
        name="deep_agent",
    )
