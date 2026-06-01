"""
[자동 등록 로직]
  space_type == "DM"        → boa_members upsert  (개인)
  space_type == "ROOM"      → boa_spaces  upsert  (단체)
  SCHEDULE_REGISTRY, TRIGGER_REGISTRY 기반 에이전트 앱 시작 시 스케줄러에 자동 등록 (MongoDB enabled 상태 반영)

[부서 등록 로직]
  순수 Chat API 앱 (gcp-sa-chat) 기반.
  동기 응답(return {"cardsV2": ...})으로 카드 즉시 표시 가능.
  버튼 클릭은 type == "CARD_CLICKED", action.actionMethodName 으로 수신.
  formInputs는 common.formInputs 에서 파싱.

  실제 이벤트 구조 (확인됨):
    event.type                    → "MESSAGE" | "CARD_CLICKED" | "ADDED_TO_SPACE" 등
    event.user.email              → 유저 ID
    event.user.displayName        → 유저 이름
    event.space.name              → space ID
    event.space.type              → "DM" | "ROOM" | "GROUP_CHAT" 등
    event.message.text            → 메시지 텍스트
    event.message.thread.name     → 스레드 이름
    event.action.actionMethodName → 버튼 클릭 시 function 이름
    event.common.formInputs       → 카드 폼 입력값
"""

import os
import json
import time
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, BackgroundTasks, Response, APIRouter
from starlette.middleware.sessions import SessionMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from src.orchestrator import orchestrate_chat_event
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph
from src.dashboard.auth import router as auth_router
from src.dashboard.task_router import router as dashboard_router
from src.dashboard.sessions_router import router as sessions_router
from src.dashboard.knowledge_v2_router import router as knowledge_v2_router
from src.dashboard.device_job_router import router as device_job_router
from src.dashboard.members_router import router as members_router
from src.dashboard.spaces_router import router as spaces_router
from src.core.members_manager import upsert_member, update_member_department
from src.core.spaces_manager import upsert_space
from src.core.google_chat import send_message
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.agent.sync_registry import sync_registry_to_mongo
from src.dashboard.agents_router import router as agents_router
from src.dashboard.reports_router import router as reports_router
from src.agent.analytics.subagents.schedule.bq_analysis_schedule.schedule_manager import (
    register_bq_schedule,
    restore_bq_schedules,
    delete_bq_schedule,
    list_user_schedules,
    bq_schedules_collection,
    INTERVAL_LABELS,
)
from src.agent.analytics.subagents.schedule.bq_analysis_schedule.cards import (
    build_bq_schedule_card,
    build_schedule_confirm_message,
)


# ── 스케줄러 ──────────────────────────────────────────────────────────────────
# 스케줄/트리거 에이전트는 다음 scheduler에 등록된다.
# AsyncIOScheduler는 앱이 시작된 후에도 job 추가/삭제/수정이 가능하다.
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


