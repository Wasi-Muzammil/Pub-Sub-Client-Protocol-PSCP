"""python -m pscp.broker --host 0.0.0.0 --port 5050"""
import argparse
import signal

from ..constants import DEFAULT_PORT, KEEPALIVE, SESSION_EXPIRY
from ..log import setup
from .server import Broker


def main():
    p = argparse.ArgumentParser(description="PSCP broker")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--max-clients", type=int, default=100)
    p.add_argument("--keepalive", type=float, default=KEEPALIVE)
    p.add_argument("--session-expiry", type=float, default=SESSION_EXPIRY)
    p.add_argument("--drop-ack", type=float, default=0.0,
                   help="fault injection: probability of dropping each outgoing PUBACK")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--log-file")
    a = p.parse_args()

    broker = Broker(a.host, a.port, a.max_clients, a.drop_ack, a.keepalive,
                    session_expiry=a.session_expiry, logger=setup("broker", a.log_level, a.log_file))
    broker.start()
    signal.signal(signal.SIGTERM, lambda *_: broker.stop())  # systemctl stop
    try:
        broker.serve_forever()
    except KeyboardInterrupt:
        broker.stop()


if __name__ == "__main__":
    main()
