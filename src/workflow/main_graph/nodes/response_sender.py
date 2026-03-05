"""
응답 전송 노드 (Response Sender Node)

모든 실행/처리 노드의 최종 단계.
state의 마지막 AI 메시지를 Google Chat으로 전송합니다.
"""
from src.workflow.graph_infra.state import GraphState
from src.core.google_chat import send_message
from src.workflow.graph_infra.state import GraphState


async def send_response(state: GraphState) -> GraphState:
    """
    [노드] Google Chat 응답 전송

    state.messages 에서 마지막 assistant 메시지를 꺼내
    chat_context.space_name 으로 전송합니다.
    """
    # 마지막 AI 메시지 추출
    last_ai_message = next(
        (m["content"] for m in reversed(state.messages) if m["role"] == "assistant"),
        None,
    )

    if not last_ai_message:
        print("[ResponseSender] 전송할 AI 메시지 없음 — 상태 확인 필요")
        state.print_state()
        return state

    space_name = state.chat_context.get("space_name", "")
    if not space_name:
        print("[ResponseSender] chat_context에 space_name 없음 — 전송 불가")
        return state

    try:
        await send_message(space_name, last_ai_message)
        preview = last_ai_message[:80] + ("..." if len(last_ai_message) > 80 else "")
        print(f"[ResponseSender] 전송 완료 → {space_name}")
        print(f"[ResponseSender] 내용 미리보기: {preview}")

    except Exception as e:
        print(f"[ResponseSender] Google Chat 전송 실패: {e}")

    # state 출력
    state.print_state()

    return state