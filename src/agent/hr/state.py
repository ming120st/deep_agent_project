from typing import Any, Optional

from pydantic import BaseModel, Field

from src.agent.base import TeamOutput


class HrAgentState(BaseModel):
    task: str
    user_message: str
    boa_intent: Optional[str] = None
    boa_extracted: Optional[dict[str, Any]] = None
    session_id: str
    thread_id: Optional[str] = None

    current_subagent: Optional[str] = None
    collected_parameters: dict[str, Any] = Field(default_factory=dict)
    internal_result: Optional[dict[str, Any]] = None

    output: Optional[TeamOutput] = None
