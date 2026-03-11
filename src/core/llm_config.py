from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

# 응답 생성, 의도분류 등 일반 작업용
llm_light = ChatGoogleGenerativeAI(
    model="gemini-3-flash-preview", 
    temperature=0,
    request_timeout=60,
    max_retries=2,)

# 복잡한 추론, 에이전트 작업용
llm_heavy = ChatGoogleGenerativeAI(
    model="gemini-3.1-pro-preview",
    temperature=0,
    request_timeout=60,
    max_retries=2,)

# model_execute = ChatOpenAI(model="gpt-4.1", temperature=0)