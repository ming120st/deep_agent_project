import os
import ssl
import asyncio
from typing import Optional, Any
from redis import asyncio as aioredis
from dotenv import load_dotenv
from src.workflow.graph_infra.state import GraphState

load_dotenv()

# ───────────────────────────────────────────────
# 셋팅
# ───────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT", 10000))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

class RedisClient:

    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        password: Optional[str] = REDIS_PASSWORD,
    ):
        self.host = host
        self.port = port
        self.password = password
        self._client: aioredis.Redis | None = None

    # ───────────────────────────────────────────────
    # 연결
    # ───────────────────────────────────────────────
    async def connect(self) -> aioredis.Redis:
        if self._client:
            return self._client

        print(f"[Redis] Connecting to {self.host}:{self.port} ...")

        try:
            self._client = aioredis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                ssl=True,
                ssl_cert_reqs=ssl.CERT_NONE,
                ssl_check_hostname=False,
                decode_responses=True,
                socket_connect_timeout=15,
                socket_timeout=15,
                health_check_interval=30
            )

            pong = await self._client.ping()
            print("[Redis] Connected:", pong)

        except Exception as e:
            print(f"[Redis] Connection failed: {e}")
            raise

        return self._client

    # ───────────────────────────────────────────────
    # 기본 메서드들
    # ───────────────────────────────────────────────
    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # 주어진 `key` 에 `value` 를 저장하고, TTL(초) 설정
        client = await self.connect()
        return await client.set(apa_key, value, ex=ex)

    async def get(self, key: str) -> Optional[str]:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # 주어진 `key` 에 저장된 문자열 값을 조회
        client = await self.connect()
        return await client.get(apa_key)

    async def delete(self, key: str) -> int:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # 주어진 `key` 를 삭제하고, 실제로 삭제된 key 개수를 반환
        client = await self.connect()
        return await client.delete(apa_key)

    async def exists(self, key: str) -> bool:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # 주어진 `key` 의 존재 여부를 True / False 로 반환
        client = await self.connect()
        return bool(await client.exists(apa_key))

    async def incr(self, key: str) -> int:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # 숫자 값으로 저장된 `key` 를 1 증가시키고, 증가된 값을 반환
        client = await self.connect()
        return await client.incr(apa_key)

    async def decr(self, key: str) -> int:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # 숫자 값으로 저장된 `key` 를 1 감소시키고, 감소된 값을 반환
        client = await self.connect()
        return await client.decr(apa_key)

    async def ttl(self, key: str) -> int:
        # key 앞에 접두사 붙이기
        apa_key = f"apa:{key}"
        # TTL(초) 조회
        client = await self.connect()
        return await client.ttl(apa_key)

    async def close(self):
        if self._client:
            await self._client.aclose()
            print("[Redis] Connection closed")

    # ───────────────────────────────────────────────
    # 실사용 메서드들. 기본 메서드를 그대로 쓰지 말고 여기에 추가해서 어떤게 쓰이는지 명확히 할 것.
    # ───────────────────────────────────────────────

    # ─────────────────────state──────────────────────────
    # state 저장. user_id가 아닌 user_id 기준으로 저장함.
    async def save_state_to_redis(self, user_id: str, state_data: GraphState) -> bool:
        # key
        key = f"state:{user_id}"
        # value
        value = state_data.model_dump_json()
        # 상태 데이터를 Redis에 저장
        return await self.set(key, value, ex=3600)  # 1시간 TTL
    
    # state 불러오기. user_id가 아닌 user_id 기준으로 불러옴.
    async def load_state_from_redis(self, user_id: str) -> Optional[GraphState]:
        # key
        key = f"state:{user_id}"
        # 상태 데이터를 Redis에서 불러오기
        data = await self.get(key)
        if data:
            return GraphState.model_validate_json(data)
        return None

# ───────────────────────────────────────────────
# 앱 전체에서 재사용할 인스턴스
# ───────────────────────────────────────────────
redis_client = RedisClient()