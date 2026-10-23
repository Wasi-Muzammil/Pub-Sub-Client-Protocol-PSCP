# PSCP v1: Publish/Subscribe Chat Protocol specification

This is the authoritative specification. The code in `pscp/` and Section 2 of the report must match it. It is written so that another group could build a compatible client or broker from it alone (requirement R1).

## 1. Overview

- A **broker** accepts TCP connections on port **5050**. **Clients** connect to it, join **rooms** (topics) and publish chat messages. The broker forwards each message to every subscriber of the room.
- All traffic travels over one TCP connection per client, which the client opens. The broker sends to the client over that same connection, so clients behind NAT need no port forwarding.
- **QoS 0** means at most once. **QoS 1** means at least once on each hop, and receivers remove duplicates, so the application sees each message once. QoS 1 applies on **both hops**: publisher → broker and broker → subscriber.
- If a connection is lost, the broker **keeps the session for 120 s**. A client that reconnects with RESUME receives every QoS 1 message it missed, in order.

## 2. Message layout

All integers are big-endian and all text is UTF-8. The fixed header is 8 bytes (Python `struct` format `"!BBBBHH"`).

```
 0        1        2        3        4                 6                 8
+--------+--------+--------+--------+--------+--------+--------+--------+---------------+----------------+
|Version |  Type  | Flags  |TopicLen|    Message ID   |  Payload Length |  Topic (0-255) | Payload (0-65535)
+--------+--------+--------+--------+--------+--------+--------+--------+---------------+----------------+
```

