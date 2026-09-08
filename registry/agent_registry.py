from __future__ import annotations

from typing import Any


AGENT_REGISTRY: dict[str, dict[str, Any]] = {}


def register_agent(
    *,
    name: str,
    description: str,
    system_prompt: str | None = None,
    model: Any = None,
    tools: list[Any] | None = None,
    skills: list[str] | None = None,
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
    }


def get_agent_catalog() -> list[dict[str, str]]:
    return [
        {
            "name": item["name"],
            "description": item["description"],
        }
        for item in AGENT_REGISTRY.values()
    ]


def get_subagents() -> list[dict[str, Any]]:
    return list(
        AGENT_REGISTRY.values()
    )