"""PSCP wire format: 8-byte header + topic + payload, big-endian, UTF-8 text.

Pure functions only (no threads, no state) so broker, client, tests and
experiments all share exactly one encoder/decoder.
"""
import struct
from dataclasses import dataclass, replace

from .constants import (
    FLAG_DUP, FLAG_QOS, FLAG_SESSION, FLAGS_RESERVED, HEADER_FMT, HEADER_LEN,
    MAX_PAYLOAD_LEN, MAX_TOPIC_LEN, MAX_USERNAME_LEN, VERSION, MsgType, Presence, Status,
)


class ProtocolError(Exception):
    """The peer violated the protocol; `status` is the code to report in ERROR."""

    def __init__(self, status, detail=""):
        super().__init__(f"{Status(status).name}: {detail}" if detail else Status(status).name)
        self.status = Status(status)


class ConnectionClosed(Exception):
    """The peer closed the TCP connection (EOF)."""


@dataclass(frozen=True)
class Message:
    type: MsgType
    flags: int = 0
    msg_id: int = 0
    topic: str = ""
    payload: bytes = b""

    @property
    def qos(self):
        return self.flags & FLAG_QOS

    @property
    def dup(self):
        return bool(self.flags & FLAG_DUP)

    @property
    def session(self):
        return bool(self.flags & FLAG_SESSION)

    def with_dup(self):
        return replace(self, flags=self.flags | FLAG_DUP)

    def status(self):
        """Status byte carried by CONNACK/SUBACK/UNSUBACK/PUBACK/ERROR (empty payload = OK)."""
        return Status(self.payload[0]) if self.payload else Status.OK

    def describe(self):
        """One-line summary used in logs."""
        parts = [self.type.name]
        if self.topic:
            parts.append(f"topic={self.topic}")
        if self.msg_id:
            parts.append(f"id={self.msg_id}")
        if self.type in (MsgType.PUBLISH, MsgType.SUBSCRIBE):
            parts.append(f"qos={self.qos}")
        if self.dup:
            parts.append("dup=1")
        if self.type in (MsgType.CONNECT, MsgType.CONNACK) and self.session:
            parts.append("session=1")
        if self.type in (MsgType.CONNACK, MsgType.SUBACK, MsgType.UNSUBACK,
                         MsgType.PUBACK, MsgType.ERROR):
            try:
                parts.append(f"status={self.status().name}")
            except ValueError:
                parts.append(f"status=0x{self.payload[0]:02x}")
        parts.append(f"len={HEADER_LEN + len(self.topic.encode('utf-8')) + len(self.payload)}")
        return " ".join(parts)


def encode(msg):
    topic = msg.topic.encode("utf-8")
    if len(topic) > MAX_TOPIC_LEN:
        raise ValueError("topic too long")
    if len(msg.payload) > MAX_PAYLOAD_LEN:
        raise ValueError("payload too long")
    header = struct.pack(HEADER_FMT, VERSION, msg.type, msg.flags, len(topic),
                         msg.msg_id, len(msg.payload))
    return header + topic + msg.payload


def decode_header(data):
    """Validate an 8-byte header; return (type, flags, topic_len, msg_id, payload_len)."""
    version, mtype, flags, topic_len, msg_id, payload_len = struct.unpack(HEADER_FMT, data)
    if version != VERSION:
        raise ProtocolError(Status.BAD_VERSION, f"version {version}")
    try:
        mtype = MsgType(mtype)
    except ValueError:
        raise ProtocolError(Status.MALFORMED, f"unknown type 0x{mtype:02x}") from None
    if flags & FLAGS_RESERVED:
        raise ProtocolError(Status.MALFORMED, f"reserved flag bits set 0x{flags:02x}")
    return mtype, flags, topic_len, msg_id, payload_len


def decode(data):
    """Decode one complete message from bytes (used by tests and tools)."""
    if len(data) < HEADER_LEN:
        raise ProtocolError(Status.MALFORMED, "short header")
    mtype, flags, topic_len, msg_id, payload_len = decode_header(data[:HEADER_LEN])
    if len(data) != HEADER_LEN + topic_len + payload_len:
        raise ProtocolError(Status.MALFORMED, "length mismatch")
    return _build(mtype, flags, msg_id, data[HEADER_LEN:HEADER_LEN + topic_len],
                  data[HEADER_LEN + topic_len:])


def recv_exact(sock, n):
    """Read exactly n bytes; TCP is a byte stream so one recv() may return less."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionClosed("peer closed connection")
        buf += chunk
    return bytes(buf)


def read_message(sock):
    mtype, flags, topic_len, msg_id, payload_len = decode_header(recv_exact(sock, HEADER_LEN))
    body = recv_exact(sock, topic_len + payload_len) if topic_len + payload_len else b""
    return _build(mtype, flags, msg_id, body[:topic_len], body[topic_len:])


def _build(mtype, flags, msg_id, topic, payload):
    try:
        topic = topic.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(Status.MALFORMED, "topic is not UTF-8") from None
    return Message(mtype, flags, msg_id, topic, payload)


# ---- payload formats ------------------------------------------------------

def status_payload(status):
    return bytes([status])


def encode_username(name):
    data = name.encode("utf-8")
    if not 1 <= len(data) <= MAX_USERNAME_LEN or "\x00" in name:
        raise ValueError("username must be 1-32 bytes of UTF-8")
    return data


def decode_username(payload):
    try:
        name = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError(Status.MALFORMED, "username is not UTF-8") from None
    if not 1 <= len(payload) <= MAX_USERNAME_LEN or "\x00" in name:
        raise ProtocolError(Status.MALFORMED, "bad username")
    return name


def encode_delivery(sender, text):
    """Broker->client PUBLISH payload: SenderLen(1) | Sender | Text."""
    s = sender.encode("utf-8")
    return bytes([len(s)]) + s + text


def decode_delivery(payload):
    if not payload or len(payload) < 1 + payload[0]:
        raise ProtocolError(Status.MALFORMED, "bad delivery payload")
    n = payload[0]
    return payload[1:1 + n].decode("utf-8", "replace"), payload[1 + n:]


def encode_presence(event, username):
    """PRESENCE payload: Event(1) | Username."""
    return bytes([event]) + username.encode("utf-8")


def decode_presence(payload):
    if not payload:
        raise ProtocolError(Status.MALFORMED, "empty presence")
    return Presence(payload[0]), payload[1:].decode("utf-8", "replace")


def valid_room(topic):
    """Rooms users may publish to: 1-255 bytes and not starting with '$' (reserved)."""
    return bool(topic) and not topic.startswith("$")
