from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from agents.network.wifi_line_change.parameter_extractor import (
    extract_parameters,
)
from agents.network.wifi_line_change.state import (
    WifiLineChangeState,
)
from integrations.device_job.schemas import (
    DeviceJobCallback,
)
from tools.network.wifi_line_change import (
    submit_wifi_line_change_job,
)

PARAMETER_PROMPTS = {
    "device_number": "단말기 번호(IMEI)를 알려주세요.",
    "country_code": "SIM을 배정할 국가를 알려주세요.",
    "expiry_date": (
        "서비스 만료일을 알려주세요. "
        "(YYYYMMDD 형식, 예: 20260131)"
    ),
}

# Assistant 메시지를 LangGraph state 형식으로 추가
def _append_message(
    message: str,
) -> dict[str, Any]:
    return {
        "messages": [
            AIMessage(content=message)
        ]
    }


def _latest_human_message_content(
    messages: list[Any] | None,
) -> str:
    for message in reversed(messages or []):
        if isinstance(message, HumanMessage):
            return str(message.content)

        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))

    return ""

# 기존 파라미터에 특정 값을 추가하거나 갱신
def _with_param(
    current_params: dict[str, Any],
    key: str,
    value: Any,
) -> dict[str, Any]:
    params = dict(current_params or {})
    params[key] = value

    return {
        "parameters": params,
    }

# 현재 사용자 발화에서 업무 파라미터를 추출해 state에 반영
async def apply_user_input(
    state: WifiLineChangeState,
) -> dict[str, Any]:
    current_input = (
        _latest_human_message_content(state.get("messages"))
        or state.get("current_input", "")
    )

    if not current_input:
        return {}

    extracted = await extract_parameters(
        current_input,
        pending_parameter=state.get(
            "pending_parameter"
        ),
    )

    params = dict(
        state.get(
            "parameters",
            {},
        )
    )

    if extracted.device_number:
        params["device_number"] = (
            extracted.device_number
        )

    if extracted.country_code:
        params["country_code"] = (
            extracted.country_code
        )

    if extracted.expiry_date:
        params["expiry_date"] = (
            extracted.expiry_date
        )

    return {
        "parameters": params,
        "pending_parameter": None,
    }

# 필수 파라미터 중 누락된 값을 찾아 사용자에게 추가 입력 요청
async def ask_missing_parameter(
    state: WifiLineChangeState,
) -> dict[str, Any]:
    params = state.get(
        "parameters",
        {},
    )

    for parameter, prompt in PARAMETER_PROMPTS.items():
        if not params.get(parameter):
            return {
                "status": "waiting_input",
                "pending_parameter": parameter,
                **_append_message(prompt),
            }

    return {}

# 수집된 파라미터로 와이파이 회선 변경 Device Job 요청
async def request_device_job(
    state: WifiLineChangeState,
) -> dict[str, Any]:
    params = state.get(
        "parameters",
        {},
    )

    if params.get("_job_id"):
        return {}

    context = state.get(
        "context",
        {},
    )

    submission = await submit_wifi_line_change_job(
        device_number=params["device_number"],
        country_code=params["country_code"],
        expiry_date=params["expiry_date"],
        thread_id=state["session_id"],
        user_name=context.get("user_name"),
    )

    return {
        "status": "running",
        **_with_param(
            params,
            "_job_id",
            submission.job_id,
        ),
        **_append_message(
            "회선변경 작업을 PC 에이전트에 전달했습니다.\n"
            "작업 완료 후 결과를 알려드리겠습니다."
        ),
    }

# Device Job 완료 Callback이 올 때까지 실행을 중단하고 결과 수신
async def wait_for_device_job(
    state: WifiLineChangeState,
) -> dict[str, Any]:
    job_id = state["parameters"]["_job_id"]

    callback_payload = interrupt(
        {
            "job_id": job_id,
            "status": "waiting",
        }
    )

    callback = DeviceJobCallback(
        **callback_payload
    )

    if callback.status == "error":
        return {
            "status": "failed",
            "response": {
                "success": False,
                "reason": (
                    callback.error_message
                    or "UNKNOWN"
                ),
            },
            "error_message": (
                callback.error_message
                or "Device Job 처리 중 오류가 발생했습니다."
            ),
        }

    return {
        "status": "completed",
        "response": callback.result or {},
        "error_message": None,
    }

# Device Job 실행 결과를 성공/실패 메시지로 변환해 최종 state에 반영
async def handle_result(
    state: WifiLineChangeState,
) -> dict[str, Any]:
    response = state.get(
        "response",
        {},
    )

    params = state.get(
        "parameters",
        {},
    )

    if response.get("success"):
        return {
            "status": "completed",
            **_with_param(
                params,
                "job_done",
                True,
            ),
            **_append_message(
                response.get(
                    "message",
                    "와이파이 회선변경이 완료되었습니다.",
                )
            ),
        }

    reason = response.get(
        "reason",
        "UNKNOWN",
    )

    return {
        "status": "failed",
        **_with_param(
            params,
            "job_done",
            True,
        ),
        "error_message": reason,
        **_append_message(
            "와이파이 회선변경에 실패했습니다.\n"
            f"사유: {reason}"
        ),
    }
