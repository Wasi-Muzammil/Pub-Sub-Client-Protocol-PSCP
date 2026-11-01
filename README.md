# PSCP: Publish/Subscribe Chat Protocol

CS-327 CEP (Option D): a multi-client chat with presence, built on our own MQTT-style protocol over raw TCP sockets. Python 3.8+, standard library only.

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
pip install -r requirements-dev.txt
streamlit run tools/streamlit_app.py      # browser chat over the same PSCP client
```
It is a demo skin only: the protocol code in `pscp/` needs no third-party package. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) §8.

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
  log.py         common log format
  broker/        server (accept + timers), connection (reader/writer threads),
                 session (survives disconnects), registry (resume/takeover), rooms
  client/        api (PSCPClient), connection (back-off), cli (chat UI)
experiments/     measure.py -> results/raw/*.csv, plot.py -> report/figures, bytes_compare.py
tools/           streamlit_app.py (optional browser demo UI)
tests/           unit + in-process integration tests
deploy/          setup_vm.sh, systemd unit, tcpdump capture script
docs/            PROTOCOL.md, DEPLOYMENT.md, handoff.md
results/         raw/ (CSV), logs/, captures/ (pcapng)
report/          CEP_Report.tex, figures/
```
