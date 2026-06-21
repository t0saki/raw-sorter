from raw_sorter.pairs import classify_ext, iter_units, resolve_unit, should_skip
from pathlib import Path


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def test_classify_ext():
    assert classify_ext(Path("a.JPG")) == "jpg"
    assert classify_ext(Path("a.jpeg")) == "jpg"
    assert classify_ext(Path("a.RW2")) == "raw"
    assert classify_ext(Path("a.dng")) == "raw"
    assert classify_ext(Path("a.MP4")) == "video"
    assert classify_ext(Path("a.mov")) == "video"
    assert classify_ext(Path("a.mts")) == "video"
    assert classify_ext(Path("a.txt")) is None


def test_video_only_unit_is_yielded(tmp_path):
    _touch(tmp_path / "trip" / "C0001.MP4")          # video with no sibling photo
    units = {u.key[1]: u for u in iter_units(tmp_path)}
    assert "c0001" in units
    assert units["c0001"].video is not None
    assert units["c0001"].jpg is None and units["c0001"].raw is None


def test_resolve_unit_routes_video(tmp_path):
    _touch(tmp_path / "C0001.MP4")
    unit = resolve_unit(tmp_path, "c0001")
    assert unit.video is not None and unit.video.name == "C0001.MP4"
    assert [p.name for p in unit.others] == []


def test_resolve_unit_ambiguous_video(tmp_path):
    _touch(tmp_path / "C1.mp4")
    _touch(tmp_path / "C1.mov")
    unit = resolve_unit(tmp_path, "c1")
    assert unit.ambiguous
    assert unit.video is None   # refuses to guess


def test_should_skip():
    assert should_skip(Path("/x/.DS_Store"))
    assert should_skip(Path("/x/.hidden.jpg"))
    assert should_skip(Path("/x/.tmp/y.heic"))
    assert should_skip(Path("/x/.quarantine/y.jpg"))
    assert should_skip(Path("/x/@eaDir/P1.JPG/SYNOPHOTO_THUMB_M.jpg"))  # Synology thumbnails
    assert should_skip(Path("/x/#recycle/P1.JPG"))
    assert should_skip(Path("/x/@Recycle/P1.JPG"))                      # QNAP, case-insensitive
    assert not should_skip(Path("/x/P1.JPG"))


def test_iter_units_prunes_nas_dirs(tmp_path):
    _touch(tmp_path / "trip" / "P1.JPG")
    _touch(tmp_path / "trip" / "P1.RW2")
    # Synology fills @eaDir with .jpg thumbnails that must never be processed
    _touch(tmp_path / "trip" / "@eaDir" / "P1.JPG" / "SYNOPHOTO_THUMB_XL.jpg")
    _touch(tmp_path / "#recycle" / "old.JPG")
    keys = sorted(u.key[1] for u in iter_units(tmp_path))
    assert keys == ["p1"]


def test_resolve_unit_pairs(tmp_path):
    _touch(tmp_path / "P1.JPG")
    _touch(tmp_path / "P1.RW2")
    _touch(tmp_path / "P1.txt")        # other
    _touch(tmp_path / ".DS_Store")     # skipped
    unit = resolve_unit(tmp_path, "p1")
    assert unit.jpg and unit.jpg.name == "P1.JPG"
    assert unit.raw and unit.raw.name == "P1.RW2"
    assert [p.name for p in unit.others] == ["P1.txt"]
    assert not unit.ambiguous


def test_resolve_unit_case_insensitive(tmp_path):
    _touch(tmp_path / "Img.jpg")
    _touch(tmp_path / "IMG.rw2")
    unit = resolve_unit(tmp_path, "IMG")
    assert unit.jpg is not None and unit.raw is not None


def test_resolve_unit_ambiguous(tmp_path):
    _touch(tmp_path / "P1.jpg")
    _touch(tmp_path / "P1.jpeg")
    unit = resolve_unit(tmp_path, "p1")
    assert unit.ambiguous
    assert unit.jpg is None   # refuses to guess


def test_iter_units_recursive_dedup(tmp_path):
    _touch(tmp_path / "a" / "P1.JPG")
    _touch(tmp_path / "a" / "P1.RW2")
    _touch(tmp_path / "b" / "P2.JPG")
    keys = sorted(u.key[1] for u in iter_units(tmp_path))
    assert keys == ["p1", "p2"]
