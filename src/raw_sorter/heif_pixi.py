"""Repair the HEIF `pixi` (PixelInformation) box for colour images.

libheif (as bundled in pillow-heif, verified with libheif 1.23.0) encodes RGB input as a normal
YCbCr 4:2:0 HEVC bitstream but writes the primary item's `pixi` property as `channels=1` — i.e. it
claims the picture is single-channel/monochrome. Lenient decoders (Finder thumbnails, macOS Photos
on an HDR pipeline) ignore `pixi` and read the real bitstream, so they show colour. Strict decoders
trust `pixi`: iOS renders the luma plane only (black-and-white) and the macOS SDR path crushes it to
black. Apple's own encoder writes `channels=3, bits=[8,8,8]` for the same picture.

The fix is a 2-byte surgical edit of the primary item's `pixi` (1 channel -> 3), with the parent
box sizes (`pixi`/`ipco`/`iprp`/`meta`) and any `iloc` absolute offsets at/after the splice point
bumped by 2 so the file stays byte-exact elsewhere — the pixel data (`mdat`) is never re-encoded.

A genuinely monochrome HEIF (libheif emits `colorspace: monochrome` for an `L`-mode source) is left
untouched: there `channels=1` is correct. We tell the two apart by `chroma_format_idc` parsed from
the HEVC SPS in the primary item's `hvcC` (0 = monochrome, >=1 = colour). This module is pure
stdlib so it doubles as a standalone tool — see `__main__` at the bottom.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_HDR = 8


def _boxes(buf: bytes, start: int, end: int):
    """Yield (type, box_start, box_size, header_len) for every box in [start, end)."""
    off = start
    while off + _HDR <= end:
        size = struct.unpack(">I", buf[off:off + 4])[0]
        typ = buf[off + 4:off + 8]
        hdr = _HDR
        if size == 1:                                   # 64-bit largesize
            size = struct.unpack(">Q", buf[off + 8:off + 16])[0]
            hdr = 16
        if size == 0:                                   # extends to container end
            size = end - off
        if size < hdr or off + size > end:
            break
        yield typ, off, size, hdr
        off += size


def _find(buf, start, end, want):
    for typ, off, size, hdr in _boxes(buf, start, end):
        if typ == want:
            return off, size, hdr
    return None


class _BitReader:
    """MSB-first bit reader over RBSP with emulation-prevention bytes removed."""

    def __init__(self, data: bytes):
        out = bytearray()
        i, n = 0, len(data)
        while i < n:
            if i + 2 < n and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 3:
                out += b"\x00\x00"
                i += 3
            else:
                out.append(data[i])
                i += 1
        self.b = bytes(out)
        self.pos = 0

    def u(self, k: int) -> int:
        v = 0
        for _ in range(k):
            byte = self.b[self.pos >> 3]
            bit = (byte >> (7 - (self.pos & 7))) & 1
            v = (v << 1) | bit
            self.pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("exp-golomb overrun")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)


def _sps_chroma_format_idc(sps_nal: bytes) -> int | None:
    """chroma_format_idc from an HEVC SPS NAL, or None if it can't be parsed confidently.

    None means "don't know" — callers treat that as "do not touch", so an unparsable file is never
    mis-edited. Only the simple (single sub-layer) SPS shape that camera stills use is decoded.
    """
    try:
        r = _BitReader(sps_nal[2:])                     # skip the 2-byte NAL header
        r.u(4)                                          # sps_video_parameter_set_id
        max_sub_layers_minus1 = r.u(3)
        r.u(1)                                          # sps_temporal_id_nesting_flag
        if max_sub_layers_minus1 != 0:                  # extra profile_tier_level sub-layers
            return None
        r.pos += 12 * 8                                 # profile_tier_level (12 bytes when 1 layer)
        r.ue()                                          # sps_seq_parameter_set_id
        return r.ue()                                   # chroma_format_idc
    except (IndexError, ValueError):
        return None


def _hvcc_sps_nals(buf: bytes, off: int, _size: int, hdr: int):
    """Yield SPS NAL payloads from an `hvcC` box."""
    p = off + hdr + 22                                  # fixed HEVCDecoderConfigurationRecord head
    num_arrays = buf[p]
    p += 1
    for _ in range(num_arrays):
        nal_type = buf[p] & 0x3F
        num_nalus = struct.unpack(">H", buf[p + 1:p + 3])[0]
        p += 3
        for _ in range(num_nalus):
            ln = struct.unpack(">H", buf[p:p + 2])[0]
            p += 2
            if nal_type == 33:                          # SPS_NUT
                yield buf[p:p + ln]
            p += ln


def _primary_item_id(buf, m_body, m_end):
    box = _find(buf, m_body, m_end, b"pitm")
    if not box:
        return None
    off, _, hdr = box
    ver = buf[off + hdr]
    p = off + hdr + 4
    return struct.unpack(">I", buf[p:p + 4])[0] if ver else struct.unpack(">H", buf[p:p + 2])[0]


def _property_indices(buf, iprp_body, iprp_end, item_id):
    """1-based ipco property indices associated with item_id, via `ipma`."""
    box = _find(buf, iprp_body, iprp_end, b"ipma")
    if not box:
        return []
    off, size, hdr = box
    ver = buf[off + hdr]
    flags = struct.unpack(">I", b"\x00" + buf[off + hdr + 1:off + hdr + 4])[0]
    wide = flags & 1
    p = off + hdr + 4
    entry_count = struct.unpack(">I", buf[p:p + 4])[0]
    p += 4
    end = off + size
    for _ in range(entry_count):
        if p >= end:
            break
        if ver >= 1:
            eid = struct.unpack(">I", buf[p:p + 4])[0]
            p += 4
        else:
            eid = struct.unpack(">H", buf[p:p + 2])[0]
            p += 2
        assoc = buf[p]
        p += 1
        idxs = []
        for _ in range(assoc):
            if wide:
                v = struct.unpack(">H", buf[p:p + 2])[0]
                p += 2
                idxs.append(v & 0x7FFF)
            else:
                idxs.append(buf[p] & 0x7F)
                p += 1
        if eid == item_id:
            return idxs
    return []


def fix_pixi(data: bytes, assume_colour: bool | None = None) -> bytes | None:
    """Return a repaired copy if the primary item's `pixi` was wrongly single-channel, else None.

    assume_colour: True/False forces the decision (the encoder knows the source mode); None auto-
    detects from the HEVC SPS and only repairs a confirmed colour (chroma_format_idc >= 1) image.
    """
    meta = _find(data, 0, len(data), b"meta")
    if not meta:
        return None
    m_off, m_size, m_hdr = meta
    m_body, m_end = m_off + m_hdr + 4, m_off + m_size       # meta is a FullBox (+4)

    iprp = _find(data, m_body, m_end, b"iprp")
    if not iprp:
        return None
    ip_off, ip_size, ip_hdr = iprp
    ipco = _find(data, ip_off + ip_hdr, ip_off + ip_size, b"ipco")
    if not ipco:
        return None
    co_off, co_size, co_hdr = ipco
    children = list(_boxes(data, co_off + co_hdr, co_off + co_size))   # 1-based property space

    item_id = _primary_item_id(data, m_body, m_end)
    if item_id is None:
        return None
    idxs = _property_indices(data, ip_off + ip_hdr, ip_off + ip_size, item_id)

    def pick(want):
        for i in idxs:
            if 1 <= i <= len(children) and children[i - 1][0] == want:
                return children[i - 1][1:]              # (off, size, hdr), matching _find()
        return None

    pixi = pick(b"pixi")
    if not pixi:
        return None
    px_off, px_size, px_hdr = pixi
    num_channels = data[px_off + px_hdr + 4]
    if num_channels != 1:
        return None                                         # already correct (or unexpected)

    if assume_colour is None:
        hvcc = pick(b"hvcC")
        if not hvcc:
            return None
        cfi = None
        for sps in _hvcc_sps_nals(data, *hvcc):
            cfi = _sps_chroma_format_idc(sps)
            if cfi is not None:
                break
        if cfi is None or cfi == 0:                         # unknown or genuinely monochrome
            return None
    elif assume_colour is False:
        return None

    # Splice: 1 channel -> 3. Insert two extra bit-depth bytes right after the existing one.
    bd = data[px_off + px_hdr + 5]
    splice_at = px_off + px_hdr + 6                         # absolute file offset of the insertion
    grow = 2
    b = bytearray(data)
    b[px_off + px_hdr + 4] = 3                              # num_channels 1 -> 3

    for off, size in ((px_off, px_size), (co_off, co_size),
                      (ip_off, ip_size), (m_off, m_size)):  # bump ancestor box sizes
        b[off:off + 4] = (size + grow).to_bytes(4, "big")

    _bump_iloc(b, m_body, m_end, splice_at, grow)
    b[splice_at:splice_at] = bytes([bd, bd])               # do the insert last
    return bytes(b)


def _bump_iloc(b: bytearray, m_body: int, m_end: int, splice_at: int, grow: int) -> None:
    """Add `grow` to every absolute construction_method=0 offset at/after `splice_at`."""
    box = _find(b, m_body, m_end, b"iloc")
    if not box:
        return
    off, _, hdr = box
    ver = b[off + hdr]
    p = off + hdr + 4
    sizes = b[p]
    offset_size, length_size = sizes >> 4, sizes & 0xF
    sizes2 = b[p + 1]
    base_offset_size, index_size = sizes2 >> 4, sizes2 & 0xF
    p += 2
    if ver < 2:
        item_count = struct.unpack(">H", b[p:p + 2])[0]
        p += 2
    else:
        item_count = struct.unpack(">I", b[p:p + 4])[0]
        p += 4

    def patch(pos, width):
        val = int.from_bytes(b[pos:pos + width], "big")
        if val >= splice_at:
            b[pos:pos + width] = (val + grow).to_bytes(width, "big")

    for _ in range(item_count):
        p += 4 if ver >= 2 else 2                           # item_id
        cm = struct.unpack(">H", b[p:p + 2])[0] & 0xF if ver in (1, 2) else 0
        if ver in (1, 2):
            p += 2
        p += 2                                              # data_reference_index
        if base_offset_size and cm == 0:
            patch(p, base_offset_size)
        p += base_offset_size
        ext_count = struct.unpack(">H", b[p:p + 2])[0]
        p += 2
        for _ in range(ext_count):
            p += index_size
            if base_offset_size == 0 and cm == 0:
                patch(p, offset_size)
            p += offset_size + length_size


def fix_pixi_file(path: Path, dry_run: bool = False) -> bool:
    """Repair `path` in place (atomic) when needed. Returns True if a fix was applied/required."""
    data = path.read_bytes()
    fixed = fix_pixi(data)
    if fixed is None:
        return False
    if not dry_run:
        tmp = path.with_name(path.name + ".pixifix.tmp")
        tmp.write_bytes(fixed)
        tmp.replace(path)
    return True


def _iter_targets(paths):
    exts = {".heic", ".heif", ".hif"}
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from (q for q in sorted(p.rglob("*")) if q.suffix.lower() in exts)
        elif p.suffix.lower() in exts:
            yield p


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv or "-n" in argv
    paths = [a for a in argv if not a.startswith("-")]
    if not paths:
        print("usage: python -m raw_sorter.heif_pixi [-n|--dry-run] <file-or-dir> ...",
              file=sys.stderr)
        return 2
    scanned = fixed = failed = 0
    for f in _iter_targets(paths):
        scanned += 1
        try:
            if fix_pixi_file(f, dry_run=dry):
                fixed += 1
                print(f"{'WOULD FIX' if dry else 'FIXED'}  {f}")
        except Exception as exc:                            # noqa: BLE001 — keep scanning the rest
            failed += 1
            print(f"ERROR  {f}: {exc}", file=sys.stderr)
    verb = "would fix" if dry else "fixed"
    print(f"\nscanned {scanned}  {verb} {fixed}  failed {failed}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
