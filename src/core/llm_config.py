import os
from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI

# 응답 생성, 의도분류 등 일반 작업용

LLM_LIGHT = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    temperature=0,
    request_timeout=60,
    max_retries=1,)

# LLM_LIGHT = ChatOpenAI(model="gpt-5.1", temperature=0)
# LLM_LIGHT = ChatOpenAI(model="gpt-5.2", temperature=0)

# 복잡한 추론, 에이전트 작업용
LLM_HEAVY = ChatGoogleGenerativeAI(
    model="gemini-3.1-pro-preview",
    temperature=0,
    request_timeout=60,
    max_retries=1,)

# model_execute = ChatOpenAI(model="gpt-4.1", temperature=0)

# 임베딩 모델
GEMINI_EMBEDDING_MODEL="gemini-embedding-2-preview"

if __name__ == "__main__":
    print(LLM_LIGHT.model)
# python src/core/llm_config.py 로 모델 로드 테스트 가능