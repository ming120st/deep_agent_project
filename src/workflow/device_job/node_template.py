"""
DeviceJob 에이전트 노드 템플릿

새 에이전트에서 로컬 PC 작업이 필요할 때 이 패턴을 참고하세요.

흐름:
  request_device_job 노드
    → 로컬에 POST (job_id 즉시 반환)
    → MongoDB에 job 저장
    → interrupt() 로 그래프 일시정지
    → [로컬 PC 작업 실행 중...]
    → /device-callback 수신
    → Command(resume=callback_payload) 로 재개
    → 이 노드의 interrupt() 반환값 = DeviceJobCallback dict
  handle_device_result 노드
    → 결과 처리
"""
import os
import httpx

from langgraph.types import interrupt

from src.workflow.graph_infra.state import GraphState
from src.workflow.device_job.schemas import DeviceJobRequest, DeviceJobCallback
from src.core.device_job_manager import create_device_job
from src.core.task_manager import set_waiting, set_completed, set_cancelled

DEVICE_JOB_URL = os.getenv("DEVICE_JOB_URL", "")       # ngrok URL (예: https://xxxx.ngrok.io)
DEVICE_JOB_SECRET = os.getenv("DEVICE_JOB_SECRET", "")  # 인증 키

AGENT_NAME = "your_agent_name"  # ← 실제 에이전트 이름으로 교체


# ── 로컬 PC에 작업 요청 + interrupt ──────────────────────────────────────────

async def request_device_job(state: GraphState) -> GraphState:
    params = state.collected_parameters.get(AGENT_NAME, {})
    thread_id = state.chat_context.get("user_id", "")

    req = DeviceJobRequest(
        thread_id=thread_id,
        task="your_task_name",       # ← 작업 이름
        parameters=params,
        context={
            "user_name": state.chat_context.get("user_name"),
            # 필요한 컨텍스트만 추가
        },
    )

    # 1. MongoDB에 job 저장 (callback 라우팅용)
    await create_device_job(req.job_id, thread_id)

    # 2. 로컬 PC에 job 전달 (job 접수 확인만, 결과는 나중에 callback으로)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{DEVICE_JOB_URL}/device-job",
                json=req.model_dump(),
                headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                timeout=10.0,
            )
            resp.raise_for_status()
    except Exception as e:
        state.add_ai_message(f"⚠️ 로컬 장치 연결에 실패했습니다. 잠시 후 다시 시도해주세요.\n({e})")
        state.task_status = "error"
        await set_cancelled(state, result=f"DeviceJob 전송 실패: {e}")
        state.current_agent = None
        return state

    state.add_ai_message("✅ 작업을 로컬 장치에 전달했습니다. 완료되면 결과를 알려드릴게요!")
    await set_waiting(state, result=f"DeviceJob 대기 중: {req.job_id}")

    # 3. 그래프 일시정지 — /device-callback 수신 시 자동 재개
    callback_payload: dict = interrupt({"job_id": req.job_id, "status": "waiting"})

    # ── 재개 후 이 라인부터 실행됨 ────────────────────────────────────────────
    callback = DeviceJobCallback(**callback_payload)

    if callback.status == "error":
        state.add_ai_message(f"⚠️ 작업 중 오류가 발생했습니다: {callback.error_message}")
        state.task_status = "error"
        await set_cancelled(state, result=f"DeviceJob 오류: {callback.error_message}")
        state.current_agent = None
        return state

    # 결과를 state.response에 저장
    state.response.update(callback.result)

    # updated_fields가 있으면 collected_parameters에 머지
    if callback.updated_fields:
        cp = dict(state.collected_parameters)
        cp.setdefault(AGENT_NAME, {}).update(callback.updated_fields)
        state.collected_parameters = cp

    return state


# ── 결과 처리 노드 ─────────────────────────────────────────────────────────────

async def handle_device_result(state: GraphState) -> GraphState:
    result = state.response.get("your_result_key", "")
    state.add_ai_message(f"✅ 작업이 완료되었습니다.\n\n{result}")
    await set_completed(state, result="DeviceJob 완료")
    state.current_agent = None
    return state