@asynccontextmanager 
async def lifespan(app: FastAPI):
    """
    앱의 시작과 종료 시 수행될 작업을 관리하는 함수.
    yield 전까지는 앱 시작 시 실행되고, yield 이후는 앱 종료 시 실행된다.
    """

    # 1. Registry에 등록된 에이전트를 몽고DB에 등록/제거된 에이전트는 몽고DB에서 삭제
    await sync_registry_to_mongo()

    # 2. RAG 엔진 초기화 (Blob → 없으면 MongoDB 전체 빌드)
    from src.core.rag_engine import init_rag_engine
    await init_rag_engine()

    # 2-1. KB v2 인덱스 확인/생성
    from src.dashboard.knowledge_v2_router import ensure_kb_v2_indexes
    await ensure_kb_v2_indexes()

    # 3. scheduler엔진에 스케줄 에이전트 등록
    for agent_id, meta in SCHEDULE_REGISTRY.items():
        scheduler.add_job(
            meta["func"], # 실제 실행할 함수
            trigger="cron",
            **meta["cron"],
            id=agent_id,
            replace_existing=True, # 기존에 같은 이름의 job이 있으면 덮어쓰기
            misfire_grace_time=600, # 실행이 지연되었을 때 최대 10분까지 허용
            coalesce=True, # 실행이 지연되어서 여러개의 job이 밀렸으면 하나로 합침
            max_instances=1, # 동시에 여러 인스턴스 실행 방지. A에이전트 실행 중인데 다음 A에이전트의 실행 시간이 되면 바로 실행하지 않고, 최대 10분까지 기다렸다가 실행. 만약 10분이 지나도 이전 작업이 끝나지 않으면 다음 작업은 건너뜀.
        )
        print(f"[Scheduler] 스케줄 등록: {agent_id}")

    # 4. scheduler엔진에 트리거 에이전트 등록
    for agent_id, meta in TRIGGER_REGISTRY.items():
        scheduler.add_job(
            meta["func"], # 실제 실행할 함수
            trigger="interval",
            **meta["interval"],
            id=agent_id,
            replace_existing=True,  # 기존에 같은 이름의 job이 있으면 덮어쓰기
            misfire_grace_time=600, # 실행이 지연되었을 때 최대 10분까지 허용
            coalesce=True, # 실행이 지연되어서 여러개의 job이 밀렸으면 하나로 합침
            max_instances=1, # 동시에 여러 인스턴스 실행 방지. A에이전트 실행 중인데 다음 A에이전트의 실행 시간이 되면 바로 실행하지 않고, 최대 10분까지 기다렸다가 실행. 만약 10분이 지나도 이전 작업이 끝나지 않으면 다음 작업은 건너뜀.
        )
        print(f"[Scheduler] 트리거 등록: {agent_id}")

    # 5. MongoDB에 저장된 BQ 분석 스케줄 복원 (사용자가 등록한 스케줄 에이전트가 추가된다면 여기 추가해줘야함)
    await restore_bq_schedules(scheduler) # 실적분석 빅쿼리 스케줄을 몽고DB에서 읽어서 여기 scheduler에 등록

    # 6. MongoDB enabled=False인 job은 시작 시 바로 pause
    from src.core.mongo_client import boa_agent_registry_collection
    cursor = boa_agent_registry_collection.find({"enabled": False, "agent_type": {"$in": ["schedule", "trigger"]}})
    # 위의 cursor는 결과를 바로 반환하는 것이 아니라, find() 메서드가 반환하는 커서 객체. 실제 데이터를 얻으려면 아래처럼 to_list() 메서드를 사용해야 한다.
    disabled = await cursor.to_list(length=None)
    for agent in disabled:
        job = scheduler.get_job(agent["agent_id"])
        if job:
            job.pause()
            print(f"[Scheduler] 비활성 상태 유지: {agent['agent_id']}")

    scheduler.start()
    yield
    scheduler.shutdown()


