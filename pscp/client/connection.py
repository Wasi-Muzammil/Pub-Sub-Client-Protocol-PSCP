"""Opening the TCP connection to the broker with exponential back-off (failure scenario 2)."""
import socket
import threading

from ..constants import BACKOFF


def open_with_backoff(host, port, log, backoff=BACKOFF, timeout=5.0, stop=None):
    """Try immediately, then after each delay in `backoff`. Raises ConnectionError when all fail."""
    stop = stop or threading.Event()
    delays = (0, *backoff)
    for attempt, delay in enumerate(delays, 1):
        if delay:
            log.warning("RETRY connecting to %s:%d in %ds", host, port, delay)
            if stop.wait(delay):
                break
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            log.info("TCP connected to %s:%d (attempt %d)", host, port, attempt)
            return sock
        except OSError as e:
            log.warning("CONNECT attempt %d/%d to %s:%d failed: %s", attempt, len(delays), host, port, e)
    raise ConnectionError(f"broker {host}:{port} unreachable after {len(delays)} attempts")
