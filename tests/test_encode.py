from pathlib import Path

import pytest
from PIL import Image

from raw_sorter import encode
from raw_sorter.config import Config


def _cfg(**kw) -> Config:
    base = dict(input=Path("/in"), album=Path("/album"), archive=Path("/archive"))
    base.update(kw)
    return Config(**base)


def test_downscale_passes_small_image_through_untouched():
    # 24 MP source under the 25 MP cap must be returned as-is (same object), so a normal photo
    # never pays a resample.
    img = Image.new("RGB", (6000, 4000))
    out = encode._downscale_if_huge(img, _cfg(max_megapixels=25, target_megapixels=24))
    assert out is img


def test_downscale_shrinks_oversized_image_to_target_mp_keeping_aspect():
    img = Image.new("RGB", (12000, 8000))  # 96 MP, the file that OOM'd the container
    out = encode._downscale_if_huge(img, _cfg(max_megapixels=25, target_megapixels=24))
    assert out is not img
    mp = out.width * out.height / 1e6
    assert 23.0 <= mp <= 24.0          # ~target, never above
    assert abs(out.width / out.height - 12000 / 8000) < 1e-3  # aspect preserved


def test_downscale_carries_info_forward_so_exif_and_colour_survive():
    img = Image.new("RGB", (12000, 8000))
    img.info["exif"] = b"Exif\x00\x00rest"
    out = encode._downscale_if_huge(img, _cfg(max_megapixels=25, target_megapixels=24))
    assert out.info.get("exif") == b"Exif\x00\x00rest"


def test_downscale_disabled_when_max_is_zero():
    img = Image.new("RGB", (12000, 8000))
    out = encode._downscale_if_huge(img, _cfg(max_megapixels=0, target_megapixels=24))
    assert out is img


@pytest.mark.parametrize("kw", [
    dict(max_megapixels=-1),
    dict(target_megapixels=0),
    dict(max_megapixels=24, target_megapixels=48),  # target above cap is nonsensical
])
def test_validate_rejects_bad_megapixel_config(tmp_path, kw):
    (tmp_path / "in").mkdir()
    (tmp_path / "album").mkdir()
    (tmp_path / "archive").mkdir()
    cfg = Config(input=tmp_path / "in", album=tmp_path / "album", archive=tmp_path / "archive",
                 video="ignore", **kw)
    with pytest.raises(SystemExit):
        cfg.validate()