| Field | Size | Meaning |
| --- | --- | --- |
| Version | 1 B | `0x01`. Anything else is answered with ERROR `0x11` and the connection is closed. |
| Type | 1 B | Message type (§3). An unknown type → ERROR `0x12`, then close. |
| Flags | 1 B | bit 0 **QoS** (PUBLISH, SUBSCRIBE, SUBACK, and on deliveries). bit 1 **DUP** (this is a retransmission). bit 2 **SESSION**: on CONNECT it means "RESUME requested", on CONNACK it means "session present". Bits 3–7 are reserved and must be 0; if any is set → ERROR `0x12`, then close. |
| Topic Len | 1 B | Length of Topic in bytes. |
| Message ID | 2 B | Client → broker: an ID chosen by the client (1–65535, unique among that client's unacknowledged messages), echoed back in the reply. Broker → client PUBLISH/PRESENCE: the room's **sequence number** (1–65535, wraps 65535 → 1). 0 = no ID. |
| Payload Length | 2 B | Length of Payload in bytes. |

**Worked example (15 bytes):** a QoS 1 PUBLISH of "Hi" to "room1" with ID 1:
`01 07 01 05 00 01 00 02 | 72 6F 6F 6D 31 | 48 69`

## 3. Message types

| Code | Type | Direction | Topic | Message ID | Payload |
| --- | --- | --- | --- | --- | --- |
| `0x01` | CONNECT | C→B | empty | 0 | username (1–32 B UTF-8, no NUL) |
| `0x02` | CONNACK | B→C | empty | 0 | Status (1 B). SESSION flag = an existing session was resumed |
| `0x03` | SUBSCRIBE | C→B | room | client ID | empty; QoS flag = requested QoS |
| `0x04` | SUBACK | B→C | room | echoed | Status; QoS flag = granted QoS |
| `0x05` | UNSUBSCRIBE | C→B | room | client ID | empty |
| `0x06` | UNSUBACK | B→C | room | echoed | Status |
| `0x07` | PUBLISH | C→B | room | client ID (0 if QoS 0) | message text |
| `0x07` | PUBLISH | B→C | room | sequence number | SenderLen (1 B), Sender, Text |
| `0x08` | PUBACK | both | room | ID or sequence number being acknowledged | Status |
| `0x09` | PING | C→B | empty | 0 | empty |
| `0x0A` | PONG | B→C | empty | 0 | empty |
| `0x0B` | PRESENCE | B→C | `$presence` | sequence number (0 for a snapshot entry) | Event (1 B), username |
| `0x0C` | DISCONNECT | C→B | empty | 0 | empty |
| `0x0D` | ERROR | B→C | optional | optional | Status |

PUBACK carries the room name in **both** directions. A sequence number is unique only within its room, so the pair (room, sequence number) is needed to identify the message being acknowledged.

**Presence events:** `0x01` JOIN, `0x02` LEAVE (the user sent DISCONNECT), `0x03` TIMEOUT (the connection was lost without DISCONNECT: keep-alive expired, TCP reset, or retransmissions exhausted).

## 4. Status codes

| Code | Name | Used in |
| --- | --- | --- |
| `0x00` | OK | all replies |
| `0x10` | USERNAME_TAKEN | CONNACK: the username is in use by a live connection and RESUME was not set |
| `0x11` | BAD_VERSION | ERROR, then close |
| `0x12` | MALFORMED | ERROR, then close (bad header, bad UTF-8, a message type not allowed in that direction, a second CONNECT) |
| `0x13` | NOT_CONNECTED | ERROR, then close: the first message was not CONNECT |
| `0x20` | INVALID_TOPIC | SUBACK, PUBACK or ERROR: empty topic, or a `$` topic other than subscribing to `$presence` |
| `0x21` | NOT_SUBSCRIBED | PUBACK or ERROR: publishing to a room the sender has not joined. UNSUBACK: the client was not subscribed |
| `0x30` | SERVER_FULL | CONNACK: the broker's limit on live clients has been reached |

## 5. Legal order and actions on receipt

**Connection states (broker side):** `AWAIT_CONNECT → CONNECTED → CLOSED`.

1. The first message must be CONNECT. If any other message arrives first, the broker sends ERROR `0x13` and closes the connection. If nothing arrives within 1.5 K, the broker closes the connection without a reply.
2. **CONNECT(username, RESUME?)**
   - A session for this username exists and is **live**, and RESUME is not set → CONNACK `0x10`, then close.
   - A session exists (live or retained) and RESUME is set → the session is **taken over**: its old connection, if any, is closed silently. Reply CONNACK OK with SESSION = 1, then replay every unacknowledged QoS 1 message in sequence order with DUP = 1, then continue with queued messages.
   - Otherwise, any old retained session is discarded and a new session is created → CONNACK OK with SESSION = 0.
   - The broker publishes PRESENCE JOIN, except on a takeover of a live connection.
3. **SUBSCRIBE(room, QoS)**: the client is added to the room with the QoS it requested → SUBACK. Subscribing to `$presence` additionally queues one QoS 0 PRESENCE JOIN (ID 0) for each user currently online.
4. **PUBLISH from a client**
   - The room is invalid → status INVALID_TOPIC. The client has not joined the room → status NOT_SUBSCRIBED.
   - Otherwise, if QoS = 1 and the Message ID is in the sender's duplicate window, the message is **not forwarded**. If not, it is forwarded: under the room's lock the broker assigns the next sequence number and queues a copy for every subscriber. The copy's QoS is **min(publish QoS, subscription QoS)**.
   - Reply: for QoS 1, a PUBACK carrying the status. For QoS 0, nothing is sent if the status is OK; otherwise ERROR with the status, and the connection stays open.
5. **PUBLISH/PRESENCE to a client**: for QoS 1, the client always sends PUBACK(room, sequence number). It passes the message to the user only if (room, sequence number) is not in its duplicate window.
6. **PUBACK**: the matching entry is removed from the receiver's outbox.
7. **PING** → PONG. Any message received resets the keep-alive timer.
8. **DISCONNECT**: the broker discards the session, publishes PRESENCE LEAVE and closes the connection.

## 6. Reliability (QoS 1)

| Rule | Value |
| --- | --- |
| Retransmit | No PUBACK within **T = 2 s** → resend with DUP = 1, at most **3** times. If the message is still unacknowledged after that, the **link is considered dead**: the connection is closed and the message stays in the outbox so it can be replayed on resume. This rule is the same at the client and at the broker. |
| Duplicate window | The last **256** IDs: per session at the broker (client Message IDs), and per room at the client (sequence numbers). IDs drop out of the window long before a 16-bit ID wraps round. |
| Flow control | At most **32** unacknowledged deliveries per subscriber. Further messages wait in a queue of at most **1,000**. If that queue overflows, the session is discarded and PRESENCE TIMEOUT is published. |
| Session retention | After a connection is lost without DISCONNECT, the session (subscriptions, outbox, duplicate window, queued QoS 1 messages) is kept for **120 s**. QoS 0 messages are not stored for an offline client. |
| Client resume | The client reconnects with back-off (immediately, then after 1, 2, 4, 8 and 16 s) and sets RESUME. On CONNACK with SESSION = 1 it replays its own unacknowledged publishes with DUP = 1. On SESSION = 0 it clears its duplicate windows (the broker may have restarted, so sequence numbers may have reset), subscribes to its rooms again, and then replays. |
| Meaning of PUBACK | "The next hop has taken responsibility for the message." It does **not** mean the other user has read it. |

## 7. Timers

| Timer | Value |
| --- | --- |
| Keep-alive K | 30 s. The client sends PING every K. |
| Broker dead-client limit | 1.5 K = 45 s of silence → close the connection, retain the session, publish PRESENCE TIMEOUT |
| Client dead-broker limit | 1.5 K with no message from the broker → close the connection and reconnect with RESUME |
| Retransmit T | 2 s, at most 3 retries |
| Session expiry | 120 s |
| Housekeeping tick | 0.5 s (the resolution of the timers above) |

## 8. Transport

- Every message type uses **TCP**. The reasons are given in report Section 4 (R4).
- Both sides set **TCP_NODELAY**, so small chat messages are not delayed by Nagle's algorithm.
- TCP is a byte stream, so a receiver must read exactly 8 header bytes, then exactly TopicLen + PayloadLen more bytes (`recv_exact`).

## 9. Differences from MQTT v3.1.1 (for originality)

| PSCP | MQTT v3.1.1 |
| --- | --- |
| Fixed 8-byte header with explicit 16-bit lengths | Variable-length "remaining length" encoding |
| Broker → subscriber ID is a **per-room sequence number**, so ordering is visible to clients | Per-session packet identifier with no ordering meaning |
| Presence (JOIN/LEAVE/TIMEOUT) is built in, with a snapshot on subscribe | No presence; Last Will is the nearest equivalent |
| A single RESUME bit, with a fixed 120 s expiry | Clean Session flag; expiry set by the broker |
| PUBACK carries the topic and a status code | PUBACK carries only the packet ID |
| No QoS 2, no wildcards, no retained messages | Supports all three |
