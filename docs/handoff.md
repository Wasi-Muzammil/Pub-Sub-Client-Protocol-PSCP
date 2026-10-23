# Handoff: CS-327 CCN CEP, PSCP Multi-Client Chat

_Last updated: 23 October 2026 (rev. 2: QoS 1 on both hops, session resume, cloud broker, code written)_

## 1. Project at a glance

| Item | Status |
| --- | --- |
| Course | CS-327 Computer Communication Networks, Complex Engineering Problem (Fall 2026) |
| Project title | Design, Implementation and Evaluation of a Multi-Host Networked Application |
| Option chosen | **D: Multi-client chat with presence** |
| Our protocol | **PSCP** (Publish/Subscribe Chat Protocol). Inspired by MQTT but our own design |
| Language | Python 3.8+, raw `socket` API, standard library only (`matplotlib` only for the graphs) |
| Group | 2 members so far (names and roll numbers are still placeholders) |
| Proposal | **Done**: one-page version, LaTeX + editable doc |
| Final report | **Template done**. Measurements, screenshots and results are placeholders. **Section 2 must be updated to rev. 2** (see §4) |
| Code | **Done and tested locally.** Broker, client, CLI, experiments; 25 tests pass |
| Deployment / measurements | **Not started.** The scripts are ready (`deploy/`, `experiments/`) |

## 2. What the brief requires (summary)

**Mandatory requirements**

| # | Requirement | Key condition |
| --- | --- | --- |
| R1 | Own protocol specification | Message types, every header field, legal order, actions on receipt, error codes. Another group must be able to implement it → `docs/PROTOCOL.md` |
| R2 | Implement on sockets directly | No library that implements the protocol |
| R3 | ≥ 3 independent hosts | Each with its own IP. **127.0.0.1 on one laptop = incomplete.** Include `ipconfig`/`ip addr` output and a network diagram |
| R4 | TCP/UDP justification | Per message type, against reliability, throughput, timing and security. Say what the opposite choice would lose |
| R5 | Wireshark capture | Annotated packet: frame header, IP header (IPs), TCP header (ports), PSCP payload. Relate it to encapsulation |
| R6 | Performance | RTT, throughput, the time of one operation. ≥ 5 repeats (mean + spread), ≥ 2 conditions, table + graph. Explain with the nodal delay model |
| R7 | Failure behaviour | ≥ 1 abnormal case: the designed behaviour plus log/capture evidence |

**Also required:**
- At least 3 design tensions, each defended with our own measurements.
- A sustainability paragraph: bytes saved → %, projected to many users, energy.
- An individual contributions table signed by all members.
- An AI-use declaration appendix.

**Report rules:**
- At most 10 pages, not counting the rubric title page, references and code appendix.
- Times New Roman, 12 pt body and 14 pt headings.
- The provided rubric goes on top as the title page.
- Hand in a hard copy, and every member uploads the PDF, named `CS24XXX.pdf`, to Google Classroom.

**Deadlines:**
- Proposal: end of Week 09, in person, all members.
- Final submission + live demo: **Week 13**. All members must be present, and each must be able to explain any part.
- Exact dates to be announced.

## 3. Decisions made

- **Option D**, framed as a custom publish/subscribe protocol: chat rooms are topics, the broker relays messages, presence (join/leave) is built in, and the broker assigns the ordering.
- **MQTT is a reference, not a copy.** `docs/PROTOCOL.md` §9 lists the deliberate differences from MQTT v3.1.1, to show originality (CPA-1).
- **Every message type uses TCP.** UDP for keep-alive pings was considered and rejected for four reasons: it needs a second socket, UDP pings must be matched to TCP sessions, lost datagrams cause false timeouts, and a TCP ping also proves the chat connection itself is alive. New argument: clients open the connection and the broker replies over it, so clients behind NAT or a hotspot need no port forwarding.
- **New (rev. 2): QoS 1 applies on both hops** (publisher → broker and broker → subscriber), so the chat is reliable. Duplicates are removed with Message IDs: the client's ID at the broker, and (room, sequence number) at the client.
- **New (rev. 2): session retention + RESUME.** TCP already loses nothing while a connection is up; messages are really lost when the connection breaks. So the broker keeps a disconnected client's session for 120 s and replays its unacknowledged messages when it reconnects with RESUME.
- **New (rev. 2): the broker runs on a cloud VM** (Host 1), and the two clients are laptops or a VM (Hosts 2 and 3). An optional comparison condition is a broker on a LAN laptop.
- **TCP_NODELAY** is set on both sides, so small messages are not held back by Nagle's algorithm (this matters for the latency results).
- **Report in LaTeX** (pdfLaTeX / Overleaf).

