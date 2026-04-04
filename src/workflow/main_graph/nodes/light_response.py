import time
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from src.core.llm_config import LLM_LIGHT
from src.workflow.graph_infra.state import GraphState
from src.core.session_logger import session_event, update_turn_meta, EventType, build_llm_meta

LIGHT_RESPONSE_SYSTEM_PROMPT = """당신은 와이드모바일 회사 내 다양한 업무를 지원하고 사용자들의 궁금증을 해결해주는 친절하고 유능한 업무 지원 AI 어시스턴트(Business Operations Agent)입니다.
- 사용자에게 친근하고 전문적인 태도로 답변하세요.
- 단순히 인사를 나누거나 일상적인 대화, 가벼운 질문 등에 자연스럽게 응답하세요.
- 업무 지시가 아닌 가벼운 대화(light_response)로 분류된 상황이므로 상황에 맞는 친절한 단답형 또는 짧은 안내를 제공하면 됩니다.
"""

light_response_prompt = ChatPromptTemplate.from_messages([
    ("system", LIGHT_RESPONSE_SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="messages")
])

light_response_chain = light_response_prompt | LLM_LIGHT # | StrOutputParser()는 usage_metadata 기록을 위해 체인에서 일단 제외하고 노드 내부에서 직접 파싱하도록 변경

async def execute_light_response(state: GraphState):
    """ActionResult가 light_response일 때 호출되어 가벼운 대화 응답을 생성합니다."""
    await update_turn_meta(state.session_id, intent="light_response")

    start_time = time.time()
    print(f"[Node] Start: execute_light_response")
    
    # 상태 객체의 messages를 LLM에 전달 가능한 형식으로 변환
    messages = [(m["role"], m["content"]) for m in state.messages]
    
    # LLM을 호출하여 응답 생성
    async with session_event(state.session_id, EventType.LLM_RESPONSE, "라이트 응답 생성") as ev:
        llm_response = await light_response_chain.ainvoke({"messages": messages})
        
        # Gemini가 content를 list로 반환하는 경우 처리
        raw = llm_response.content
        if isinstance(raw, list):
            response_content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in raw
            )
        else:
            response_content = str(raw)
        
        ev.content = response_content[:200]
        ev.llm = build_llm_meta(LLM_LIGHT.model, llm_response.usage_metadata)
    
    # 생성된 응답을 상태에 추가 (기존 리스트 복제 후 추가하여 덮어쓰기)
    new_messages = list(state.messages)
    new_messages.append({"role": "assistant", "content": response_content})
    
    end_time = time.time()
    print(f"[Node] End: execute_light_response | Duration: {end_time - start_time:.2f}s")
    
    return {"messages": new_messages}