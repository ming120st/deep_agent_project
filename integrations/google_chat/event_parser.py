"""
Gogole Chat Parser
"""

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
        user_email=user.get("email","")
    )