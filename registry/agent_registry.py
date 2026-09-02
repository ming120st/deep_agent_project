from __future__ import annotations

from typing import Any


AGENT_REGISTRY: dict[str, dict[str, Any]] = {}


def register_agent(
    name: str,
    description: str,
):
    def decorator(factory):
        AGENT_REGISTRY[name] = {
            "factory": factory,
            "description": description,
        }
        return factory

    return decorator



def get_agent(name: str):
    item = AGENT_REGISTRY.get(name)

    if not item:
        raise ValueError(
            f"Agent not found: {name}"
        )

    agent_or_factory = item["factory"]

    if hasattr(agent_or_factory, "ainvoke"):
        return agent_or_factory

    return agent_or_factory()

def get_agent_catalog() -> list[dict]:
    return [
        {
            "name": name,
            "description": item["description"],
        }
        for name, item in AGENT_REGISTRY.items()
    ]

def get_subagents() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": item["description"],
            "runnable": get_agent(name),
        }
        for name, item in AGENT_REGISTRY.items()
    ]