# ── FastAPI 앱 ────────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "")
)

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
@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    print(f"[수신데이터 전문] {json.dumps(event, ensure_ascii=False)}")

    event_type = event.get("type")

    # ── 스페이스 추가/제거 이벤트도 API로 들어오지만 무시 ────────────────────────────────────────
    if event_type in ("ADDED_TO_SPACE", "REMOVED_FROM_SPACE"):
        return {}

    # ── 공통 필드 파싱 (확인된 실제 구조 기반) ─────────────────────────────────
    user       = event.get("user", {})              # 구글챗 사용자 정보 (json)
    user_id    = user.get("email", "")              # 구글챗 사용자 이메일
    user_name  = user.get("displayName", "")        # 구글챗 사용자 이름
    space      = event.get("space", {})             # 구글챗 스페이스 정보 (json)
    space_name = space.get("name", "")              # 구글챗 스페이스 이름
    space_type = space.get("type", "")              # 구글챗 스페이스 타입
    display_name = space.get("displayName", "")     # 구글챗 스페이스 표시 이름

    # ── 카드 인터랙션 처리 ────────────────────────────────────────────────────
    if event_type == "CARD_CLICKED":

        # 개별 버튼 
        action_method = event.get("action", {}).get("actionMethodName", "")

        # BQ 분석 스케줄 등록
        if action_method == "submitBqSchedule":
            form_inputs = event.get("common", {}).get("formInputs", {})

            agent_name = form_inputs.get("bq_agent_name", {}).get("stringInputs", {}).get("value", [""])[0].strip()
            query      = form_inputs.get("bq_query",      {}).get("stringInputs", {}).get("value", [""])[0].strip()
            interval   = form_inputs.get("bq_interval",   {}).get("stringInputs", {}).get("value", ["daily"])[0]
            hour       = int(form_inputs.get("bq_hour",   {}).get("stringInputs", {}).get("value", ["8"])[0])
            minute     = int(form_inputs.get("bq_minute", {}).get("stringInputs", {}).get("value", ["0"])[0])

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

        # BQ 분석 스케줄 삭제
        if action_method == "deleteBqSchedule":
            form_inputs = event.get("common", {}).get("formInputs", {})
            schedule_id = form_inputs.get("schedule_id", {}).get("stringInputs", {}).get("value", [""])[0]
            if schedule_id:
                deleted = await delete_bq_schedule(scheduler, schedule_id)
                msg = (f"🗑️ 스케줄 `{schedule_id[-8:]}` 이 삭제되었습니다."
                       if deleted else "⚠️ 스케줄을 찾을 수 없습니다.")
                return {"text": msg}
            return {}

        # 도움말 카드 — 에이전트 상세 사용방법 보기
        if action_method == "showAgentHelp":
            from src.agent.registry import AGENT_REGISTRY, SCHEDULE_REGISTRY, TRIGGER_REGISTRY
            from src.core.google_chat import send_card
            from src.workflow.main_graph.nodes.help_request import _build_agent_detail_card

            # 버튼에서 전달된 agent_key 파싱
            raw_params = event.get("action", {}).get("parameters", [])
            agent_key  = next(
                (p["value"] for p in raw_params if p.get("key") == "agent_key"),
                None,
            )

            if agent_key:
                all_registries = {**AGENT_REGISTRY, **SCHEDULE_REGISTRY, **TRIGGER_REGISTRY}
                info = all_registries.get(agent_key)
                if info:
                    card_body = _build_agent_detail_card(agent_key, info)
                    await send_card(space_name, card_body)
                    return {}

            return {"text": "⚠️ 해당 에이전트 정보를 찾을 수 없습니다."}

        # 다른 카드 클릭 이벤트는 여기서 추가 처리
        return {}

    # ── 일반 메시지 처리 ───────────────────────────────────────────────────────
    if event_type != "MESSAGE":
        return {}

    message_obj    = event.get("message", {})
    message        = message_obj.get("text", "")
    thread_name    = message_obj.get("thread", {}).get("name")
    event_time     = event.get("eventTime", {})
    quoted_message = (
        message_obj.get("quotedMessageMetadata", {})
                   .get("quotedMessageSnapshot", {})
                   .get("text")
    )
    quoted_message_sender = (
        message_obj.get("quotedMessageMetadata", {})
                   .get("quotedMessageSnapshot", {})
                   .get("sender", {})
    )

    # ── DM/ROOM 정보 자동 등록 ─────────────────────────────────────────────────
    if space_type == "DM":
        await upsert_member(user_id, user_name, space_name)

    elif space_type in ("ROOM", "GROUP_CHAT", "SPACE"):
        background_tasks.add_task(upsert_space, space_name, display_name, space_type)

    # ── 세션 처리 ─────────────────────────────────────────────────────────────
    config   = {"configurable": {"thread_id": user_id}}
    existing = await asyncio.to_thread(main_graph.get_state, config)

    if existing.values:
        state        = GraphState(**existing.values)
        current_time = int(time.time())
        if state.last_activity_time and current_time - state.last_activity_time > 3600:
            print("세션 타임아웃 → 초기화")
            state = GraphState()
            is_new_session = True
        else:
            print("기존 세션 유지")
            is_new_session = False
    else:
        state = GraphState()
        print("새로운 세션 생성")
        is_new_session = True

    state.last_activity_time = int(time.time())
    state.chat_context = {
        "user_id":               user_id, # 구글챗 사용자(직원)의 이메일 주소
        "user_name":             user_name,
        "space_name":            space_name,
        "space_type":            space_type,
        "thread_name":           thread_name,
        "quoted_message":        quoted_message,
        "quoted_message_sender": quoted_message_sender,
        "event_time":            event_time,
    }
    state.add_user_message(message)

    # ── 슬래시 커맨드 처리 (LLM 없이 즉시 카드/텍스트 반환) ─────────────────────
    # 추가로 슬래시 커맨드로 처리할 기능이 있으면 여기에 if문 추가
    if message.strip() == "/데이터분석스케줄":
        return build_bq_schedule_card()

    if message.strip().startswith("/분석스케줄취소"):
        parts = message.strip().split()
        if len(parts) >= 2:
            short_id = parts[1]
            doc = await bq_schedules_collection.find_one(
                {"user_id": user_id, "schedule_id": {"$regex": short_id + "$"}}
            )
            if doc:
                await delete_bq_schedule(scheduler, doc["schedule_id"])
                return {"text": f"🗑️ 스케줄 `{short_id}` 이 삭제되었습니다."}
            return {"text": "⚠️ 해당 스케줄 ID를 찾을 수 없습니다."}
        return {"text": "사용법: `/분석스케줄취소 {스케줄ID}`"}

    if message.strip() == "/내스케줄":
        schedules = await list_user_schedules(user_id)
        if not schedules:
            return {"text": "등록된 분석 스케줄이 없습니다."}
        lines = ["📋 *등록된 데이터 분석 스케줄*\n"]
        for s in schedules:
            label    = INTERVAL_LABELS.get(s["interval"], s["interval"])
            time_str = f"{s['hour']:02d}:{s['minute']:02d}" if s["interval"] != "hourly" else "매시간"
            lines.append(f"• `{s['schedule_id'][-8:]}` | {label} {time_str}\n  _{s['query'][:40]}_")
        return {"text": "\n".join(lines)}

    asyncio.create_task(orchestrate_chat_event(state, config, is_new_session))
    # 메시지 수신에 대한 즉각적인 응답
    return Response(
        content=json.dumps({}),
        media_type="application/json",
        status_code=200
    )




