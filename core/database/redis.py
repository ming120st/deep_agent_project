"""Short-lived, user-scoped JSON helpers for Azure Managed Redis.

This is deliberately a raw Redis utility. The current company-managed Redis
instance does not include RedisJSON or RediSearch, so LangGraph's official
AsyncRedisStore cannot be used against it.
"""

import json
import os
import ssl
from typing import Any, TypeAlias

from dotenv import load_dotenv
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster
from langgraph.config import get_config


load_dotenv()


REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", "10000"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

DEFAULT_TTL_SECONDS = int(os.getenv("REDIS_SHARED_TTL_SECONDS", "21600"))

RedisClient: TypeAlias = Redis | RedisCluster

_redis_client: RedisClient | None = None


def _require_redis_config() -> tuple[str, str]:
    if not REDIS_HOST:
        raise RuntimeError("REDIS_HOST is required.")
    if not REDIS_PASSWORD:
        raise RuntimeError("REDIS_PASSWORD is required.")
    return REDIS_HOST, REDIS_PASSWORD


async def get_redis_client() -> RedisClient:
    """Return the process-wide client, preferring Azure's OSS cluster mode."""
    global _redis_client

    if _redis_client is not None:
        try:
            await _redis_client.ping()
            return _redis_client
        except Exception:
            await close_redis_client()

    host, password = _require_redis_config()
    is_private_link = ".privatelink.redis.azure.net" in host.lower()
    params = {
        "host": host,
        "port": REDIS_PORT,
        "password": password,
        "ssl": True,
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
        "ssl_check_hostname": not is_private_link,
        "decode_responses": False,
        "socket_connect_timeout": 15,
        "socket_timeout": 15,
        "socket_keepalive": True,
        "health_check_interval": 30,
    }

    cluster_client: RedisCluster | None = None
    try:
        cluster_client = RedisCluster(**params)
        await cluster_client.initialize()
        await cluster_client.ping()
        client: RedisClient = cluster_client
    except Exception:
        if cluster_client is not None:
            await cluster_client.aclose()
        standalone_client = Redis(**params)
        try:
            await standalone_client.ping()
        except Exception:
            await standalone_client.aclose()
            raise
        client = standalone_client

    _redis_client = client
    return client


async def close_redis_client() -> None:
    global _redis_client

    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


def _resolve_user_id() -> str:
    try:
        config = get_config()
        configurable = config.get("configurable", {})

        user_id = (
            configurable.get("user_id")
            or configurable.get("user_email")
            or configurable.get("google_user_id")
        )

        if user_id:
            return str(user_id)

    except Exception:
        pass

    raise ValueError("user_id could not be resolved from RunnableConfig.")


def _shared_key(workflow: str, key: str) -> str:
    clean_workflow = str(workflow).strip().lstrip(":")
    clean_key = str(key).strip().lstrip(":")

    if not clean_workflow:
        raise ValueError("workflow is required.")
    if not clean_key:
        raise ValueError("key is required.")

    return f"boa:{_resolve_user_id()}:{clean_workflow}:{clean_key}"


async def put(
    workflow: str,
    key: str,
    value: Any,
    *,
    ttl: int | None = DEFAULT_TTL_SECONDS,
) -> bool:
    client = await get_redis_client()
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    redis_key = _shared_key(workflow, key)

    if ttl is None:
        return bool(await client.set(redis_key, payload))

    if ttl <= 0:
        raise ValueError("ttl must be greater than zero or None.")

    return bool(await client.set(redis_key, payload, ex=ttl))


async def get(workflow: str, key: str, *, default: Any = None) -> Any:
    client = await get_redis_client()
    value = await client.get(_shared_key(workflow, key))

    if value is None:
        return default

    if isinstance(value, bytes):
        value = value.decode("utf-8")

    return json.loads(value)


async def delete(workflow: str, key: str) -> int:
    client = await get_redis_client()
    return int(await client.delete(_shared_key(workflow, key)))


async def exists(workflow: str, key: str) -> bool:
    client = await get_redis_client()
    return bool(await client.exists(_shared_key(workflow, key)))


async def ttl(workflow: str, key: str) -> int:
    client = await get_redis_client()
    return int(await client.ttl(_shared_key(workflow, key)))


redis_put = put
redis_get = get
redis_delete = delete
redis_exists = exists
redis_ttl = ttl

"""
사용방법 예시
await redis_put("esim_reissue", "order_summary", order_data)
data = await redis_get("esim_reissue", "order_summary")
await redis_delete("esim_reissue", "order_summary")
exists = await redis_exists("esim_reissue", "order_summary")
# 여기서 예시로 esim_reissue라고 명시된 workflow이름은 agents/ 하단 디렉토리 이름과 동일하게 하는걸로!
"""