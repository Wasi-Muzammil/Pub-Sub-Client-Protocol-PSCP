"""PSCP protocol constants. docs/PROTOCOL.md is the authoritative spec; keep both in sync."""
from enum import IntEnum

VERSION = 0x01
DEFAULT_PORT = 5050

HEADER_FMT = "!BBBBHH"  # version, type, flags, topic_len, msg_id, payload_len
HEADER_LEN = 8
MAX_TOPIC_LEN = 255
MAX_PAYLOAD_LEN = 65535
MAX_USERNAME_LEN = 32
MAX_MSG_ID = 0xFFFF


class MsgType(IntEnum):
    CONNECT = 0x01
    CONNACK = 0x02
    SUBSCRIBE = 0x03
    SUBACK = 0x04
    UNSUBSCRIBE = 0x05
    UNSUBACK = 0x06
    PUBLISH = 0x07
    PUBACK = 0x08
    PING = 0x09
    PONG = 0x0A
    PRESENCE = 0x0B
    DISCONNECT = 0x0C
    ERROR = 0x0D


class Status(IntEnum):
    OK = 0x00
    USERNAME_TAKEN = 0x10
    BAD_VERSION = 0x11
    MALFORMED = 0x12
    NOT_CONNECTED = 0x13
    INVALID_TOPIC = 0x20
    NOT_SUBSCRIBED = 0x21
    SERVER_FULL = 0x30


class Presence(IntEnum):
    JOIN = 0x01
    LEAVE = 0x02    # clean DISCONNECT
    TIMEOUT = 0x03  # connection lost without DISCONNECT (keep-alive, reset, retries exhausted)


# Flags byte
FLAG_QOS = 0x01      # bit 0: QoS 0/1 (PUBLISH, SUBSCRIBE)
FLAG_DUP = 0x02      # bit 1: retransmission (PUBLISH)
FLAG_SESSION = 0x04  # bit 2: CONNECT = RESUME requested, CONNACK = session present
FLAGS_RESERVED = 0xF8  # bits 3-7 must be 0

PRESENCE_TOPIC = "$presence"

# Timers (seconds) and limits
KEEPALIVE = 30.0
TIMEOUT_FACTOR = 1.5
RETRY_AFTER = 2.0
MAX_RETRIES = 3
SESSION_EXPIRY = 120.0
INFLIGHT_MAX = 32
QUEUE_MAX = 1000
DEDUP_WINDOW = 256
BACKOFF = (1, 2, 4, 8, 16)
TICK = 0.5  # housekeeping period
