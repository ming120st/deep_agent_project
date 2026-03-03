"""
오케스트레이터 (Orchestrator)

FastAPI 백엔드에서 호출되는 진입점.
Google Chat 메시지를 받아 메인 대화 그래프를 실행합니다.
"""
from src.workflow.graph_infra.state import GraphState
from src.workflow.main_graph.graph import main_graph


async def orchestrate_chat_event(state: GraphState, config: dict) -> None:
    """
    메인 대화 그래프를 실행합니다.

    흐름:
      1. 의도 분류 (없을 시 LLM으로 분류)
      2. 의도에 맞는 실행 노드 라우팅
      3. Google Chat으로 응답 전송
      4. END

    예외 발생 시 사용자에게 오류 안내 메시지를 직접 전송합니다.
    """
    try:
        await main_graph.ainvoke(state, config=config)
        # 체크포인터가 자동으로 저장.

    except Exception as e:
        print(f"[Orchestrator] 오류: {e}")
        _send_fallback_error(state)


def _send_fallback_error(state: GraphState) -> None:
    """그래프 실행이 완전히 실패했을 때 최후 수단으로 오류 메시지를 전송합니다."""
    from src.services.google_chat import send_response

    space_name = state.chat_context.get("space_name", "")
    if space_name:
        try:
            send_response(
                space_name,
                "죄송합니다. 시스템 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            )
        except Exception as e:
            print(f"[Orchestrator] Fallback 메시지 전송도 실패: {e}")