# Deployment: 3 hosts with the broker in Docker on an Oracle Cloud VM

```
 Host 2: Laptop A ─ Streamlit client ─┐                         ┌─ Host 1: Oracle Cloud VM (Ubuntu)
 Host 3: Laptop B ─ Streamlit client ─┴─ Internet ─ TCP 5050 ───┤   docker: pscp-broker (network_mode: host)
        (Wi-Fi / hotspot, NAT)         client-initiated only    └─ VCN security list + iptables: 22, 5050
```
The broker has no GUI. You watch it over SSH with `docker compose logs`. Each user runs the web client on their own laptop (see [WEBCLIENT.md](WEBCLIENT.md)).

## 1. Create the VM (Oracle Cloud Free Tier)
1. **Create the instance.** Go to Compute → Instances → Create instance.
   - Image: **Canonical Ubuntu 22.04 or 24.04**.
   - Shape: VM.Standard.A1.Flex (Ampere, arm64) or VM.Standard.E2.1.Micro (x86). The image works on both.
   - Download the SSH key and note the **public IP**.
2. **Allow TCP 5050 in the VCN.** Go to Networking → Virtual Cloud Networks → your VCN → Security Lists → Default → **Add Ingress Rule**:
   - Source CIDR: each member's public IP, `/32`. Using `0.0.0.0/0` works but is less safe: PSCP has no authentication and no encryption.
   - IP protocol: TCP. Destination port: **5050**.
3. **The VM's own firewall must also allow it.** Oracle's Ubuntu images block everything except SSH with iptables. The setup script below opens port 5050 for you.

## 2. Install and start the broker
```bash
ssh -i <key> ubuntu@<public-ip>
git clone <repo-url> pscp && cd pscp
sudo bash deploy/oracle_setup.sh      # installs Docker, opens 5050 in iptables, docker compose up -d --build
```

Day-to-day commands (on the VM, in the repo folder):

| Task | Command |
| --- | --- |
| Live protocol log | `sudo docker compose logs -f broker` |
| Last 200 lines, saved | `sudo docker compose logs --no-color --tail 200 broker > results/logs/broker.log` |
| Shell in the container | `sudo docker exec -it pscp-broker sh` |
| Status | `sudo docker compose ps` |
| Update after a code change | `git pull && sudo docker compose up -d --build` |
| Stop / start | `sudo docker compose stop` / `sudo docker compose start` |
| Fault injection | Edit `command:` in `docker-compose.yml` (e.g. `--drop-ack 0.3`), then `up -d` |

The container restarts automatically after a crash or a VM reboot (`restart: unless-stopped`). Its logs rotate at 10 MB × 3.

**Why host networking:** the broker binds the VM's own interface directly. So the logs show the real client IPs in `ACCEPT <ip>:<port>`, and `tcpdump` on the VM sees the PSCP traffic. There is also no extra Docker NAT layer (172.17.x.x) to explain in R3/R5.

## 3. Connect the clients (Hosts 2 and 3)
- **Web client:** `streamlit run webclient/app.py`, then create a profile with broker host = `<public-ip>`. See [WEBCLIENT.md](WEBCLIENT.md).
- **Terminal client:** `python -m pscp.client --host <public-ip> --user alice --log-file alice.log`

## 4. Evidence for R3 (independent hosts)
- On the VM, `ip addr` shows a **private** address (for example 10.0.0.x). The public IP is mapped to it by Oracle's 1:1 NAT. Screenshot it next to the instance page that shows the public IP, and explain the mapping in the report.
- On each laptop, run `ipconfig` (Windows) or `ip addr` (Linux), and note each laptop's public IP. That public IP is what the broker log shows in `ACCEPT`.
- Reachability: `python experiments/measure.py --host <public-ip> --exp rtt`. ICMP ping is blocked by the default security list unless you add a rule for it.
- Export the network diagram (`docs/network_diagram.drawio`) to `report/figures/`.

## 5. Wireshark (R5)
- **On a laptop:** capture on the Wi-Fi interface with the filter `tcp.port == 5050`. Send one chat message, select the PUBLISH frame and annotate it: Ethernet → IP (laptop → VM public IP) → TCP (ephemeral port → 5050) → PSCP bytes.
- **On the VM:** run `sudo bash deploy/capture.sh 60`, then copy the `.pcapng` back with `scp`. This capture shows the VM's private IP, which is more evidence of the NAT.
- **QoS 1 on both hops:** set `--drop-ack 0.3` to capture a DUP=1 retransmission.

## 6. Measurements (R6)
Run every command from a laptop, once for each condition label:
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
| 1. Client killed mid-session | Kill a client (close the Streamlit terminal, `taskkill /F` or `kill -9`). | The broker log shows `CLOSE … peer closed` or `keep-alive timeout`, then `SESSION … retained`. The other user sees the person go offline. |
| 2. Broker unreachable at client start | Run `sudo docker compose stop`, then start a client. | The client log (`~/.pscp/<user>.log` or `--log-file`) shows `CONNECT attempt n/6 failed` and `RETRY … in 1/2/4/8/16s`. In the web client, messages stay 🕓 and are sent after `docker compose start`. |
| 3. Lost acknowledgements | Run with `--drop-ack 0.3` (compose `command:` and/or the terminal client). | `FAULT dropped PUBACK`, `RETX … DUP=1` and `DUP … re-ack, not forwarded` in the logs, with each message shown once. |
| 4. Link loss and resume | Turn off a laptop's Wi-Fi for about 20 s while another user sends messages. A hotspot reconnect that changes the laptop's IP reliably breaks the TCP connection. | The client log shows `LOST` and `CONNACK OK session_present=1`. The broker shows `taken over` or `SESSION … retained`, then `resumed: replaying N`. Every missed message appears exactly once. If the IP did not change, TCP may simply carry the old connection through the outage; the messages then arrive late but once, with no resume. |
