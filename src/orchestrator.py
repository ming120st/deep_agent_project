from langchain_core.messages import AIMessage, HumanMessage, BaseMessage
from src.workflow.graph_infra.state import GraphState
from src.core.llm_config import llm_light, llm_heavy
from src.core.llm_calls import generate_response
from src.services.google_chat import send_response

# 텍스트 메시지에 대한 응답 생성 함수
async def orchestrate_chat_event(state: GraphState) -> GraphState:
    # state에서 user 메시지 추출
    user_query = next(
        (m["content"] for m in reversed(state.messages) if m["role"] == "user"), None
    )
    if user_query:
        instruction = "사용자 메시지에 대한 적절한 응답을 생성하세요. 필요한 경우 추가 정보를 요청할 수 있습니다."
        response = await generate_response(instruction, state)
        state.messages.append(AIMessage(content=response))

        send_response(state.chat_context["space_name"], response)

        state.print_state()