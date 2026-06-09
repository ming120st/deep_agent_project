"""
src/agent/chat/wifi_line_change/node.py

와이파이 회선변경 에이전트 노드.
흐름:
  ask_device_number → ask_country_code → request_device_job (interrupt) → handle_result
"""
from __future__ import annotations

import os
import httpx

from langgraph.types import interrupt

from src.workflow.graph_infra.state import GraphState
from src.workflow.device_job.schemas import DeviceJobRequest, DeviceJobCallback
from src.core.device_job_manager import create_device_job
from src.core.task_manager import set_waiting, set_completed, set_cancelled
from src.core.session_logger import add_event, session_event, EventType

AGENT_NAME = "wifi_line_change"

DEVICE_JOB_URL    = os.getenv("DEVICE_JOB_URL", "")
DEVICE_JOB_SECRET = os.getenv("DEVICE_JOB_SECRET", "")

INTER_AGENT_CALLBACK_URL    = os.getenv("INTER_AGENT_CALLBACK_URL", "")
INTER_AGENT_CALLBACK_SECRET = os.getenv("INTER_AGENT_CALLBACK_SECRET", "")


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

async def send_inter_agent_callback(state: GraphState, success: bool, status: str, message: str, value: dict = None):
    """CS챗봇 등 Inter-Agent 요청에 대한 콜백 전송"""
    if not state.chat_context.get("is_cs_api"):
        return
        
    job_id = state.chat_context.get("ia_job_id", "")
    session_id = state.session_id
    user_id = state.chat_context.get("user_id", "")
    
    if not INTER_AGENT_CALLBACK_URL:
        print("[IA Callback] URL이 설정되지 않았습니다.")
        return
        
    payload = {
        "job_id": job_id,
        "session_id": session_id,
        "user_id": user_id,
        "agent_name": AGENT_NAME,
        "success": success,
        "status": status,
        "resultValue": {
            "message": message,
            "value": value or {}
        }
    }
    
    headers = {
        "Content-Type": "application/json",
        "X-Inter-Agent-Secret": INTER_AGENT_CALLBACK_SECRET
    }
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(INTER_AGENT_CALLBACK_URL, json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
        print(f"[IA Callback] 콜백 전송 성공: {payload}")
    except Exception as e:
        print(f"[IA Callback Failed] 콜백 전송 실패: {e}")


def _get_params(state: GraphState) -> dict:
    return state.collected_parameters.get(AGENT_NAME, {})


def _set_param(state: GraphState, key: str, value) -> None:
    cp = dict(state.collected_parameters)
    if AGENT_NAME not in cp:
        cp[AGENT_NAME] = {}
    cp[AGENT_NAME] = {**cp[AGENT_NAME], key: value}
    state.collected_parameters = cp


# ── 노드 ──────────────────────────────────────────────────────────────────────

async def ask_device_number(state: GraphState) -> GraphState:
    """단말기 번호(IMEI) 요청."""
    state.add_ai_message(
        "와이파이 회선변경을 진행합니다!\n"
        "단말기 번호(IMEI)를 알려주세요."
    )
    await add_event(state.session_id, EventType.HITL_WAIT, "단말기 번호 입력 요청", task_id=state.task_id)
    await set_waiting(state, result="단말기 번호 입력 요청")
    return state


async def ask_country_code(state: GraphState) -> GraphState:
    """국가코드 요청."""
    state.add_ai_message(
        "SIM을 배정할 하나의 국가를 알려주세요."
    )
    await add_event(state.session_id, EventType.HITL_WAIT, "국가코드 입력 요청", task_id=state.task_id)
    await set_waiting(state, result="국가코드 입력 요청")
    return state

async def ask_expiry_date(state: GraphState) -> GraphState:
    """회선 만료일 요청."""
    state.add_ai_message(
        "서비스 만료일을 알려주세요. 해당일자까지 유효하도록 설정하겠습니다. (YYYYMMDD 형식, 예: 20260131)"
    )
    await add_event(state.session_id, EventType.HITL_WAIT, "회선 만료일 입력 요청", task_id=state.task_id)
    await set_waiting(state, result="회선 만료일 입력 요청")
    return state


async def cancel_inquiry(state: GraphState) -> GraphState:
    """업무 취소."""
    state.add_ai_message("와이파이 회선변경 업무가 취소되었습니다.")
    await add_event(state.session_id, EventType.CANCEL, "업무 취소", task_id=state.task_id)
    await set_cancelled(state, result="사용자 취소 의사")
    state.current_agent = None
    return state


async def request_device_job(state: GraphState) -> GraphState:
    """
    device_agent에 회선변경 작업 요청 후 job_id를 상태에 저장하고 반환.
    (대기는 다음 노드에서 수행)
    """
    params    = _get_params(state)
    is_cs_api = state.chat_context.get("is_cs_api", False)
    thread_id = state.session_id if is_cs_api else state.chat_context.get("user_id", "")
    
    # 이미 job_id가 있다면 그대로 통과 (재실행 방지 최후 보루)
    if params.get("_job_id"):
        return state

    req = DeviceJobRequest(
        thread_id=thread_id,
        task="wifi_sim_change",
        parameters={
            "imei":         params.get("device_number", ""),
            "country_code": params.get("country_code", ""),
            "expiry_date":  params.get("expiry_date", ""),
        },
        context={"user_name": state.chat_context.get("user_name")},
    )
    job_id = req.job_id

    # 상태 업데이트
    _set_param(state, "_job_id", job_id)

    # MongoDB에 job 저장
    await create_device_job(
        job_id, 
        thread_id, 
        task_id=state.task_id, 
        is_cs_api=is_cs_api, 
        agent_name=AGENT_NAME
    )

    # device_agent에 전송
    try:
        async with session_event(state.session_id, EventType.DEVICE_JOB, "DeviceJob 요청",
                                  task_id=state.task_id) as ev:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{DEVICE_JOB_URL}/device-job",
                    json=req.model_dump(),
                    headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                    timeout=10.0,
                )
                resp.raise_for_status()
            ev.content = f"job_id: {job_id}"
    except Exception as e:
        state.add_ai_message(f"⚠️ PC 에이전트 연결에 실패했습니다. 잠시 후 다시 시도해주세요.\n({e})")
        await set_cancelled(state, result=f"DeviceJob 전송 실패: {e}")
        state.current_agent = None
        return state

    state.add_ai_message(
        "✅ 회선변경 작업을 PC에이전트에 전달했습니다.\n"
        "완료되면 결과를 알려드릴게요! 잠시만 기다려주세요."
    )
    await add_event(state.session_id, EventType.HITL_WAIT, "DeviceJob 콜백 대기", task_id=state.task_id)
    await set_waiting(state, result=f"DeviceJob 대기 중: {job_id}")

    return state