## 4. Protocol (rev. 2)

**The full specification is `docs/PROTOCOL.md`.** It is authoritative; copy it into report §2. Below is a summary with the **changes from the 22 Oct draft** marked.

**Message layout** (unchanged):
- An 8-byte fixed header, then Topic (0–255 B), then Payload (0–65,535 B).
- Big-endian integers, UTF-8 text. The broker listens on **TCP port 5050**. `struct` format: `"!BBBBHH"`.

| Field | Size | Meaning |
| --- | --- | --- |
| Version | 1 B | `0x01` |
| Type | 1 B | Message type code |
| Flags | 1 B | bit 0 = QoS, bit 1 = DUP, **bit 2 = SESSION (new: CONNECT means RESUME requested, CONNACK means session present)**, bits 3–7 reserved (0) |
| Topic Len | 1 B | Length of the topic |
| Message ID | 2 B | Client→broker: an ID echoed in the reply. Broker→client PUBLISH/PRESENCE: the per-room sequence number (1–65535, wraps; 0 = none) |
| Payload Length | 2 B | Length of the payload |

**Message types** (unchanged codes):
`0x01` CONNECT, `0x02` CONNACK, `0x03` SUBSCRIBE, `0x04` SUBACK, `0x05` UNSUBSCRIBE, `0x06` UNSUBACK, `0x07` PUBLISH, `0x08` PUBACK, `0x09` PING, `0x0A` PONG, `0x0B` PRESENCE, `0x0C` DISCONNECT, `0x0D` ERROR.

**Status codes** (unchanged):
`0x00` OK, `0x10` USERNAME_TAKEN, `0x11` BAD_VERSION, `0x12` MALFORMED, `0x13` NOT_CONNECTED, `0x20` INVALID_TOPIC, `0x21` NOT_SUBSCRIBED, `0x30` SERVER_FULL.

**Payload formats (new, now fixed):**
- CONNECT carries the username (1–32 B).
- Every reply carries a 1-byte status.
- Broker→client PUBLISH = `SenderLen(1) | Sender | Text`.
- PRESENCE = `Event(1) | Username`. The events are `0x01` JOIN, `0x02` LEAVE (sent DISCONNECT), `0x03` TIMEOUT (connection lost).
- PUBACK carries the room in **both** directions, because sequence numbers are only unique within a room.

**Rules and timers:**
- The first message must be CONNECT; anything else gets ERROR `0x13` and the connection is closed. (unchanged)
- Keep-alive K = 30 s: the client sends PING every K. The broker declares a client dead after 1.5 K, and the client does the same for the broker. (rev. 2: the client also detects a silent broker)
- QoS 1, **on both hops**: retransmit after T = 2 s with DUP = 1, at most 3 times. The receiver re-acknowledges a duplicate but does not forward or display it again. **New:** if no ack arrives after 3 retries, the link is considered dead and the message is kept for replay.
- **New:** delivery QoS = min(publish QoS, subscription QoS). SUBSCRIBE uses its QoS flag to request a QoS.
- **New:** duplicate windows hold the last 256 IDs, per session at the broker and per room at the client.
- **New:** at most 32 unacknowledged deliveries per subscriber, and a queue of at most 1,000. If the queue overflows, the session is dropped.
- **New:** a session is retained for 120 s after a connection is lost.
  - **Reconnect with RESUME:** replay the unacknowledged messages in order with DUP = 1.
  - **Reconnect with RESUME while the old connection still looks live:** the new connection takes the session over.
  - **Reconnect without RESUME while the old connection is live:** USERNAME_TAKEN.
