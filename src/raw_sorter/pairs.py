"""Group files in a directory into per-stem units of {jpg, raw, others}."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import JPG_EXTS, RAW_EXTS

# Names/dirs we create ourselves or that are never photo input.
SKIP_DIR_NAMES = {".tmp", ".quarantine"}
SKIP_FILE_NAMES = {".ds_store"}


@dataclass
class Unit:
    """All files in one directory sharing a case-insensitive stem."""
    directory: Path
    stem: str                      # lower-cased key
    jpgs: list[Path] = field(default_factory=list)
    raws: list[Path] = field(default_factory=list)
    others: list[Path] = field(default_factory=list)

    @property
    def jpg(self) -> Path | None:
        return self.jpgs[0] if len(self.jpgs) == 1 else None

    @property
    def raw(self) -> Path | None:
        return self.raws[0] if len(self.raws) == 1 else None

    @property
    def ambiguous(self) -> bool:
        return len(self.jpgs) > 1 or len(self.raws) > 1

    @property
    def key(self) -> tuple[str, str]:
        return (str(self.directory), self.stem)


def classify_ext(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in JPG_EXTS:
        return "jpg"
    if ext in RAW_EXTS:
        return "raw"
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
        else:
            unit.others.append(entry)
    return unit


def iter_units(root: Path):
    """Walk `root` recursively and yield one Unit per (directory, stem) that has a photo file."""
    seen: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or should_skip(path):
            continue
        if classify_ext(path) is None:
            continue
        key = (str(path.parent), path.stem.lower())
        if key in seen:
            continue
        seen.add(key)
        yield resolve_unit(path.parent, path.stem)
