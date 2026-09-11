from __future__ import annotations

from typing import Any

from core.database.mongo import mongo_db


def _get_tool_name(
    tool: Any,
) -> str:
    name = getattr(
        tool,
        "name",
        None,
    )

    if name:
        return name

    name = getattr(
        tool,
        "__name__",
        None,
    )

    if name:
        return name

    raise ValueError(
        f"Tool 이름을 확인할 수 없습니다: {tool}"
    )


async def sync_agent_to_db(
    agent: dict[str, Any],
) -> None:
    agent_id = agent["name"]

    agent_collection = mongo_db[
        "agent_registry"
    ]

    settings_collection = mongo_db[
        "agent_settings"
    ]

    # Agent 기본정보 동기화
    await agent_collection.update_one(
        {
            "agent_id": agent_id,
        },
        {
            "$set": {
                "description": agent[
                    "description"
                ],
            },
            "$setOnInsert": {
                "agent_id": agent_id,
                "name": agent_id,
                "domain_id": "common",
                "enabled": True,
            },
        },
        upsert=True,
    )

    # runnable Agent는 Tool 동기화 제외
    if "runnable" in agent:
        return

    settings = await settings_collection.find_one(
        {
            "agent_id": agent_id,
        }
    )

    existing_tools = {}

    if settings:
        for item in settings.get(
            "tools",
            [],
        ):
            if (
                isinstance(item, dict)
                and item.get("name")
            ):
                existing_tools[
                    item["name"]
                ] = item

    synced_tools = []

    for tool in agent.get(
        "tools",
        [],
    ):
        tool_name = _get_tool_name(
            tool
        )

        existing = existing_tools.get(
            tool_name
        )

        synced_tools.append(
            {
                "name": tool_name,
                "enabled": (
                    existing.get(
                        "enabled",
                        True,
                    )
                    if existing
                    else True
                ),
            }
        )

    await settings_collection.update_one(
        {
            "agent_id": agent_id,
        },
        {
            "$set": {
                "tools": synced_tools,
            },
            "$setOnInsert": {
                "agent_id": agent_id,
                "model": "LLM_LIGHT",
                "skills": [],
                "prompt_key": None,
                "enabled": True,
            },
        },
        upsert=True,
    )


async def is_agent_enabled(
    agent_id: str,
) -> bool:
    agent = await mongo_db[
        "agent_registry"
    ].find_one(
        {
            "agent_id": agent_id,
        }
    )

    if not agent:
        return True

    return agent.get(
        "enabled",
        True,
    )


async def get_enabled_tools(
    agent_id: str,
    tools: list[Any],
) -> list[Any]:
    settings = await mongo_db[
        "agent_settings"
    ].find_one(
        {
            "agent_id": agent_id,
        }
    )

    if not settings:
        return tools

    enabled_names = {
        item["name"]
        for item in settings.get(
            "tools",
            []
        )
        if (
            isinstance(item, dict)
            and item.get(
                "enabled",
                True,
            )
        )
    }

    return [
        tool
        for tool in tools
        if _get_tool_name(tool)
        in enabled_names
    ]
async def get_agent_config(
    agent_id: str,
) -> dict | None:

    return await mongo_db[
        "agent_settings"
    ].find_one(
        {
            "agent_id": agent_id,
        },
        {
            "_id": 0,
        },
    )