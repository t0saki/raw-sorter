"""Configuration: load from environment + CLI flags (CLI overrides env), then validate."""
from __future__ import annotations

import argparse
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .log import get

log = get("config")

# File classification. Stems are matched case-insensitively; extensions here are lowercase.
JPG_EXTS = {".jpg", ".jpeg"}
RAW_EXTS = {
    ".rw2", ".dng", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".sr2", ".srf",
    ".raf", ".orf", ".rwl", ".pef", ".srw", ".x3f", ".3fr", ".fff", ".iiq", ".erf",
    ".mef", ".mos", ".mrw", ".kdc", ".dcr", ".raw", ".gpr",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".m2t", ".avi", ".3gp", ".3g2",
    ".mkv", ".webm", ".wmv", ".flv", ".mpg", ".mpeg",
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
    # --- video ---
    video: str = "transcode"          # transcode (1080p HEVC/AAC to album) | copy (original to album) | ignore
    video_disposition: str = "archive"  # archive the original beside RAW | delete it
    video_crf: int = 30               # x265 CRF (higher = smaller)
    video_preset: str = "fast"        # x265 preset (faster on a weak NAS)
    video_height: int = 1080          # cap output height (never upscales)
    video_bitdepth: int = 10          # 8 (yuv420p) | 10 (yuv420p10le, Main10 — more efficient)
    video_x265_params: str = "aq-mode=3:aq-strength=1.0:psy-rd=2.0"  # camera-footage tuning
    video_acodec: str = "aac"         # aac (max compatibility in MP4) | copy
    video_abitrate: str = "128k"
    video_timeout: float = 7200.0     # a transcode is far slower than an image encode
    max_retries: int = 3
    once: bool = False
    dry_run: bool = False
    log_level: str = "info"

    @property
    def out_ext(self) -> str:
        return ".avif" if self.fmt == "avif" else ".heic"

    @property
    def out_ext_video(self) -> str:
        return ".mp4"

    @property
    def video_pix_fmt(self) -> str:
        return "yuv420p10le" if self.video_bitdepth == 10 else "yuv420p"

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
        if self.video not in {"transcode", "copy", "ignore"}:
            raise SystemExit(f"VIDEO must be transcode, copy or ignore, got {self.video!r}")
        if self.video_disposition not in {"archive", "delete"}:
            raise SystemExit(f"VIDEO_DISPOSITION must be archive or delete, got {self.video_disposition!r}")
        if self.video_acodec not in {"aac", "copy"}:
            raise SystemExit(f"VIDEO_ACODEC must be aac or copy, got {self.video_acodec!r}")
        if self.video_bitdepth not in {8, 10}:
            raise SystemExit(f"VIDEO_BITDEPTH must be 8 or 10, got {self.video_bitdepth!r}")
        if not (0 <= self.video_crf <= 51):
            raise SystemExit("VIDEO_CRF must be between 0 and 51")
        # Video transcoding needs ffmpeg/ffprobe on PATH. If they're missing, don't crash the whole
        # tool — disable video so the image pipeline keeps working — but say so loudly.
        if self.video == "transcode":
            missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
            if missing:
                log.warning("VIDEO=transcode but %s not found on PATH — disabling video processing "
                            "(images still processed). Install ffmpeg or set VIDEO=ignore.",
                            "/".join(missing))
                self.video = "ignore"


def build_parser() -> argparse.ArgumentParser:
    e = os.environ.get
    p = argparse.ArgumentParser(
        prog="raw-sorter",
        description="Watch a RAW+JPG folder; emit compact HEIF to an album folder and "
                    "move RAW masters to a cold archive. Env vars mirror every flag.",
    )
    p.add_argument("--input", default=e("INPUT_DIR", "/input"),
                   help="watched RAW+JPG root (env INPUT_DIR, default /input)")
    p.add_argument("--album", default=e("ALBUM_DIR", "/album"),
                   help="HEIF-only output, synced (env ALBUM_DIR, default /album)")
    p.add_argument("--archive", default=e("ARCHIVE_DIR", "/archive"),
                   help="cold RAW archive (env ARCHIVE_DIR, default /archive)")
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
    p.add_argument("--video", default=e("VIDEO", "transcode"), choices=["transcode", "copy", "ignore"])
    p.add_argument("--video-disposition", default=e("VIDEO_DISPOSITION", "archive"),
                   choices=["archive", "delete"])
    p.add_argument("--video-crf", type=int, default=int(e("VIDEO_CRF", "30")))
    p.add_argument("--video-preset", default=e("VIDEO_PRESET", "fast"))
    p.add_argument("--video-height", type=int, default=int(e("VIDEO_HEIGHT", "1080")))
    p.add_argument("--video-bitdepth", type=int, default=int(e("VIDEO_BITDEPTH", "10")), choices=[8, 10])
    p.add_argument("--video-x265-params",
                   default=e("VIDEO_X265_PARAMS", "aq-mode=3:aq-strength=1.0:psy-rd=2.0"))
    p.add_argument("--video-acodec", default=e("VIDEO_ACODEC", "aac"), choices=["aac", "copy"])
    p.add_argument("--video-abitrate", default=e("VIDEO_ABITRATE", "128k"))
    p.add_argument("--video-timeout", default=e("VIDEO_TIMEOUT", "2h"))
    p.add_argument("--max-retries", type=int, default=int(e("MAX_RETRIES", "3")))
    p.add_argument("--once", action="store_true", default=_env_bool("ONCE", False))
    p.add_argument("--dry-run", action="store_true", default=_env_bool("DRY_RUN", False))
    p.add_argument("--log-level", default=e("LOG_LEVEL", "info"))
    return p


def load(argv: list[str] | None = None) -> Config:
    args = build_parser().parse_args(argv)
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
        video=args.video, video_disposition=args.video_disposition, video_crf=args.video_crf,
        video_preset=args.video_preset, video_height=args.video_height,
        video_bitdepth=args.video_bitdepth, video_x265_params=args.video_x265_params,
        video_acodec=args.video_acodec, video_abitrate=args.video_abitrate,
        video_timeout=parse_duration(args.video_timeout),
        max_retries=args.max_retries, once=args.once, dry_run=args.dry_run,
        log_level=args.log_level,
    )
    cfg.validate()
    return cfg
