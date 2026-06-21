"""Entry point: parse config, then run a one-shot sweep or the continuous watcher."""
from __future__ import annotations

import signal
import threading

from . import __version__, config, log, watch


def main(argv: list[str] | None = None) -> int:
    cfg = config.load(argv)
    log.setup(cfg.log_level)
    logger = log.get()
    logger.info("raw-sorter %s starting", __version__)
    logger.info("input=%s album=%s archive=%s", cfg.input, cfg.album, cfg.archive)
    logger.info("format=%s quality=%d preset=%s chroma=%s color=%s jpg=%s raw-only=%s",
                cfg.fmt, cfg.quality, cfg.preset, cfg.chroma, cfg.color,
                cfg.jpg_disposition, cfg.raw_without_jpg)
    if cfg.dry_run:
        logger.info("DRY-RUN: no files will be written, moved or deleted")

    if cfg.once:
        watch.run_once(cfg)
        return 0

    stop = threading.Event()

    def _handle(signum, _frame):
        logger.info("received signal %s", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    watch.run_watch(cfg, stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
