from __future__ import annotations

import threading
import uuid


class TurnRegistry:
    """Own active/cancelled turn state without coupling it to a transport."""

    def __init__(self):
        self._active_by_session: dict[str, str] = {}
        self._session_by_turn: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def begin(self, session_id: str, request_id: str, *, turn_id: str | None = None) -> str:
        del request_id
        with self._lock:
            if session_id in self._active_by_session:
                raise RuntimeError("session_busy")
            value = turn_id or str(uuid.uuid4())
            self._active_by_session[session_id] = value
            self._session_by_turn[value] = session_id
            self._cancelled.discard(value)
            return value

    def cancel(self, turn_id: str) -> bool:
        with self._lock:
            session_id = self._session_by_turn.pop(turn_id, None)
            if session_id is None:
                return False
            self._active_by_session.pop(session_id, None)
            self._cancelled.add(turn_id)
            return True

    def complete(self, turn_id: str) -> bool:
        with self._lock:
            session_id = self._session_by_turn.pop(turn_id, None)
            if session_id is None:
                return False
            self._active_by_session.pop(session_id, None)
            return True

    def is_active(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._session_by_turn

    def is_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._cancelled

