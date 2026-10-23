"""python -m pscp.client --host <broker-ip> --user alice"""
import argparse
import sys

from ..constants import DEFAULT_PORT, KEEPALIVE
from ..log import setup
from .api import ConnectError, PSCPClient
from .cli import ChatCLI


def main():
    p = argparse.ArgumentParser(description="PSCP chat client")
    p.add_argument("--host", required=True, help="broker IP or hostname")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--user", required=True)
    p.add_argument("--qos", type=int, choices=(0, 1), default=1)
    p.add_argument("--keepalive", type=float, default=KEEPALIVE)
    p.add_argument("--drop-ack", type=float, default=0.0,
                   help="fault injection: probability of dropping each outgoing PUBACK")
    p.add_argument("--log-level", default="INFO", help="level for --log-file")
    p.add_argument("--log-file", help="protocol log (console shows warnings only)")
    a = p.parse_args()

    logger = setup("client", a.log_level, a.log_file, console_level="WARNING")
    client = PSCPClient(a.host, a.user, a.port, qos=a.qos, keepalive=a.keepalive,
                        drop_ack=a.drop_ack, logger=logger)
    try:
        ChatCLI(client).run()
    except (ConnectError, ConnectionError) as e:
        print(f"cannot connect: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
