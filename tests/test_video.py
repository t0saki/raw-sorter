import json
import shutil
import subprocess
from pathlib import Path

import pytest

from raw_sorter import video
from raw_sorter.config import Config

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _cfg(**kw) -> Config:
    base = dict(input=Path("/in"), album=Path("/a"), archive=Path("/r"), video="ignore")
    base.update(kw)
    return Config(**base)


def test_build_cmd_has_the_important_flags():
    cmd = video.build_cmd(Path("in.MP4"), Path("out.mp4"), _cfg())
    s = " ".join(cmd)
    assert "libx265" in s
    assert "-tag:v hvc1" in s
    assert "scale=-2:'min(1080,ih)':flags=lanczos" in s
    assert "yuv420p10le" in s          # default 10-bit Main10
    assert "+faststart" in s
    assert "-c:a aac" in s
    assert "-crf 30" in s
    # camera-footage tuning + forced BT.709 VUI both ride in -x265-params
    i = cmd.index("-x265-params")
    params = cmd[i + 1]
    assert "aq-mode=3" in params
    assert "colorprim=bt709" in params and "transfer=bt709" in params and "colormatrix=bt709" in params


def test_build_cmd_8bit_and_height():
    cmd = video.build_cmd(Path("in.MP4"), Path("out.mp4"), _cfg(video_bitdepth=8, video_height=720))
    s = " ".join(cmd)
    assert "yuv420p10le" not in s and "yuv420p" in s
    assert "min(720,ih)" in s


def test_build_cmd_audio_copy():
    cmd = video.build_cmd(Path("in.MP4"), Path("out.mp4"), _cfg(video_acodec="copy"))
    s = " ".join(cmd)
    assert "-c:a copy" in s
    assert "-b:a" not in s


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_transcode_end_to_end(tmp_path):
    src = tmp_path / "C0001.MP4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc2=size=3840x2160:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
         "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(src)],
        check=True,
    )
    dst = tmp_path / "out.mp4"
    cfg = _cfg(video="transcode", video_preset="ultrafast")  # fast for the test
    video.transcode(src, dst, cfg)
    assert video.verify(dst)

    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(dst)],
        capture_output=True, text=True, check=True,
    ).stdout)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert vstream["codec_name"] == "hevc"
    assert vstream["codec_tag_string"] == "hvc1"
    assert vstream["height"] <= 1080
    assert vstream["color_primaries"] == "bt709" and vstream["color_transfer"] == "bt709"
    assert any(s["codec_type"] == "audio" for s in info["streams"])


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_verify_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert not video.verify(bad)
    assert not video.verify(tmp_path / "missing.mp4")
