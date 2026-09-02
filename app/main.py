from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.google_chat import router as google_chat_router
from core.deep_agent.agent import create_deep_agent
from integrations.device_job.client import (
    init_device_job_client,
    close_device_job_client,
)
import agents.test.dummy_agents
import agents.network.wifi_line_change.agent

@asynccontextmanager
async def lifespan(app: FastAPI):

    # Device Job API 호출에 사용할 HTTP 클라이언트 초기화
    await init_device_job_client()

    # 서버 시작시 한 번만 deep agent 생성 후 재사용
    app.state.deep_agent = await create_deep_agent()

    yield

    await close_device_job_client()


app = FastAPI(
    lifespan=lifespan,
)

app.include_router(
    google_chat_router,
)
