import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SESSION_TIMEOUT = timedelta(hours=1)

@dataclass
class SessionInfo:
    session_id: str
    last_active_at: datetime


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, SessionInfo] = {}

    def get_or_create(
        self,
        user_id: str,
        space_id: str,
    ) -> str:
        key = f"{space_id}:{user_id}"
        now = datetime.now(timezone.utc)

        session = self._sessions.get(key)

        if session is None:
            session = SessionInfo(
                session_id=str(uuid.uuid4()),
                last_active_at=now,
            )
            self._sessions[key] = session
            return session.session_id

        if now - session.last_active_at > SESSION_TIMEOUT:
            session = SessionInfo(
                session_id=str(uuid.uuid4()),
                last_active_at=now,
            )
            self._sessions[key] = session
            return session.session_id

        session.last_active_at = now
        return session.session_id


session_manager = SessionManager()