# Agent 정의를 등록하고 DB 설정을 반영해
# 최종 SubAgent 구성을 생성하는 레지스트리

from __future__ import annotations

from typing import Any

from core.deep_agent.agent_config import (
    get_agent_config,
    get_enabled_tools,
    is_agent_enabled,
    sync_agent_to_db,
    disable_missing_agents,
)
from core.llm.model_service import (
    model_service,
)
from core.prompt.prompt_service import (
    prompt_service,
)

# 코드에 등록한 Agent 정의를 메모리에 저장
AGENT_REGISTRY: dict[
    str,
    dict[str, Any],
] = {}




def _compiled_subagent(
    *,
    name: str,
    description: str,
    runnable: Any,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "runnable": runnable,
    }

def _raw_subagent(
    *,
    agent: dict[str, Any],
    model: Any,
    tools: list[Any],
) -> dict[str, Any]:
    return {
        "name": agent["name"],
        "description": agent["description"],
        "system_prompt": agent.get(
            "system_prompt"
        ),
        "model": model,
        "tools": tools,
        "middleware": agent.get("middleware", []),
        "skills": agent.get("skills", []),
    }

def register_agent(
    *,
    name: str,
    description: str,
    system_prompt: str | None = None,
    prompt_key: str | None = None,
    tools: list[Any] | None = None,
    skills: list[str] | None = None,
    planning_skill: str | None = None,
    middleware: list[Any] | None = None,
    runnable: Any | None = None,
) -> None:

    agent = {
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "prompt_key": prompt_key,
        "tools": tools or [],
        "skills": skills or [],    
        "planning_skill": planning_skill,
        "middleware": middleware or [],
    }

    if runnable is not None:
        agent["runnable"] = runnable

    AGENT_REGISTRY[name] = agent


async def sync_agents_to_db() -> None:
    """
    코드에 등록된 Agent 정의를 DB 설정과 동기화한다.
    """

    registered_agent_ids = set(
        AGENT_REGISTRY.keys()
    )

    for agent in AGENT_REGISTRY.values():
        await sync_agent_to_db(
            agent
        )

    await disable_missing_agents(
        registered_agent_ids
    )


async def get_subagents() -> list[
    dict[str, Any]
]:
    """
    Registry의 Agent 정의에 DB 설정을 적용하여
    Deep Agent가 사용할 최종 SubAgent 목록을 만든다.

    - Runnable SubAgent:
      이미 생성된 runnable을 그대로 사용한다.

    - Sandbox SubAgent:
      DB 설정을 적용한 model/tools를 사용해
      DockerSandboxBackend 기반 Agent를 생성한다.

    - 일반 SubAgent:
      기존 방식대로 model/tools를 적용한다.
    """

    result = []

    for agent in AGENT_REGISTRY.values():
        agent_id = agent["name"]

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

        # =====================================================
        # 1. 이미 생성된 Runnable SubAgent
        # =====================================================
        if "runnable" in agent:
            result.append(
                _compiled_subagent(
                    name=agent["name"],
                    description=agent[
                        "description"
                    ],
                    runnable=agent[
                        "runnable"
                    ],
                )
            )

            continue

        # =====================================================
        # 2. DB 설정 적용
        # =====================================================
        model_name = agent_config.get(
            "model"
        )

        model = await model_service.get_model(
            model_name
        )

        enabled_tools = await get_enabled_tools(
            agent_id=agent_id,
            tools=agent.get(
                "tools",
                [],
            ),
        )
        # =====================================================
        # 3. 일반 SubAgent
        # =====================================================
        subagent = _raw_subagent(
            agent=agent,
            model=model,
            tools=enabled_tools,
        )

        result.append(
            subagent
        )

    return result


async def get_planning_skill_refs() -> list[
    dict[str, str]
]:
    """
    MongoDB에 등록된 활성 Planning Skill 목록을
    동적으로 조회한다.

    Main Agent와 SubAgent의 Planning Skill을
    구분하지 않고 조회한다.
    """

    return await (
        prompt_service
        .get_active_planning_skill_refs()
    )
