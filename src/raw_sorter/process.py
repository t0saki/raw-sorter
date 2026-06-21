"""Per-unit state machine: encode -> publish HEIF -> archive RAW -> dispose JPG, in strict order.

Ordering guarantees there is never data loss on a crash: the RAW master and the original JPG stay
in place until their replacements are confirmed on disk. Every step is idempotent, so a retry (or
a restart) simply resumes from wherever it stopped. A fully-completed unit leaves no files in the
input tree, so it never reappears in a rescan.
"""
from __future__ import annotations

import os
import uuid
from enum import Enum
from pathlib import Path

from . import encode, fsops, rawprev, settle, video
from .config import Config
from .log import get
from .pairs import Unit, resolve_unit

log = get("process")


class Result(str, Enum):
    DONE = "done"          # made progress (encoded/archived/disposed something)
    NOOP = "noop"          # nothing to do
    SKIPPED = "skipped"    # intentionally left alone (e.g. raw-only with skip policy)
    NOT_READY = "not_ready"  # source still settling; retry later
    AMBIGUOUS = "ambiguous"  # >1 jpg or >1 raw share a stem; needs a human


def _relpath(directory: Path, root: Path) -> str:
    return os.path.relpath(directory, root)


def process_unit(unit: Unit, cfg: Config) -> Result:
    directory, stem = unit.directory, unit.stem

    # Pairing-aware settle: settle the files we can see, then re-scan to absorb a sibling (e.g.
    # the RAW) that landed while we were settling the JPG. Repeat until no new file appears, so a
    # shot that arrives as two filesystem events is still processed as one unit. Each file is
    # only settled once.
    settled: frozenset[str] = frozenset()
    for _ in range(8):
        if unit.ambiguous:
            log.warning("ambiguous stem %r in %s (jpgs=%s raws=%s) — skipping",
                        stem, directory, [p.name for p in unit.jpgs], [p.name for p in unit.raws])
            return Result.AMBIGUOUS
        sources = [p for p in (unit.jpg, unit.raw, unit.video) if p is not None]
        unsettled = [p for p in sources if p.name not in settled]
        if unsettled and not settle.group_stable(
                unsettled, cfg.settle_seconds, cfg.poll_interval, cfg.settle_max_seconds):
            log.debug("sources for %r not stable yet", stem)
            return Result.NOT_READY
        settled = frozenset(p.name for p in sources)
        unit = resolve_unit(directory, stem)
        if frozenset(p.name for p in unit.jpgs + unit.raws + unit.videos) <= settled:
            break  # nothing new landed

    jpg, raw, video_src = unit.jpg, unit.raw, unit.video
    if jpg is None and raw is None and video_src is None:
        return Result.NOOP

    rel = _relpath(directory, cfg.input)
    album_dir = cfg.album / rel
    archive_dir = cfg.archive / rel

    # Decide the HEIF source: paired/standalone JPG, else the RAW's embedded preview.
    heif_src = None  # ("file", path) | ("preview", path)
    out_stem = None
    if jpg is not None:
        heif_src, out_stem = ("file", jpg), jpg.stem
    elif raw is not None and cfg.raw_without_jpg == "preview":
        heif_src, out_stem = ("preview", raw), raw.stem
    elif raw is not None and cfg.raw_without_jpg == "skip":
        log.debug("raw-only %s with policy=skip — leaving in place", raw.name)
        return Result.SKIPPED

    progressed = False

    # 1) ENCODE + PUBLISH (atomic) -------------------------------------------------
    if heif_src is not None:
        final = album_dir / (out_stem + cfg.out_ext)
        if final.exists() and encode.verify(final):
            log.debug("heif already present: %s", final)
        else:
            kind, src = heif_src
            if cfg.dry_run:
                log.info("[dry-run] would encode %s -> %s", src.name, final)
            else:
                # Unique per attempt so an abandoned (timed-out) encode can't collide with a retry.
                tmp = fsops.tmp_dir_for(final) / f"{out_stem}.{uuid.uuid4().hex}.partial"
                try:
                    if kind == "file":
                        encode.encode_file_timeout(src, tmp, cfg)
                    else:
                        img = rawprev.extract_preview(src)
                        if img is None:
                            raise RuntimeError(f"no embedded preview in {src.name}")
                        encode.encode_pil(img, tmp, cfg)
                    if not encode.verify(tmp):
                        raise RuntimeError("encoded output failed verification")
                    size_mb = tmp.stat().st_size / 1e6
                    fsops.publish(tmp, final)
                    log.info("encoded %s -> %s (%.2f MB)", src.name,
                             os.path.relpath(final, cfg.album), size_mb)
                    progressed = True
                finally:
                    tmp.unlink(missing_ok=True)

    # 2) ARCHIVE RAW (only after the HEIF is safely published) ---------------------
    if raw is not None:
        dst = archive_dir / raw.name
        if cfg.dry_run:
            log.info("[dry-run] would archive %s -> %s", raw.name, dst)
        elif raw.exists():
            fsops.safe_move(raw, fsops.unique_dest(dst))
            log.info("archived RAW %s -> %s", raw.name, os.path.relpath(dst, cfg.archive))
            progressed = True

    # 3) DISPOSE the original JPG (only after HEIF published AND RAW archived) ------
    if jpg is not None and heif_src is not None and heif_src[0] == "file":
        if cfg.dry_run:
            log.info("[dry-run] would %s original JPG %s", cfg.jpg_disposition, jpg.name)
        elif jpg.exists():
            if cfg.jpg_disposition == "delete":
                jpg.unlink()
                log.info("deleted original JPG %s", jpg.name)
            else:
                dst = archive_dir / jpg.name
                fsops.safe_move(jpg, fsops.unique_dest(dst))
                log.info("archived original JPG %s -> %s", jpg.name,
                         os.path.relpath(dst, cfg.archive))
            progressed = True

    # 4) VIDEO: publish a compact derivative to the album, then archive/dispose the original ------
    # Strict order, same as the photo path: the original is only moved/removed once the album file
    # is confirmed on disk, so a crash never loses the master.
    if video_src is not None and cfg.video != "ignore":
        final = album_dir / (video_src.stem + cfg.out_ext_video)
        published = final.exists() and video.verify(final)
        if cfg.dry_run:
            log.info("[dry-run] would %s video %s -> %s, then %s the original",
                     cfg.video, video_src.name, final, cfg.video_disposition)
            progressed = True
        else:
            if not published:
                tmp = (fsops.tmp_dir_for(final)
                       / f"{video_src.stem}.{uuid.uuid4().hex}.partial{cfg.out_ext_video}")
                try:
                    if cfg.video == "transcode":
                        video.transcode(video_src, tmp, cfg)
                        if not video.verify(tmp):
                            raise RuntimeError("transcoded video failed verification")
                    else:  # copy the original verbatim into the album
                        fsops.copy_into(video_src, tmp)
                        if tmp.stat().st_size != video_src.stat().st_size:
                            raise RuntimeError("copied video size mismatch")
                    size_mb = tmp.stat().st_size / 1e6
                    fsops.publish(tmp, final)
                    log.info("video %s %s -> %s (%.2f MB)", cfg.video, video_src.name,
                             os.path.relpath(final, cfg.album), size_mb)
                    published = True
                    progressed = True
                finally:
                    tmp.unlink(missing_ok=True)
            if published and video_src.exists():
                if cfg.video_disposition == "delete":
                    video_src.unlink()
                    log.info("deleted original video %s", video_src.name)
                else:
                    dst = archive_dir / video_src.name
                    fsops.safe_move(video_src, fsops.unique_dest(dst))
                    log.info("archived video %s -> %s", video_src.name,
                             os.path.relpath(dst, cfg.archive))
                progressed = True

    return Result.DONE if progressed or cfg.dry_run else Result.NOOP
