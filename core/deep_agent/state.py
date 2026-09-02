from __future__ import annotations
from typing import Any, NotRequired
from deepagents import DeepAgentState

class MainDeepAgentState(DeepAgentState):

    session_id: NotRequired[str]
    current_input: NotRequired[str]
    user_id: NotRequired[str | None]
    context: NotRequired[dict[str, Any]]

    subagent_candidates: NotRequired[
        list[dict[str, Any]]
    ]