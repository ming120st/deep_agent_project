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
from deepagents import (
    create_deep_agent as create_official_deep_agent,
)

from core.sandbox.docker_backend import (
    DockerSandboxBackend,
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
    subagent = {
        **agent,
        "model": model,
        "tools": tools,
    }

    subagent.pop(
        "planning_skill",
        None,
    )

    subagent.pop(
        "sandbox",
        None,
    )

    return subagent

def _sandbox_subagent(
    *,
    agent_id: str,
    agent: dict[str, Any],
    model: Any,
    tools: list[Any],
) -> dict[str, Any]:

    runnable = create_official_deep_agent(
        model=model,
        tools=tools,
        system_prompt=agent.get(
            "system_prompt"
        ),
        skills=agent.get(
            "skills",
            [],
        ),
        middleware=agent.get(
            "middleware",
            [],
        ),
        backend=(
            DockerSandboxBackend()
        ),
        name=agent_id,
    )

    return _compiled_subagent(
        name=agent_id,
        description=agent["description"],
        runnable=runnable,
    )

def register_agent(
    *,
    name: str,
    description: str,
    system_prompt: str | None = None,
    tools: list[Any] | None = None,
    skills: list[str] | None = None,
    skill_files: dict[str, Any] | None = None,
    planning_skill: str | None = None,
    middleware: list[Any] | None = None,
    runnable: Any | None = None,
    sandbox: bool = False,
) -> None:

    agent = {
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "tools": tools or [],
        "skills": skills or [],
        "skill_files": skill_files or {},
        "planning_skill": planning_skill,
        "middleware": middleware or [],
        "sandbox": sandbox,
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
        # 3. Sandbox SubAgent
        # =====================================================
        if agent.get("sandbox"):
            subagent = _sandbox_subagent(
                agent_id=agent_id,
                agent=agent,
                model=model,
                tools=enabled_tools,
            )

        # =====================================================
        # 4. 일반 SubAgent
        # =====================================================
        else:
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
    활성화된 Agent 중 Planning Skill이 등록된
    Agent와 Skill 이름을 반환한다.
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
            continue

        planning_skill = agent_config.get(
            "planning_skill"
        )

        if not planning_skill:
            continue

        result.append(
            {
                "agent": agent_id,
                "skill_name": planning_skill,
            }
        )

    return result
