"""
Session Logger — 세션 기반 통합 대화 로깅
session_logs 컬렉션에 턴/이벤트 단위로 기록합니다.
"""

import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.core.mongo_client import session_logs_collection

KST = timezone(timedelta(hours=9))


def _now_kst_naive() -> datetime:
    """MongoDB에 KST 시각 그대로 저장하기 위한 timezone 없는 datetime."""
    return datetime.now(KST).replace(tzinfo=None)


# ── 이벤트 타입 ───────────────────────────────────────────────────────────────
class EventType:
    USER_MESSAGE  = "user_message"
    LLM_INTENT    = "llm_intent"
    LLM_EXTRACT   = "llm_extract"
    LLM_RESPONSE  = "llm_response"
    LLM_GENERIC   = "llm_generic"
    AGENT_ROUTE   = "agent_route"
    RAG_SEARCH    = "rag_search"
    API_CALL      = "api_call"
    DEVICE_JOB    = "device_job"
    HITL_WAIT     = "hitl_wait"
    HITL_RESUME   = "hitl_resume"
    CHAT_SEND     = "chat_send"
    ERROR         = "error"
    COMPLETE      = "complete"
    CANCEL        = "cancel"


# ── LLM 비용 계산 ─────────────────────────────────────────────────────────────
_MODEL_PRICING = {
    "gemini-2.5-flash":       (0.30,   2.50),
    "gemini-2.5-pro":         (1.25,  10.00),
    "gemini-3.1-flash-lite-preview": (0.25,   1.50),
    "gpt-4o":                 (2.50,  10.00),
    "gpt-4o-mini":            (0.15,   0.60),
    "claude-sonnet-4-6":      (3.00,  15.00),
    "claude-haiku-4-5":       (1.00,   5.00),
}

