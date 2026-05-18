from typing import Any, Literal, Optional

from pydantic import BaseModel


class TeamInput(BaseModel):
    """Request payload passed from BOA to a domain team subgraph."""

    user_message: str
    boa_intent: Optional[str] = None
    boa_extracted: Optional[dict[str, Any]] = None
    session_id: str
    thread_id: str


class TeamOutput(BaseModel):
    """Response payload returned from a domain team subgraph to BOA."""

    status: Literal[
        "completed",
        "needs_user_input",
        "waiting_async",
        "escalate",
        "error",
    ]
    response: Optional[str] = None
    interrupt_data: Optional[dict[str, Any]] = None
    async_job_id: Optional[str] = None
    escalation_context: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
