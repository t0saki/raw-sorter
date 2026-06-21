"""File-stability detection: wait until files stop changing before processing them.

On a NAS the input often arrives over SMB/AFP or an `mv` from a card dump, so a file can be
visible while still growing. We require size+mtime to stay constant across `settle_seconds`.
A whole group of files (e.g. a JPG+RAW pair) is settled together in a single window rather than
one after another, so a pair settles in `settle_seconds`, not `2 * settle_seconds`.
"""
from __future__ import annotations

import time
from pathlib import Path


def _sig(path: Path):
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return None


def group_stable(paths, settle_seconds: float, poll_interval: float, max_wait: float) -> bool:
    """Block until every path in `paths` is unchanged for `settle_seconds` simultaneously.

    Returns True when all are stable, or False if any vanished or `max_wait` elapsed first.
    """
    paths = list(paths)
    if not paths:
        return True
    poll = max(0.1, min(poll_interval, settle_seconds if settle_seconds > 0 else poll_interval))
    deadline = time.monotonic() + max_wait
    last = {p: _sig(p) for p in paths}
    if any(v is None for v in last.values()):
        return False
    stable_since = time.monotonic()
    while True:
        time.sleep(poll)
        cur = {p: _sig(p) for p in paths}
        if any(v is None for v in cur.values()):
            return False
        if cur == last:
            if time.monotonic() - stable_since >= settle_seconds:
                return True
        else:
            last = cur
            stable_since = time.monotonic()
        if time.monotonic() > deadline:
            return False