def calc_cost(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    for key, (in_price, out_price) in _MODEL_PRICING.items():
        if key in model:
            billed_input = (input_tokens - cached_tokens) + (cached_tokens * 0.5)
            return round(
                (billed_input / 1_000_000) * in_price +
                (output_tokens / 1_000_000) * out_price,
                6
            )
    return 0.0

def build_llm_meta(model: str, usage_metadata) -> dict:
    """LangChain response.usage_metadata → event.llm 딕셔너리 변환"""
    if isinstance(usage_metadata, dict):
        input_tokens  = usage_metadata.get("input_tokens", 0)
        output_tokens = usage_metadata.get("output_tokens", 0)
        cached_tokens = usage_metadata.get("cache_read_input_tokens", 0)
    else:
        input_tokens  = getattr(usage_metadata, "input_tokens", 0) or 0
        output_tokens = getattr(usage_metadata, "output_tokens", 0) or 0
        cached_tokens = getattr(usage_metadata, "cache_read_input_tokens", 0) or 0

    return {
        "model":         model,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
        "cost_usd":      calc_cost(model, input_tokens, output_tokens, cached_tokens),
    }


# ── Context Manager ───────────────────────────────────────────────────────────
class _EventCtx:
    def __init__(self):
        self.content:  Optional[str]  = None
        self.llm:      Optional[dict] = None
        self.meta:     dict           = {}
        self.task_id:  Optional[str]  = None

@asynccontextmanager
async def session_event(session_id: str, type: str, label: str, task_id: Optional[str] = None):
    """
    이벤트 기록용 context manager. 블록 종료 시 duration_ms 자동 계산.

    async with session_event(session_id, EventType.LLM_INTENT, "의도 분류") as ev:
        response = await llm.ainvoke(...)
        ev.content = response.content
        ev.llm = build_llm_meta(LLM_LIGHT.model, response.usage_metadata)
    """
    ctx = _EventCtx()
    ctx.task_id = task_id
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
            await add_event(
                session_id, EventType.ERROR, label,
                content=f"[ERROR] {error_info}",
                duration_ms=duration_ms,
                task_id=ctx.task_id,
            )
        else:
            await add_event(
                session_id, type, label,
                content=ctx.content,
                duration_ms=duration_ms,
                llm=ctx.llm,
                meta=ctx.meta,
                task_id=ctx.task_id,
            )


# ── 세션 CRUD ─────────────────────────────────────────────────────────────────
async def create_session(session_id: str, user_id: str, user_name: str, space_name: str) -> None:
    """새 GraphState 생성 시 호출 — 세션 문서 초기화."""
    try:
        now = _now_kst_naive()
        result = await session_logs_collection.insert_one({
            "session_id":        session_id,
            "user_id":           user_id,
            "user_name":         user_name,
            "space_name":        space_name,
            "started_at":        now,
            "last_activity_at":  now,
            "current_turn_idx":  -1,   # 현재 턴 인덱스 추적용
            "turns":             [],
            "summary": {
                "total_turns":    0,
                "total_cost_usd": 0.0,
                "total_llm_calls": 0,
            }
        })
        print(f"[SessionLogger] create_session 성공: {result.inserted_id}")
    except Exception as e:
        print(f"[SessionLogger] create_session 실패: {e}")


async def touch_session(session_id: str) -> None:
    """마지막 활동 시간 갱신."""
    await session_logs_collection.update_one(
        {"session_id": session_id},
        {"$set": {"last_activity_at": _now_kst_naive()}}
    )


# ── 턴 CRUD ───────────────────────────────────────────────────────────────────
async def start_turn(session_id: str) -> None:
    """
    사용자 메시지 수신 시 호출 — 새 턴 추가.
    current_turn_idx를 원자적으로 증가시켜 이후 이벤트가 올바른 턴에 기록되도록 함.
    """
    now = _now_kst_naive()
    turn = {
        "started_at": now,
        "intent":     None,
        "task_id":    None,
        "events":     [],
        "summary":    None,
    }
    await session_logs_collection.update_one(
        {"session_id": session_id},
        {
            "$push": {"turns": turn},
            "$inc":  {"current_turn_idx": 1},   # 인덱스 원자적 증가
        }
    )


async def update_turn_meta(session_id: str, intent: Optional[str] = None, task_id: Optional[str] = None) -> None:
    """턴의 intent / task_id 업데이트."""
    doc = await session_logs_collection.find_one(
        {"session_id": session_id},
        {"current_turn_idx": 1}
    )
    if not doc:
        return
    idx = doc.get("current_turn_idx", 0)
    fields = {}
    if intent  is not None: fields[f"turns.{idx}.intent"]  = intent
    if task_id is not None: fields[f"turns.{idx}.task_id"] = task_id
    if not fields:
        return
    await session_logs_collection.update_one(
        {"session_id": session_id},
        {"$set": fields}
    )


async def end_turn(session_id: str) -> None:
    """응답 전송 직후 호출 — 현재 턴 summary 계산 + 세션 summary 누적."""
    doc = await session_logs_collection.find_one({"session_id": session_id})
    if not doc or not doc.get("turns"):
        return

    idx       = doc.get("current_turn_idx", len(doc["turns"]) - 1)
    last_turn = doc["turns"][idx]
    events    = last_turn.get("events", [])

    llm_events     = [e for e in events if e.get("llm")]
    turn_cost      = round(sum(e["llm"]["cost_usd"] for e in llm_events), 6)
    turn_llm_calls = len(llm_events)

    started_at = last_turn.get("started_at")
    now        = _now_kst_naive()
    turn_duration_ms = int((now - started_at).total_seconds() * 1000) if started_at else None

    turn_summary = {
        "duration_ms":    turn_duration_ms,
        "llm_calls":      turn_llm_calls,
        "total_cost_usd": turn_cost,
    }

    sess_summary = doc.get("summary", {})
    new_summary  = {
        "total_turns":     sess_summary.get("total_turns", 0) + 1,
        "total_cost_usd":  round(sess_summary.get("total_cost_usd", 0.0) + turn_cost, 6),
        "total_llm_calls": sess_summary.get("total_llm_calls", 0) + turn_llm_calls,
    }

    await session_logs_collection.update_one(
        {"session_id": session_id},
        {"$set": {
            f"turns.{idx}.summary": turn_summary,
            "summary":              new_summary,
            "last_activity_at":     now,
        }}
    )


# ── 이벤트 추가 ───────────────────────────────────────────────────────────────
async def add_event(
    session_id:  str,
    type:        str,
    label:       str,
    content:     Optional[str]  = None,
    duration_ms: Optional[int]  = None,
    llm:         Optional[dict] = None,
    meta:        Optional[dict] = None,
    task_id:     Optional[str]  = None,
) -> None:
    """현재 턴(current_turn_idx)의 events 배열에 이벤트 추가."""
    doc = await session_logs_collection.find_one(
        {"session_id": session_id},
        {"current_turn_idx": 1}
    )
    if not doc:
        return
    idx = doc.get("current_turn_idx", 0)

    now = _now_kst_naive()
    event = {
        "type":        type,
        "label":       label,
        "content":     content,
        "ts":          now,
        "duration_ms": duration_ms,
        "llm":         llm,
        "meta":        meta or {},
    }
    if task_id:
        event["task_id"] = task_id

    await session_logs_collection.update_one(
        {"session_id": session_id},
        {"$push": {f"turns.{idx}.events": event}}
    )


# ── 조회 ──────────────────────────────────────────────────────────────────────
async def get_session(session_id: str) -> Optional[dict]:
    """세션 전체 조회."""
    doc = await session_logs_collection.find_one({"session_id": session_id})
    if not doc:
        return None
    doc.pop("_id", None)
    doc.pop("current_turn_idx", None)
    _inject_seq(doc)
    return doc


async def get_sessions_by_user(user_id: str, skip: int = 0, limit: int = 50) -> dict:
    """유저별 세션 목록 (이벤트 제외, 요약만)."""
    total = await session_logs_collection.count_documents({"user_id": user_id})
    cursor = session_logs_collection.find(
        {"user_id": user_id},
        {"turns.events": 0, "current_turn_idx": 0}
    ).sort("started_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=None)
    for doc in docs:
        doc.pop("_id", None)
        _serialize_dates(doc)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


async def get_all_sessions(skip: int = 0, limit: int = 50) -> dict:
    """전체 세션 목록 (이벤트 제외, 요약만)."""
    total = await session_logs_collection.count_documents({})
    cursor = session_logs_collection.find(
        {},
        {"turns.events": 0, "current_turn_idx": 0}
    ).sort("started_at", -1).skip(skip).limit(limit)
    docs = await cursor.to_list(length=None)
    for doc in docs:
        doc.pop("_id", None)
        _serialize_dates(doc)
    return {"items": docs, "total": total, "skip": skip, "limit": limit}


async def get_task_trace(task_id: str) -> list:
    """task_id로 관련 이벤트만 추출 (task 상세 실행 시퀀스 조회용)."""
    pipeline = [
        {"$match": {"turns.events.task_id": task_id}},
        {"$unwind": "$turns"},
        {"$unwind": "$turns.events"},
        {"$match": {"turns.events.task_id": task_id}},
        {"$replaceRoot": {"newRoot": "$turns.events"}},
        {"$sort": {"ts": 1}},
    ]
    cursor = session_logs_collection.aggregate(pipeline)
    events = await cursor.to_list(length=None)
    for i, ev in enumerate(events):
        ev.pop("_id", None)
        ev["seq"] = i + 1
        if ev.get("ts"):
            ev["ts"] = _to_kst_iso(ev["ts"])
    return events


# ── 내부 헬퍼 ─────────────────────────────────────────────────────────────────
def _to_kst_iso(dt) -> str:
    """KST 기준 datetime을 +09:00 ISO 문자열로 변환."""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+09:00"
    return str(dt)

def _inject_seq(doc: dict) -> None:
    """조회 시 turn_seq, event seq 주입 + datetime 직렬화."""
    for t_idx, turn in enumerate(doc.get("turns", [])):
        turn["turn_seq"] = t_idx + 1
        for e_idx, event in enumerate(turn.get("events", [])):
            event["seq"] = e_idx + 1
            if event.get("ts"):
                event["ts"] = _to_kst_iso(event["ts"])
        if turn.get("started_at"):
            turn["started_at"] = _to_kst_iso(turn["started_at"])
    if doc.get("started_at"):
        doc["started_at"] = _to_kst_iso(doc["started_at"])
    if doc.get("last_activity_at"):
        doc["last_activity_at"] = _to_kst_iso(doc["last_activity_at"])

def _serialize_dates(doc: dict) -> None:
    """목록 조회용 — 최상위 datetime 필드만 변환."""
    for key in ("started_at", "last_activity_at"):
        if isinstance(doc.get(key), datetime):
            doc[key] = _to_kst_iso(doc[key])
