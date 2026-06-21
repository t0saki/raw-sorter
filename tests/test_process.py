import shutil
import subprocess
from pathlib import Path

import pytest

from raw_sorter.config import Config
from raw_sorter.pairs import resolve_unit
from raw_sorter.process import Result, process_unit

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _dirs(tmp_path):
    for name in ("in", "album", "archive"):
        (tmp_path / name).mkdir(exist_ok=True)
    return tmp_path / "in", tmp_path / "album", tmp_path / "archive"


def _make_clip(dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc2=size=1920x1080:rate=30", "-t", "1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)],
        check=True,
    )


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_video_only_unit_transcodes_and_archives(tmp_path):
    inp, album, archive = _dirs(tmp_path)
    _make_clip(inp / "trip" / "C0001.MP4")
    cfg = Config(input=inp, album=album, archive=archive,
                 video_preset="ultrafast", settle_seconds=0.0, poll_interval=0.05)

    result = process_unit(resolve_unit(inp / "trip", "c0001"), cfg)
    assert result == Result.DONE
    assert (album / "trip" / "C0001.mp4").exists()        # derivative published to album
    assert (archive / "trip" / "C0001.MP4").exists()      # original archived
    assert not (inp / "trip" / "C0001.MP4").exists()      # consumed from input

    # idempotent: re-running finds nothing left to do
    assert process_unit(resolve_unit(inp / "trip", "c0001"), cfg) == Result.NOOP


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_video_disposition_delete(tmp_path):
    inp, album, archive = _dirs(tmp_path)
    _make_clip(inp / "C0002.MP4")
    cfg = Config(input=inp, album=album, archive=archive, video_disposition="delete",
                 video_preset="ultrafast", settle_seconds=0.0, poll_interval=0.05)

    assert process_unit(resolve_unit(inp, "c0002"), cfg) == Result.DONE
    assert (album / "C0002.mp4").exists()
    assert not (archive / "C0002.MP4").exists()           # original deleted, not archived
    assert not (inp / "C0002.MP4").exists()