- **New:** the client reconnects with back-off (immediately, then after 1, 2, 4, 8 and 16 s).
- **New:** subscribing to `$presence` returns a snapshot of who is online.
- Ordering: one lock per room on the broker; the sequence number is incremented per message. (unchanged)
- Worked example (15 bytes, unchanged and covered by a unit test): `01 07 01 05 00 01 00 02 | 72 6F 6F 6D 31 | 48 69` = QoS 1 PUBLISH of "Hi" to "room1", ID 1.
- **Report note:** a PUBACK means the next hop has the message, not that the other user has read it. Read receipts are out of scope.

## 5. File structure (rev. 2)

```
Pub_Sub_Client_Protocol/
├── README.md                 quick start, test command, layout
├── requirements-dev.txt      matplotlib, pytest (the runtime needs nothing)
├── pscp/                     the protocol package; the only code deployed to the VM
│   ├── constants.py          types, status codes, flags, timers, limits
│   ├── wire.py               Message, encode/decode, recv_exact, read_message, payload helpers
│   ├── reliability.py        Outbox (QoS 1 retransmit/replay) + DedupWindow, shared by broker and client
│   ├── log.py                one log format for every host (R7 evidence)
│   ├── broker/
│   │   ├── __main__.py       python -m pscp.broker [--host --port --max-clients --keepalive --drop-ack --log-file]
│   │   ├── server.py         Broker: accept loop, housekeeping timers, presence, fan-out
│   │   ├── connection.py     one TCP connection: reader thread (replies) + writer thread (deliveries)
│   │   ├── session.py        per-user state that outlives a connection (subscriptions, outbox, queue)
│   │   ├── registry.py       username → session: fresh / resume / takeover / reject, expiry
│   │   └── rooms.py          room → subscribers, per-room lock and sequence number
│   └── client/
│       ├── __main__.py       python -m pscp.client --host <ip> --user <name> [--qos --drop-ack --log-file]
│       ├── api.py            PSCPClient: used by both the CLI and measure.py
│       ├── connection.py     TCP connect with back-off
│       └── cli.py            chat commands: /join /leave /msg /qos /who /rooms /ping /quit
├── experiments/
│   ├── measure.py            rtt | latency | throughput | op (persistent/new) | resume → results/raw/*.csv
│   ├── plot.py               CSV → mean ± std tables + error-bar figures in report/figures/
│   └── bytes_compare.py      PSCP vs JSON, QoS 0 vs 1, persistent vs new connection, yearly GB/kWh
├── tests/                    test_wire, test_reliability, test_rooms, test_integration (25 tests)
├── deploy/                   setup_vm.sh, pscp-broker.service (systemd), capture.sh (tcpdump)
├── docs/
│   ├── PROTOCOL.md           authoritative spec (R1)
│   ├── DEPLOYMENT.md         VM setup, R3/R5/R6/R7 step-by-step
│   └── handoff.md            this file
├── results/                  raw/ (CSV), logs/ (failure evidence), captures/ (pcapng)
└── report/                   CEP_Report.tex (to be copied in), figures/
```

Compared with the first plan:
- `reliability.py` moved to the package level so the broker and client share it.
- The broker separates **Session** (survives a disconnect) from **Connection** (one socket); this split is what makes resume work.
- The client's receiver and keep-alive code live inside `api.py` rather than in separate files.
- The failure scenarios are documented as steps in `DEPLOYMENT.md` §7 rather than as scripts.

## 6. Planned deployment and tests

