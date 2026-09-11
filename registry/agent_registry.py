# Agent 정의를 등록하고 DB 설정을 반영해 최종 SubAgent 구성을 생성하는 레지스트리

from __future__ import annotations
from typing import Any
from core.database.agent import (
    get_agent_config,
    get_enabled_tools,
    is_agent_enabled,
    sync_agent_to_db,
)
from core.llm.model_service import (
    model_service,
)

# 코드에 등록한 Agent 들을 메모리에 저장함.
AGENT_REGISTRY: dict[
    str,
    dict[str, Any],
] = {}

# 에이전트를 AGENT_REGISTRY 구조로 등록
def register_agent(
    *,
    name: str,
    description: str,
    system_prompt: str | None = None,
    tools: list[Any] | None = None,
    skills: list[str] | None = None,
    middleware: list[Any] | None = None,
) -> None:


    AGENT_REGISTRY[name] = {
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "tools": tools or [],
        "skills": skills or [],
        "middleware": middleware or [],
    }

# DB 랑 코드랑 agent 들 sync 맞춰줌 
async def sync_agents_to_db() -> None:
    for agent in (
        AGENT_REGISTRY.values()
    ):
        await sync_agent_to_db(
            agent
        )

# 코드에 등록된 Agent 정의에 DB 설정을 적용해서 Deep Agent가 사용할 최종 SubAgent 목록을 만든다.
async def get_subagents() -> list[
    dict[str, Any]
]:
    result = []

    for agent in (
        AGENT_REGISTRY.values()
    ):
        agent_id = agent["name"]

        # Agent 의 활성화 여부를 가져옴
        enabled = await is_agent_enabled(
            agent_id
        )

        if not enabled:
            continue

        agent_config = await get_agent_config(
            agent_id
        )

        if agent_config is None:
            raise ValueError(
                "Agent config not found: "
                f"{agent_id}"
            )

        model = agent_config.get(
            "model"
        )
        model = await model_service.get_model(
            model
        )

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
                "model": model,
                "tools": enabled_tools,
            }
        )

    return result