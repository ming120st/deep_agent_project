from fastapi import FastAPI, Request
from typing import Dict
import httpx
import json
import asyncio
from logger import logger

app = FastAPI()

# 카카오에 보낼 응답 포맷팅
def format_response_kakao(return_message: str, useCallback: bool = False, handover: bool = False):
    data = {
        "version": "2.0",
        "useCallback": useCallback,
        "template": {
            "outputs": []
        },
        "data": {
            "text": ""
        }
    }

    if handover:
        # 상담원 연결용 응답 포맷
        data["template"]["outputs"].append(
            {
                "textCard": {
                    "title": "상담직원 연결",
                    "description": "불편을 겪고 계시거나 챗봇이 도와드릴 수 없는 부분은 상담직원이 직접 도와드립니다.\n아래 버튼을 눌러 상담을 시작해주세요.",
                    "buttons": [
                        {
                            "action": "operator",
                            "label": "상담직원 연결"
                        }
                    ]
                }
            }
        )
    else:
        # 기본 simpleText 포맷
        data["template"]["outputs"].append(
            {
                "simpleText": {
                    "text": return_message
                }
            }
        )

    if useCallback:
        # 콜백 사용 시 quick response
        data["data"]["text"] = "바로 답변 드리겠습니다. 잠시만 기다려주세요!"

    return data


@app.post("/chat/message")
async def chat_message(request: Request) -> Dict:
    logger.info(f"{'-'*50}\nAI에이전트 메시지 인입\n{'-'*50}")
    data_from_kakao = await request.json()
    logger.info(f"카카오에서 받은 데이터: {data_from_kakao}")
    user_message = data_from_kakao["userRequest"]["utterance"]
    logger.info(f"사용자 메시지: {user_message}")
    format_response_kakao("안녕하세요! 와이드모바일 AI에이전트입니다. 어떤 일을 해볼까요?")
    return format_response_kakao("안녕하세요! 와이드모바일 AI에이전트입니다. 어떤 일을 해볼까요?")