async def wait_for_device_job(state: GraphState) -> GraphState:
    """
    이전 노드에서 저장된 job_id를 기반으로 인터럽트 대기.
    """
    params = _get_params(state)
    job_id = params.get("_job_id")

    # interrupt — /device-callback 수신 시 자동 재개
    callback_payload: dict = interrupt({"job_id": job_id, "status": "waiting"})

    # ── 재개 후 이 라인부터 실행 ──────────────────────────────────────────────
    await add_event(state.session_id, EventType.HITL_RESUME, "DeviceJob 콜백 수신", task_id=state.task_id)
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "error":
        reason = callback.error_message or "UNKNOWN"
        state.response.setdefault(AGENT_NAME, {}).update({"success": False, "reason": reason})
    else:
        state.response.setdefault(AGENT_NAME, {}).update(callback.result)
        
    return state


async def handle_result(state: GraphState) -> GraphState:
    """회선변경 결과 메시지 생성."""
    result = state.response.get(AGENT_NAME, {})

    if not result.get("success"):
        reason = result.get("reason", "UNKNOWN")
        err_msg = _error_message(reason)
        state.add_ai_message(err_msg)
        await add_event(state.session_id, EventType.CANCEL, f"회선변경 실패: {reason}", task_id=state.task_id)
        await set_cancelled(state, result=f"회선변경 실패: {reason}")

        # 외부 API 요청 시 콜백 전송 (실패)
        await send_inter_agent_callback(state, success=False, status="ERROR", message=err_msg)
    else:
        imsi      = result.get("changed_imsi", "")
        plan       = result.get("plan_name", "")
        diff_plan  = result.get("is_different_plan", False)
        plan_note  = "다른 플랜" if diff_plan else "동일 플랜"
        
        success_msg = (
            f"✅ 와이파이 회선변경이 완료되었습니다!\n\n"
            f"• 변경된 IMSI: `{imsi}`\n"
            f"• 기존 요금제: {plan}\n"
            f"• SIM 유형: {plan_note}\n\n"
            "_단말기를 재부팅하면 새 회선이 적용됩니다._"
        )

        state.add_ai_message(success_msg)
        await add_event(state.session_id, EventType.COMPLETE, f"회선변경 완료: {imsi}", task_id=state.task_id)
        await set_completed(state, result=f"회선변경 완료: {imsi}")

        # 외부 API 요청 시 콜백 전송 (성공)
        callback_msg = f"사용자가 사용중인 와이파이도시락의 장애 조치를 위한 회선 변경이 성공적으로 완료됨."
        await send_inter_agent_callback(state, success=True, status="COMPLETED", message=callback_msg, value=result)

    state.current_agent = None
    _set_param(state, "job_done", True)
    return state


def _error_message(reason: str) -> str:
    messages = {
        "Device Status: Manual shutdown":      "⚠️ 기기가 꺼져 있습니다. 기기를 켠 후 다시 시도해주세요.",
        "NO_SIM":          "⚠️ 해당 국가에서 사용 가능한 SIM을 찾을 수 없습니다.",
        "ALL_ICCID_OCCUPIED": "⚠️ 조회된 SIM이 모두 사용 중입니다. 잠시 후 다시 시도해주세요.",
    }
    return messages.get(reason, f"⚠️ 회선변경 중 오류가 발생했습니다.")