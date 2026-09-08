from pydantic import BaseModel

class ChatMessage(BaseModel):
    text: str
    user_id: str
    user_name: str
    space_id: str
    thread_id: str
    message_id: str
    user_email: str