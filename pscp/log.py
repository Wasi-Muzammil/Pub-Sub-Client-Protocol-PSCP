"""One log format for broker and client, so logs from different hosts can be lined up (R7 evidence)."""
import logging
import sys

FORMAT = "%(asctime)s.%(msecs)03d %(name)-6s %(levelname)-7s %(message)s"
DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup(name, level="INFO", logfile=None, console_level=None):
    """console_level lets the chat CLI keep the terminal quiet while the file gets every message."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger
    fmt = logging.Formatter(FORMAT, DATEFMT)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level or level)
    console.setFormatter(fmt)
    logger.addHandler(console)
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