# ── 인터 에이전트 데이터 검증 ───────────────────────────────────────────────────────

from pydantic import BaseModel
from typing import Optional, Dict, Any

class IARequestModel(BaseModel):
    session_id: str
    user_id: str
    agent_name: str
    parameters: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    
class IAResponseValue(BaseModel):
    message: Optional[str] = None
    value: Optional[Dict[str, Any]] = None

class IAResponseModel(BaseModel):
    success: bool
    status: str
    resultValue: IAResponseValue

from fastapi import Header

# ── 인터 에이전트 엔드포인트 ───────────────────────────────────────────────────────
@app.post("/agent_request", response_model=IAResponseModel)
async def receive_agent_request(
    request: IARequestModel,
    x_inter_agent_secret: Optional[str] = Header(None, alias="X-Inter-Agent-Secret")
):
    print(f"[Agent Request 수신 전문] {request.model_dump()}")

    # 환경변수 시크릿 키 검증
    secret_key = os.getenv("INTER_AGENT_SECRET")
    if not secret_key or x_inter_agent_secret != secret_key:
        print("[Agent Request] ❌ 시크릿 키 검증 실패 (Unauthorized)")
        return IAResponseModel(
            success=False,
            status="UNAUTHORIZED",
            resultValue=IAResponseValue(message="인증에 실패했습니다. 유효하지 않은 요청입니다.")
        )
    print("[Agent Request] ✅ 시크릿 키 검증 성공")

    from src.agent.registry import AGENT_REGISTRY
    from src.core.mongo_client import checkpointer
    
    agent_name = request.agent_name
    print(f"[Agent Request] 🔍 대상 에이전트: {agent_name}")
    
    if agent_name not in AGENT_REGISTRY:
        print(f"[Agent Request] ❌ 존재하지 않는 에이전트: {agent_name}")
        return IAResponseModel(
            success=False,
            status="ERROR",
            resultValue=IAResponseValue(message=f"존재하지 않는 에이전트입니다: {agent_name}")
        )

    # CS챗봇 등 외부 시스템에서 단독 호출할 때는 서브에이전트에 checkpointer를 붙여서 컴파일
    base_subagent_graph = AGENT_REGISTRY[agent_name]["graph"]
    subagent_graph = base_subagent_graph.builder.compile(checkpointer=checkpointer)
    
    # 1. Thread ID (세션 ID) 기반으로 이전 State 복원 시도
    config = {"configurable": {"thread_id": request.session_id}}
    print(f"[Agent Request] 🔄 Graph State 복원 시도, config: {config}")
    existing = await asyncio.to_thread(subagent_graph.get_state, config)

    # 2. State 합치기
    if existing and existing.values:
        # 이미 세션이 존재하면 복원
        print(f"[Agent Request] 🔄 기존 상태 복원 완료. (values keys: {list(existing.values.keys())})")
        state = GraphState(**existing.values)
    else:
        # 없으면 새로 생성
        print("[Agent Request] ⭐ 기존 상태 없음 -> 신규 상태 생성")
        state = GraphState()
        state.session_id = request.session_id
        
    # 현재 실행할 에이전트 지정
    state.current_agent = agent_name
    
    # 필요한 context 세팅
    if not isinstance(state.chat_context, dict):
        state.chat_context = {}
        
    state.chat_context["user_id"] = request.user_id
    state.chat_context["is_cs_api"] = True
    
    # CS챗봇에서 넘겨준 파라미터가 있다면 주입
    if request.parameters:
        print(f"[Agent Request] 📥 파라미터 주입: {request.parameters}")
        if agent_name not in state.collected_parameters:
            state.collected_parameters[agent_name] = {}
        state.collected_parameters[agent_name].update(request.parameters)
        state.print_state()  # 상태 출력 (디버깅용)
        
    if request.message:
        print(f"[Agent Request] 💬 사용자 메시지 추가: {request.message}")
        state.add_user_message(request.message)
        
    try:
        print(f"[Agent Request] 🚀 서브에이전트 ainvoke 호출 시작 ({agent_name})")
        # 서브에이전트 직접 호출 (checkpointer가 config의 thread_id를 보고 이전 기록과 합침)
        result_state = await subagent_graph.ainvoke(state, config=config)
        print("[Agent Request] ✅ 서브에이전트 ainvoke 실행 완료")
        
        reply_message = ""
        # 서브에이전트 그래프의 상태 반환 형태에 따라 처리
        if isinstance(result_state, dict) and result_state.get("messages"):
            last_msg = result_state["messages"][-1]
            reply_message = getattr(last_msg, "content", "") if hasattr(last_msg, "content") else last_msg.get("content", "")
        elif hasattr(result_state, "messages") and result_state.messages:
            last_msg = result_state.messages[-1]
            reply_message = getattr(last_msg, "content", "") if hasattr(last_msg, "content") else last_msg.get("content", "")
        
        action_response = {}
        if isinstance(result_state, dict):
            action_response = result_state.get("response", {}).get(agent_name, {})
        elif hasattr(result_state, "response"):
            action_response = result_state.response.get(agent_name, {})
        
        print(f"[Agent Request] 📤 추출된 응답 메시지: {reply_message}")
        print(f"[Agent Request] 📤 추출된 action_response: {action_response}")
        return IAResponseModel(
            success=True,
            status="COMPLETED",
            resultValue=IAResponseValue(
                message=reply_message,
                value=action_response
            )
        )
        
    except Exception as e:
        print(f"[Agent Request] ❌ 실행 중 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
        return IAResponseModel(
            success=False,
            status="ERROR",
            resultValue=IAResponseValue(message=f"실행 중 오류 발생: {str(e)}")
        )


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)