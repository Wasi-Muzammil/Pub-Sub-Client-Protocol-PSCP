"""R6 measurements over the real PSCPClient code path. Appends rows to results/raw/<exp>.csv.

  python experiments/measure.py --host <broker> --exp rtt        --condition cloud-wifi
  python experiments/measure.py --host <broker> --exp latency    --condition cloud-wifi --qos 0
  python experiments/measure.py --host <broker> --exp throughput --condition cloud-wifi --count 500 --size 256
  python experiments/measure.py --host <broker> --exp op         --condition cloud-wifi --mode new
  python experiments/measure.py --host <broker> --exp resume     --condition cloud-wifi --qos 1

Each run (--runs, default 5) produces --count samples; plot.py averages per run,
then reports mean ± std across runs.
"""
import argparse
import csv
import os
import socket
import sys
import threading
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pscp.client import PSCPClient  # noqa: E402
from pscp.constants import DEFAULT_PORT, Status  # noqa: E402
from pscp.log import setup  # noqa: E402

FIELDS = ["timestamp", "host", "experiment", "condition", "qos", "run", "sample", "value", "unit"]


class Echo:
    """Collects deliveries of our own messages so latency is timed on one clock."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seen = {}

    def on_message(self, room, sender, text, seq):
        with self.cond:
            self.seen[text] = time.perf_counter()
            self.cond.notify_all()

    def wait_for(self, text, timeout=10.0):
        with self.cond:
            if not self.cond.wait_for(lambda: text in self.seen, timeout):
                raise TimeoutError(f"echo of {text!r} not received")
            return self.seen.pop(text)

    def wait_count(self, prefix, n, timeout=60.0):
        with self.cond:
            ok = self.cond.wait_for(lambda: sum(k.startswith(prefix) for k in self.seen) >= n, timeout)
            return ok, max((v for k, v in self.seen.items() if k.startswith(prefix)), default=None)


def new_client(args, tag, echo=None, **kw):
    log = setup("measure", args.log_level)
    name = f"{tag}-{uuid.uuid4().hex[:6]}"
    return PSCPClient(args.host, name, args.port, qos=args.qos, logger=log,
                      on_message=echo.on_message if echo else None, **kw)


def exp_rtt(args, run):
    c = new_client(args, "rtt")
    c.connect()
    try:
        return [(c.ping() * 1000, "ms") for _ in range(args.count)]
    finally:
        c.close()


def exp_latency(args, run):
    echo = Echo()
    c = new_client(args, "lat", echo)
    c.connect()
    room = f"lat-{c.username}"
    c.subscribe(room)
    out = []
    try:
        for i in range(args.count):
            text = f"r{run}-{i}-" + "x" * max(0, args.size - 12)
            t0 = time.perf_counter()
            c.publish(room, text)
            out.append(((echo.wait_for(text) - t0) * 1000, "ms"))
    finally:
        c.close()
    return out


def exp_throughput(args, run):
    echo = Echo()
    c = new_client(args, "tput", echo)
    c.connect()
    room = f"tput-{c.username}"
    c.subscribe(room)
    body = "x" * args.size
    prefix = f"r{run}-"
    try:
        t0 = time.perf_counter()
        for i in range(args.count):
            c.publish(room, f"{prefix}{i:06d}{body}")
        ok, t_last = echo.wait_count(prefix, args.count)
        if not ok:
            raise TimeoutError("not all messages echoed")
    finally:
        c.close()
    secs = t_last - t0
    payload_bits = args.count * (args.size + len(prefix) + 6) * 8
    return [(args.count / secs, "msg/s"), (payload_bits / secs / 1000, "kbit/s")]


def exp_op(args, run):
    """Time to get one chat message accepted by the broker (publish -> PUBACK).

    --mode persistent reuses one connection; --mode new opens TCP + CONNECT for every message.
    """
    out = []
    if args.mode == "persistent":
        c = new_client(args, "op")
        c.connect()
        c.subscribe("op-room")
        try:
            for _ in range(args.count):
                t0 = time.perf_counter()
                c.publish("op-room", "hello", qos=1, wait=True)
                out.append(((time.perf_counter() - t0) * 1000, "ms"))
        finally:
            c.close()
    else:
        for _ in range(args.count):
            t0 = time.perf_counter()
            c = new_client(args, "op", reconnect=False)
            c.connect()
            c.subscribe("op-room")
            c.publish("op-room", "hello", qos=1, wait=True)
            out.append(((time.perf_counter() - t0) * 1000, "ms"))
            c.close()
    return out


def exp_resume(args, run):
    """Delivered % of messages sent while the subscriber's link is down, then resumed."""
    echo = Echo()
    pub = new_client(args, "pub")
    sub = new_client(args, "sub", echo, reconnect=False)
    pub.connect()
    sub.connect()
    room = f"resume-{sub.username}"
    pub.subscribe(room)
    sub.subscribe(room, qos=args.qos)
    sub._drop_link("measure: simulated link loss")
    while sub.connected.is_set():
        time.sleep(0.05)
    time.sleep(0.5)  # let the broker notice the lost connection
    prefix = f"r{run}-"
    for i in range(args.count):
        pub.publish(room, f"{prefix}{i}", qos=1)
    while pub.pending_count():  # all accepted by the broker while sub is offline
        time.sleep(0.05)
    sub.connect(resume=True)
    _, _ = echo.wait_count(prefix, args.count, timeout=5)
    got = sum(k.startswith(prefix) for k in echo.seen)
    pub.close()
    sub.close()
    return [(100.0 * got / args.count, "% delivered")]


EXPERIMENTS = {"rtt": exp_rtt, "latency": exp_latency, "throughput": exp_throughput,
               "op": exp_op, "resume": exp_resume}


def main():
    p = argparse.ArgumentParser(description="PSCP performance experiments")
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--exp", choices=EXPERIMENTS, required=True)
    p.add_argument("--condition", required=True, help="label, e.g. cloud-wifi, lan-wired")
    p.add_argument("--qos", type=int, choices=(0, 1), default=1)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--count", type=int, default=20, help="samples (or messages) per run")
    p.add_argument("--size", type=int, default=64, help="payload bytes (latency, throughput)")
    p.add_argument("--mode", choices=("persistent", "new"), default="persistent", help="for --exp op")
    p.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "raw"))
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args()

    condition = f"{args.condition}/{args.mode}" if args.exp == "op" else args.condition
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{args.exp}.csv")
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new_file:
            w.writeheader()
        for run in range(1, args.runs + 1):
            samples = EXPERIMENTS[args.exp](args, run)
            stamp = datetime.now().isoformat(timespec="seconds")
            for i, (value, unit) in enumerate(samples, 1):
                w.writerow({"timestamp": stamp, "host": socket.gethostname(), "experiment": args.exp,
                            "condition": condition, "qos": args.qos, "run": run, "sample": i,
                            "value": f"{value:.4f}", "unit": unit})
            f.flush()
            vals = [v for v, _ in samples]
            print(f"run {run}/{args.runs}: n={len(vals)} mean={sum(vals) / len(vals):.3f} {samples[0][1]}")
    print(f"appended to {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
