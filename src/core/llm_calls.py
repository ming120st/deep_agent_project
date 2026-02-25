from langchain_core.runnables import Runnable
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableMap
from langchain_core.runnables import RunnableLambda
from src.core.llm_config import llm_light, llm_heavy
from src.workflow.graph_infra.state import GraphState

# -----------------------------------------------------------------
# "system_role 없이" 프롬프트만 개별 작성 하여 응답 생성하는 모듈
# -----------------------------------------------------------------
simple_response_prompt = ChatPromptTemplate.from_messages(
    [
        {"role": "system", "content": "{instruction_prompt}"},
        {"role": "user", "content": "{userQuery}"},
    ]
)

# Runnable 파이프라인 구성
simple_response_chain: Runnable = RunnableMap({
    "instruction_prompt": lambda input: input["instruction_prompt"],
    "userQuery": lambda input: input["userQuery"]
}) | simple_response_prompt | llm_light | StrOutputParser()

# 응답 생성 함수
async def generate_response(instruction_prompt: str, state: GraphState) -> str:
    result = await simple_response_chain.ainvoke({
        "instruction_prompt": instruction_prompt,
        "userQuery": next(
            (m["content"] for m in reversed(state.messages) if m["role"] == "user"),
            "아직 대화가 없습니다."
        )
    })
    return result