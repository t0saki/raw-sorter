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
