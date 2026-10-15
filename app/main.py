from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI


load_dotenv()


from agents.registry import (
    register_all_agents,
)
from app.api.google_chat import (
    router as google_chat_router,
)
from core.deep_agent.agent import (
    create_deep_agent,
)
from core.database.redis import (
    close_redis_client,
)
from integrations.device_job.client import (
    close_device_job_client,
    init_device_job_client,
)
from registry.agent_registry import (
    sync_agents_to_db,
)
from tools.integrations.google_workspace_mcp.runtime import (
    google_workspace_runtime,
)
from tools.integrations.meta_ads_mcp.runtime import (
    meta_ads_runtime,
)
from app.api.device_callback import (
    router as device_job_router,
)
from core.deep_agent.reload_manager import (
    agent_reload_manager,
)

@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    meta_ads_started = False
    google_workspace_started = False
    device_job_started = False

    app.state.background_tasks = set()
    try:
        # 1. 외부 리소스 시작
        await meta_ads_runtime.start()
        meta_ads_started = True

        await google_workspace_runtime.start()
        google_workspace_started = True

        await init_device_job_client()
        device_job_started = True

        await register_all_agents()

        await sync_agents_to_db()

        # 현재 각 SubAgent Prompt version을
        # ReloadManager의 기준값으로 저장
        await agent_reload_manager.initialize()

        app.state.deep_agent = (
            await create_deep_agent()
        )

        yield

    finally:
        # 역순으로 종료
        if device_job_started:
            await close_device_job_client()

        await close_redis_client()

        if google_workspace_started:
            await google_workspace_runtime.stop()

        if meta_ads_started:
            await meta_ads_runtime.stop()


app = FastAPI(
    lifespan=lifespan,
)

app.include_router(
    google_chat_router,
)

app.include_router(
    device_job_router,
)