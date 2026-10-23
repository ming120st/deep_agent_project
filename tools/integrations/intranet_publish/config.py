"""Settings and fixed rules for publishing intranet posts through the device_agent."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass(frozen=True)
class BoardSpec:
    name: str
    label: str
    allows_recipients: bool
    public: bool


# device_agent owns the board name -> URL mapping. BOA only ever sends one of these names.
BOARDS: dict[str, BoardSpec] = {
    "genai": BoardSpec("genai", "생성형AI게시판", allows_recipients=False, public=True),
    "work": BoardSpec("work", "업무게시판", allows_recipients=True, public=False),
}

JOB_ID_PREFIX = "intranet-"

# Reasons reported by the device_agent handler (result.reason / error_message).
RETRYABLE_REASONS = frozenset({"LOGIN_REQUIRED", "IMAGE_UPLOAD_FAILED", "SUBMIT_FAILED"})
# The post may exist on the board: a person has to look before anything is sent again.
MANUAL_CHECK_REASONS = frozenset({"SUBMIT_UNCERTAIN", "VERIFY_FAILED"})
# Input or configuration problems: retrying the same request cannot help.
FINAL_REASONS = frozenset(
    {
        "BODY_REJECTED",
        "PLACEHOLDER_UNRESOLVED",
        "EDITOR_NOT_FOUND",
        "INVALID_PARAMS",
        "RECIPIENT_NOT_FOUND",
        "RECIPIENT_AMBIGUOUS",
        "RECIPIENT_FAILED",
    }
)

MAX_RECIPIENTS = 20
MAX_RECIPIENT_CHARS = 30
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # intranet upload limit


@dataclass(frozen=True)
class PublishSettings:
    task_name: str = "post_intranet_board"
    blob_container: str = "wm-device-agent-ai"
    sas_ttl: timedelta = timedelta(hours=1)
    callback_timeout: timedelta = timedelta(minutes=10)
    # waits before automatic retry #1, #2 (only for RETRYABLE_REASONS)
    retry_delays_seconds: tuple[float, ...] = (30.0, 120.0)
    # attempts for the POST /device-job itself when the connection could not be made
    submit_attempts: int = 3
    submit_backoff_seconds: float = 2.0
    boards: dict[str, BoardSpec] = field(default_factory=lambda: dict(BOARDS))

    @classmethod
    def from_env(cls) -> "PublishSettings":
        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except ValueError:
                return default

        return cls(
            task_name=os.getenv("INTRANET_POST_DEVICE_TASK", cls.task_name),
            blob_container=os.getenv("INTRANET_POST_BLOB_CONTAINER", cls.blob_container),
            callback_timeout=timedelta(minutes=_float("INTRANET_POST_CALLBACK_TIMEOUT_MINUTES", 10)),
        )


def blob_connection_string() -> str:
    """BOA's .env uses AZURE_STORAGE_CONNECTION_STRING; AZURE_STORAGE_CONNECTION_STRING is accepted too."""
    return os.getenv("AZURE_STORAGE_CONNECTION_STRING") or os.getenv("AZURE_STORAGE_CONNECTION_STRING") or ""
