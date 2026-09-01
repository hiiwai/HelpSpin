"""Filesystem failures.

Spectrometer data lives on network shares. A stale mount makes an innocuous
is_dir() or iterdir() raise OSError, and on some platforms blocks for 30+
seconds first. None of these may propagate as a crash: the node renders in a
failed state and the window stays responsive.
"""

from pathlib import Path

from helspin.domain.paths import (
    expnos_in,
    is_expno,
    is_procno,
    is_sample_dir,
    procnos_in,
    resolve,
    scan_for_datasets,
)


class Unreachable:
    """Stand-in for a path on a stale mount."""

    def __init__(self, name="11", parent=None):
        self.name = name
        self._parent = parent

    @property
    def parent(self):
        return self._parent or self

    def is_dir(self):
        raise OSError("stale NFS file handle")

    def is_file(self):
        raise OSError("stale NFS file handle")

    def iterdir(self):
        raise OSError("stale NFS file handle")

    def __truediv__(self, other):
        return Unreachable(str(other), parent=self)


def test_is_expno_survives_unreachable_mount():
    assert is_expno(Unreachable()) is False


def test_is_sample_dir_survives_unreachable_mount():
    assert is_sample_dir(Unreachable()) is False


def test_is_procno_survives_unreachable_mount():
    assert is_procno(Unreachable()) is False


def test_expnos_in_survives_unreachable_mount():
    assert expnos_in(Unreachable()) == []


def test_procnos_in_survives_unreachable_mount():
    assert procnos_in(Unreachable()) == []


def test_permission_denied_is_not_a_crash(tmp_path, monkeypatch):
    """A directory the user cannot read is skipped, not fatal."""
    locked = tmp_path / "locked"
    locked.mkdir()

    real_iterdir = Path.iterdir

    def deny(self):
        if self.name == "locked":
            raise PermissionError("denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", deny)
    assert scan_for_datasets(tmp_path) == []
    assert is_sample_dir(locked) is False


def test_scan_skips_an_unreadable_subtree(tmp_path, monkeypatch):
    """One bad directory must not lose the datasets beside it."""
    good = tmp_path / "good" / "11"
    good.mkdir(parents=True)
    (good / "acqus").write_text("x")
    bad = tmp_path / "bad"
    bad.mkdir()

    real_iterdir = Path.iterdir

    def deny(self):
        if self.name == "bad":
            raise OSError("stale handle")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", deny)
    found = scan_for_datasets(tmp_path)
    assert [p.name for p in found] == ["11"]


def test_resolve_on_unreachable_returns_none(tmp_path, monkeypatch):
    target = tmp_path / "sample" / "11"
    target.mkdir(parents=True)

    def always_fail(self):
        raise OSError("stale handle")

    monkeypatch.setattr(Path, "is_dir", always_fail)
    monkeypatch.setattr(Path, "is_file", always_fail)
    monkeypatch.setattr(Path, "iterdir", always_fail)
    assert resolve(target) is None


def test_resolve_stops_at_filesystem_root():
    """Walking up must terminate, not loop forever at '/'."""
    assert resolve(Path("/"), max_up=10) is None


def test_scan_of_a_file_rather_than_a_directory(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert scan_for_datasets(f) == []


def test_resolve_of_a_file_inside_an_expno(tmp_path):
    """Dropping acqus itself should still find the dataset."""
    expno = tmp_path / "sample" / "11"
    expno.mkdir(parents=True)
    (expno / "acqus").write_text("x")
    r = resolve(expno / "acqus")
    assert r is not None and r.expno == expno
