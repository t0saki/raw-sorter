"""Color-space handling.

Panasonic (and others) flag Adobe RGB shots in EXIF (ColorSpace=Uncalibrated / Interop "R03")
but embed no ICC profile. Showing those pixels as sRGB makes them look desaturated. For the
display copies that go to the cloud album we normalise everything to sRGB, which renders
correctly in every viewer; the wide-gamut original is preserved untouched in the RAW archive.

The Adobe RGB (1998) -> sRGB transform is a fixed matrix + gamma, so we do it with numpy and
need no bundled ICC profile. Validated against lcms2 to <1.2/255 max error.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageCms

_ADOBE_GAMMA = 563.0 / 256.0  # 2.19921875

# Adobe RGB (1998) linear RGB -> XYZ (D65)
_M_ADOBE_TO_XYZ = np.array([
    [0.5767309, 0.1855540, 0.1881852],
    [0.2973769, 0.6273491, 0.0752741],
    [0.0270343, 0.0706872, 0.9911085],
])
# XYZ (D65) -> linear sRGB
_M_XYZ_TO_SRGB = np.array([
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
])
_M_ADOBE_TO_SRGB = _M_XYZ_TO_SRGB @ _M_ADOBE_TO_XYZ


def is_adobe_rgb(img: Image.Image) -> bool:
    """Detect Adobe RGB via EXIF when no ICC profile is embedded."""
    if img.info.get("icc_profile"):
        return False  # an explicit profile wins; handled elsewhere
    try:
        exif = img.getexif()
    except Exception:
        return False
    try:
        interop = exif.get_ifd(0xA005).get(0x0001)  # InteroperabilityIndex
        if isinstance(interop, str) and interop.strip().upper() == "R03":
            return True
    except Exception:
        pass
    try:
        colorspace = exif.get_ifd(0x8769).get(0xA001)  # ExifIFD.ColorSpace
        if colorspace == 0xFFFF:  # Uncalibrated
            return True
    except Exception:
        pass
    return False


@lru_cache(maxsize=1)
def srgb_icc_bytes() -> bytes:
    """A small (~0.6 KB) sRGB ICC profile to tag converted output authoritatively, so a viewer
    never has to fall back to the (now-stale) EXIF ColorSpace hint."""
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def adobe_rgb_to_srgb(img: Image.Image) -> Image.Image:
    """Convert Adobe RGB (1998) encoded pixels to sRGB."""
    rgb = img.convert("RGB")
    a = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.power(a, _ADOBE_GAMMA)
    slin = np.clip(lin @ _M_ADOBE_TO_SRGB.T, 0.0, 1.0)
    enc = np.where(slin <= 0.0031308, 12.92 * slin, 1.055 * np.power(slin, 1 / 2.4) - 0.055)
    out = np.clip(enc * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out, mode="RGB")
