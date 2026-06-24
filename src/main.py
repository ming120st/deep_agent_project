import os

import uvicorn
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from src.app.lifespan import lifespan
from src.dashboard.agents_router import router as agents_router
from src.dashboard.auth import router as auth_router
from src.dashboard.device_job_router import router as device_job_router
from src.dashboard.knowledge_v2_router import router as knowledge_v2_router
from src.dashboard.members_router import router as members_router
from src.dashboard.reports_router import router as reports_router
from src.dashboard.sessions_router import router as sessions_router
from src.dashboard.spaces_router import router as spaces_router
from src.dashboard.task_router import router as dashboard_router
from src.interfaces.google_chat.router import router as google_chat_router
from src.interfaces.inter_agent.router import router as inter_agent_router


def _create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY", ""))
    _register_api_routers(app)
    return app


def _register_api_routers(app: FastAPI) -> None:
    app.include_router(google_chat_router)
    app.include_router(inter_agent_router)
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(sessions_router)
    app.include_router(agents_router)
    app.include_router(members_router)
    app.include_router(spaces_router)
    app.include_router(knowledge_v2_router)
    app.include_router(device_job_router)
    app.include_router(reports_router)

app = _create_app()

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)