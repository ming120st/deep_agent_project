from __future__ import annotations

from typing import Any

from core.database.agent import (
    get_enabled_tools,
    is_agent_enabled,
    sync_agent_to_db,
)
from core.database.mongo import mongo_db


AGENT_REGISTRY: dict[
    str,
    dict[str, Any],
] = {}


def register_agent(
    *,
    name: str,
    description: str,
    system_prompt: str | None = None,
    model: Any = None,
    tools: list[Any] | None = None,
    skills: list[str] | None = None,
    middleware: list[Any] | None = None,
    runnable: Any = None,
) -> None:

    if runnable is not None:
        AGENT_REGISTRY[name] = {
            "name": name,
            "description": description,
            "runnable": runnable,
        }
        return

    AGENT_REGISTRY[name] = {
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "model": model,
        "tools": tools or [],
        "skills": skills or [],
        "middleware": middleware or [],
    }


async def get_agent_catalog() -> list[
    dict[str, Any]
]:
    cursor = mongo_db[
        "agent_registry"
    ].find(
        {},
        {
            "_id": 0,
            "agent_id": 1,
            "name": 1,
            "description": 1,
            "domain_id": 1,
            "enabled": 1,
        },
    )

    return await cursor.to_list(
        length=None
    )


async def sync_agents_to_db() -> None:
    for agent in (
        AGENT_REGISTRY.values()
    ):
        await sync_agent_to_db(
            agent
        )


async def get_subagents() -> list[
    dict[str, Any]
]:
    result = []

    for agent in (
        AGENT_REGISTRY.values()
    ):
        agent_id = agent["name"]

        enabled = await is_agent_enabled(
            agent_id
        )

        if not enabled:
            continue

        if "runnable" in agent:
            result.append(
                agent
            )
            continue

        enabled_tools = (
            await get_enabled_tools(
                agent_id=agent_id,
                tools=agent.get(
                    "tools",
                    [],
                ),
            )
        )

        result.append(
            {
                **agent,
                "tools": enabled_tools,
            }
        )

    return result