"""
Trace Logger — task별 실행 시퀀스 로깅
task_traces 컬렉션에 step 단위로 기록합니다.

사용 예시:
    # trace 초기화 (task 생성 직후)
    await create_trace(task_id)

    # step 기록 (context manager)
    async with trace_step(task_id, StepType.LLM_INTENT, "의도 분류") as step:
        result = await llm_call(...)
        step.content = result.text
        step.llm = build_llm_meta("gemini-1.5-flash", result.usage_metadata)

    # 단순 step 기록 (LLM 없는 노드)
    await add_step(task_id, StepType.AGENT_ROUTE, "에이전트 라우팅", content="esim_reissue")

    # trace 마무리 (task 완료/실패 시)
    await finalize_trace(task_id)
"""

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 에이전트 연동 예시 (기존 노드에 trace 추가하는 패턴)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
── 예시 1: task 생성 시 (task_manager.create_task 호출 직후) ──

    task_id = await create_task(user_id, user_name, agent_name, agent_display_name)
    await create_trace(task_id)                          # ← 추가
    await add_step(task_id, StepType.USER_MESSAGE,
                   "사용자 메시지 수신",
                   content=state.messages[-1].content)  # ← 추가

── 예시 2: LLM 호출 노드 ──

    async with trace_step(task_id, StepType.LLM_INTENT, "의도 분류") as step:
        response = await llm.ainvoke(messages)
        step.content = response.content
        step.llm = build_llm_meta("gemini-1.5-flash", response.usage_metadata)

── 예시 3: HITL interrupt 직전/직후 ──

    await add_step(task_id, StepType.HITL_WAIT, "사용자 입력 대기 (iccid 선택)")
    interrupt({"type": "select", ...})

    # interrupt resume 후 첫 노드에서
    await add_step(task_id, StepType.HITL_RESUME,
                   "사용자 응답 수신",
                   content=f"선택된 iccid: {selected_iccid}")

── 예시 4: DeviceJob ──

    async with trace_step(task_id, StepType.DEVICE_JOB, "DeviceJob 실행 (Playwright)") as step:
        job_id = await dispatch_device_job(payload)
        step.content = f"job_id: {job_id}"
    # DeviceJob 콜백 수신 후
    await add_step(task_id, StepType.DEVICE_JOB,
                   "DeviceJob 완료",
                   content=f"결과: {callback_result}")

── 예시 5: task 완료/실패 시 ──

    await add_step(task_id, StepType.COMPLETE, "완료", content="이심 재발급 완료 (1건)")
    await finalize_trace(task_id)   # summary 계산

    # 오류 시
    await add_step(task_id, StepType.ERROR, "오류", content=str(e))
    await finalize_trace(task_id)
