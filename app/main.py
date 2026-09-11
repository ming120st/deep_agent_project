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
from core.llm.model_service import (
    model_service,
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


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):
    # 1. 외부 리소스 시작
    await google_workspace_runtime.start()
    await meta_ads_runtime.start()
    await init_device_job_client()

    # 2. Agent 등록
    await register_all_agents()

    # 3. Agent / Tool 정보를 DB와 동기화
    await sync_agents_to_db()

    # 4. DB 설정 적용 후 DeepAgent 생성
    app.state.deep_agent = (
        await create_deep_agent()
    )

    try:
        yield

    finally:
        await close_device_job_client()
        await meta_ads_runtime.stop()
        await google_workspace_runtime.stop()


app = FastAPI(
    lifespan=lifespan,
)

app.include_router(
    google_chat_router,
)