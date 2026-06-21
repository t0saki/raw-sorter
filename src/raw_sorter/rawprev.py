"""Extract the camera's embedded (LUT-baked) preview JPEG from a RAW, for the RAW-without-JPG path.

Uses libraw (via rawpy) so we stay in-process and need no exiftool. We deliberately use the
embedded preview rather than demosaicing the sensor data, because the embedded JPEG already has
the camera's picture profile / LUT applied (matching how the paired Fine JPGs look).
"""
from __future__ import annotations

import io
from pathlib import Path

import rawpy
from PIL import Image


def extract_preview(raw_path: Path) -> Image.Image | None:
    """Return the largest embedded preview as a PIL image, or None if there isn't a usable one."""
    try:
        with rawpy.imread(str(raw_path)) as raw:
            thumb = raw.extract_thumb()
    except Exception:
        return None
    if thumb is None:
        return None
    if thumb.format == rawpy.ThumbFormat.JPEG:
        img = Image.open(io.BytesIO(thumb.data))
        img.load()
        return img
    if thumb.format == rawpy.ThumbFormat.BITMAP:
        return Image.fromarray(thumb.data)
    return None
