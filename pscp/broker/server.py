"""Broker: listening socket, accept loop, housekeeping timers, presence."""
import os
import socket
import threading
import time

from ..constants import (
    DEFAULT_PORT, KEEPALIVE, PRESENCE_TOPIC, SESSION_EXPIRY, TICK, TIMEOUT_FACTOR, MsgType, Presence,
)
from ..log import setup
from ..wire import encode_presence
from .connection import Connection
from .registry import Registry
from .rooms import Rooms


class Broker:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT, max_clients=100, drop_ack=0.0,
                 keepalive=KEEPALIVE, timeout_factor=TIMEOUT_FACTOR, session_expiry=SESSION_EXPIRY,
                 logger=None):
        self.host = host
        self.port = port
        self.drop_ack = drop_ack
        self.keepalive = keepalive
        self.timeout_factor = timeout_factor
        self.log = logger or setup("broker")
        self.rooms = Rooms()
        self.registry = Registry(self.rooms, max_clients, session_expiry)
        self._stop = threading.Event()
        self._listener = None

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        """Bind and start background threads. Returns the bound port (useful with port=0)."""
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":  # on Windows SO_REUSEADDR allows port hijacking
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((self.host, self.port))
        self._listener.listen()
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._accept_loop, name="accept", daemon=True).start()
        threading.Thread(target=self._timers, name="timers", daemon=True).start()
        self.log.info("START listening on %s:%d (keepalive=%gs, drop_ack=%g)",
                      self.host, self.port, self.keepalive, self.drop_ack)
        return self.port

    def serve_forever(self):
        while not self._stop.wait(0.5):  # short waits keep Ctrl+C responsive on Windows
            pass

    def stop(self):
        self._stop.set()
        if self._listener:
            self._listener.close()
        for s in self.registry.live_sessions():
            if s.conn:
                s.conn.drop("broker shutting down")
        self.log.info("STOP")

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._listener.accept()
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.log.info("ACCEPT %s:%d", *addr)
            Connection(self, sock, addr).start()

    # ---- housekeeping --------------------------------------------------

    def _timers(self):
        limit = self.keepalive * self.timeout_factor
        while not self._stop.wait(TICK):
            now = time.monotonic()
            for s in self.registry.live_sessions():
                conn = s.conn
                if conn is None:
                    continue
                if now - conn.last_seen > limit:
                    conn.drop(f"keep-alive timeout ({limit:g}s silent)")
                    continue
                resend, dead = s.outbox.poll(now)
                if dead:
                    conn.drop("no PUBACK after max retransmissions")
                elif resend:
                    self.log.info("RETX %s %d message(s) with DUP=1", conn.name, len(resend))
                    s.queue_resends(resend)
            for s in self.registry.expire(now):
                self.log.info("EXPIRE session %s discarded (%d unacked lost)", s.username, len(s.outbox))

    def connection_ended(self, conn, clean, reason):
        self.log.info("CLOSE %s (%s)", conn.name, reason)
        s = conn.session
        if s is None:
            return
        if clean:
            if self.registry.discard(s, conn):
                self.presence(Presence.LEAVE, s.username)
        elif self.registry.detach(s, conn):
            self.log.info("SESSION %s retained for %gs (%d unacked, %d queued)",
                          s.username, self.registry.session_expiry, len(s.outbox), len(s.pending))
            self.presence(Presence.TIMEOUT, s.username)

    # ---- fan-out -------------------------------------------------------

    def fanout(self, topic, payload, qos, msg_type=MsgType.PUBLISH):
        seq, overflowed = self.rooms.publish(topic, payload, qos, msg_type)
        for s in overflowed:
            conn = s.conn
            if self.registry.discard(s):
                self.log.info("OVERFLOW %s queue full: session dropped", s.username)
                if conn:
                    conn.drop("outbound queue overflow")
                self.presence(Presence.TIMEOUT, s.username)
        return seq

    def presence(self, event, username):
        self.fanout(PRESENCE_TOPIC, encode_presence(event, username), 1, MsgType.PRESENCE)
