"""
Startup registry sync for the domain-agent architecture.

The legacy boa_agents sync deleted documents that were not present in
AGENT_REGISTRY. Domain routing now reads domain_registry and agent_registry, so
startup sync seeds those collections and leaves boa_agents untouched for
backward-compatible dashboards/history.
"""

from scripts.init_domain_agent_registries import (
    upsert_agent_registry,
    upsert_domain_registry,
)
from src.core.router import clear_routing_context_cache


async def sync_registry_to_mongo() -> None:
    await upsert_domain_registry()
    await upsert_agent_registry()
    clear_routing_context_cache()
    print("[DomainRegistrySync] domain_registry / agent_registry sync complete")
