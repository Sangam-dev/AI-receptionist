import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_HISTORY = 20


@dataclass
class Session:
    history: list[dict] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_seen: float = 0.0


class SessionStore:
    def __init__(self, ttl_s: float = 1800.0) -> None:
        self._ttl_s = ttl_s
        self._sessions: dict[str, Session] = {}

    def _get(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = Session()
            self._sessions[session_id] = session
        return session

    def history(self, session_id: str) -> list[dict]:
        return list(self._get(session_id).history)

    def append(self, session_id: str, role: str, text: str) -> None:
        session = self._get(session_id)
        session.history.append({"role": role, "content": text})
        if len(session.history) > MAX_HISTORY:
            session.history = session.history[-MAX_HISTORY:]

    def pop_last(self, session_id: str) -> None:
        session = self._get(session_id)
        if session.history:
            session.history.pop()

    def touch(self, session_id: str) -> None:
        self._get(session_id).last_seen = time.monotonic()

    def active_sessions(self) -> int:
        now = time.monotonic()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if s.last_seen and now - s.last_seen > self._ttl_s
        ]
        for sid in expired:
            logger.debug("expiring session %s", sid)
            self._sessions.pop(sid, None)
        return len(self._sessions)

    async def run_with_lock(self, session_id: str, coro_fn):
        async with self._get(session_id).lock:
            return await coro_fn()