"""
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from src.core.mongo_client import task_traces_collection


# ── Step 타입 상수 ────────────────────────────────────────────────────────────
class StepType:
    USER_MESSAGE = "user_message"   # 사용자 발화 수신
    LLM_INTENT   = "llm_intent"    # 의도 분류 LLM 호출
    LLM_EXTRACT  = "llm_extract"   # 파라미터 추출 LLM 호출
    LLM_RESPONSE = "llm_response"  # 응답 메시지 생성 LLM 호출
    LLM_GENERIC  = "llm_generic"   # 기타 LLM 호출
    AGENT_ROUTE  = "agent_route"   # 에이전트 선택/라우팅
    API_CALL     = "api_call"      # 외부 API 호출
    DEVICE_JOB   = "device_job"    # DeviceJob (Playwright)
    HITL_WAIT    = "hitl_wait"     # 사용자 입력 대기 시작
    HITL_RESUME  = "hitl_resume"   # 사용자 응답으로 재개
    ERROR        = "error"         # 오류 발생
    COMPLETE     = "complete"      # 정상 완료
    CANCEL       = "cancel"        # 취소


# ── LLM 메타 헬퍼 ─────────────────────────────────────────────────────────────
# 모델별 1M 토큰당 USD 단가 (input, output)
_MODEL_PRICING = {
    "gemini-3-flash-preview":                (0.5,  3),
    "gemini-3.1-pro-preview":                (2,   15),
    "gemini-3.1-flash-lite-preview":         (0.25,    1.5),
    "gpt-5.1":                               (1.25,    10),
    "gpt-5.2":                               (1.75,    14),
    "claude-sonnet-4-6":                     (3,     15),
    "claude-haiku-4-5":                      (1,    5),
}

def calc_cost(model: str, input_tokens: int, output_tokens: int,
              cached_tokens: int = 0) -> float:
    for key, (in_price, out_price) in _MODEL_PRICING.items():
        if key in model:
            # 캐시 토큰은 절반 단가 적용 (GPT 기준, 지원 안 하면 0이라 무해)
            billed_input = (input_tokens - cached_tokens) + (cached_tokens * 0.5)
            return round(
                (billed_input / 1_000_000) * in_price +
                (output_tokens / 1_000_000) * out_price,
                6
            )
    return 0.0

def build_llm_meta(model: str, usage_metadata: dict) -> dict:
    input_tokens  = usage_metadata.get("input_tokens", 0)
    output_tokens = usage_metadata.get("output_tokens", 0)
    cached_tokens = usage_metadata.get("cache_read_input_tokens", 0)  # GPT만 들어옴

    return {
        "model":          model,
        "input_tokens":   input_tokens,
        "output_tokens":  output_tokens,
        "cached_tokens":  cached_tokens,  # 0이면 프론트에서 표시 안 하면 됨
        "cost_usd":       calc_cost(model, input_tokens, output_tokens, cached_tokens),
    }


# ── Context Manager ───────────────────────────────────────────────────────────
class _StepCtx:
    """trace_step context manager 내부 상태 객체"""
    def __init__(self):
        self.content: Optional[str] = None
        self.llm: Optional[dict] = None

@asynccontextmanager
async def trace_step(task_id: str, type: str, label: str):
    """
    step 기록용 context manager. 블록 종료 시 duration_ms 자동 계산.

    async with trace_step(task_id, StepType.LLM_INTENT, "의도 분류") as step:
        result = await llm_call(...)
        step.content = result.text
        step.llm = build_llm_meta("gemini-1.5-flash", result.usage_metadata)
    """
    ctx = _StepCtx()
    start = time.time()
    error_info = None
    try:
        yield ctx
    except Exception as e:
        error_info = str(e)
        raise
    finally:
        duration_ms = int((time.time() - start) * 1000)
        if error_info:
            ctx.content = f"[ERROR] {error_info}"
            await add_step(task_id, StepType.ERROR, label, content=ctx.content, duration_ms=duration_ms)
        else:
            await add_step(task_id, type, label, content=ctx.content, duration_ms=duration_ms, llm=ctx.llm)


# ── CRUD ──────────────────────────────────────────────────────────────────────
async def create_trace(task_id: str) -> None:
    """task 생성 직후 호출 — trace 문서 초기화"""
    now = datetime.now(timezone.utc)
    await task_traces_collection.insert_one({
        "task_id": task_id,
        "steps": [],
        "summary": None,
        "created_at": now,
        "updated_at": now,
    })


async def add_step(
    task_id: str,
    type: str,
    label: str,
    content: Optional[str] = None,
    duration_ms: Optional[int] = None,
    llm: Optional[dict] = None,
) -> None:
    """
    step 하나를 trace에 추가.
    seq는 저장하지 않고, 조회 시 배열 인덱스 + 1로 계산.
    """
    now = datetime.now(timezone.utc)
    step = {
        "type":        type,
        "label":       label,
        "content":     content,
        "ts":          now,
        "duration_ms": duration_ms,
        "llm":         llm,
    }
    await task_traces_collection.update_one(
        {"task_id": task_id},
        {
            "$push": {"steps": step},
            "$set":  {"updated_at": now},
        }
    )


async def finalize_trace(task_id: str) -> None:
    """
    task 완료/실패 시 호출 — summary 계산 후 저장.
    총 시간, LLM 호출 횟수, 토큰 합계, 비용 합계.
    """
    doc = await task_traces_collection.find_one({"task_id": task_id})
    if not doc:
        return

    steps = doc.get("steps", [])
    llm_steps = [s for s in steps if s.get("llm")]

    # 총 소요 시간: 첫 step ts ~ 마지막 step ts
    timestamps = [s["ts"] for s in steps if s.get("ts")]
    total_duration_ms = None
    if len(timestamps) >= 2:
        delta = timestamps[-1] - timestamps[0]
        total_duration_ms = int(delta.total_seconds() * 1000)

    summary = {
        "total_duration_ms":   total_duration_ms,
        "total_llm_calls":     len(llm_steps),
        "total_input_tokens":  sum(s["llm"]["input_tokens"]  for s in llm_steps),
        "total_output_tokens": sum(s["llm"]["output_tokens"] for s in llm_steps),
        "total_cost_usd":      round(sum(s["llm"]["cost_usd"] for s in llm_steps), 6),
    }

    await task_traces_collection.update_one(
        {"task_id": task_id},
        {"$set": {"summary": summary, "updated_at": datetime.now(timezone.utc)}}
    )


async def get_trace(task_id: str) -> Optional[dict]:
    """task_id로 trace 조회. steps에 seq(1-based) 필드 추가해서 반환."""
    doc = await task_traces_collection.find_one({"task_id": task_id})
    if not doc:
        return None
    doc.pop("_id", None)

    # seq 주입 (저장은 안 하고 반환용으로만)
    for i, step in enumerate(doc.get("steps", [])):
        step["seq"] = i + 1
        if step.get("ts"):
            step["ts"] = step["ts"].isoformat()

    return doc