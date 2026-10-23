"""PSCPClient: the one client implementation, used by the chat CLI and by experiments/measure.py.

Threads: a receiver (reads the socket, sends PUBACKs) and a timer
(PING every K, broker-silence detection, QoS 1 retransmission). After a lost
link it reconnects with back-off and CONNECT+RESUME, then replays its unacked
publishes with DUP=1.
"""
import random
import socket
import threading
import time

from ..constants import (
    BACKOFF, DEFAULT_PORT, FLAG_SESSION, KEEPALIVE, MAX_MSG_ID, PRESENCE_TOPIC, TICK,
    TIMEOUT_FACTOR, MsgType, Status,
)
from ..log import setup
from ..reliability import DedupWindow, Outbox
from ..wire import (
    ConnectionClosed, Message, ProtocolError, decode_delivery, decode_presence, encode,
    encode_username, read_message, status_payload,
)
from .connection import open_with_backoff


class ConnectError(Exception):
    def __init__(self, status):
        super().__init__(f"CONNACK {Status(status).name}")
        self.status = Status(status)


class NotConnected(Exception):
    pass


class PSCPClient:
    def __init__(self, host, username, port=DEFAULT_PORT, *, qos=1, keepalive=KEEPALIVE,
                 drop_ack=0.0, reconnect=True, backoff=BACKOFF, logger=None,
                 on_message=None, on_presence=None, on_status=None):
        self.host, self.port, self.username = host, port, username
        self.qos = qos
        self.keepalive = keepalive
        self.drop_ack = drop_ack
        self.reconnect = reconnect
        self.backoff = backoff
        self.log = logger or setup("client")
        self.on_message = on_message or (lambda room, sender, text, seq: None)
        self.on_presence = on_presence or (lambda event, user: None)
        self.on_status = on_status or (lambda text: None)

        self.subscriptions = {}  # room -> QoS, re-sent after a non-resumed reconnect
        self.connected = threading.Event()
        self._sock = None
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._outbox = Outbox()
        self._dedup = {}  # topic -> DedupWindow over broker sequence numbers
        self._waiters = {}  # msg_id -> [Event, reply Message]
        self._pong = threading.Event()
        self._ping_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._last_id = 0
        self._last_rx = self._last_tx = time.monotonic()
        self._closing = threading.Event()
        self._timer = None

    # ---- public API ----------------------------------------------------

    def connect(self, resume=False):
        """Connect (with back-off) and start background threads. Raises ConnectError/ConnectionError."""
        self._open(resume)
        if self._timer is None:
            self._timer = threading.Thread(target=self._timer_loop, name="client-timer", daemon=True)
            self._timer.start()

    def subscribe(self, room, qos=None, timeout=10.0):
        qos = self.qos if qos is None else qos
        reply = self._request(Message(MsgType.SUBSCRIBE, qos, self._alloc_id(), room), timeout)
        if reply.status() == Status.OK:
            self.subscriptions[room] = qos
        return reply.status()

    def unsubscribe(self, room, timeout=10.0):
        reply = self._request(Message(MsgType.UNSUBSCRIBE, 0, self._alloc_id(), room), timeout)
        self.subscriptions.pop(room, None)
        return reply.status()

    def publish(self, room, text, qos=None, wait=False, timeout=10.0):
        """QoS 1: kept in the outbox until PUBACK, even across reconnects.

        Returns the msg_id, or the PUBACK status if wait=True.
        """
        qos = self.qos if qos is None else qos
        payload = text.encode("utf-8") if isinstance(text, str) else text
        msg_id = self._alloc_id() if qos else 0
        msg = Message(MsgType.PUBLISH, qos, msg_id, room, payload)
        if not qos:
            self._send(msg)
            return 0
        waiter = self._add_waiter(msg_id) if wait else None
        self._outbox.add(msg_id, msg)
        try:
            self._send(msg)
        except (OSError, NotConnected):
            pass  # stays in the outbox; replayed after reconnect
        if waiter is None:
            return msg_id
        return self._wait(msg_id, waiter, timeout).status()

    def ping(self, timeout=10.0):
        """Round-trip time of one PING/PONG in seconds."""
        with self._ping_lock:
            self._pong.clear()
            t0 = time.perf_counter()
            self._send(Message(MsgType.PING))
            if not self._pong.wait(timeout):
                raise TimeoutError("no PONG")
            return time.perf_counter() - t0

    def pending_count(self):
        return len(self._outbox)

    def close(self):
        """Clean DISCONNECT: the broker discards the session and announces LEAVE."""
        self._closing.set()
        sock = self._sock
        try:
            self._send(Message(MsgType.DISCONNECT))
            # half-close (FIN) and let the broker close first: a full shutdown with
            # unread data sends RST, which can make the broker discard the DISCONNECT
            sock.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + 2.0
            while self.connected.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
        except (OSError, NotConnected, AttributeError):
            pass
        self._drop_link()

    # ---- connection management ----------------------------------------

    def _open(self, resume):
        while True:
            sock = open_with_backoff(self.host, self.port, self.log, self.backoff, stop=self._closing)
            try:
                flags = FLAG_SESSION if resume else 0
                sock.sendall(encode(Message(MsgType.CONNECT, flags, 0, "", encode_username(self.username))))
                sock.settimeout(10.0)
                reply = read_message(sock)
                sock.settimeout(None)
            except (OSError, ConnectionClosed, ProtocolError) as e:
                sock.close()
                if self._closing.is_set():
                    raise ConnectionError("client closing") from e
                self.log.warning("CONNECT handshake failed: %s", e)
                continue
            if reply.type != MsgType.CONNACK or reply.status() != Status.OK:
                sock.close()
                status = reply.status() if reply.type in (MsgType.CONNACK, MsgType.ERROR) else Status.MALFORMED
                raise ConnectError(status)
            break

        present = reply.session
        self.log.info("CONNACK OK session_present=%d", present)
        with self._state_lock:
            self._sock = sock
            self._last_rx = self._last_tx = time.monotonic()
        threading.Thread(target=self._receiver, args=(sock,), name="client-recv", daemon=True).start()
        self.connected.set()

        if not present:
            # new session on the broker: its room sequence numbers may have restarted
            for d in self._dedup.values():
                d.clear()
            for room, qos in list(self.subscriptions.items()):
                self.subscribe(room, qos)
        replay = self._outbox.restart()
        if replay:
            self.log.info("REPLAY %d unacked publish(es) with DUP=1", len(replay))
        for msg in replay:
            self._send(msg)
        self.on_status("connected" + (" (session resumed)" if present else ""))

    def _drop_link(self, reason=None):
        sock = self._sock
        if sock is None:
            return
        if reason:
            self.log.warning("DROP link: %s", reason)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _link_lost(self, sock, reason):
        with self._state_lock:
            if self._sock is not sock:
                return
            self._sock = None
            self.connected.clear()
        if self._closing.is_set():
            return
        self.log.warning("LOST connection: %s", reason)
        self.on_status(f"connection lost ({reason})")
        if self.reconnect:
            threading.Thread(target=self._reconnect, name="client-reconnect", daemon=True).start()

    def _reconnect(self):
        try:
            self._open(resume=True)
        except (ConnectionError, ConnectError) as e:
            self.log.error("GIVE UP reconnecting: %s", e)
            self.on_status(f"gave up reconnecting: {e}")

    # ---- I/O -----------------------------------------------------------

    def _send(self, msg):
        sock = self._sock
        if sock is None:
            raise NotConnected("not connected")
        with self._send_lock:
            sock.sendall(encode(msg))
            self._last_tx = time.monotonic()
        self.log.info("SEND %s", msg.describe())

    def _receiver(self, sock):
        reason = "broker closed connection"
        try:
            while True:
                msg = read_message(sock)
                self._last_rx = time.monotonic()
                self.log.info("RECV %s", msg.describe())
                self._handle(msg)
        except ConnectionClosed:
            pass
        except ProtocolError as e:
            reason = f"protocol error {e}"
        except OSError as e:
            reason = f"socket error: {e}"
        finally:
            sock.close()
            self._link_lost(sock, reason)

    def _handle(self, msg):
        t = msg.type
        if t in (MsgType.PUBLISH, MsgType.PRESENCE):
            if msg.qos:
                self._ack_delivery(msg)
                window = self._dedup.setdefault(msg.topic, DedupWindow())
                if window.check_and_add(msg.msg_id):
                    self.log.info("DUP  topic=%s seq=%d already delivered: re-acked, not shown",
                                  msg.topic, msg.msg_id)
                    return
            if t == MsgType.PUBLISH:
                sender, text = decode_delivery(msg.payload)
                self.on_message(msg.topic, sender, text.decode("utf-8", "replace"), msg.msg_id)
            else:
                event, user = decode_presence(msg.payload)
                self.on_presence(event, user)
        elif t == MsgType.PUBACK:
            self._outbox.ack(msg.msg_id)
            if msg.status() != Status.OK:
                self.on_status(f"publish to {msg.topic} rejected: {msg.status().name}")
            self._resolve(msg)
        elif t in (MsgType.SUBACK, MsgType.UNSUBACK):
            self._resolve(msg)
        elif t == MsgType.PONG:
            self._pong.set()
        elif t == MsgType.ERROR:
            self.on_status(f"broker error {msg.status().name}" + (f" ({msg.topic})" if msg.topic else ""))
        else:
            raise ProtocolError(Status.MALFORMED, f"unexpected {t.name} from broker")

    def _ack_delivery(self, msg):
        if self.drop_ack and random.random() < self.drop_ack:
            self.log.info("FAULT dropped PUBACK topic=%s seq=%d (--drop-ack)", msg.topic, msg.msg_id)
            return
        try:
            self._send(Message(MsgType.PUBACK, 0, msg.msg_id, msg.topic, status_payload(Status.OK)))
        except (OSError, NotConnected):
            pass  # broker will retransmit or replay on resume

    def _timer_loop(self):
        limit = self.keepalive * TIMEOUT_FACTOR
        last_ping = time.monotonic()
        while not self._closing.wait(TICK):
            if not self.connected.is_set():
                continue
            now = time.monotonic()
            try:
                if now - last_ping >= self.keepalive:
                    last_ping = now
                    self._send(Message(MsgType.PING))
                if now - self._last_rx > limit:
                    self._drop_link(f"broker silent for {limit:g}s")
                    continue
                resend, dead = self._outbox.poll(now)
                if dead:
                    self._drop_link("no PUBACK after max retransmissions")
                for msg in resend:
                    self.log.info("RETX id=%d with DUP=1", msg.msg_id)
                    self._send(msg)
            except (OSError, NotConnected):
                pass

    # ---- request/reply helpers ----------------------------------------

    def _alloc_id(self):
        with self._id_lock:
            for _ in range(MAX_MSG_ID):
                self._last_id = self._last_id % MAX_MSG_ID + 1
                if self._last_id not in self._outbox and self._last_id not in self._waiters:
                    return self._last_id
        raise RuntimeError("no free message IDs")

    def _add_waiter(self, msg_id):
        waiter = [threading.Event(), None]
        self._waiters[msg_id] = waiter
        return waiter

    def _request(self, msg, timeout):
        waiter = self._add_waiter(msg.msg_id)
        try:
            self._send(msg)
        except BaseException:
            self._waiters.pop(msg.msg_id, None)
            raise
        return self._wait(msg.msg_id, waiter, timeout)

    def _wait(self, msg_id, waiter, timeout):
        try:
            if not waiter[0].wait(timeout):
                raise TimeoutError(f"no reply to message id {msg_id}")
            return waiter[1]
        finally:
            self._waiters.pop(msg_id, None)

    def _resolve(self, reply):
        waiter = self._waiters.get(reply.msg_id)
        if waiter is not None:
            waiter[1] = reply
            waiter[0].set()
