import numpy as np
from PIL import Image

from raw_sorter import color


def test_srgb_icc_bytes_is_a_profile():
    data = color.srgb_icc_bytes()
    assert isinstance(data, bytes) and len(data) > 100
    assert data is color.srgb_icc_bytes()  # cached


def test_adobe_to_srgb_neutral_is_unchanged():
    # Greys sit on the achromatic axis in both gamuts, so they must round-trip ~unchanged.
    grey = Image.fromarray(np.full((4, 4, 3), 128, np.uint8), "RGB")
    out = np.asarray(color.adobe_rgb_to_srgb(grey), dtype=int)
    assert abs(out.mean() - 128) <= 2


def test_adobe_to_srgb_increases_saturation_of_primaries():
    # A saturated green in Adobe RGB occupies a wider gamut; rendered correctly into sRGB its
    # encoded chroma should INCREASE vs. naively reading the same values as sRGB.
    px = np.zeros((4, 4, 3), np.uint8)
    px[..., 1] = 220
    img = Image.fromarray(px, "RGB")
    out = np.asarray(color.adobe_rgb_to_srgb(img), dtype=int)
    naive_chroma = int(px.max()) - int(px.min())            # 220
    conv_chroma = out.max(2).mean() - out.min(2).mean()
    assert conv_chroma > naive_chroma


def test_is_adobe_rgb_false_for_plain_image():
    assert color.is_adobe_rgb(Image.new("RGB", (2, 2))) is False