- **Hosts:**
  - Host 1 = **broker on a cloud VM** (Ubuntu, systemd service, port 5050 open in the security group).
  - Host 2 = Client A (Member 2's laptop).
  - Host 3 = Client B (a second laptop, a VirtualBox VM in bridged mode, or a lab PC).
- **R3 evidence:**
  - On the VM, `ip addr` shows a **private** IP, while the public IP is 1:1 NAT'd by the cloud. Include both and explain the mapping.
  - Include `ipconfig` output from each laptop.
- **Performance conditions (R6):**
  - QoS 0 vs QoS 1. QoS 1 now costs 4 PSCP messages per chat message instead of 2.
  - A persistent connection vs a new connection per message.
  - Cloud broker vs LAN broker, or Wi-Fi vs wired.
  - **New:** a disconnect-and-resume run, measuring the % of messages delivered.
- **Metrics:** RTT (PING→PONG), throughput (bulk publish, echo), end-to-end latency (publish to your own room, timed on one clock), operation time (publish→PUBACK, persistent vs new).
- **Local smoke-test results (127.0.0.1, 2 runs each). These only check the scripts work; they are not report data:**
  - latency 0.62 ms (QoS 0) vs 1.13 ms (QoS 1)
  - throughput ≈ 2,300 msg/s (QoS 0) vs ≈ 900 msg/s (QoS 1)
  - op 1.05 ms (persistent) vs 5.5 ms (new connection)
  - resume 0 % (QoS 0) vs 100 % (QoS 1)
- **Failure scenarios (R7):**
  1. A client killed mid-session → keep-alive timeout → PRESENCE TIMEOUT, session retained.
  2. Broker unreachable when the client starts → back-off retries 1, 2, 4, 8, 16 s.
  3. Lost PUBACKs (`--drop-ack`) → DUP retransmission and duplicate removal on each hop.
  4. **New:** link loss → resume, with every missed message delivered once.
- **Design tensions:**
  - Reliability vs latency (QoS 0 vs 1 on both hops).
  - Compact binary vs readable text (PSCP vs JSON: 46 B vs 92 B for a publish).
  - **State vs simplicity:** session retention costs broker memory, and the state is lost if the broker crashes.
  - Optional 4th: connection reuse vs server resources.
- **Sustainability:** `bytes_compare.py` already gives the numbers for 5 recipients, QoS 1, persistent:
  - PSCP saves about 36 % of bytes on the wire compared with JSON.
  - A persistent connection saves about 34 % compared with a new connection per message.
  - The yearly GB and kWh projections take 0.06 kWh/GB as an input (Aslan et al. 2018). **Check this figure before using it.**

## 7. Open items and risks

- [ ] **Update report §2** to rev. 2: copy it from `docs/PROTOCOL.md`, including the new SESSION flag, the payload formats, two-hop QoS 1 and resume.
- [ ] **Proposal may be missing required items.** The brief asks for the plan for three hosts and the division of work. Consider adding one sentence for each back in.
- [ ] Fill in member names, roll numbers and group number (proposal, report header, contributions table).
- [ ] A 3rd or 4th member may join; update the work split if so.
- [ ] **Page-limit risk:** the body already reaches about 10 pages with placeholders, and rev. 2 adds material. Move 2.7 (the receive-action table) and 2.8 (the worked example) to an appendix. Keep failure scenarios 1 and 4 in the body and move 2 and 3 to an appendix if needed.
- [ ] Choose a cloud provider and create the VM (free tier). Keep port 5050 limited to the group's IPs, because PSCP has no authentication or encryption. **Mention this in the R4 security discussion,** including that RESUME takeover could be abused without authentication.
- [ ] Draw `docs/network_diagram.drawio` and export it to `report/figures/`.
- [ ] Copy `CEP_Report.tex` into `report/`.
- [ ] Write the AI-declaration appendix honestly. Claude was used for reading the brief, the proposal, the report structure, the LaTeX template, the draft protocol spec, the rev. 2 protocol design, and **writing the code, tests, experiment scripts and deployment files**. Every member must understand the code well enough to explain it at the demo.

## 8. Next steps (suggested order)

1. ~~Submit the proposal~~ (Week 09) and get Option D confirmed.
2. ~~Write `pscp/`, the tests and the experiments~~ (done). Every member reads `docs/PROTOCOL.md` and the code, and runs `python -m unittest discover -s tests -t .` once.
3. Create the cloud VM and run `sudo bash deploy/setup_vm.sh`. Smoke-test from both laptops (`docs/DEPLOYMENT.md` §1–3).
4. Record `ip addr` / `ipconfig` and the public IP; draw the network diagram.
5. Make a Wireshark capture of one QoS 1 exchange on both hops, plus one DUP retransmission, and annotate it.
6. Run `experiments/measure.py` for every condition (5 runs each), then `plot.py` and `bytes_compare.py`.
7. Run failure scenarios 1–4 and save the logs to `results/logs/`.
8. Update report §2 to rev. 2 and fill in the placeholders. Check it is ≤ 10 pages, get the contributions table signed and attach the rubric.
9. Rehearse the live demo: all members must be able to explain every part.
