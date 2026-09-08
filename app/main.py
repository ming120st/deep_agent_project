from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.google_chat import (
    router as google_chat_router,
)
from core.deep_agent.agent import (
    create_deep_agent,
)
from integrations.device_job.client import (
    init_device_job_client,
    close_device_job_client,
)
from tools.integrations.google_workspace_mcp.runtime import (
    google_workspace_runtime,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 외부 리소스 시작
    await google_workspace_runtime.start()
    await init_device_job_client()

    # 2. DeepAgent 생성
    app.state.deep_agent = (
        await create_deep_agent()
    )

    try:
        yield

    finally:
        # 3. 종료는 역순
        await close_device_job_client()
        await google_workspace_runtime.stop()


app = FastAPI(
    lifespan=lifespan,
)

app.include_router(
    google_chat_router,
)