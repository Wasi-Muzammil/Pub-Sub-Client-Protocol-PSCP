"""Per-user state that outlives a single TCP connection (enables RESUME)."""
import threading
import time
from collections import deque

from ..constants import INFLIGHT_MAX, QUEUE_MAX
from ..reliability import DedupWindow, Outbox


class Session:
    def __init__(self, username):
        self.username = username
        self.subscriptions = {}       # topic -> granted QoS
        self.outbox = Outbox()        # (topic, seq) -> PUBLISH sent, awaiting client PUBACK
        self.inbound = DedupWindow()  # client msg_ids already accepted
        self.pending = deque()        # PUBLISH/PRESENCE not yet sent (FIFO keeps room order)
        self.resend = deque()         # retransmissions; written before `pending`
        self.cond = threading.Condition()
        self.conn = None              # live Connection, or None while retained
        self.expires_at = None        # set while retained

    @property
    def live(self):
        return self.conn is not None

    def enqueue(self, msg):
        """Queue a delivery. Returns False if the queue overflowed (caller drops the session).

        While retained (no connection) only QoS 1 messages are kept: QoS 0 is at-most-once.
        """
        with self.cond:
            if self.conn is None and msg.qos == 0:
                return True
            if len(self.pending) >= QUEUE_MAX:
                return False
            self.pending.append(msg)
            self.cond.notify_all()
            return True

    def next_to_send(self, conn):
        """Block until the writer of `conn` may send something; None when conn is detached."""
        with self.cond:
            while True:
                if self.conn is not conn:
                    return None
                if self.resend:
                    return self.resend.popleft()
                if self.pending and len(self.outbox) < INFLIGHT_MAX:
                    msg = self.pending.popleft()
                    if msg.qos:
                        self.outbox.add((msg.topic, msg.msg_id), msg)
                    return msg
                self.cond.wait()

    def ack(self, topic, seq):
        with self.cond:
            found = self.outbox.ack((topic, seq))
            self.cond.notify_all()
            return found

    def queue_resends(self, msgs):
        with self.cond:
            self.resend.extend(msgs)
            self.cond.notify_all()

    def attach(self, conn):
        """Bind a new connection; anything still unacked is replayed first, in order, DUP=1."""
        with self.cond:
            self.conn = conn
            self.expires_at = None
            self.resend = deque(self.outbox.restart())
            self.cond.notify_all()

    def detach(self, conn, expiry):
        """Unbind `conn` if it is still the current one. Returns True if it was."""
        with self.cond:
            if self.conn is not conn:
                return False
            self.conn = None
            self.expires_at = time.monotonic() + expiry
            self.resend.clear()
            # QoS 0 deliveries are not kept for an offline client
            self.pending = deque(m for m in self.pending if m.qos)
            self.cond.notify_all()
            return True
