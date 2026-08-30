"""Shared logging: console + rotating file, under the 'tm' logger tree."""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import config

_configured = False


def setup(level=None):
    global _configured
    if _configured:
        return logging.getLogger("tm")
    level = (level or config.LOG_LEVEL).upper()
    os.makedirs(config.LOG_DIR, exist_ok=True)

    root = logging.getLogger("tm")
    root.setLevel(level)
    root.propagate = False

    console_fmt = logging.Formatter("%(asctime)s %(levelname)-5s | %(message)s", "%H:%M:%S")
    file_fmt = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s | %(message)s")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(console_fmt)
    root.addHandler(ch)

    fh = RotatingFileHandler(
        os.path.join(config.LOG_DIR, "tm_scraper.log"),
        maxBytes=5_000_000, backupCount=3,
    )
    fh.setFormatter(file_fmt)
    root.addHandler(fh)

    _configured = True
    return root


def get(name="tm"):
    setup()
    return logging.getLogger(name if name.startswith("tm") else f"tm.{name}")


def fmt_secs(seconds):
    """1234 -> '20m34s'."""
    seconds = int(max(seconds, 0))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"
