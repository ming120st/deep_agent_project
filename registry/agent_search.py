from __future__ import annotations

import re
from typing import Any

from registry.agent_registry import (
    get_agent_catalog,
)


def _normalize(text: str) -> str:
    """검색 비교를 위해 문자열을 정규화한다."""

    return re.sub(
        r"\s+",
        " ",
        text.lower().strip(),
    )


def _calculate_score(
    query: str,
    agent: dict[str, Any],
) -> float:
    """사용자 요청과 Agent description의 관련도를 계산한다."""

    normalized_query = _normalize(query)
    description = _normalize(
        agent["description"]
    )

    query_tokens = set(
        normalized_query.split()
    )

    description_tokens = set(
        description.split()
    )

    overlap = (
        query_tokens
        & description_tokens
    )

    return float(len(overlap))


async def search_subagents(
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """사용자 요청과 관련된 SubAgent 후보를 반환한다."""

    catalog = get_agent_catalog()

    scored_agents = [
        {
            **agent,
            "score": _calculate_score(
                query=query,
                agent=agent,
            ),
        }
        for agent in catalog
    ]

    scored_agents = [
        agent
        for agent in scored_agents
        if agent["score"] > 0
    ]
    
    scored_agents.sort(
        key=lambda item: item["score"],
        reverse=True,
    )
    
    return scored_agents[:top_k]