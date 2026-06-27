"""pixi-repair: colour HEIFs must be re-tagged 3-channel; monochrome must be left alone."""
from pathlib import Path

import pillow_heif
import pytest
from PIL import Image

from raw_sorter import encode, heif_pixi
from raw_sorter.config import Config

pillow_heif.register_heif_opener()


def _pixi_channels(data: bytes) -> int:
    i = data.find(b"pixi")
    assert i > 0, "no pixi box"
    return data[i + 8]                                  # size(4)+'pixi'(4) -> ver/flags(4) -> num


def _colour_heic(path: Path) -> bytes:
    img = Image.new("RGB", (320, 240))
    px = img.load()
    for y in range(240):
        for x in range(320):
            px[x, y] = (x % 256, y % 256, (x + y) % 256)
    img.save(path, format="HEIF", quality=50)
    return path.read_bytes()


def _grey_heic(path: Path) -> bytes:
    img = Image.new("L", (320, 240))
    px = img.load()
    for y in range(240):
        for x in range(320):
            px[x, y] = (x + y) % 256
    img.save(path, format="HEIF", quality=50)
    return path.read_bytes()


def test_colour_heic_is_retagged_three_channel(tmp_path):
    data = _colour_heic(tmp_path / "c.heic")
    assert _pixi_channels(data) == 1                    # the libheif bug we are fixing
    fixed = heif_pixi.fix_pixi(data)
    assert fixed is not None
    assert _pixi_channels(fixed) == 3


def test_repaired_file_still_decodes_to_the_same_size(tmp_path):
    p = tmp_path / "c.heic"
    fixed = heif_pixi.fix_pixi(_colour_heic(p))
    p.write_bytes(fixed)
    with Image.open(p) as im:
        im.load()
        assert im.size == (320, 240)                    # iloc offsets survived the splice


def test_monochrome_heic_is_left_untouched(tmp_path):
    data = _grey_heic(tmp_path / "g.heic")
    assert _pixi_channels(data) == 1                    # correct for a real single-channel image
    assert heif_pixi.fix_pixi(data) is None             # auto-detect must not touch it


def test_fix_is_idempotent(tmp_path):
    data = _colour_heic(tmp_path / "c.heic")
    once = heif_pixi.fix_pixi(data)
    assert heif_pixi.fix_pixi(once) is None             # already 3-channel -> no-op


def test_assume_colour_overrides_detection(tmp_path):
    colour = _colour_heic(tmp_path / "c.heic")
    grey = _grey_heic(tmp_path / "g.heic")
    assert heif_pixi.fix_pixi(colour, assume_colour=False) is None   # forced skip
    assert heif_pixi.fix_pixi(grey, assume_colour=True) is not None  # forced fix


def test_fix_pixi_file_rewrites_in_place_and_reports(tmp_path):
    p = tmp_path / "c.heic"
    _colour_heic(p)
    assert heif_pixi.fix_pixi_file(p) is True
    assert _pixi_channels(p.read_bytes()) == 3
    assert heif_pixi.fix_pixi_file(p) is False          # second pass is a clean no-op


def test_dry_run_does_not_write(tmp_path):
    p = tmp_path / "c.heic"
    before = _colour_heic(p)
    assert heif_pixi.fix_pixi_file(p, dry_run=True) is True
    assert p.read_bytes() == before                     # untouched on disk


def test_cli_walks_a_directory(tmp_path, capsys):
    _colour_heic(tmp_path / "a.heic")
    (tmp_path / "sub").mkdir()
    _colour_heic(tmp_path / "sub" / "b.heic")
    _grey_heic(tmp_path / "g.heic")                     # must be skipped
    rc = heif_pixi.main([str(tmp_path)])
    assert rc == 0
    assert _pixi_channels((tmp_path / "a.heic").read_bytes()) == 3
    assert _pixi_channels((tmp_path / "sub" / "b.heic").read_bytes()) == 3
    assert _pixi_channels((tmp_path / "g.heic").read_bytes()) == 1
    assert "fixed 2" in capsys.readouterr().err


def test_encode_pipeline_emits_three_channel_pixi(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (640, 480), (180, 40, 90)).save(src, quality=92)
    dst = tmp_path / "out.heic"
    cfg = Config(input=tmp_path, album=tmp_path, archive=tmp_path)
    encode.encode_file(src, dst, cfg)
    assert encode.verify(dst)
    assert _pixi_channels(dst.read_bytes()) == 3        # the whole point: colour on iOS


@pytest.mark.parametrize("bad", [b"not a heif", b"", b"\x00\x00\x00\x18ftypheic"])
def test_fix_pixi_is_safe_on_garbage(bad):
    assert heif_pixi.fix_pixi(bad) is None               # never raises on malformed input
