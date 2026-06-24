import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, Request, Response
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from src.agent.analytics.subagents.schedule.bq_analysis_schedule.cards import (
    build_bq_schedule_card,
    build_schedule_confirm_message,
)
from src.agent.analytics.subagents.schedule.bq_analysis_schedule.schedule_manager import (
    INTERVAL_LABELS,
    bq_schedules_collection,
    delete_bq_schedule,
    list_user_schedules,
    register_bq_schedule,
    restore_bq_schedules,
)
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.agent.sync_registry import sync_registry_to_mongo
from src.core.members_manager import upsert_member
from src.core.spaces_manager import upsert_space
from src.dashboard.agents_router import router as agents_router
from src.dashboard.auth import router as auth_router
from src.dashboard.device_job_router import router as device_job_router
from src.dashboard.knowledge_v2_router import router as knowledge_v2_router
from src.dashboard.members_router import router as members_router
from src.dashboard.reports_router import router as reports_router
from src.dashboard.sessions_router import router as sessions_router
from src.dashboard.spaces_router import router as spaces_router
from src.dashboard.task_router import router as dashboard_router
from src.orchestrator import orchestrate_chat_event
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph

load_dotenv()

# ── 스케줄러 ──────────────────────────────────────────────────────────────────
# 스케줄/트리거 에이전트는 앱 시작 시 이 scheduler에 등록된다.
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


class IARequestModel(BaseModel):
    job_id: str
    session_id: str
    user_id: str
    agent_name: str
    new_task: bool
    parameters: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class IAResponseValue(BaseModel):
    message: Optional[str] = None
    value: Optional[Dict[str, Any]] = None


class IAResponseModel(BaseModel):
    success: bool
    status: str
    resultValue: IAResponseValue


# ── 앱 초기화 ────────────────────────────────────────────────────────────────
async def _initialize_app_runtime() -> None:
    await sync_registry_to_mongo()

    from src.core.rag_engine import init_rag_engine
    await init_rag_engine()

    from src.dashboard.knowledge_v2_router import ensure_kb_v2_indexes
    await ensure_kb_v2_indexes()


async def _bootstrap_scheduler_jobs() -> None:
    _register_registry_schedule_jobs()
    _register_registry_trigger_jobs()
    await restore_bq_schedules(scheduler)
    await _pause_disabled_registry_jobs()


