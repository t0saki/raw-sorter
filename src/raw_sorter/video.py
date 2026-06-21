"""Video transcode via ffmpeg (subprocess): camera footage -> a compact 1080p HEVC/AAC MP4.

Unlike the image path (in-process libheif/libraw), video transcoding shells out to ffmpeg: it is
the right tool for the job, reliably ships libx265 in Debian, and a subprocess can be cleanly
killed on timeout. The album only ever sees a finished file (the caller encodes to a `.tmp`
sibling and atomically renames), exactly like the HEIF path.

Defaults are tuned for real-world camera footage: 10-bit Main10 (more efficient + no banding even
from an 8-bit source), Lanczos downscale, adaptive quantisation, and correct BT.709 tagging. We do
NOT use an x265 `tune` (ssim/psnr would disable psy-rd and hurt perceived quality).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import Config
from .log import get

log = get("video")

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Force BT.709 SDR into the HEVC SPS VUI. ffmpeg's -color_* flags alone only tag the matrix in the
# container; writing the VUI via x265 is what makes primaries+transfer actually stick for players.
_COLOR_VUI = "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited"


def build_cmd(src: Path, dst: Path, cfg: Config) -> list[str]:
    """Construct the ffmpeg command line (pure function — no side effects)."""
    # Downscale to at most `video_height` lines, keep aspect, even width, sharp Lanczos. Never upscale.
    vf = f"scale=-2:'min({cfg.video_height},ih)':flags=lanczos"
    x265_params = f"{cfg.video_x265_params}:{_COLOR_VUI}" if cfg.video_x265_params else _COLOR_VUI
    cmd = [
        FFMPEG, "-hide_banner", "-nostdin", "-y",
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",          # first video + first audio (audio optional)
        "-vf", vf,
        "-c:v", "libx265", "-crf", str(cfg.video_crf), "-preset", cfg.video_preset,
        "-pix_fmt", cfg.video_pix_fmt,
        "-x265-params", x265_params,
        "-tag:v", "hvc1",                            # so Apple/Synology/Google recognise the HEVC
        "-colorspace", "bt709", "-color_primaries", "bt709",
        "-color_trc", "bt709", "-color_range", "tv",
    ]
    if cfg.video_acodec == "copy":
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", cfg.video_acodec, "-b:a", cfg.video_abitrate]
    cmd += [
        "-map_metadata", "0",                        # carry creation_time / GPS / etc.
        "-movflags", "+faststart",                   # moov atom up front -> progressive cloud preview
        str(dst),
    ]
    return cmd


def transcode(src: Path, dst: Path, cfg: Config) -> None:
    """Run ffmpeg, giving up (and killing it) after cfg.video_timeout seconds."""
    cmd = build_cmd(src, dst, cfg)
    log.debug("ffmpeg: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=cfg.video_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"transcode of {src.name} exceeded {cfg.video_timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}) on {src.name}:\n{tail}")


def verify(dst: Path) -> bool:
    """Confirm the output is a real, non-trivial video file with a decodable stream and duration."""
    try:
        if dst.stat().st_size <= 0:
            return False
    except OSError:
        return False
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(dst)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if out.returncode != 0:
        return False
    try:
        info = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return False
    has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    try:
        duration = float(info.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return has_video and duration > 0.0
