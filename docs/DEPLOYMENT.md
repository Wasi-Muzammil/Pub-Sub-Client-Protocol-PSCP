# Deployment: 3 hosts with the broker on a cloud VM

```
 Host 2: Laptop A (client) ─┐                            ┌─ Host 1: Cloud VM (Ubuntu 22.04/24.04)
 Host 3: Laptop B or VM    ─┴── Internet ── TCP 5050 ────┤   python3 -m pscp.broker  (systemd: pscp-broker)
        (Wi-Fi / hotspot, NAT)    client-initiated only  └─ security group: 22 (SSH), 5050/tcp from our IPs only
```

## 1. Create the VM (any provider)
- Use the smallest free-tier Linux VM, for example AWS t2.micro/t3.micro, GCP e2-micro, Azure B1s or Oracle Always Free. Choose Ubuntu.
- Inbound firewall / security group rules:
  - **TCP 22** from your own IP.
  - **TCP 5050** from the group members' public IPs. 0.0.0.0/0 also works but is less safe: PSCP has no authentication and no encryption.
- Make a note of the **public IP**.

## 2. Install the broker
```bash
ssh ubuntu@<public-ip>
git clone <repo-url> pscp && cd pscp      # or: scp -r pscp deploy ubuntu@<public-ip>:~/pscp/
sudo bash deploy/setup_vm.sh
journalctl -u pscp-broker -f              # live protocol log
```
To update after a code change, run `git pull && sudo bash deploy/setup_vm.sh`.

## 3. Connect the clients (Hosts 2 and 3)
```bash
python -m pscp.client --host <public-ip> --user alice --log-file alice.log
```

## 4. Evidence for R3 (independent hosts)
- On the VM, run `ip addr`. It shows a **private** address (for example 10.0.0.5), because the cloud provider maps the public IP to it with 1:1 NAT. Screenshot this together with the provider console page showing the public IP, and explain the mapping in the report.
- On each laptop, run `ipconfig` (Windows) or `ip addr` (Linux). Also take each laptop's public IP from a "what is my IP" site; this is the address the broker log shows in `ACCEPT <ip>:<port>`.
- Ping: ICMP is often blocked on cloud VMs, so `python experiments/measure.py --exp rtt` is the reliable reachability test.
- Draw the network diagram in `docs/network_diagram.drawio` and export it to `report/figures/`.

## 5. Wireshark (R5)
- On a laptop: capture on the Wi-Fi interface with the filter `tcp.port == 5050`. Send one chat message, then select the PUBLISH frame and annotate Ethernet → IP (laptop IP → VM public IP) → TCP (ephemeral port → 5050) → PSCP bytes.
- On the VM: run `sudo bash deploy/capture.sh 60` and copy the `.pcapng` file back. The VM's capture shows its private IP, which is the NAT evidence again.
- QoS 1 on both hops: start the broker with `--drop-ack 0.3` to capture a DUP=1 retransmission.

## 6. Measurements (R6)
Run every command from a laptop and repeat it for each condition label:
```bash
python experiments/measure.py --host <ip> --exp rtt        --condition cloud-wifi
python experiments/measure.py --host <ip> --exp latency    --condition cloud-wifi --qos 0
python experiments/measure.py --host <ip> --exp latency    --condition cloud-wifi --qos 1
python experiments/measure.py --host <ip> --exp throughput --condition cloud-wifi --qos 1 --count 500 --size 256
python experiments/measure.py --host <ip> --exp op         --condition cloud-wifi --mode persistent
python experiments/measure.py --host <ip> --exp op         --condition cloud-wifi --mode new
python experiments/measure.py --host <ip> --exp resume     --condition cloud-wifi --qos 0
python experiments/measure.py --host <ip> --exp resume     --condition cloud-wifi --qos 1
python experiments/plot.py           # tables to stdout, figures to report/figures/
python experiments/bytes_compare.py  # sustainability numbers
```
For the LAN-versus-cloud condition, run `python -m pscp.broker` on a laptop on the same Wi-Fi and repeat the commands with `--condition lan-wifi`.

## 7. Failure scenarios (R7)

Save the logs to `results/logs/`.

| Scenario | How to run it | Evidence to save |
| --- | --- | --- |
| 1. Client killed mid-session | Close a laptop's client window, or use `taskkill /F` or `kill -9`. | The broker journal shows `CLOSE … peer closed` or `keep-alive timeout`, then `SESSION … retained`. Other clients see `* bob timeout`. |
| 2. Broker unreachable at client start | `sudo systemctl stop pscp-broker`, then start a client. | The client log shows `CONNECT attempt n/6 failed` and `RETRY … in 1/2/4/8/16s`. |
| 3. Lost acknowledgements | Run the broker and/or client with `--drop-ack 0.3`. | `FAULT dropped PUBACK`, `RETX … DUP=1` and `DUP … re-ack, not forwarded` in the logs, with each message shown once. |
| 4. Link loss and resume | Turn off a laptop's Wi-Fi for about 20 s while another client sends messages. A hotspot reconnect that changes the laptop's IP reliably breaks the TCP connection. | The client log shows `LOST` and `CONNACK OK session_present=1`. The broker shows `taken over` or `SESSION … retained`, then `resumed: replaying N`. Every missed message appears exactly once. If the IP did not change, TCP may simply carry the old connection through the outage; the messages then arrive late but once, with no resume. |

## 8. Streamlit demo client (optional)

`tools/streamlit_app.py` is a browser chat UI for the live demo. It is a **skin over `pscp.client.PSCPClient`**: it opens normal PSCP connections, so the protocol package stays standard-library only. Streamlit itself needs Python 3.11+ even though `pscp/` supports 3.8+.

```bash
pip install -r requirements-dev.txt          # streamlit==1.66.1
streamlit run tools/streamlit_app.py         # browser opens on http://localhost:8501
```

- Sidebar: broker host/port, username, Connect/Disconnect, room join/leave, QoS 0/1, Ping (RTT). Main area: chat history, online users, protocol log.
- With three hosts: Host 1 runs the broker, Laptop A runs Streamlit (browser on the same laptop, port 8501 stays local), Laptop B runs `python -m pscp.client`. Both are clients, so only the broker needs TCP 5050 open.
- Do not expose port 8501: Streamlit has no authentication of its own.
- **Take over an existing session (RESUME)** clears a session left behind by a browser refresh. Without it the old socket keeps the username and the broker answers `USERNAME_TAKEN`.
- One Streamlit process keeps at most one client per username. Connecting with a username that is already live in the same process disconnects the older client instead of looping, and the older tab shows "this session was taken over by another client".
- Use the Disconnect button before refreshing the page; a refresh alone leaves the old session to time out after 120 s.
