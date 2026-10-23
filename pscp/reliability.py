"""QoS 1 machinery shared by broker and client.

Outbox      - sent-but-unacknowledged messages, retransmitted with DUP=1.
DedupWindow - recently seen message keys, so a retransmission is acked again
              but not delivered again.
"""
import threading
import time
from collections import OrderedDict, deque

from .constants import DEDUP_WINDOW, MAX_RETRIES, RETRY_AFTER


class Outbox:
    """Keys are msg_id (client side) or (topic, seq) (broker side); insertion order = send order."""

    def __init__(self, retry_after=RETRY_AFTER, max_retries=MAX_RETRIES):
        self.retry_after = retry_after
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._items = OrderedDict()  # key -> [msg, sent_at, retries]

    def __len__(self):
        with self._lock:
            return len(self._items)

    def __contains__(self, key):
        with self._lock:
            return key in self._items

    def add(self, key, msg, now=None):
        with self._lock:
            self._items[key] = [msg, time.monotonic() if now is None else now, 0]

    def ack(self, key):
        with self._lock:
            return self._items.pop(key, None) is not None

    def poll(self, now=None):
        """Return (messages to retransmit with DUP=1, link_dead).

        link_dead is True once an entry has already been retransmitted
        max_retries times and is due again: the peer is treated as unreachable.
        Entries are never dropped here; they survive for resume.
        """
        now = time.monotonic() if now is None else now
        resend = []
        with self._lock:
            for entry in self._items.values():
                if now - entry[1] < self.retry_after:
                    continue
                if entry[2] >= self.max_retries:
                    return resend, True
                entry[0] = entry[0].with_dup()
                entry[1] = now
                entry[2] += 1
                resend.append(entry[0])
        return resend, False

    def restart(self, now=None):
        """On resume: every pending message, in order, marked DUP, with retry counters reset."""
        now = time.monotonic() if now is None else now
        with self._lock:
            out = []
            for entry in self._items.values():
                entry[0] = entry[0].with_dup()
                entry[1] = now
                entry[2] = 0
                out.append(entry[0])
            return out

    def clear(self):
        with self._lock:
            self._items.clear()


class DedupWindow:
    """FIFO set of the last `size` keys. Old keys fall out long before a 16-bit ID wraps."""

    def __init__(self, size=DEDUP_WINDOW):
        self.size = size
        self._seen = set()
        self._order = deque()
        self._lock = threading.Lock()

    def check_and_add(self, key):
        """Return True if key was already seen (a duplicate); otherwise remember it."""
        with self._lock:
            if key in self._seen:
                return True
            self._seen.add(key)
            self._order.append(key)
            if len(self._order) > self.size:
                self._seen.discard(self._order.popleft())
            return False

    def clear(self):
        with self._lock:
            self._seen.clear()
            self._order.clear()
