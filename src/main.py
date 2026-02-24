import os
import json
import time  # 지연을 주기 위해 추가
from fastapi import FastAPI, Request, BackgroundTasks
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv
import uvicorn

load_dotenv()

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/chat.bot"]

key_path_or_content = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH")

if not key_path_or_content:
    raise KeyError("GOOGLE_SERVICE_ACCOUNT_KEY_PATH environment variable is not set.")

try:
    service_account_info = json.loads(key_path_or_content)
except json.JSONDecodeError as e:
    raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_KEY_PATH 환경변수가 유효한 JSON이 아닙니다: {e}")

credentials = service_account.Credentials.from_service_account_info(
    service_account_info,
    scopes=SCOPES
)

chat_service = build("chat", "v1", credentials=credentials)

# 1. thread_name 파라미터 추가
def send_real_response(space_name: str, user_message: str, thread_name: str = None):
    try:
        # 2. 첫 동기 응답이 구글 서버에 무사히 도착하고 화면에 렌더링되도록 1~2초 대기 (Race Condition 방지)
        # 일반 def 함수이므로 time.sleep을 써도 FastAPI 메인 스레드를 블로킹하지 않아!
        time.sleep(1.5) 

        real_response = f"echo: {user_message}"
        body = {"text": real_response}
        
        # 3. 스레드 정보가 있으면 유저가 말 건 스레드에 그대로 답장하도록 설정
        if thread_name:
            body["thread"] = {"name": thread_name}

        chat_service.spaces().messages().create(
            parent=space_name,
            body=body
        ).execute()
        print(f"Successfully sent response to {space_name}")
    except Exception as e:
        print(f"Error sending response: {e}")

@app.post("/chat")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    event = await request.json()
    print(f"Received event: {json.dumps(event)}")

    if event.get("type") in ("ADDED_TO_SPACE", "REMOVED_FROM_SPACE"):
        return {}

    chat_payload = event.get("chat", {})
    message_payload = chat_payload.get("messagePayload", {})

    message_text = (
        event.get("message", {}).get("text") or
        message_payload.get("message", {}).get("text") or
        message_payload.get("argumentText") or
        ""
    )
    
    space_name = (
        event.get("space", {}).get("name") or
        event.get("message", {}).get("space", {}).get("name") or
        message_payload.get("space", {}).get("name") or
        message_payload.get("message", {}).get("space", {}).get("name") or
        ""
    )

    # 4. 스레드(Thread) 이름 추출 추가
    thread_name = event.get("message", {}).get("thread", {}).get("name")

    if not space_name:
        print("Missing space name in event")
        return {"text": "오류: 공간 정보를 찾을 수 없습니다."}

    # 백그라운드 태스크에 thread_name도 같이 전달
    background_tasks.add_task(send_real_response, space_name, message_text, thread_name)

    return {
        "text": "답변을 생성하고 있습니다. 잠시만 기다려주세요..."
    }

if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8080, reload=True)