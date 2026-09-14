"""
Agent의 DB 설정을 저장하고 조회

주요 역할:
Agent 기본정보 DB 동기화
Tool 목록 DB 동기화
Agent 활성화 여부 조회
활성 Tool 조회
모델/skills/prompt_key 같은 설정 조회
"""
from __future__ import annotations
from typing import Any
from core.database.mongo import mongo_db
from core.llm.model_service import (
    model_service,
)
# Tool 객체에서 이름을 추출한다.
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

# 코드에 등록된 Agent 기본정보와 Tool 목록을 DB 설정과 동기화한다.
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

    settings = await settings_collection.find_one(
        {
            "agent_id": agent_id,
        }
    )
    default_model_key = None

    if settings is None:
        default_model_key = (
            await model_service
            .get_default_model_key()
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
                "model": default_model_key,
                "skills": [],
                "prompt_key": None,
                "enabled": True,
            },
        },
        upsert=True,
    )

# DB에 저장된 Agent의 활성화 여부를 조회한다.
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

# DB 설정을 기준으로 활성화된 Tool만 필터링해 반환한다.
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

# Agent의 실행 설정(model, tools, skills 등)을 DB에서 조회한다.
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

# 코드에 더 이상 등록되지 않은 Agent를 비활성화한다.
async def disable_missing_agents(
    registered_agent_ids: set[str],
) -> None:
    await mongo_db[
        "agent_registry"
    ].update_many(
        {
            "agent_id": {
                "$nin": list(
                    registered_agent_ids
                )
            }
        },
        {
            "$set": {
                "enabled": False
            }
        },
    )