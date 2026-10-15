from __future__ import annotations

import asyncio

from agents.registry import (
    AGENT_LOADERS,
    reload_agent,
)
from core.deep_agent.agent import (
    create_deep_agent,
)
from core.prompt.prompt_service import (
    prompt_service,
)
from registry.agent_registry import (
    AGENT_REGISTRY,
)


class AgentReloadManager:

    def __init__(self):
        self._prompt_versions: dict[
            str,
            int,
        ] = {}

        self._lock = asyncio.Lock()

    async def _get_prompt_version(
        self,
        agent_name: str,
    ) -> int | None:

        agent = AGENT_REGISTRY.get(
            agent_name
        )

        if agent is None:
            raise ValueError(
                "Agent not registered: "
                f"{agent_name}"
            )

        prompt_key = agent.get(
            "prompt_key"
        )

        if not prompt_key:
            return None

        return await (
            prompt_service.get_prompt_version(
                prompt_key
            )
        )

    async def initialize(
        self,
    ) -> None:
        """
        서버 시작 시 현재 Prompt version을
        기준값으로 저장한다.
        """

        for agent_name in AGENT_LOADERS:

            version = await (
                self._get_prompt_version(
                    agent_name
                )
            )

            if version is None:
                continue

            self._prompt_versions[
                agent_name
            ] = version

    async def refresh_if_needed(
        self,
        app,
    ) -> bool:
        """
        Prompt version이 변경된 SubAgent가 있으면
        해당 Agent만 다시 등록한 뒤
        Main DeepAgent를 재생성한다.
        """

        changed_agents: list[str] = []

        for agent_name in AGENT_LOADERS:

            remote_version = await (
                self._get_prompt_version(
                    agent_name
                )
            )

            if remote_version is None:
                continue

            local_version = (
                self._prompt_versions.get(
                    agent_name
                )
            )

            if (
                local_version
                != remote_version
            ):
                print(
                    "[AGENT_PROMPT_CHANGED]",
                    agent_name,
                    local_version,
                    "->",
                    remote_version,
                )
                changed_agents.append(
                    agent_name
                )

        if not changed_agents:
            return False

        async with self._lock:

            reloaded = False

            for agent_name in changed_agents:

                remote_version = await (
                    self._get_prompt_version(
                        agent_name
                    )
                )

                local_version = (
                    self._prompt_versions.get(
                        agent_name
                    )
                )

                if (
                    remote_version is None
                    or local_version
                    == remote_version
                ):
                    continue

                print(
                    "[AGENT_RELOAD]",
                    agent_name,
                    local_version,
                    "->",
                    remote_version,
                )

                await reload_agent(
                    agent_name
                )

                self._prompt_versions[
                    agent_name
                ] = remote_version

                reloaded = True

            if reloaded:
                print(
                    "[DEEP_AGENT_RECREATE]"
                )
                app.state.deep_agent = (
                    await create_deep_agent()
                )

            return reloaded


agent_reload_manager = (
    AgentReloadManager()
)