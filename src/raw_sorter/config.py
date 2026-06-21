"""Configuration: load from environment + CLI flags (CLI overrides env), then validate."""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# File classification. Stems are matched case-insensitively; extensions here are lowercase.
JPG_EXTS = {".jpg", ".jpeg"}
RAW_EXTS = {
    ".rw2", ".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".sr2", ".srf",
    ".raf", ".orf", ".rwl", ".pef", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".erf",
    ".mef", ".mos", ".mrw", ".kdc", ".dcr", ".raw", ".gpr",
}

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.I)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: str | float | int) -> float:
    """Parse '90', '30s', '5m', '2h' -> seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    m = _DURATION_RE.match(str(value))
    if not m:
        raise ValueError(f"invalid duration: {value!r}")
    return float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    input: Path
    album: Path
    archive: Path
    fmt: str = "heif"                  # heif | avif
    quality: int = 50
    preset: str = "slow"              # x265 preset (heif only)
    tune: str = "ssim"               # x265 tune (heif only)
    chroma: str = "420"              # 420 | 422 | 444
    color: str = "srgb"              # srgb (convert wide-gamut) | preserve (keep/embed source profile)
    workers: int = 1
    settle_seconds: float = 10.0
    settle_max_seconds: float = 600.0
    poll_interval: float = 2.0
    rescan_interval: float = 300.0
    encode_timeout: float = 300.0
    jpg_disposition: str = "archive"  # archive (beside RAW) | delete
    raw_without_jpg: str = "preview"  # preview | archive | skip
    max_retries: int = 3
    once: bool = False
    dry_run: bool = False
    log_level: str = "info"

    @property
    def out_ext(self) -> str:
        return ".avif" if self.fmt == "avif" else ".heic"

    def validate(self) -> None:
        for label, p in (("INPUT", self.input), ("ALBUM", self.album), ("ARCHIVE", self.archive)):
            if not p.exists():
                raise SystemExit(f"{label} path does not exist: {p}")
            if not p.is_dir():
                raise SystemExit(f"{label} path is not a directory: {p}")
            if not os.access(p, os.W_OK):
                raise SystemExit(f"{label} path is not writable: {p}")
        resolved = {k: v.resolve() for k, v in
                    (("input", self.input), ("album", self.album), ("archive", self.archive))}
        if len({str(v) for v in resolved.values()}) != 3:
            raise SystemExit("INPUT, ALBUM and ARCHIVE must be three distinct directories")
        for label in ("album", "archive"):
            child, parent = resolved[label], resolved["input"]
            if parent == child or parent in child.parents:
                raise SystemExit(f"{label.upper()} must not be nested inside INPUT")
        if self.fmt not in {"heif", "avif"}:
            raise SystemExit(f"FORMAT must be heif or avif, got {self.fmt!r}")
        if self.chroma not in {"420", "422", "444"}:
            raise SystemExit(f"CHROMA must be 420, 422 or 444, got {self.chroma!r}")
        if self.color not in {"srgb", "preserve"}:
            raise SystemExit(f"COLOR must be srgb or preserve, got {self.color!r}")
        if self.jpg_disposition not in {"archive", "delete"}:
            raise SystemExit(f"JPG_DISPOSITION must be archive or delete, got {self.jpg_disposition!r}")
        if self.raw_without_jpg not in {"preview", "archive", "skip"}:
            raise SystemExit(f"RAW_WITHOUT_JPG must be preview, archive or skip, got {self.raw_without_jpg!r}")
        if not (0 <= self.quality <= 100):
            raise SystemExit("QUALITY must be between 0 and 100")
        if self.workers < 1:
            raise SystemExit("WORKERS must be >= 1")


def build_parser() -> argparse.ArgumentParser:
    e = os.environ.get
    p = argparse.ArgumentParser(
        prog="raw-sorter",
        description="Watch a RAW+JPG folder; emit compact HEIF to an album folder and "
                    "move RAW masters to a cold archive. Env vars mirror every flag.",
    )
    p.add_argument("--input", default=e("INPUT_DIR"), help="watched RAW+JPG root (env INPUT_DIR)")
    p.add_argument("--album", default=e("ALBUM_DIR"), help="HEIF-only output, synced (env ALBUM_DIR)")
    p.add_argument("--archive", default=e("ARCHIVE_DIR"), help="cold RAW archive (env ARCHIVE_DIR)")
    p.add_argument("--format", dest="fmt", default=e("FORMAT", "heif"), choices=["heif", "avif"])
    p.add_argument("--quality", type=int, default=int(e("QUALITY", "50")))
    p.add_argument("--preset", default=e("PRESET", "slow"))
    p.add_argument("--tune", default=e("TUNE", "ssim"))
    p.add_argument("--chroma", default=e("CHROMA", "420"), choices=["420", "422", "444"])
    p.add_argument("--color", default=e("COLOR", "srgb"), choices=["srgb", "preserve"])
    p.add_argument("--workers", type=int, default=int(e("WORKERS", "1")))
    p.add_argument("--settle-seconds", type=float, default=parse_duration(e("SETTLE_SECONDS", "10")))
    p.add_argument("--rescan-interval", default=e("RESCAN_INTERVAL", "5m"))
    p.add_argument("--encode-timeout", default=e("ENCODE_TIMEOUT", "5m"))
    p.add_argument("--jpg-disposition", default=e("JPG_DISPOSITION", "archive"),
                   choices=["archive", "delete"])
    p.add_argument("--raw-without-jpg", default=e("RAW_WITHOUT_JPG", "preview"),
                   choices=["preview", "archive", "skip"])
    p.add_argument("--max-retries", type=int, default=int(e("MAX_RETRIES", "3")))
    p.add_argument("--once", action="store_true", default=_env_bool("ONCE", False))
    p.add_argument("--dry-run", action="store_true", default=_env_bool("DRY_RUN", False))
    p.add_argument("--log-level", default=e("LOG_LEVEL", "info"))
    return p


def load(argv: list[str] | None = None) -> Config:
    args = build_parser().parse_args(argv)
    for required in ("input", "album", "archive"):
        if not getattr(args, required):
            raise SystemExit(f"--{required} (or {required.upper()}_DIR) is required")
    # Resolve symlinks once so all later relpath arithmetic is consistent with the paths the
    # filesystem watcher reports (e.g. macOS /tmp -> /private/tmp). A no-op on a real NAS volume.
    cfg = Config(
        input=Path(args.input).resolve(), album=Path(args.album).resolve(),
        archive=Path(args.archive).resolve(),
        fmt=args.fmt, quality=args.quality, preset=args.preset, tune=args.tune,
        chroma=args.chroma, color=args.color, workers=args.workers,
        settle_seconds=args.settle_seconds,
        rescan_interval=parse_duration(args.rescan_interval),
        encode_timeout=parse_duration(args.encode_timeout),
        jpg_disposition=args.jpg_disposition, raw_without_jpg=args.raw_without_jpg,
        max_retries=args.max_retries, once=args.once, dry_run=args.dry_run,
        log_level=args.log_level,
    )
    cfg.validate()
    return cfg
