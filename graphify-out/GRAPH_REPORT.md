# Graph Report - Pub_Sub_Client_Protocol  (2026-11-10)

## Corpus Check
- Corpus is ~13,277 words - fits in a single context window. You may not need a graph.

## Summary
- 394 nodes · 923 edges · 18 communities (9 shown, 9 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 125 edges (avg confidence: 0.89)
- Token cost: 142,925 input · 0 output

## Community Hubs (Navigation)
- Package Wiring & Wire Format
- Deployment & Evaluation Plan
- PSCPClient Library
- Session & QoS Reliability
- Measurement Experiments
- Report Protocol Design
- Rooms & Delivery Queues
- Registry & Broker Entry
- Bytes & Plotting Tools
- Integration Tests
- Broker Connection Threads
- Chat CLI
- Wireshark & Security
- Capture Script
- VM Setup Script
- Sustainability & Energy

## God Nodes (most connected - your core abstractions)
1. `Message` - 41 edges
2. `PSCPClient` - 37 edges
3. `Status` - 31 edges
4. `Connection` - 23 edges
5. `MsgType` - 22 edges
6. `Outbox` - 21 edges
7. `Broker` - 20 edges
8. `ProtocolError` - 20 edges
9. `Session` - 17 edges
10. `setup()` - 16 edges

## Surprising Connections (you probably didn't know these)
- `per_message()` --uses--> `MsgType`  [INFERRED]
  experiments/bytes_compare.py → pscp/constants.py
- `per_message()` --uses--> `Status`  [INFERRED]
  experiments/bytes_compare.py → pscp/constants.py
- `ChatTest` --uses--> `MsgType`  [INFERRED]
  tests/test_integration.py → pscp/constants.py
- `pub()` --uses--> `MsgType`  [INFERRED]
  tests/test_reliability.py → pscp/constants.py
- `ChatTest` --uses--> `Status`  [INFERRED]
  tests/test_integration.py → pscp/constants.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **QoS 1 Reliability Mechanism (retransmit, dedup, ack, flow control)** — docs_protocol_qos, docs_protocol_retransmit, docs_protocol_duplicate_window, docs_protocol_puback, docs_protocol_flow_control [EXTRACTED 1.00]
- **Session Retention and Resume Flow** — docs_protocol_session_retention, docs_protocol_client_resume, docs_protocol_session_takeover, docs_protocol_flags_field, docs_handoff_session_connection_split [INFERRED 0.85]
- **Requirement Evidence Steps (R3, R5, R6, R7)** — docs_deployment_nat_evidence, docs_deployment_wireshark_capture, docs_deployment_measurements, docs_deployment_failure_scenarios, docs_handoff_requirements_r1_r7 [EXTRACTED 1.00]
- **Design tensions and engineering justification (Section 7)** — report_cep_report_reliability_vs_latency, report_cep_report_compactness_vs_readability, report_cep_report_state_vs_simplicity, report_cep_report_tcp_transport_choice [EXTRACTED 1.00]
- **Behaviour under failure scenarios (Section 9)** — report_cep_report_failure_client_killed, report_cep_report_failure_broker_unreachable, report_cep_report_failure_lost_puback, report_cep_report_keepalive_timers [EXTRACTED 1.00]
- **Deliberate PSCP differences from MQTT (Table 1)** — report_cep_report_fixed_8_byte_header, report_cep_report_per_topic_sequence_number, report_cep_report_presence_message, report_cep_report_qos_levels, report_cep_report_mqtt_v3_1_1 [EXTRACTED 1.00]

## Communities (18 total, 9 thin omitted)

### Community 0 - "Package Wiring & Wire Format"
Cohesion: 0.08
Nodes (21): open_with_backoff(), MsgType, Presence, Status, _build(), ConnectionClosed, decode(), decode_delivery() (+13 more)

### Community 1 - "Deployment & Evaluation Plan"
Cohesion: 0.07
Nodes (46): Cloud VM (Ubuntu, free tier), --drop-ack Fault Injection, Failure Scenarios (R7), Scenario 4: Link Loss and Resume, Measurements (R6): rtt/latency/throughput/op/resume, 1:1 NAT Evidence (R3), pscp-broker systemd service, Security Group (TCP 22, TCP 5050 restricted) (+38 more)

### Community 3 - "Session & QoS Reliability"
Cohesion: 0.11
Nodes (5): DedupWindow, Outbox, DedupWindowTest, OutboxTest, pub()

### Community 4 - "Measurement Experiments"
Cohesion: 0.11
Nodes (11): Echo, exp_latency(), exp_op(), exp_resume(), exp_rtt(), exp_throughput(), new_client(), ConnectError (+3 more)

### Community 5 - "Report Protocol Design"
Cohesion: 0.07
Nodes (29): CEP Report: PSCP Multi-Client Chat (CS-327), Action-on-receipt rules (Table 5), Declaration of AI Assistance, Central Broker (star topology), Client state machine (DISCONNECTED, AWAIT_CONNACK, CONNECTED), Failure scenario 2: broker unreachable, exponential back-off (1,2,4,8,16 s), Failure scenario 1: client killed mid-session (PRESENCE TIMEOUT/LEAVE), Failure scenario 3: lost PUBACK, DUP retransmission (+21 more)

### Community 6 - "Rooms & Delivery Queues"
Cohesion: 0.11
Nodes (4): Room, Rooms, Session, RoomsTest

### Community 7 - "Registry & Broker Entry"
Cohesion: 0.12
Nodes (3): main(), Registry, Broker

### Community 8 - "Bytes & Plotting Tools"
Cohesion: 0.14
Nodes (11): jbytes(), main(), per_message(), total(), main(), load(), main(), plot() (+3 more)

### Community 9 - "Integration Tests"
Cohesion: 0.18
Nodes (6): BrokerTestCase, ChatTest, Inbox, LossyAckTest, ResumeTest, wait_until()

## Knowledge Gaps
- **24 isolated node(s):** `Quick Start (broker + two clients)`, `Rooms (Topics)`, `Broker Connection States (AWAIT_CONNECT/CONNECTED/CLOSED)`, `Flow Control (32 inflight, 1000 queue)`, `TCP_NODELAY (disable Nagle)` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 113 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Message` connect `PSCPClient Library` to `Package Wiring & Wire Format`, `Session & QoS Reliability`, `Rooms & Delivery Queues`, `Bytes & Plotting Tools`, `Integration Tests`, `Broker Connection Threads`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `Message` (e.g. with `Connection` and `Rooms`) actually correct?**
  _`Message` has 8 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Quick Start (broker + two clients)`, `Rooms (Topics)`, `Broker Connection States (AWAIT_CONNECT/CONNECTED/CLOSED)` to the rest of the system?**
  _24 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Package Wiring & Wire Format` be split into smaller, more focused modules?**
  _Cohesion score 0.0792838874680307 - nodes in this community are weakly interconnected._
- **Why does `PSCPClient` connect `PSCPClient Library` to `Package Wiring & Wire Format`, `Integration Tests`, `Session & QoS Reliability`, `Measurement Experiments`?**
  _High betweenness centrality (0.107) - this node is a cross-community bridge._
- **Are the 7 inferred relationships involving `PSCPClient` (e.g. with `MsgType` and `Status`) actually correct?**
  _`PSCPClient` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Should `Deployment & Evaluation Plan` be split into smaller, more focused modules?**
  _Cohesion score 0.06775510204081632 - nodes in this community are weakly interconnected._