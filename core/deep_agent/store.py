"""DeepAgents StoreBackend에서 사용하는 프로세스 로컬 LangGraph Store 모듈.

[사용 방법 예시]
1. 에이전트 생성 시 CompositeBackend 라우트로 등록:
    ```python
    import os
    from deepagents import create_deep_agent
    from deepagents.backends import CompositeBackend, StoreBackend
    from deepagents.backends.filesystem import FilesystemBackend
    from core.deep_agent.store import _resolve_user_namespace, shared_store

    agent = create_deep_agent(
        ...,
        store=shared_store,
        backend=CompositeBackend(
            default=FilesystemBackend(root_dir=os.environ["DEEP_AGENT_ROOT_DIR"]),
            routes={
                "/shared/": StoreBackend(
                    store=shared_store,
                    namespace=_resolve_user_namespace,
                ),
            },
        ),
    )
    ```

2. 에이전트 실행 시 사용자 식별 정보 주입 (configurable 또는 context):
    ```python
    # 런타임 config에 user_id / user_email을 전달하여 사용자별 네임스페이스 격리
    config = {
        "configurable": {
            "thread_id": "thread-123",
            "user_id": "user@example.com",
        }
    }
    await agent.ainvoke(..., config=config)
    ```
"""

import re
from typing import Any

from langgraph.config import get_config
from langgraph.store.memory import InMemoryStore


# 이 Store는 의도적으로 프로세스 로컬(In-Memory)로 동작합니다.
# 현재 Azure Managed Redis 인스턴스는 AsyncRedisStore에서 요구하는
# RedisJSON 및 RediSearch 모듈을 제공하지 않으므로,
# 공유 Redis 데이터 처리는 core.database.redis가 담당하고
# DeepAgent 내부 저장소는 본 인메모리 스토어를 싱글톤으로 공유합니다.
shared_store = InMemoryStore()


def _resolve_user_namespace(rt: Any = None) -> tuple[str, ...]:
    """DeepAgents StoreBackend를 위한 사용자 격리 네임스페이스를 추출/생성합니다."""
    user_id = None

    try:
        configurable = get_config().get("configurable", {})
        user_id = (
            configurable.get("user_id")
            or configurable.get("user_email")
            or configurable.get("google_user_id")
        )
    except (RuntimeError, KeyError):
        pass

    if not user_id and rt is not None:
        server_info = getattr(rt, "server_info", None)
        server_user = getattr(server_info, "user", None)
        user_id = getattr(server_user, "identity", None)

    if not user_id and rt is not None:
        context = getattr(rt, "context", None)
        if isinstance(context, dict):
            user_id = context.get("user_id") or context.get("user_email")
        elif context is not None:
            user_id = getattr(context, "user_id", None) or getattr(
                context,
                "user_email",
                None,
            )

    if not user_id:
        user_id = "default"

    # LangGraph BaseStore는 네임스페이스 라벨에 마침표(.) 사용을 금지합니다.
    # 아래 정규식 치환은 허용되지 않는 문자를 '_'로 대체하여
    # DeepAgents StoreBackend 검증 기준을 충족하도록 합니다.
    safe_user_id = re.sub(r"[^A-Za-z0-9\-_@+:~]", "_", str(user_id))
    return ("shared", safe_user_id)