def _register_registry_schedule_jobs() -> None:
    for agent_id, meta in SCHEDULE_REGISTRY.items():
        scheduler.add_job(
            meta["func"],
            trigger="cron",
            **meta["cron"],
            id=agent_id,
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        print(f"[Scheduler] 스케줄 등록: {agent_id}")


def _register_registry_trigger_jobs() -> None:
    for agent_id, meta in TRIGGER_REGISTRY.items():
        scheduler.add_job(
            meta["func"],
            trigger="interval",
            **meta["interval"],
            id=agent_id,
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
        )
        print(f"[Scheduler] 트리거 등록: {agent_id}")


async def _pause_disabled_registry_jobs() -> None:
    from src.core.mongo_client import boa_agent_registry_collection

    cursor = boa_agent_registry_collection.find(
        {"enabled": False, "agent_type": {"$in": ["schedule", "trigger"]}}
    )
    disabled_agents = await cursor.to_list(length=None)

    for agent in disabled_agents:
        job = scheduler.get_job(agent["agent_id"])
        if job:
            job.pause()
            print(f"[Scheduler] 비활성 상태 유지: {agent['agent_id']}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _initialize_app_runtime()
    await _bootstrap_scheduler_jobs()
    scheduler.start()

    yield

    scheduler.shutdown()


def _create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET_KEY", ""))
    _register_api_routers(app)
    return app


def _register_api_routers(app: FastAPI) -> None:
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(sessions_router)
    app.include_router(agents_router)
    app.include_router(members_router)
    app.include_router(spaces_router)
    app.include_router(knowledge_v2_router)
    app.include_router(device_job_router)
    app.include_router(reports_router)


# ── Google Chat Webhook ───────────────────────────────────────────────────────
def _parse_google_chat_event(event: dict) -> dict:
    user = event.get("user", {})
    space = event.get("space", {})
    message = event.get("message", {})
    quoted_snapshot = (
        message.get("quotedMessageMetadata", {})
        .get("quotedMessageSnapshot", {})
    )

    return {
        "event_type": event.get("type"),
        "user_id": user.get("email", ""),
        "user_name": user.get("displayName", ""),
        "space_name": space.get("name", ""),
        "space_type": space.get("type", ""),
        "display_name": space.get("displayName", ""),
        "message": message.get("text", ""),
        "thread_name": message.get("thread", {}).get("name"),
        "event_time": event.get("eventTime", {}),
        "quoted_message": quoted_snapshot.get("text"),
        "quoted_message_sender": quoted_snapshot.get("sender", {}),
    }


def _is_ignored_chat_event(event_type: str | None) -> bool:
    return event_type in {"ADDED_TO_SPACE", "REMOVED_FROM_SPACE"}


async def _remember_chat_space(chat_event: dict, background_tasks: BackgroundTasks) -> None:
    if chat_event["space_type"] == "DM":
        await upsert_member(
            chat_event["user_id"],
            chat_event["user_name"],
            chat_event["space_name"],
        )
    elif chat_event["space_type"] in ("ROOM", "GROUP_CHAT", "SPACE"):
        background_tasks.add_task(
            upsert_space,
            chat_event["space_name"],
            chat_event["display_name"],
            chat_event["space_type"],
        )


async def _restore_chat_session(user_id: str) -> tuple[GraphState, bool]:
    config = {"configurable": {"thread_id": user_id}}
    existing = await asyncio.to_thread(main_graph.get_state, config)

    if existing.values:
        state = GraphState(**existing.values)
        current_time = int(time.time())
        if state.last_activity_time and current_time - state.last_activity_time > 3600:
            print("세션 타임아웃 → 초기화")
            return GraphState(), True

        print("기존 세션 유지")
        return state, False

    print("새로운 세션 생성")
    return GraphState(), True


def _attach_chat_context(state: GraphState, chat_event: dict) -> None:
    state.last_activity_time = int(time.time())
    state.chat_context = {
        "user_id": chat_event["user_id"],
        "user_name": chat_event["user_name"],
        "space_name": chat_event["space_name"],
        "space_type": chat_event["space_type"],
        "thread_name": chat_event["thread_name"],
        "quoted_message": chat_event["quoted_message"],
        "quoted_message_sender": chat_event["quoted_message_sender"],
        "event_time": chat_event["event_time"],
    }
    state.add_user_message(chat_event["message"])


async def _handle_chat_card_click(event: dict, user_id: str, space_name: str) -> dict:
    action_method = event.get("action", {}).get("actionMethodName", "")

    if action_method == "submitBqSchedule":
        return await _submit_bq_schedule(event, user_id, space_name)

    if action_method == "deleteBqSchedule":
        return await _delete_bq_schedule_from_card(event)

    if action_method == "showAgentHelp":
        return await _show_agent_help_card(event, space_name)

    return {}


async def _submit_bq_schedule(event: dict, user_id: str, space_name: str) -> dict:
    form_inputs = event.get("common", {}).get("formInputs", {})

    agent_name = _get_form_value(form_inputs, "bq_agent_name", "").strip()
    query = _get_form_value(form_inputs, "bq_query", "").strip()
    interval = _get_form_value(form_inputs, "bq_interval", "daily")
    hour = int(_get_form_value(form_inputs, "bq_hour", "8"))
    minute = int(_get_form_value(form_inputs, "bq_minute", "0"))

    if not agent_name:
        return {"text": "⚠️ 에이전트 이름을 입력해주세요."}
    if not query:
        return {"text": "⚠️ 분석 요청 내용을 입력해주세요."}

    try:
        schedule_id = await register_bq_schedule(
            scheduler=scheduler,
            user_id=user_id,
            space_name=space_name,
            agent_name=agent_name,
            query=query,
            hour=hour,
            minute=minute,
            interval=interval,
        )
    except ValueError as e:
        return {"text": f"⚠️ {e}\n다른 이름을 사용해주세요."}

    return {"text": build_schedule_confirm_message(agent_name, query, interval, hour, minute, schedule_id)}


async def _delete_bq_schedule_from_card(event: dict) -> dict:
    form_inputs = event.get("common", {}).get("formInputs", {})
    schedule_id = _get_form_value(form_inputs, "schedule_id", "")
    if not schedule_id:
        return {}

    deleted = await delete_bq_schedule(scheduler, schedule_id)
    msg = f"🗑️ 스케줄 `{schedule_id[-8:]}` 이 삭제되었습니다." if deleted else "⚠️ 스케줄을 찾을 수 없습니다."
    return {"text": msg}


async def _show_agent_help_card(event: dict, space_name: str) -> dict:
    from src.agent.registry import AGENT_REGISTRY
    from src.core.google_chat import send_card
    from src.workflow.main_graph.nodes.help_request import _build_agent_detail_card

    raw_params = event.get("action", {}).get("parameters", [])
    agent_key = next((p["value"] for p in raw_params if p.get("key") == "agent_key"), None)

    if agent_key:
        all_registries = {**AGENT_REGISTRY, **SCHEDULE_REGISTRY, **TRIGGER_REGISTRY}
        info = all_registries.get(agent_key)
        if info:
            card_body = _build_agent_detail_card(agent_key, info)
            await send_card(space_name, card_body)
            return {}

    return {"text": "⚠️ 해당 에이전트 정보를 찾을 수 없습니다."}


def _get_form_value(form_inputs: dict, key: str, default: str = "") -> str:
    return form_inputs.get(key, {}).get("stringInputs", {}).get("value", [default])[0]


async def _handle_chat_slash_command(message: str, user_id: str) -> dict | None:
    stripped_message = message.strip()

    if stripped_message == "/데이터분석스케줄":
        return build_bq_schedule_card()

    if stripped_message.startswith("/분석스케줄취소"):
        return await _cancel_bq_schedule_by_command(stripped_message, user_id)

    if stripped_message == "/내스케줄":
        return await _list_bq_schedules(user_id)

    return None


async def _cancel_bq_schedule_by_command(message: str, user_id: str) -> dict:
    parts = message.split()
    if len(parts) < 2:
        return {"text": "사용법: `/분석스케줄취소 {스케줄ID}`"}

    short_id = parts[1]
    doc = await bq_schedules_collection.find_one(
        {"user_id": user_id, "schedule_id": {"$regex": short_id + "$"}}
    )
    if not doc:
        return {"text": "⚠️ 해당 스케줄 ID를 찾을 수 없습니다."}

    await delete_bq_schedule(scheduler, doc["schedule_id"])
    return {"text": f"🗑️ 스케줄 `{short_id}` 이 삭제되었습니다."}


async def _list_bq_schedules(user_id: str) -> dict:
    schedules = await list_user_schedules(user_id)
    if not schedules:
        return {"text": "등록된 분석 스케줄이 없습니다."}

    lines = ["📋 *등록된 데이터 분석 스케줄*\n"]
    for schedule in schedules:
        label = INTERVAL_LABELS.get(schedule["interval"], schedule["interval"])
        time_str = (
            f"{schedule['hour']:02d}:{schedule['minute']:02d}"
            if schedule["interval"] != "hourly"
            else "매시간"
        )
        lines.append(
            f"• `{schedule['schedule_id'][-8:]}` | {label} {time_str}\n"
            f"  _{schedule['query'][:40]}_"
        )
    return {"text": "\n".join(lines)}


def _empty_json_response() -> Response:
    return Response(content=json.dumps({}), media_type="application/json", status_code=200)


async def _run_chat_orchestration(state: GraphState, user_id: str, is_new_session: bool) -> None:
    config = {"configurable": {"thread_id": user_id}}
    asyncio.create_task(orchestrate_chat_event(state, config, is_new_session))


# ── Inter-Agent API ──────────────────────────────────────────────────────────
def _validate_inter_agent_secret(x_inter_agent_secret: Optional[str]) -> bool:
    secret_key = os.getenv("INTER_AGENT_SECRET")
    return bool(secret_key and x_inter_agent_secret == secret_key)


def _unauthorized_agent_response() -> IAResponseModel:
    return IAResponseModel(
        success=False,
        status="UNAUTHORIZED",
        resultValue=IAResponseValue(message="인증에 실패했습니다. 유효하지 않은 요청입니다."),
    )


def _unknown_agent_response(agent_name: str) -> IAResponseModel:
    return IAResponseModel(
        success=False,
        status="ERROR",
        resultValue=IAResponseValue(message=f"존재하지 않는 에이전트입니다: {agent_name}"),
    )


def _agent_error_response(error: Exception) -> IAResponseModel:
    return IAResponseModel(
        success=False,
        status="ERROR",
        resultValue=IAResponseValue(message=f"실행 중 오류 발생: {str(error)}"),
    )


async def _handle_agent_request(request: IARequestModel) -> IAResponseModel:
    from src.agent.registry import AGENT_REGISTRY
    from src.core.mongo_client import checkpointer

    agent_name = request.agent_name
    print(f"[Agent Request] 🔍 대상 에이전트: {agent_name}")

    if agent_name not in AGENT_REGISTRY:
        print(f"[Agent Request] ❌ 존재하지 않는 에이전트: {agent_name}")
        return _unknown_agent_response(agent_name)

    base_subagent_graph = AGENT_REGISTRY[agent_name]["graph"]
    subagent_graph = base_subagent_graph.builder.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": request.session_id}}

    state = await _restore_agent_request_state(request, agent_name, subagent_graph)
    await _merge_agent_request_inputs(state, request, agent_name)

    result_state = await _invoke_agent_request_graph(subagent_graph, state, config, agent_name)
    reply_message, action_response, status_str = _normalize_agent_request_response(result_state, agent_name)

    print(f"[Agent Request] 📤 추출된 응답 메시지: {reply_message}")
    print(f"[Agent Request] 📤 추출된 action_response: {action_response}")

    return IAResponseModel(
        success=True,
        status=status_str,
        resultValue=IAResponseValue(message=reply_message, value=action_response),
    )


async def _restore_agent_request_state(request: IARequestModel, agent_name: str, subagent_graph) -> GraphState:
    config = {"configurable": {"thread_id": request.session_id}}
    print(f"[Agent Request] 🔄 Graph State 복원 시도, config: {config}")
    existing = await asyncio.to_thread(subagent_graph.get_state, config)

    if existing and existing.values:
        print(f"[Agent Request] 🔄 기존 상태 복원 완료. (values keys: {list(existing.values.keys())})")
        state = GraphState(**existing.values)
    else:
        print("[Agent Request] ⭐ 기존 상태 없음 -> 신규 상태 생성")
        state = GraphState()
        state.session_id = request.session_id

    state.current_agent = agent_name
    if not isinstance(state.chat_context, dict):
        state.chat_context = {}

    state.chat_context["user_id"] = request.user_id
    state.chat_context["user_name"] = "CS챗봇"
    state.chat_context["is_cs_api"] = True
    state.chat_context["ia_job_id"] = request.job_id
    return state


async def _merge_agent_request_inputs(state: GraphState, request: IARequestModel, agent_name: str) -> None:
    from src.agent.registry import AGENT_REGISTRY
    from src.core.mongo_client import get_collection

    agent_doc = await get_collection("boa_agent_registry").find_one({"agent_id": agent_name})
    if agent_doc and agent_doc.get("domain_id"):
        state.domain_id = agent_doc["domain_id"]

    if request.new_task:
        print("[Agent Request] 🆕 새로운 작업 시작: 파라미터 초기화 및 Task 발급")
        state.collected_parameters[agent_name] = {}

        from src.core.task_manager import create_task

        task_id = await create_task(
            user_id=request.user_id,
            user_name="CS챗봇",
            agent_name=agent_name,
            agent_display_name=AGENT_REGISTRY[agent_name].get("display_name", agent_name),
            parameters=request.parameters or {},
        )
        state.task_id = task_id

    if request.parameters:
        print(f"[Agent Request] 📥 파라미터 업데이트: {request.parameters}")
        if agent_name not in state.collected_parameters:
            state.collected_parameters[agent_name] = {}
        state.collected_parameters[agent_name].update(request.parameters)
        state.print_state()

    if request.message:
        print(f"[Agent Request] 💬 사용자 메시지 추가: {request.message}")
        state.add_user_message(request.message)


async def _invoke_agent_request_graph(subagent_graph, state: GraphState, config: dict, agent_name: str):
    print(f"[Agent Request] 🚀 서브에이전트 ainvoke 호출 시작 ({agent_name})")
    result_state = await subagent_graph.ainvoke(state, config=config)
    print("[Agent Request] ✅ 서브에이전트 ainvoke 실행 완료")
    return result_state


def _normalize_agent_request_response(result_state, agent_name: str) -> tuple[str, dict, str]:
    reply_message = _extract_last_message_content(result_state)
    action_response = _extract_action_response(result_state, agent_name)
    final_agent = _get_result_value(result_state, "current_agent")
    status_str = "COMPLETED" if final_agent is None else "NEED_INFO"
    return reply_message, action_response, status_str


def _extract_last_message_content(result_state) -> str:
    messages = _get_result_value(result_state, "messages") or []
    if not messages:
        return ""

    last_msg = messages[-1]
    return getattr(last_msg, "content", "") if hasattr(last_msg, "content") else last_msg.get("content", "")


def _extract_action_response(result_state, agent_name: str) -> dict:
    response = _get_result_value(result_state, "response") or {}
    return response.get(agent_name, {})


def _get_result_value(result_state, key: str):
    if isinstance(result_state, dict):
        return result_state.get(key)
    return getattr(result_state, key, None)


# ── FastAPI 앱/엔드포인트 ─────────────────────────────────────────────────────
app = _create_app()


@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    print(f"[수신데이터 전문] {json.dumps(event, ensure_ascii=False)}")

    chat_event = _parse_google_chat_event(event)
    event_type = chat_event["event_type"]

    if _is_ignored_chat_event(event_type):
        return {}

    if event_type == "CARD_CLICKED":
        return await _handle_chat_card_click(
            event,
            chat_event["user_id"],
            chat_event["space_name"],
        )

    if event_type != "MESSAGE":
        return {}

    await _remember_chat_space(chat_event, background_tasks)

    state, is_new_session = await _restore_chat_session(chat_event["user_id"])
    _attach_chat_context(state, chat_event)

    slash_response = await _handle_chat_slash_command(chat_event["message"], chat_event["user_id"])
    if slash_response is not None:
        return slash_response

    await _run_chat_orchestration(state, chat_event["user_id"], is_new_session)
    return _empty_json_response()


@app.post("/agent_request", response_model=IAResponseModel)
async def receive_agent_request(
    request: IARequestModel,
    x_inter_agent_secret: Optional[str] = Header(None, alias="X-Inter-Agent-Secret"),
):
    print(f"[Agent Request 수신 전문] {request.model_dump()}")

    if not _validate_inter_agent_secret(x_inter_agent_secret):
        print("[Agent Request] ❌ 시크릿 키 검증 실패 (Unauthorized)")
        return _unauthorized_agent_response()

    print("[Agent Request] ✅ 시크릿 키 검증 성공")

    try:
        return await _handle_agent_request(request)
    except Exception as e:
        print(f"[Agent Request] ❌ 실행 중 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        return _agent_error_response(e)


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)