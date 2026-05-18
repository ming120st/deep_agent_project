from src.core.mongo_client import get_collection


async def get_agent_registry(domain_id: str = "analytics") -> list[dict]:
    collection = get_collection("agent_registry")
    return await collection.find(
        {"domain_id": domain_id, "enabled": True},
        {"_id": 0},
    ).to_list(None)


async def get_domain_capabilities(domain_id: str = "analytics") -> list[str]:
    agents = await get_agent_registry(domain_id)
    return [agent["agent_id"] for agent in agents]
