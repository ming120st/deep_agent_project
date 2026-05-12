"""
오케스트레이터 (Orchestrator)

FastAPI 백엔드에서 호출되는 진입점.
Google Chat 메시지를 받아 메인 대화 그래프를 실행합니다.
"""
import asyncio
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph
from src.core.session_logger import create_session, start_turn, end_turn, touch_session, add_event, EventType

_user_locks: dict[str, asyncio.Lock] = {}

def _get_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]

async def orchestrate_chat_event(state: GraphState, config: dict, is_new_session: bool) -> None:
    """
    메인 대화 그래프를 실행합니다.

    흐름:
    우선 user_id별로 락을 걸어 동일 사용자의 이벤트가 순차적으로 처리되도록 합니다. (예: A라는 사용자가 메시지를 보내고 그 메시지에 대한 응답이 오기 전에 또 메시지를 보내는 경우)
      1. 의도 분류 (없을 시 LLM으로 분류)
      2. 의도에 맞는 실행 노드 라우팅
      3. Google Chat으로 응답 전송
      4. END

    예외 발생 시 사용자에게 오류 안내 메시지를 직접 전송합니다.
    """
    user_id = state.chat_context.get("user_id", "")
    lock = _get_lock(user_id)

    if lock.locked():
        # 이미 실행 중이면 메시지 드랍 or 안내
        from src.core.google_chat import send_message
        space_name = state.chat_context.get("space_name", "")
        await send_message(space_name, "⏳ 이전 요청을 처리 중입니다. 잠시 후 다시 시도해주세요.")
        return

    async with lock:
        session_id = state.session_id

        # 신규 세션이면 세션 문서 생성
        if is_new_session:
            await create_session(
                session_id=session_id,
                user_id=state.chat_context.get("user_id", ""),
                user_name=state.chat_context.get("user_name", ""),
                space_name=state.chat_context.get("space_name", ""),
            )

        # 매 턴마다 턴 시작
        await start_turn(session_id)

        try:
            await main_graph.ainvoke(state, config=config)
            # 체크포인터가 자동으로 저장.

        except Exception as e:
            print(f"[Orchestrator] 오류: {e}")
            await add_event(session_id, EventType.ERROR, "그래프 실행 오류", content=str(e))
            await _send_fallback_error(state)
            
        finally:
            # 턴 종료 + 세션 활동 시간 갱신
            await end_turn(session_id)
            await touch_session(session_id)


async def _send_fallback_error(state: GraphState) -> None:
    """그래프 실행이 완전히 실패했을 때 최후 수단으로 오류 메시지를 전송합니다."""
    from src.core.google_chat import send_message
    space_name = state.chat_context.get("space_name", "")
    if space_name:
        try:
            state.add_ai_message("죄송합니다. 시스템 오류가 발생했습니다. AI팀으로 알려주세요!")
            await send_message(
                space_name,
                "죄송합니다. 시스템 오류가 발생했습니다. AI팀으로 알려주세요!",
            )
        except Exception as e:
            print(f"[Orchestrator] Fallback 메시지 전송도 실패: {e}")