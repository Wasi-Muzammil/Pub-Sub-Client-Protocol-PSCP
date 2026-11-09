# PSCP: Publish/Subscribe Chat Protocol

CS-327 CEP (Option D): a multi-client chat with presence, built on our own MQTT-style protocol over raw TCP sockets. Python 3.8+, standard library only.

- Simple explanation: [docs/PSCP_IN_SIMPLE_WORDS.md](docs/PSCP_IN_SIMPLE_WORDS.md)
- Protocol spec: [docs/PROTOCOL.md](docs/PROTOCOL.md)
- Cloud deployment, evidence and experiments: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- Project status and decisions: [docs/handoff.md](docs/handoff.md)

## Quick start (one machine)
```bash
python -m pscp.broker --host 127.0.0.1                    # terminal 1
python -m pscp.client --host 127.0.0.1 --user alice       # terminal 2
python -m pscp.client --host 127.0.0.1 --user bob         # terminal 3
```
In the client, `/join room1`, then type to chat. `/help` lists the other commands. Add `--log-file alice.log` to record every protocol message.

## Optional demo UI
```bash
python tools/webui.py                               # http://127.0.0.1:8080
```
WhatsApp-style browser chat: rooms on the left, messages on the right. `tools/webui.py` is a standard-library bridge that holds a normal PSCP client and serves `tools/web/index.html`; the page polls it once per second. Room previews and history come from the broker's SQLite mirror (`data/broker.db`). No extra package to install. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) §8.

## Tests
```bash
python -m unittest discover -s tests -t .     # or: pytest
```

## Layout
```
pscp/            protocol package (the only code deployed to the VM)
  constants.py   message types, status codes, flags, timers
  wire.py        encode/decode, recv_exact, payload formats
  reliability.py Outbox (QoS 1 retransmit) + DedupWindow, shared by broker and client
  store.py       SQLite mirror of rooms, users, memberships and messages
  log.py         common log format
  broker/        server (accept + timers), connection (reader/writer threads),
                 session (survives disconnects), registry (resume/takeover), rooms
  client/        api (PSCPClient), connection (back-off), cli (chat UI)
experiments/     measure.py -> results/raw/*.csv, plot.py -> report/figures, bytes_compare.py
tools/           webui.py + web/index.html (optional browser demo UI)
tests/           unit + in-process integration tests
deploy/          setup_vm.sh, systemd unit, tcpdump capture script
docs/            PROTOCOL.md, DEPLOYMENT.md, handoff.md
results/         raw/ (CSV), logs/, captures/ (pcapng)
report/          CEP_Report.tex, figures/
```
