import time
from typing import Optional

from src.core.mongo_client import get_collection


"""
도메인 에이전트 라우팅 관련 로직과 캐싱을 담당하는 모듈입니다.
라우팅은 사용자의 발화를 분석하여 어떤 행동을 취해야 할지 분류하는 과정입니다. 
이 모듈에서는 라우팅에 필요한 도메인과 에이전트 정보를 MongoDB에서 조회하여 라우팅 컨텍스트를 구성하고, 이를 캐싱하여 성능을 최적화합니다. 
실제 파라미터 추출과 행동 수행은 extract_parameters 노드에서 처리됩니다. 라우팅 노드는 오직 어떤 행동을 취해야 할지 결정하는 역할만 수행합니다.
"""

_ROUTING_CONTEXT_CACHE: dict[str, Optional[object]] = {
    "expires_at": 0.0,
    "value": None,
}
ROUTING_CONTEXT_TTL_SECONDS = 300


def clear_routing_context_cache() -> None:
    _ROUTING_CONTEXT_CACHE["expires_at"] = 0.0
    _ROUTING_CONTEXT_CACHE["value"] = None


async def build_routing_context(force_refresh: bool = False) -> str:
    # force_refresh가 강제되지 않았고, 저장된 문자열이 있으며, 만료 시간(5분)이 지나지 않았다면 DB 조회 없이 즉시 캐시된 문자열을 반환하여 성능을 최적화
    now = time.monotonic()
    cached_value = _ROUTING_CONTEXT_CACHE.get("value")
    expires_at = float(_ROUTING_CONTEXT_CACHE.get("expires_at") or 0.0)
    if not force_refresh and isinstance(cached_value, str) and now < expires_at:
        return cached_value

    # 도메인 컬렉션 조회
    domain_collection = get_collection("boa_domain_registry")
    agent_collection = get_collection("boa_agent_registry")

    # MongoDB에서 활성화된 도메인 리스트를 domain_id 기준으로 정렬하여 조회하고,
    domains = await domain_collection.find(
        {"enabled": True},
        {"_id": 0},
    ).sort("domain_id", 1).to_list(None)
    # 각 도메인에 대해 활성화된 에이전트 리스트를 agent_id 기준으로 정렬하여 조회
    context_lines: list[str] = []
    for domain in domains:
        domain_id = domain["domain_id"]
        agents = await agent_collection.find(
            {"domain_id": domain_id, "enabled": True},
            {"_id": 0}, # 몽고db의 기본 _id 필드 제외
        ).sort("agent_id", 1).to_list(None)

        agent_descriptions = "\n    ".join(
            f"- {agent.get('agent_id')}: {agent.get('description', '')}"
            for agent in agents
        )
        if not agent_descriptions:
            agent_descriptions = "- 등록된 활성 서브에이전트 없음"

        context_lines.append(
            "\n".join(
                [
                    f"도메인: {domain_id} ({domain.get('name', domain_id)})",
                    f"설명: {domain.get('description', '')}",
                    "처리 가능 업무:",
                    f"    {agent_descriptions}",
                ]
            )
        )

    value = "\n\n".join(context_lines)
    _ROUTING_CONTEXT_CACHE["value"] = value
    _ROUTING_CONTEXT_CACHE["expires_at"] = now + ROUTING_CONTEXT_TTL_SECONDS
    return value
