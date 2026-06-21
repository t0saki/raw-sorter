"""Coordinator: a bounded worker pool fed by three sources — a startup sweep, live filesystem
events (watchdog), and a periodic full rescan that backstops missed inotify events on NAS mounts.
Units are de-duplicated by (directory, stem) so the three sources never double-process one."""
from __future__ import annotations

import concurrent.futures
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import fsops
from .config import Config
from .log import get
from .pairs import classify_ext, iter_units, resolve_unit, should_skip
from .process import Result, process_unit

log = get("watch")


class Coordinator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=cfg.workers, thread_name_prefix="rs-worker")
        self.lock = threading.Lock()
        self.pending: set[tuple[str, str]] = set()   # queued or running
        self.failed: set[tuple[str, str]] = set()     # gave up after max_retries
        self.retries: dict[tuple[str, str], int] = {}
        self.futures: set[concurrent.futures.Future] = set()
        self.stopping = False

    def submit(self, directory: Path, stem: str) -> None:
        key = (str(directory), stem.lower())
        with self.lock:
            if self.stopping or key in self.pending or key in self.failed:
                return
            self.pending.add(key)
            fut = self.pool.submit(self._run, directory, stem, key)
            self.futures.add(fut)
            fut.add_done_callback(lambda f: self._discard_future(f))

    def _discard_future(self, fut) -> None:
        with self.lock:
            self.futures.discard(fut)

    def _run(self, directory: Path, stem: str, key) -> None:
        try:
            result = process_unit(resolve_unit(directory, stem), self.cfg)
            if result != Result.NOT_READY:
                with self.lock:
                    self.retries.pop(key, None)
        except Exception as exc:  # noqa: BLE001 — isolation: one bad unit must not stop the loop
            self._handle_failure(directory, stem, key, exc)
        finally:
            with self.lock:
                self.pending.discard(key)

    def _handle_failure(self, directory: Path, stem: str, key, exc: Exception) -> None:
        with self.lock:
            n = self.retries.get(key, 0) + 1
            self.retries[key] = n
            give_up = n >= self.cfg.max_retries
            if give_up:
                self.retries.pop(key, None)
                self.failed.add(key)
        if give_up:
            u = resolve_unit(directory, stem)
            files = [p.name for p in u.jpgs + u.raws]
            log.error("giving up on %r in %s after %d attempts (%s): %s — left in place for inspection",
                      stem, directory, n, files, exc)
        else:
            log.warning("unit %r failed (attempt %d/%d): %s — will retry",
                        stem, n, self.cfg.max_retries, exc)

    def sweep(self) -> int:
        count = 0
        for unit in iter_units(self.cfg.input):
            self.submit(unit.directory, unit.stem)
            count += 1
        return count

    def drain(self) -> None:
        while True:
            with self.lock:
                fs = list(self.futures)
                pending = bool(self.pending)
            if not fs and not pending:
                return
            if fs:
                concurrent.futures.wait(fs, timeout=0.5)
            else:
                time.sleep(0.1)

    def shutdown(self) -> None:
        with self.lock:
            self.stopping = True
        self.pool.shutdown(wait=True)


class _Handler(FileSystemEventHandler):
    def __init__(self, coord: Coordinator):
        self.coord = coord

    def _consider(self, raw_path: str) -> None:
        path = Path(raw_path)
        if should_skip(path) or classify_ext(path) is None:
            return
        self.coord.submit(path.parent, path.stem)

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        self._consider(event.src_path)
        dest = getattr(event, "dest_path", None)
        if dest:
            self._consider(dest)


def run_once(cfg: Config) -> None:
    coord = Coordinator(cfg)
    log.info("one-shot run over %s", cfg.input)
    for _ in range(3):  # a couple of passes to pick up anything that was still settling
        if coord.sweep() == 0:
            break
        coord.drain()
    coord.shutdown()
    log.info("one-shot run complete")


def run_watch(cfg: Config, stop_event: threading.Event) -> None:
    coord = Coordinator(cfg)
    log.info("watching %s (workers=%d, rescan=%.0fs)", cfg.input, cfg.workers, cfg.rescan_interval)

    n = coord.sweep()
    log.info("startup sweep queued %d unit(s)", n)

    observer = Observer()
    observer.schedule(_Handler(coord), str(cfg.input), recursive=True)
    observer.start()

    try:
        next_rescan = time.monotonic() + cfg.rescan_interval
        while not stop_event.is_set():
            stop_event.wait(timeout=1.0)
            if time.monotonic() >= next_rescan:
                queued = coord.sweep()
                if queued:
                    log.debug("periodic rescan queued %d unit(s)", queued)
                next_rescan = time.monotonic() + cfg.rescan_interval
    finally:
        log.info("shutting down…")
        observer.stop()
        observer.join(timeout=5)
        coord.shutdown()
        log.info("stopped")
