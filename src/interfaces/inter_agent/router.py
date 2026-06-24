import asyncio
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel

from src.workflow.graph_infra.state import GraphState

router = APIRouter()


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


@router.post("/agent_request", response_model=IAResponseModel)
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
