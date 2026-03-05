from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import uuid
import json


class GraphState(BaseModel):

    # ── 채팅 컨텍스트 ─────────────────────────────────────────────────────────
    chat_context: Dict[str, Any] = Field(default_factory=dict)
    # user_id, user_name, space_name, thread_name, quoted_message, event_time 등

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    last_activity_time: Optional[int] = None

    # ── 메시지 히스토리 ───────────────────────────────────────────────────────
    messages: List[Dict[str, str]] = Field(default_factory=list)
    # [{"role": "user"|"assistant", "content": "..."}]

    extra_context: Optional[str] = None
    # 모델 응답 요청 시 추가로 전달할 컨텍스트 (에이전트 실행 결과 등)

    # ── 의도 분류 ─────────────────────────────────────────────────────────────

    action: Dict[str, Any] = Field(default_factory=dict)
    # action_classifier의 결과 (action, agent_name, parameters 등)

    # ── 에이전트 상태 ─────────────────────────────────────────────────────────
    current_agent: Optional[str] = None
    # 현재 활성화된 에이전트 이름 (멀티턴 대화 유지용)

    collected_parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # 에이전트별 수집된 파라미터 (예: {"wifi_usage_inquiry": {"imei_list": [...], "start_date": "..."}})

    current_sop: Optional[str] = None
    # 현재 실행 중인 에이전트의 SOP 내용

    current_experience: Optional[str] = None
    # 현재 실행 중인 에이전트의 관련 경험(메모리) 내용

    response: Dict[str, Any] = Field(default_factory=dict)
    # 에이전트 실행 결과 (usage_data 등)

    task_id: Optional[str] = None
    # 현재 진행 중인 작업 ID (있을 경우) - 작업 관리 시스템과 연동 시 사용

    task_status: Optional[str] = None
    # 작업 상태 (예: "waiting", "in_progress", "completed", "cancelled", "error") - 에이전트 작업 중 상태를 관리하는데 활용. graph분기 등에 사용 (선택 사항)
    # 에이전트 작업 중 상태를 관리하는데 활용. graph분기 등에 사용 (선택 사항)

    # ── 에러 처리 ─────────────────────────────────────────────────────────────
    error_message: Optional[str] = None

    # ── 헬퍼 메서드 ──────────────────────────────────────────────────────────
    def add_ai_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})

    def get_last_user_message(self) -> Optional[str]:
        return next(
            (m["content"] for m in reversed(self.messages) if m["role"] == "user"),
            None,
        )

    def get_recent_history_text(self, n: int = 10) -> str:
        """최근 n개 메시지를 텍스트로 반환합니다."""
        recent = self.messages[-n:] if len(self.messages) > n else self.messages
        return "\n".join(
            f"{'사용자' if m['role'] == 'user' else 'AI'}: {m['content']}"
            for m in recent
        )

    def print_state(self) -> None:
        state_dict = self.model_dump()
        state_json = json.dumps(state_dict, indent=4, ensure_ascii=False)
        print(f"[State]\n{state_json}")