"""JPEG/preview -> HEIF (or AVIF) encoding via libheif (pillow-heif), in-process.

Pipeline per image:
  1. open + `exif_transpose` so orientation is baked into pixels (single source of truth: no
     conflicting EXIF-orientation vs HEIF-`irot`, which can double-rotate in some viewers).
  2. normalise colour to sRGB when the source is Adobe-RGB-flagged (see color.py).
  3. encode with the x265 knobs (preset/tune/chroma) and carry the EXIF block (GPS, date, etc.).
"""
from __future__ import annotations

import threading
from pathlib import Path

import pillow_heif
from PIL import Image, ImageOps

from . import color
from .config import Config

_REGISTERED = False


def _ensure_registered() -> None:
    global _REGISTERED
    if not _REGISTERED:
        pillow_heif.register_heif_opener()
        try:
            pillow_heif.register_avif_opener()
        except Exception:
            pass
        _REGISTERED = True


def _prepare(img: Image.Image, cfg: Config) -> Image.Image:
    img = ImageOps.exif_transpose(img)            # bake orientation, clears the EXIF tag
    if cfg.color == "srgb" and color.is_adobe_rgb(img):
        exif = img.info.get("exif")
        img = color.adobe_rgb_to_srgb(img)
        if exif:
            img.info["exif"] = exif               # carry GPS/date through the numpy round-trip
        img.info["icc_profile"] = color.srgb_icc_bytes()  # authoritative sRGB tag
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGB")
    return img


def _save_kwargs(cfg: Config, exif: bytes | None, icc: bytes | None) -> dict:
    kw: dict = {"quality": cfg.quality}
    if exif:
        kw["exif"] = exif
    if icc:
        kw["icc_profile"] = icc
    if cfg.fmt == "avif":
        # aom uses different knobs than x265; keep it to the portable ones.
        kw["enc_params"] = {"chroma": cfg.chroma}
        kw["format"] = "AVIF"
    else:
        kw["enc_params"] = {"preset": cfg.preset, "tune": cfg.tune, "chroma": cfg.chroma}
        kw["format"] = "HEIF"
    return kw


def encode_pil(img: Image.Image, dst: Path, cfg: Config) -> None:
    _ensure_registered()
    prepared = _prepare(img, cfg)
    exif = prepared.info.get("exif")
    # Carry an embedded profile through verbatim. Adobe-RGB-without-ICC is already handled in
    # _prepare (pixels converted to sRGB), so the default sRGB nclx tag is then correct.
    icc = prepared.info.get("icc_profile")
    kw = _save_kwargs(cfg, exif, icc)
    fmt = kw.pop("format")
    prepared.save(dst, format=fmt, **kw)


def encode_file(src: Path, dst: Path, cfg: Config) -> None:
    with Image.open(src) as img:
        img.load()
        encode_pil(img, dst, cfg)


def encode_file_timeout(src: Path, dst: Path, cfg: Config) -> None:
    """Encode, but give up waiting after cfg.encode_timeout seconds.

    A libheif encode can't be interrupted from Python, so on timeout we abandon the (daemon)
    worker thread and raise. The orphaned encode finishes on its own and writes to `dst`, which
    is a unique per-attempt temp path that the caller ignores — so it never pollutes the album.
    """
    box: dict = {}

    def _work() -> None:
        try:
            encode_file(src, dst, cfg)
            box["ok"] = True
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller below
            box["err"] = exc

    t = threading.Thread(target=_work, name="rs-encode", daemon=True)
    t.start()
    t.join(cfg.encode_timeout)
    if t.is_alive():
        raise TimeoutError(f"encode of {src.name} exceeded {cfg.encode_timeout:.0f}s")
    if "err" in box:
        raise box["err"]


def verify(dst: Path) -> bool:
    """Confirm the output is a real, decodable image of non-trivial size."""
    _ensure_registered()
    try:
        if dst.stat().st_size <= 0:
            return False
        with Image.open(dst) as img:
            img.load()
            return img.size[0] > 0 and img.size[1] > 0
    except Exception:
        return False
