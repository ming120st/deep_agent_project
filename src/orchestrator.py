"""
오케스트레이터 (Orchestrator)

FastAPI 백엔드에서 호출되는 진입점.
Google Chat 메시지를 받아 메인 대화 그래프를 실행합니다.
"""
import asyncio
from langgraph.types import Command
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph
from src.core.session_logger import create_session, start_turn, end_turn, touch_session, add_event, EventType

_user_locks: dict[str, asyncio.Lock] = {}

def _get_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]
def _get_chat_reply_kwargs(state: GraphState) -> dict:
    """
    Google Chat 원문 인용/스레드 답장용 kwargs를 만든다.
    """
    chat_context = getattr(state, "chat_context", {}) or {}

    return {
        "thread_name": chat_context.get("thread_name"),
        "quoted_message_name": chat_context.get("source_message_name"),
        "quoted_message_last_update_time": chat_context.get("source_message_last_update_time"),
    }


async def orchestrate_chat_event(state: GraphState, config: dict, is_new_session: bool, is_pending_interrupt: bool = False, user_message: str = "") -> None:
    """
    메인 대화 그래프를 실행합니다.

    흐름:
      1. user_id별 lock 획득
      2. 세션 생성/복원
      3. 턴 시작
      4. main_graph 실행
      5. 예외 발생 시 fallback 메시지 전송
      6. 턴 종료 및 세션 활동시간 갱신
    """
    user_id = state.chat_context.get("user_id", "")
    lock = _get_lock(user_id)

    if lock.locked():
        from src.core.google_chat import send_message

        space_name = state.chat_context.get("space_name", "")
        if space_name:
            await send_message(
                space_name=space_name,
                message="⏳ 이전 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.",
                **_get_chat_reply_kwargs(state),
            )
        return

    async with lock:
        session_id = state.session_id

        if is_new_session:
            await create_session(
                session_id=session_id,
                user_id=state.chat_context.get("user_id", ""),
                user_name=state.chat_context.get("user_name", ""),
                space_name=state.chat_context.get("space_name", ""),
            )

        await start_turn(session_id)

        try:
            if is_pending_interrupt:
                await main_graph.ainvoke(Command(resume=user_message), config=config)
            else:
                await main_graph.ainvoke(state, config=config)

        except Exception as e:
            print(f"[Orchestrator] 오류: {e}")
            await add_event(
                session_id,
                EventType.ERROR,
                "그래프 실행 오류",
                content=str(e),
            )
            await _send_fallback_error(state)

        finally:
            await end_turn(session_id)
            await touch_session(session_id)


async def _send_fallback_error(state: GraphState) -> None:
    """그래프 실행이 완전히 실패했을 때 최후 수단으로 오류 메시지를 전송합니다."""
    from src.core.google_chat import send_message

    space_name = state.chat_context.get("space_name", "")
    if not space_name:
        return

    try:
        error_message = "죄송합니다. 시스템 오류가 발생했습니다. AI팀으로 알려주세요!"
        state.add_ai_message(error_message)

        await send_message(
            space_name=space_name,
            message=error_message,
            **_get_chat_reply_kwargs(state),
        )

    except Exception as e:
        print(f"[Orchestrator] Fallback 메시지 전송도 실패: {e}")