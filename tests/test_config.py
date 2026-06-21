import pytest

from raw_sorter.config import Config, parse_duration


@pytest.mark.parametrize("value,expected", [
    ("90", 90), ("30s", 30), ("5m", 300), ("2h", 7200), ("1d", 86400), (45, 45), (1.5, 1.5),
])
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


def test_parse_duration_invalid():
    with pytest.raises(ValueError):
        parse_duration("soon")


def _cfg(tmp_path, **kw):
    (tmp_path / "in").mkdir(exist_ok=True)
    (tmp_path / "album").mkdir(exist_ok=True)
    (tmp_path / "archive").mkdir(exist_ok=True)
    base = dict(input=tmp_path / "in", album=tmp_path / "album", archive=tmp_path / "archive")
    base.update(kw)
    return Config(**base)


def test_validate_ok(tmp_path):
    _cfg(tmp_path).validate()


def test_validate_rejects_nested_album_in_input(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "album").mkdir()
    (tmp_path / "archive").mkdir()
    cfg = Config(input=tmp_path / "in", album=tmp_path / "in" / "album", archive=tmp_path / "archive")
    with pytest.raises(SystemExit):
        cfg.validate()


def test_validate_rejects_missing_dir(tmp_path):
    cfg = Config(input=tmp_path / "nope", album=tmp_path, archive=tmp_path)
    with pytest.raises(SystemExit):
        cfg.validate()


def test_validate_rejects_bad_quality(tmp_path):
    with pytest.raises(SystemExit):
        _cfg(tmp_path, quality=200).validate()


def test_out_ext(tmp_path):
    assert _cfg(tmp_path, fmt="heif").out_ext == ".heic"
    assert _cfg(tmp_path, fmt="avif").out_ext == ".avif"


def test_video_defaults(tmp_path):
    cfg = _cfg(tmp_path, video="ignore")   # ignore so validate() skips the ffmpeg probe
    assert cfg.video_crf == 30
    assert cfg.video_preset == "fast"
    assert cfg.video_height == 1080
    assert cfg.video_bitdepth == 10
    assert cfg.video_acodec == "aac"
    assert cfg.out_ext_video == ".mp4"
    assert cfg.video_pix_fmt == "yuv420p10le"
    cfg.validate()


def test_video_pix_fmt_8bit(tmp_path):
    assert _cfg(tmp_path, video="ignore", video_bitdepth=8).video_pix_fmt == "yuv420p"


def test_validate_rejects_bad_video_enum(tmp_path):
    with pytest.raises(SystemExit):
        _cfg(tmp_path, video="reencode").validate()
    with pytest.raises(SystemExit):
        _cfg(tmp_path, video="ignore", video_acodec="opus").validate()
    with pytest.raises(SystemExit):
        _cfg(tmp_path, video="ignore", video_bitdepth=12).validate()
    with pytest.raises(SystemExit):
        _cfg(tmp_path, video="ignore", video_crf=99).validate()
