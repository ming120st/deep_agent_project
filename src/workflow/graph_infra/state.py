from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import uuid

class GraphState(BaseModel):
    user_id: str
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4())) # 세션 고유 ID. 같은 user_id라도 세션이 바뀌면 다르게 생성된다.
    created_time: Optional[int] = None
    last_activity_time: Optional[int] = None
    action: Dict[str, Any] = Field(default_factory=dict)
    messages: List[Dict[str, str]] = Field(default_factory=list)
    extra_context: Optional[str] = None  # 추가로 모델 응답 요청 시 전달할 컨텍스트 정보. 상품상담 에이전트 결과내용 등을 담아서 후속질문에 답할 수 있게 한다.
    response: Optional[Dict[str, Any]] = None  # 각 챗봇별 응답에 쓰일 항목들을 담는 딕셔너리. 필요한 파라미터를 key로 담아서 전달해서 꺼내쓴다.

    def add_ai_message(self, content: str):
        self.messages.append({"role": "assistant", "content": content})
    
    def add_user_message(self, content: str):
        self.messages.append({"role":"user", "content": content})