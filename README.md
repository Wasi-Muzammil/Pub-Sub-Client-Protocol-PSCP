# PSCP: Publish/Subscribe Chat Protocol

CS-327 CEP (Option D): a multi-client chat with presence, built on our own MQTT-style protocol over raw TCP sockets. The broker and protocol use only the Python standard library. The WhatsApp-style web client adds Streamlit.

- Protocol spec: [docs/PROTOCOL.md](docs/PROTOCOL.md)
- Broker in Docker on Oracle Cloud, evidence and experiments: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- Web client (Streamlit + local SQLite): [docs/WEBCLIENT.md](docs/WEBCLIENT.md)
- Project status and decisions: [docs/handoff.md](docs/handoff.md)

## Quick start (one machine)
```bash
pip install -r requirements.txt
python -m pscp.broker --host 127.0.0.1                    # terminal 1: broker
streamlit run webclient/app.py                            # terminal 2: web client -> http://localhost:8501
python -m pscp.client --host 127.0.0.1 --user bob         # terminal 3: or the terminal client
```
In the web client, create a profile, type a group name under *New group*, and chat. In the terminal client, use `/join <group>` and then type; `/help` lists the other commands.

## Broker on a cloud VM
```bash
sudo bash deploy/oracle_setup.sh            # Docker + firewall + docker compose up -d --build
sudo docker compose logs -f broker          # live log
```

## Tests
```bash
python -m unittest discover -s tests -t .     # or: pytest
```

## Layout
```
pscp/            protocol package (the only code in the broker image)
  constants.py   message types, status codes, flags, timers
  wire.py        encode/decode, recv_exact, payload formats
  reliability.py Outbox (QoS 1 retransmit) + DedupWindow, shared by broker and client
  log.py         common log format
  broker/        server (accept + timers), connection (reader/writer threads),
                 session (survives disconnects), registry (resume/takeover), rooms
  client/        api (PSCPClient), connection (back-off), cli (terminal chat)
webclient/       Streamlit app (app.py, ui.py), ChatService, SQLite store, profile convention
experiments/     measure.py -> results/raw/*.csv, plot.py -> report/figures, bytes_compare.py
tests/           unit + in-process integration + web client tests
deploy/          oracle_setup.sh (Docker + iptables), capture.sh (tcpdump)
Dockerfile, docker-compose.yml   broker image and service (host networking, auto-restart)
docs/            PROTOCOL.md, DEPLOYMENT.md, WEBCLIENT.md, handoff.md
results/         raw/ (CSV), logs/, captures/ (pcapng)
report/          CEP_Report, figures/
```
