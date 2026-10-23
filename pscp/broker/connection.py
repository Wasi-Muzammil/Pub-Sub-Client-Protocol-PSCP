"""One TCP connection to the broker: a reader thread and a writer thread.

The reader handles every incoming message and sends the direct replies
(CONNACK, SUBACK, UNSUBACK, PUBACK, PONG, ERROR). The writer sends the
session's deliveries (PUBLISH/PRESENCE), at most INFLIGHT_MAX unacked at a time.
Both write under `send_lock`, so each message goes out whole.
"""
import random
import socket
import threading
import time

from ..constants import FLAG_QOS, FLAG_SESSION, PRESENCE_TOPIC, MsgType, Presence, Status
from ..wire import (
    ConnectionClosed, Message, ProtocolError, decode_username, encode, encode_delivery,
    encode_presence, read_message, status_payload, valid_room,
)


class Connection:
    def __init__(self, broker, sock, addr):
        self.broker = broker
        self.log = broker.log
        self.sock = sock
        self.name = f"{addr[0]}:{addr[1]}"
        self.session = None
        self.last_seen = time.monotonic()
        self.send_lock = threading.Lock()
        self.drop_reason = None
        self._closed = False

    def start(self):
        threading.Thread(target=self._reader, name=f"reader-{self.name}", daemon=True).start()

    # ---- output --------------------------------------------------------

    def send(self, msg):
        data = encode(msg)
        with self.send_lock:
            self.sock.sendall(data)
        self.log.info("SEND %s %s", self.name, msg.describe())

    def _reply(self, mtype, msg_id=0, topic="", status=Status.OK, flags=0):
        self.send(Message(mtype, flags, msg_id, topic, status_payload(status)))

    def drop(self, reason):
        """Close from another thread (timers, takeover, overflow). The reader then cleans up."""
        if self.drop_reason is None:
            self.drop_reason = reason
        self._shutdown()

    def _shutdown(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    # ---- input ---------------------------------------------------------

    def _reader(self):
        clean = False
        reason = "peer closed connection"
        try:
            # a connection must send CONNECT before the keep-alive limit
            self.sock.settimeout(self.broker.keepalive * self.broker.timeout_factor)
            first = self._recv()
            if first.type != MsgType.CONNECT:
                raise ProtocolError(Status.NOT_CONNECTED, f"first message was {first.type.name}")
            self.sock.settimeout(None)
            if not self._on_connect(first):
                reason = "connect rejected"
                return
            while True:
                msg = self._recv()
                if msg.type == MsgType.DISCONNECT:
                    clean, reason = True, "DISCONNECT"
                    return
                self._dispatch(msg)
        except ProtocolError as e:
            reason = f"protocol error {e}"
            try:
                self._reply(MsgType.ERROR, status=e.status)
            except OSError:
                pass
        except ConnectionClosed:
            pass
        except socket.timeout:
            reason = "no CONNECT received"
        except OSError as e:
            reason = f"socket error: {e}"
        finally:
            self._shutdown()
            self.sock.close()
            self.broker.connection_ended(self, clean, self.drop_reason or reason)

    def _recv(self):
        msg = read_message(self.sock)
        self.last_seen = time.monotonic()
        self.log.info("RECV %s %s", self.name, msg.describe())
        return msg

    def _on_connect(self, msg):
        username = decode_username(msg.payload)
        status, session, present, replaced = self.broker.registry.connect(username, msg.session, self)
        if status != Status.OK:
            self._reply(MsgType.CONNACK, status=status)
            return False
        self.session = session
        self.name = f"{username}@{self.name}"
        if replaced is not None:
            replaced.drop("taken over by a resumed connection")
        self._reply(MsgType.CONNACK, flags=FLAG_SESSION if present else 0)
        if present:
            self.log.info("SESSION %s resumed: replaying %d unacked, %d queued",
                          self.name, len(session.resend), len(session.pending))
        threading.Thread(target=self._writer, name=f"writer-{self.name}", daemon=True).start()
        if replaced is None:
            self.broker.presence(Presence.JOIN, username)
        return True

    def _dispatch(self, msg):
        t = msg.type
        if t == MsgType.PUBLISH:
            self._on_publish(msg)
        elif t == MsgType.PUBACK:
            if not self.session.ack(msg.topic, msg.msg_id):
                self.log.info("ACK  %s late/unknown PUBACK topic=%s id=%d", self.name, msg.topic, msg.msg_id)
        elif t == MsgType.SUBSCRIBE:
            self._on_subscribe(msg)
        elif t == MsgType.UNSUBSCRIBE:
            found = self.broker.rooms.unsubscribe(self.session, msg.topic)
            self._reply(MsgType.UNSUBACK, msg.msg_id, msg.topic,
                        Status.OK if found else Status.NOT_SUBSCRIBED)
        elif t == MsgType.PING:
            self.send(Message(MsgType.PONG))
        else:
            raise ProtocolError(Status.MALFORMED, f"unexpected {t.name} from client")

    def _on_subscribe(self, msg):
        topic = msg.topic
        if not (valid_room(topic) or topic == PRESENCE_TOPIC):
            self._reply(MsgType.SUBACK, msg.msg_id, topic, Status.INVALID_TOPIC)
            return
        self.broker.rooms.subscribe(self.session, topic, msg.qos)
        self._reply(MsgType.SUBACK, msg.msg_id, topic, flags=msg.qos & FLAG_QOS)
        if topic == PRESENCE_TOPIC:
            # snapshot of who is online, as QoS 0 JOIN events without a sequence number
            for s in self.broker.registry.live_sessions():
                self.session.enqueue(Message(MsgType.PRESENCE, 0, 0, PRESENCE_TOPIC,
                                             encode_presence(Presence.JOIN, s.username)))

    def _on_publish(self, msg):
        session = self.session
        if not valid_room(msg.topic):
            status = Status.INVALID_TOPIC
        elif msg.topic not in session.subscriptions:
            status = Status.NOT_SUBSCRIBED
        else:
            status = Status.OK
            if msg.qos and session.inbound.check_and_add(msg.msg_id):
                self.log.info("DUP  %s id=%d already accepted: re-ack, not forwarded", self.name, msg.msg_id)
            else:
                seq = self.broker.fanout(msg.topic, encode_delivery(session.username, msg.payload), msg.qos)
                self.log.debug("FWD  %s id=%d -> %s seq=%d", self.name, msg.msg_id, msg.topic, seq)
        if msg.qos:
            if self.broker.drop_ack and random.random() < self.broker.drop_ack:
                self.log.info("FAULT %s dropped PUBACK id=%d (--drop-ack)", self.name, msg.msg_id)
                return
            self._reply(MsgType.PUBACK, msg.msg_id, msg.topic, status)
        elif status != Status.OK:
            self._reply(MsgType.ERROR, msg.msg_id, msg.topic, status)

    # ---- writer --------------------------------------------------------

    def _writer(self):
        session = self.session
        try:
            while True:
                msg = session.next_to_send(self)
                if msg is None:
                    return
                self.send(msg)
        except OSError:
            self._shutdown()
