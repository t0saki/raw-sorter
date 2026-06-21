"""Group files in a directory into per-stem units of {jpg, raw, others}."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import JPG_EXTS, RAW_EXTS, VIDEO_EXTS

# Directories that are never photo input: our own working dirs plus common NAS system folders
# (Synology @eaDir thumbnails / #recycle / #snapshot, QNAP @Recycle, lost+found). Matched
# case-insensitively. Any dotfile/dotdir is also skipped (see should_skip).
SKIP_DIR_NAMES = {
    ".tmp", ".quarantine",
    "@eadir", "#recycle", "#snapshot", "@tmp", "@recycle", "lost+found",
}
SKIP_FILE_NAMES = {".ds_store"}


@dataclass
class Unit:
    """All files in one directory sharing a case-insensitive stem."""
    directory: Path
    stem: str                      # lower-cased key
    jpgs: list[Path] = field(default_factory=list)
    raws: list[Path] = field(default_factory=list)
    videos: list[Path] = field(default_factory=list)
    others: list[Path] = field(default_factory=list)

    @property
    def jpg(self) -> Path | None:
        return self.jpgs[0] if len(self.jpgs) == 1 else None

    @property
    def raw(self) -> Path | None:
        return self.raws[0] if len(self.raws) == 1 else None

    @property
    def video(self) -> Path | None:
        return self.videos[0] if len(self.videos) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.jpgs) > 1 or len(self.raws) > 1 or len(self.videos) > 1

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.directory), self.stem)


def classify_ext(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in JPG_EXTS:
        return "jpg"
    if ext in RAW_EXTS:
        return "raw"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def should_skip(path: Path) -> bool:
    if path.name.lower() in SKIP_FILE_NAMES or path.name.startswith("."):
        return True
    return any(part.lower() in SKIP_DIR_NAMES for part in path.parts)


def resolve_unit(directory: Path, stem: str) -> Unit:
    """Re-read `directory` fresh and collect every photo file whose stem matches (case-insensitive)."""
    unit = Unit(directory=directory, stem=stem.lower())
    if not directory.is_dir():
        return unit
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or should_skip(entry):
            continue
        if entry.stem.lower() != unit.stem:
            continue
        kind = classify_ext(entry)
        if kind == "jpg":
            unit.jpgs.append(entry)
        elif kind == "raw":
            unit.raws.append(entry)
        elif kind == "video":
            unit.videos.append(entry)
        else:
            unit.others.append(entry)
    return unit


def iter_units(root: Path):
    """Walk `root` recursively and yield one Unit per (directory, stem) that has a photo file.

    Skip dirs (`@eaDir`, dotdirs, …) are pruned from the traversal so we never descend into them.
    """
    seen: set[tuple[str, str]] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d.lower() not in SKIP_DIR_NAMES]
        directory = Path(dirpath)
        for name in sorted(filenames):
            path = directory / name
            if should_skip(path) or classify_ext(path) is None:
                continue
            key = (str(directory), path.stem.lower())
            if key in seen:
                continue
            seen.add(key)
            yield resolve_unit(directory, path.stem)
