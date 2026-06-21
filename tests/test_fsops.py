from pathlib import Path

from raw_sorter import fsops


def test_publish_moves_into_place(tmp_path):
    tmp = tmp_path / ".tmp" / "x.partial"
    tmp.parent.mkdir()
    tmp.write_bytes(b"data")
    final = tmp_path / "out" / "x.heic"
    fsops.publish(tmp, final)
    assert final.read_bytes() == b"data"
    assert not tmp.exists()


def test_safe_move(tmp_path):
    src = tmp_path / "src.RW2"
    src.write_bytes(b"raw")
    dst = tmp_path / "arch" / "src.RW2"
    fsops.safe_move(src, dst)
    assert dst.read_bytes() == b"raw"
    assert not src.exists()


def test_unique_dest(tmp_path):
    a = tmp_path / "f.JPG"
    a.write_bytes(b"1")
    assert fsops.unique_dest(a).name == "f (2).JPG"
    (tmp_path / "f (2).JPG").write_bytes(b"2")
    assert fsops.unique_dest(a).name == "f (3).JPG"


def test_unique_dest_when_free(tmp_path):
    p = tmp_path / "free.JPG"
    assert fsops.unique_dest(p) == p
