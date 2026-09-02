from __future__ import annotations

from typing import Any, NotRequired

from core.state.subagent import SubAgentState


class WifiLineChangeState(SubAgentState):
    """
    Wifi Line Change LangGraph 내부에서만 사용하는 상태.
    """
    parameters: NotRequired[dict[str, Any]]
    pending_parameter: NotRequired[str | None]
    context: NotRequired[dict[str, Any]]
    response: NotRequired[dict[str, Any]]