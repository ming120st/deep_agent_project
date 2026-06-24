import asyncio
import json
import time

from fastapi import APIRouter, BackgroundTasks, Request, Response

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
)
from src.agent.registry import SCHEDULE_REGISTRY, TRIGGER_REGISTRY
from src.app.lifespan import get_scheduler
from src.core.members_manager import upsert_member
from src.core.spaces_manager import upsert_space
from src.orchestrator import orchestrate_chat_event
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph

# Google Chat 이벤트 수신/해석 진입 라우터
router = APIRouter()


# 원본 Google Chat payload를 내부 표준 이벤트 형태로 정규화
def _parse_google_chat_event(event: dict) -> dict:
    user = event.get("user", {})
    space = event.get("space", {})
    message = event.get("message", {})
    quoted_snapshot = message.get("quotedMessageMetadata", {}).get("quotedMessageSnapshot", {})

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
    # 설치/제거 이벤트는 챗 처리 대상이 아니므로 무시
    return event_type in {"ADDED_TO_SPACE", "REMOVED_FROM_SPACE"}


async def _remember_chat_space(chat_event: dict, background_tasks: BackgroundTasks) -> None:
    # DM은 멤버 기준, ROOM/GROUP/SPACE는 스페이스 기준으로 메타를 저장한다.
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
    # 사용자(thread_id) 단위 기존 세션을 복원하고, 1시간 무활동이면 새 세션으로 리셋한다.
    config = {"configurable": {"thread_id": user_id}}
    # get_state는 langgraph의 메서드인데 graph.get_state(config) 하면 체크포인터에 있는 state를 반환한다.
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
    # 이벤트 메타를 state.chat_context에 붙이고, 사용자 메시지를 대화 히스토리에 추가한다.
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
    # Google Chat 카드 액션 버튼별 처리 분기
    action_method = event.get("action", {}).get("actionMethodName", "")

    if action_method == "submitBqSchedule":
        return await _submit_bq_schedule(event, user_id, space_name)

    if action_method == "deleteBqSchedule":
        return await _delete_bq_schedule_from_card(event)

    if action_method == "showAgentHelp":
        return await _show_agent_help_card(event, space_name)

    return {}


async def _submit_bq_schedule(event: dict, user_id: str, space_name: str) -> dict:
    # 카드 폼 값을 읽어 BQ 스케줄을 등록하고, 사용자에게 확인 메시지를 반환한다.
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
            scheduler=get_scheduler(),
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
    # 카드에서 전달된 schedule_id로 스케줄을 삭제한다.
    form_inputs = event.get("common", {}).get("formInputs", {})
    schedule_id = _get_form_value(form_inputs, "schedule_id", "")
    if not schedule_id:
        return {}

    deleted = await delete_bq_schedule(get_scheduler(), schedule_id)
    msg = f"🗑️ 스케줄 `{schedule_id[-8:]}` 이 삭제되었습니다." if deleted else "⚠️ 스케줄을 찾을 수 없습니다."
    return {"text": msg}


async def _show_agent_help_card(event: dict, space_name: str) -> dict:
    # 카드 파라미터(agent_key)로 에이전트 상세 도움 카드를 구성해 전송한다.
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
    # Google Chat formInputs의 중첩 구조에서 문자열 값을 안전하게 꺼낸다.
    return form_inputs.get(key, {}).get("stringInputs", {}).get("value", [default])[0]


async def _handle_chat_slash_command(message: str, user_id: str) -> dict | None:
    # 메시지가 슬래시 명령이면 즉시 처리하고, 아니면 None을 반환해 일반 오케스트레이션으로 넘긴다.
    stripped_message = message.strip()

    if stripped_message == "/데이터분석스케줄":
        return build_bq_schedule_card()

    if stripped_message.startswith("/분석스케줄취소"):
        return await _cancel_bq_schedule_by_command(stripped_message, user_id)

    if stripped_message == "/내스케줄":
        return await _list_bq_schedules(user_id)

    return None


async def _cancel_bq_schedule_by_command(message: str, user_id: str) -> dict:
    # `/분석스케줄취소 {스케줄ID}` 형태를 파싱해 사용자 스케줄을 취소한다.
    parts = message.split()
    if len(parts) < 2:
        return {"text": "사용법: `/분석스케줄취소 {스케줄ID}`"}

    short_id = parts[1]
    doc = await bq_schedules_collection.find_one(
        {"user_id": user_id, "schedule_id": {"$regex": short_id + "$"}}
    )
    if not doc:
        return {"text": "⚠️ 해당 스케줄 ID를 찾을 수 없습니다."}

    await delete_bq_schedule(get_scheduler(), doc["schedule_id"])
    return {"text": f"🗑️ 스케줄 `{short_id}` 이 삭제되었습니다."}


async def _list_bq_schedules(user_id: str) -> dict:
    # 사용자의 등록 스케줄 목록을 사람 읽기 쉬운 텍스트로 포맷한다.
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
    # Google Chat webhook 즉시 응답용 빈 JSON(비동기 처리 시 사용)
    return Response(content=json.dumps({}), media_type="application/json", status_code=200)


async def _run_chat_orchestration(state: GraphState, user_id: str, is_new_session: bool) -> None:
    # webhook 응답 지연을 막기 위해 오케스트레이션은 백그라운드 태스크로 실행한다.
    config = {"configurable": {"thread_id": user_id}}
    asyncio.create_task(orchestrate_chat_event(state, config, is_new_session))


@router.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    # /chat 메인 엔드포인트 처리 순서:
    # 1) 이벤트 파싱 2) 무시/카드클릭/일반메시지 분기 3) 세션 복원 4) 슬래시 명령 우선 처리 5) 오케스트레이션 실행
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
