# 구글챗 응답을 필요한 값만 추출하도록 만든 parser
# 채팅 인터페이스 확장성 고려하여 만듦

from models.chat_message import ChatMessage

def parse_google_chat_event(event: dict) -> ChatMessage:
    message = event.get("message", {})
    user = event.get("user", {})
    space = event.get("space", {})

    return ChatMessage(
        text=message.get("text", ""),
        user_id=user.get("name", ""),
        user_name=user.get("displayName", ""),
        space_id=space.get("name", ""),
        thread_id=message.get("thread", {}).get("name", ""),
        message_id=message.get("name", ""),
    )