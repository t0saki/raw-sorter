"""Filesystem operations: atomic publish and crash-safe moves that work across volumes.

All durable writes go through here. The album folder must only ever contain finished files, so
we encode to a `.tmp` sibling and atomically rename into place.
"""
from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def fsync_path(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def tmp_dir_for(final: Path) -> Path:
    d = final.parent / ".tmp"
    ensure_dir(d)
    return d


def publish(tmp: Path, final: Path) -> None:
    """Atomically move a finished temp file to its final path (same filesystem expected)."""
    ensure_dir(final.parent)
    fsync_path(tmp)
    try:
        os.replace(tmp, final)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        _copy_then_replace(tmp, final)
        tmp.unlink(missing_ok=True)
    fsync_dir(final.parent)


def _copy_then_replace(src: Path, dst: Path) -> None:
    staging = dst.parent / (dst.name + ".partial")
    shutil.copy2(src, staging)
    fsync_path(staging)
    os.replace(staging, dst)


def copy_into(src: Path, dst: Path) -> None:
    """Full byte-copy of src to dst (metadata preserved). Used to stage a verbatim album copy."""
    ensure_dir(dst.parent)
    shutil.copy2(src, dst)
    fsync_path(dst)


def safe_move(src: Path, dst: Path) -> None:
    """Move src->dst, preferring an atomic rename, falling back to copy+verify+delete across volumes."""
    ensure_dir(dst.parent)
    try:
        os.replace(src, dst)
        fsync_dir(dst.parent)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    staging = dst.parent / (dst.name + ".partial")
    shutil.copy2(src, staging)
    fsync_path(staging)
    if staging.stat().st_size != src.stat().st_size:
        staging.unlink(missing_ok=True)
        raise OSError(f"size mismatch copying {src} -> {dst}")
    os.replace(staging, dst)
    fsync_dir(dst.parent)
    src.unlink(missing_ok=True)


def unique_dest(dst: Path) -> Path:
    """If dst exists, append ' (2)', ' (3)', ... to avoid clobbering a different file."""
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    n = 2
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1
