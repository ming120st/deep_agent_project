from __future__ import annotations
from typing import Any, NotRequired
from typing_extensions import TypedDict

class SubAgentState(TypedDict):
    """
    모든 SubAgent가 공통으로 사용하는 LangGraph 상태.
    """

    session_id: str
    current_input: str

    user_id: NotRequired[str | None]
    messages: NotRequired[list[dict[str, Any]]]
    error_message: NotRequired[str | None]
