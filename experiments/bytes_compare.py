"""Bytes on the wire per chat message: PSCP vs an equivalent JSON protocol, QoS 0 vs 1,
persistent vs new connection; projected to many users (sustainability paragraph, design tension 2).

    python experiments/bytes_compare.py --users 1000 --msgs-per-day 200 --recipients 5

Assumptions (state them in the report): one PSCP/JSON message per TCP segment (TCP_NODELAY),
IPv4 + TCP headers without options (40 B) + Ethernet II header (14 B) = 54 B per segment,
pure TCP ACKs ignored. kWh/GB is an input - check the figure and its source before quoting it.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pscp.constants import FLAG_QOS, MsgType, Status  # noqa: E402
from pscp.wire import Message, encode, encode_delivery, encode_username, status_payload  # noqa: E402

L2_L3_L4 = 54  # Ethernet 14 + IPv4 20 + TCP 20
HANDSHAKE_AND_CLOSE = 3 * L2_L3_L4 + 4 * L2_L3_L4 + 2 * 12  # SYN, SYN-ACK, ACK (+MSS options), FIN/ACK x2


def jbytes(obj):
    return len(json.dumps(obj, separators=(",", ":")).encode()) + 1  # newline-delimited


def per_message(room, sender, text, recipients):
    t = text.encode()
    pscp = {
        "publish": len(encode(Message(MsgType.PUBLISH, FLAG_QOS, 1, room, t))),
        "deliver": len(encode(Message(MsgType.PUBLISH, FLAG_QOS, 42, room, encode_delivery(sender, t)))),
        "puback": len(encode(Message(MsgType.PUBACK, 0, 42, room, status_payload(Status.OK)))),
        "connect": len(encode(Message(MsgType.CONNECT, 0, 0, "", encode_username(sender)))),
        "connack": len(encode(Message(MsgType.CONNACK, 0, 0, "", status_payload(Status.OK)))),
    }
    js = {
        "publish": jbytes({"type": "publish", "room": room, "id": 1, "qos": 1, "text": text}),
        "deliver": jbytes({"type": "message", "room": room, "seq": 42, "qos": 1, "from": sender, "text": text}),
        "puback": jbytes({"type": "puback", "room": room, "id": 42, "status": "ok"}),
        "connect": jbytes({"type": "connect", "version": 1, "username": sender}),
        "connack": jbytes({"type": "connack", "status": "ok"}),
    }

    def total(sizes, qos, new_conn):
        app = sizes["publish"] + recipients * sizes["deliver"]
        segs = 1 + recipients
        if qos:
            app += (1 + recipients) * sizes["puback"]
            segs += 1 + recipients
        wire = app + segs * L2_L3_L4
        if new_conn:  # sender opens a fresh connection for this one message
            wire += sizes["connect"] + sizes["connack"] + 2 * L2_L3_L4 + HANDSHAKE_AND_CLOSE
            app += sizes["connect"] + sizes["connack"]
        return app, wire

    return pscp, js, total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--room", default="room1")
    p.add_argument("--sender", default="alice")
    p.add_argument("--text", default="Hello, are you coming to the lab?")
    p.add_argument("--recipients", type=int, default=5)
    p.add_argument("--users", type=int, default=1000)
    p.add_argument("--msgs-per-day", type=int, default=200)
    p.add_argument("--kwh-per-gb", type=float, default=0.06, help="VERIFY before quoting (Aslan et al. 2018)")
    a = p.parse_args()

    pscp, js, total = per_message(a.room, a.sender, a.text, a.recipients)
    print("| message | PSCP B | JSON B |\n|---|---|---|")
    for k in pscp:
        print(f"| {k} | {pscp[k]} | {js[k]} |")

    print(f"\nPer chat message to {a.recipients} recipients (app bytes / bytes on wire incl. 54 B per segment):\n")
    print("| scenario | PSCP app | PSCP wire | JSON app | JSON wire | wire saved by PSCP |\n|---|---|---|---|---|---|")
    rows = {}
    for qos in (0, 1):
        for new_conn in (False, True):
            pa, pw = total(pscp, qos, new_conn)
            ja, jw = total(js, qos, new_conn)
            name = f"QoS {qos}, {'new connection' if new_conn else 'persistent'}"
            rows[name] = (pw, jw)
            print(f"| {name} | {pa} | {pw} | {ja} | {jw} | {100 * (jw - pw) / jw:.1f} % |")

    pw, jw = rows["QoS 1, persistent"]
    pw_new, _ = rows["QoS 1, new connection"]
    msgs = a.users * a.msgs_per_day
    gb = lambda b: b * msgs * 365 / 1e9  # noqa: E731
    print(f"\nProjection: {a.users} users x {a.msgs_per_day} msgs/day x 365 days, QoS 1:")
    for label, b in (("PSCP persistent", pw), ("JSON persistent", jw), ("PSCP new connection", pw_new)):
        print(f"  {label:20s} {gb(b):8.2f} GB/year  ~{gb(b) * a.kwh_per_gb:7.2f} kWh/year")
    print(f"  PSCP vs JSON saves {gb(jw - pw):.2f} GB/year ({100 * (jw - pw) / jw:.1f} %); "
          f"persistent vs new connection saves {gb(pw_new - pw):.2f} GB/year "
          f"({100 * (pw_new - pw) / pw_new:.1f} %)")


if __name__ == "__main__":
    main()
