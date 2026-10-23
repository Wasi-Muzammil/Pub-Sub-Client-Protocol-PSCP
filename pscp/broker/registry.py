"""username -> Session, deciding fresh / resumed / taken-over / rejected on CONNECT."""
import threading
import time

from ..constants import SESSION_EXPIRY, Status
from .session import Session


class Registry:
    def __init__(self, rooms, max_clients, session_expiry=SESSION_EXPIRY):
        self.rooms = rooms
        self.max_clients = max_clients
        self.session_expiry = session_expiry
        self._lock = threading.Lock()
        self._sessions = {}

    def connect(self, username, resume, conn):
        """Return (status, session, session_present, replaced_conn)."""
        with self._lock:
            s = self._sessions.get(username)
            if s is not None and s.live and not resume:
                return Status.USERNAME_TAKEN, None, False, None
            if s is not None and resume:
                # resume a retained session, or take over a connection the broker
                # has not yet noticed is dead (client reconnected first)
                old = s.conn
                s.attach(conn)
                return Status.OK, s, True, old
            if s is not None:
                self._discard(s)
            if self._live_count() >= self.max_clients:
                return Status.SERVER_FULL, None, False, None
            s = Session(username)
            self._sessions[username] = s
            s.attach(conn)
            return Status.OK, s, False, None

    def detach(self, session, conn):
        """Connection lost: keep the session for session_expiry seconds."""
        with self._lock:
            return session.detach(conn, self.session_expiry)

    def discard(self, session, conn=None):
        """Clean DISCONNECT or overflow: forget the session entirely."""
        with self._lock:
            if conn is not None and session.conn is not conn:
                return False
            self._discard(session)
            return True

    def _discard(self, session):
        session.detach(session.conn, 0)
        self.rooms.remove_session(session)
        if self._sessions.get(session.username) is session:
            del self._sessions[session.username]

    def expire(self, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            dead = [s for s in self._sessions.values()
                    if not s.live and s.expires_at is not None and s.expires_at <= now]
            for s in dead:
                self._discard(s)
            return dead

    def live_sessions(self):
        with self._lock:
            return [s for s in self._sessions.values() if s.live]

    def _live_count(self):
        return sum(1 for s in self._sessions.values() if s.live)
