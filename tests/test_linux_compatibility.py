"""Cross-platform behaviour, and the Linux cases in particular.

Three separate concerns live here because they share one motivation: things
that behave differently on Linux from macOS and Windows, and that no other
test would notice because the suite runs on one platform at a time.

The symlink tests skip themselves on a platform that cannot create a symlink
(Windows without Developer Mode) rather than failing: the code is still
correct there, it simply cannot be demonstrated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from helspin import CONTACT_EMAIL
from helspin.core.dataset_index import cache_dir, discover_samples, scan_expnos
from helspin.domain.paths import scan_for_samples


def _can_symlink(tmp_path: Path) -> bool:
    try:
        (tmp_path / "_probe_target").mkdir()
        (tmp_path / "_probe_link").symlink_to(
            tmp_path / "_probe_target", target_is_directory=True
        )
    except (OSError, NotImplementedError):
        return False
    return True


needs_symlinks = pytest.mark.skipif(
    sys.platform == "win32", reason="symlink creation needs elevation on Windows"
)


def _make_sample(root: Path, name: str, expnos=(1, 2)) -> Path:
    """A minimally convincing Bruker sample: integer expno dirs with acqus."""
    sample = root / name
    for expno in expnos:
        d = sample / str(expno)
        d.mkdir(parents=True)
        (d / "acqus").write_text("##TITLE= fake\n", encoding="utf-8")
        pdata = d / "pdata" / "1"
        pdata.mkdir(parents=True)
        (pdata / "1r").write_bytes(b"\x00\x00\x00\x00")
    return sample


# --- symlinked data, the reason the walkers changed -------------------------


@needs_symlinks
def test_sample_reached_through_a_symlinked_directory_is_found(tmp_path):
    """The bug this replaces: an entire linked instrument mount was invisible.

    A data root holding `spect600 -> /mnt/raw/spect600` is ordinary on a Linux
    or macOS workstation. Excluding symlinks meant the browser reported
    nothing under it, with no error and no dimmed row -- the failure mode that
    looks like "the share is empty" rather than like a bug.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    real = tmp_path / "elsewhere"
    real.mkdir()
    _make_sample(real, "ABC-100")

    root = tmp_path / "root"
    root.mkdir()
    (root / "spect600").symlink_to(real, target_is_directory=True)

    found, truncated = discover_samples(str(root))
    assert not truncated
    assert [Path(p).name for p in found] == ["ABC-100"]


@needs_symlinks
def test_symlinked_expno_still_registers_its_sample_and_lists(tmp_path):
    """Discovery and expno listing must agree about a linked experiment.

    If one counts a linked expno and the other does not, the sample appears in
    the tree and then opens onto nothing -- worse than either consistent
    answer, because the row promises data that never arrives.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    donor = tmp_path / "donor"
    _make_sample(donor, "SRC", expnos=(7,))

    root = tmp_path / "root"
    sample = root / "ABC-200"
    sample.mkdir(parents=True)
    (sample / "7").symlink_to(donor / "SRC" / "7", target_is_directory=True)

    found, _ = discover_samples(str(root))
    assert [Path(p).name for p in found] == ["ABC-200"]

    expnos = scan_expnos(str(sample))
    assert [e.name for e in expnos] == ["7"], (
        "discovery counted the linked expno, so listing must too"
    )
    assert expnos[0].has_acqus


@needs_symlinks
def test_symlink_pointing_at_an_ancestor_terminates(tmp_path):
    """A loop must end, and must not report the same sample twice.

    Following symlinks without identity tracking turns `root/loop -> root`
    into a descent that only stops when the depth limit runs out, reporting
    the same sample once per level. The (device, inode) set is what makes the
    second visit a no-op instead.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    root = tmp_path / "root"
    root.mkdir()
    holder = root / "instruments"
    holder.mkdir()
    _make_sample(holder, "ABC-300")
    (holder / "loop").symlink_to(root, target_is_directory=True)

    found, _ = discover_samples(str(root))
    names = [Path(p).name for p in found]
    assert names.count("ABC-300") == 1, f"sample reported more than once: {names}"


@needs_symlinks
def test_two_links_to_one_directory_yield_one_result(tmp_path):
    """Aliases of the same directory are the same directory.

    Identity is taken from the filesystem, not from the path text, precisely
    so that two names for one target collapse.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    real = tmp_path / "real"
    real.mkdir()
    _make_sample(real, "ABC-400")

    root = tmp_path / "root"
    root.mkdir()
    (root / "by-name").symlink_to(real, target_is_directory=True)
    (root / "by-alias").symlink_to(real, target_is_directory=True)

    found, _ = discover_samples(str(root))
    assert len(found) == 1, f"one directory, two names, should be one sample: {found}"


@needs_symlinks
def test_broken_symlink_does_not_abort_the_scan(tmp_path):
    """A dangling link is a fact of life on an unmounted share.

    It must be stepped over, not raised through: one dead link must not cost
    the user every other sample on the share.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    root = tmp_path / "root"
    root.mkdir()
    (root / "dead").symlink_to(tmp_path / "was-never-here", target_is_directory=True)
    _make_sample(root, "ABC-500")

    found, _ = discover_samples(str(root))
    assert [Path(p).name for p in found] == ["ABC-500"]


@needs_symlinks
def test_domain_find_samples_agrees_with_discover_samples(tmp_path):
    """The two walkers must not disagree about what is on disk.

    They were inconsistent before this change -- one followed symlinks, the
    other did not -- so the answer depended on which code path asked.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    real = tmp_path / "real"
    real.mkdir()
    _make_sample(real, "ABC-600")

    root = tmp_path / "root"
    root.mkdir()
    (root / "linked").symlink_to(real, target_is_directory=True)

    from_index = {Path(p).resolve() for p in discover_samples(str(root))[0]}
    from_domain = {p.resolve() for p in scan_for_samples(root)[0]}
    assert from_index == from_domain


def test_ordinary_directories_are_unaffected(tmp_path):
    """The regression guard: no symlinks involved, nothing changes."""
    root = tmp_path / "root"
    root.mkdir()
    _make_sample(root, "ABC-700")
    _make_sample(root, "ABC-701")

    found, truncated = discover_samples(str(root))
    assert sorted(Path(p).name for p in found) == ["ABC-700", "ABC-701"]
    assert not truncated


# --- cache location ---------------------------------------------------------


def test_xdg_cache_home_is_honoured_on_linux(tmp_path, monkeypatch):
    """On a machine where /home is a slow NFS mount, XDG_CACHE_HOME is
    redirected to local disk. Writing the cache of a network mount back onto
    the network is the one placement that defeats its purpose."""
    monkeypatch.delenv("HELSPIN_CACHE_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "fast-local"))
    assert cache_dir() == tmp_path / "fast-local" / "helspin"


def test_relative_xdg_cache_home_is_ignored(tmp_path, monkeypatch):
    """The XDG spec says a relative value is invalid and must be ignored.

    Honouring it would put the cache wherever the process happened to be
    started from, which for a desktop launcher is unpredictable.
    """
    monkeypatch.delenv("HELSPIN_CACHE_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
    assert cache_dir() == Path.home() / ".cache" / "helspin"


def test_explicit_override_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "explicit"))
    assert cache_dir() == tmp_path / "explicit"


def test_linux_default_without_xdg(monkeypatch):
    monkeypatch.delenv("HELSPIN_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert cache_dir() == Path.home() / ".cache" / "helspin"


def test_windows_branch_is_unchanged(tmp_path, monkeypatch):
    """XDG must not leak into the Windows branch."""
    monkeypatch.delenv("HELSPIN_CACHE_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert cache_dir() == tmp_path / "local" / "HelSpin" / "cache"


# --- contact address --------------------------------------------------------


def test_contact_address_is_defined_once():
    """One definition, so About and the licence cannot drift apart."""
    assert "@" in CONTACT_EMAIL
    assert CONTACT_EMAIL == "iwai@ligsciss.com"


def test_licence_text_carries_the_contact_address():
    """A licence that says 'contact the copyright holder' without saying how
    leaves the commercial route with no entry point at all."""
    licence = Path(__file__).resolve().parent.parent / "LICENSE"
    text = licence.read_text(encoding="utf-8")
    assert CONTACT_EMAIL in text


def test_shipped_licence_matches_the_repository_copy():
    """The packaged build shows resources/LICENSE.txt, not LICENSE. If the two
    diverge, the copy the user actually reads is the stale one."""
    base = Path(__file__).resolve().parent.parent
    assert (
        (base / "LICENSE").read_text(encoding="utf-8")
        == (base / "helspin" / "resources" / "LICENSE.txt").read_text(encoding="utf-8")
    )


def test_desktop_entry_exists_and_names_the_binary():
    """The .desktop file is what a Wayland compositor reads the dock icon
    from; setDesktopFileName("helspin") is meaningless without it."""
    entry = (
        Path(__file__).resolve().parent.parent / "packaging" / "helspin.desktop"
    )
    assert entry.is_file(), "packaging/helspin.desktop is missing"
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]")
    assert "Exec=helspin" in text
    assert "Icon=helspin" in text
    # StartupWMClass is what matches an ALREADY-RUNNING window to this entry;
    # without it the running app gets a second, iconless dock item.
    assert "StartupWMClass=" in text


def test_save_image_suffix_survives_a_dotted_directory(tmp_path, qtbot):
    """The bug fixed alongside: rsplit on '.' read part of a DIRECTORY name as
    the image format, so a figure saved under `v1.2/` failed to write."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)

    dotted = tmp_path / "project.v1.2"
    dotted.mkdir()
    out = dotted / "figure.png"
    canvas.save_image(out)
    assert out.is_file() and out.stat().st_size > 0


def test_save_image_defaults_to_png_when_there_is_no_extension(tmp_path, qtbot):
    """An extensionless path under a dotted directory used to produce a
    nonsense format string; it must fall back to PNG instead."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)

    dotted = tmp_path / "data.backup"
    dotted.mkdir()
    out = dotted / "figure"
    canvas.save_image(out)
    assert out.is_file() and out.stat().st_size > 0


def test_no_hardcoded_path_separators_in_shipping_code():
    """A literal backslash separator is the classic Windows-only path bug.

    Checked as source text because such a path only fails at run time, on the
    one platform the author is not currently sitting at.
    """
    base = Path(__file__).resolve().parent.parent / "helspin"
    offenders = []
    for py in base.rglob("*.py"):
        for number, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if '"\\\\' in line or "'\\\\" in line:
                # paths.py legitimately detects UNC prefixes as INPUT
                if py.name == "paths.py":
                    continue
                offenders.append(f"{py.name}:{number}")
    assert not offenders, f"hardcoded Windows separators: {offenders}"


def test_os_path_join_is_not_used_for_display_paths():
    """Not a correctness rule but a consistency one: the codebase uses
    pathlib throughout, and a stray os.path.join returns a str that then
    behaves differently in the one place it appears."""
    base = Path(__file__).resolve().parent.parent / "helspin"
    hits = [
        py.name
        for py in base.rglob("*.py")
        if "os.path.join(" in py.read_text(encoding="utf-8")
    ]
    assert not hits, f"os.path.join in {hits}; use pathlib"


# --- macOS, and network shares reached from it ------------------------------


def test_a_tree_without_symlinks_costs_no_extra_stat_calls(tmp_path):
    """Speed is the reason this application exists, so this is a hard budget.

    `DirEntry.stat()` is a real round trip, unlike `is_dir()` and
    `is_symlink()` which answer from the directory read already performed. An
    earlier version of the symlink work called stat on every child directory:
    448 extra round trips on a 400-sample share, roughly a second over SMB.

    A share with no symlinks -- the ordinary case -- must cost exactly what it
    cost before symlinks were supported at all: zero.
    """
    import os as _os

    from helspin.core import dataset_index as di

    root = tmp_path / "share"
    for i in range(12):
        _make_sample(root / f"grp{i // 4}", f"ABC-9{i:02d}", expnos=(1,))

    stats = {"n": 0}
    real_scandir = _os.scandir

    class _Counting:
        __slots__ = ("_e",)

        def __init__(self, e):
            self._e = e

        @property
        def name(self):
            return self._e.name

        @property
        def path(self):
            return self._e.path

        def is_dir(self, **kw):
            return self._e.is_dir(**kw)

        def is_symlink(self):
            return self._e.is_symlink()

        def stat(self, **kw):
            stats["n"] += 1
            return self._e.stat(**kw)

    class _Scandir:
        def __init__(self, path):
            self._it = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()

        def __iter__(self):
            for e in self._it:
                yield _Counting(e)

    _os.scandir = _Scandir
    try:
        found, _ = di.discover_samples(str(root))
    finally:
        _os.scandir = real_scandir

    assert len(found) == 12
    assert stats["n"] == 0, (
        f"{stats['n']} stat calls on a symlink-free tree; the budget is zero, "
        "because each one is a network round trip"
    )


@needs_symlinks
def test_a_symlink_costs_a_stat_but_only_the_symlink(tmp_path):
    """The cost is paid where it buys something, and nowhere else."""
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    import os as _os

    from helspin.core import dataset_index as di

    real = tmp_path / "real"
    real.mkdir()
    _make_sample(real, "ABC-950", expnos=(1,))

    root = tmp_path / "share"
    root.mkdir()
    for i in range(6):
        _make_sample(root, f"ABC-96{i}", expnos=(1,))
    (root / "linked").symlink_to(real, target_is_directory=True)

    stats = {"n": 0}
    real_scandir = _os.scandir

    class _Counting:
        __slots__ = ("_e",)

        def __init__(self, e):
            self._e = e

        @property
        def name(self):
            return self._e.name

        @property
        def path(self):
            return self._e.path

        def is_dir(self, **kw):
            return self._e.is_dir(**kw)

        def is_symlink(self):
            return self._e.is_symlink()

        def stat(self, **kw):
            stats["n"] += 1
            return self._e.stat(**kw)

    class _Scandir:
        def __init__(self, path):
            self._it = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._it.close()

        def __iter__(self):
            for e in self._it:
                yield _Counting(e)

    _os.scandir = _Scandir
    try:
        found, _ = di.discover_samples(str(root))
    finally:
        _os.scandir = real_scandir

    assert len(found) == 7, "six local samples plus the linked one"
    assert stats["n"] <= 2, (
        f"{stats['n']} stat calls for a single symlink among seven entries; "
        "only the symlink should cost one"
    )


def test_link_identity_falls_back_to_realpath_without_an_inode(tmp_path):
    """Some SMB/CIFS and FUSE mounts report st_ino as 0 for every entry.

    A link on such a share still has to be identifiable, or a loop would run
    to the depth limit. The resolved path is weaker than an inode but breaks
    the cycle, which is what matters.
    """
    import os as _os

    from helspin.core.dataset_index import _link_identity

    (tmp_path / "target").mkdir()

    class _Link:
        path = str(tmp_path / "target")

        def stat(self):
            class S:
                st_dev = 33
                st_ino = 0
            return S()

        def is_symlink(self):
            return True

    assert _link_identity(_Link()) == _os.path.realpath(_Link.path)


def test_link_identity_uses_a_real_inode_when_there_is_one(tmp_path):
    from helspin.core.dataset_index import _link_identity

    d = tmp_path / "real"
    d.mkdir()

    class _Entry:
        path = str(d)

        def stat(self):
            return d.stat()

        def is_symlink(self):
            return True

    identity = _link_identity(_Entry())
    assert identity == (d.stat().st_dev, d.stat().st_ino)


def test_link_identity_returns_none_for_an_unreadable_target():
    """A broken link must be skipped, not raised through."""
    from helspin.core.dataset_index import _link_identity

    class _Broken:
        path = "/nonexistent/target"

        def stat(self):
            raise OSError("no such file")

        def is_symlink(self):
            return True

    assert _link_identity(_Broken()) is None


def test_domain_and_core_identity_helpers_agree(tmp_path):
    """The two copies exist because the domain layer may not import core.
    They must not drift apart."""
    from helspin.core.dataset_index import _link_identity as core_version
    from helspin.domain.paths import _link_identity as domain_version

    d = tmp_path / "same"
    d.mkdir()

    class _Entry:
        path = str(d)

        def stat(self):
            return d.stat()

        def is_symlink(self):
            return True

    assert core_version(_Entry()) == domain_version(_Entry())


def test_desktop_file_name_is_not_set_on_macos(monkeypatch):
    """macOS has no .desktop concept. Qt would ignore it, but application
    identity on the primary development platform is not altered on the
    strength of "probably ignored"."""
    from helspin import __main__ as m

    called = []

    class _App:
        def setDesktopFileName(self, name):
            called.append(name)

    monkeypatch.setattr(m.sys, "platform", "darwin")
    m._claim_linux_desktop_identity(_App())
    assert called == []

    monkeypatch.setattr(m.sys, "platform", "win32")
    m._claim_linux_desktop_identity(_App())
    assert called == []

    monkeypatch.setattr(m.sys, "platform", "linux")
    m._claim_linux_desktop_identity(_App())
    assert called == ["helspin"]


def test_macos_cache_location_is_unchanged(monkeypatch):
    """macOS must not pick up the Linux XDG branch, even with the variable
    set -- which happens on a Mac with cross-platform dotfiles."""
    monkeypatch.delenv("HELSPIN_CACHE_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/should-be-ignored")
    assert cache_dir() == Path.home() / ".cache" / "helspin"


def test_windows_taskbar_identity_is_a_noop_off_windows(monkeypatch):
    """Guard against the mirror-image mistake: the Windows call must not
    start running on macOS."""
    from helspin import __main__ as m

    monkeypatch.setattr(m.sys, "platform", "darwin")
    m._claim_windows_taskbar_identity()   # must simply return


def test_a_link_costs_a_fixed_price_not_one_per_sample(tmp_path):
    """The second performance trap, and the more expensive of the two.

    De-duplicating RESULTS meant resolving every sample found, so a single
    link anywhere on a share cost one round trip per sample -- 400 on a
    400-sample share, worse than the problem it replaced. The rule now is a
    containment check on the link itself: a link pointing back inside the
    root is not followed, because everything under it is reachable by its real
    path anyway. That is one resolve per link, and links are rare.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    import os as _os

    from helspin.core import dataset_index as di

    outside = tmp_path / "outside"
    outside.mkdir()
    _make_sample(outside, "ABC-990", expnos=(1,))

    root = tmp_path / "share"
    root.mkdir()
    for i in range(20):
        _make_sample(root, f"ABC-97{i:02d}", expnos=(1,))
    (root / "linked").symlink_to(outside, target_is_directory=True)

    resolves = {"n": 0}
    real_realpath = _os.path.realpath

    def counting(path, *a, **kw):
        resolves["n"] += 1
        return real_realpath(path, *a, **kw)

    _os.path.realpath = counting
    try:
        found, _ = di.discover_samples(str(root))
    finally:
        _os.path.realpath = real_realpath

    assert len(found) == 21, "twenty local samples plus the linked one"
    assert resolves["n"] <= 3, (
        f"{resolves['n']} path resolutions for one link among 21 samples; "
        "the cost must scale with links, not with samples"
    )


# --- cost: the app's whole point is being faster than TopSpin ---------------


def test_a_symlink_free_scan_costs_no_extra_round_trips(tmp_path):
    """One directory read per directory, and NOT ONE stat more.

    This is the performance contract the browser is built on: on a network
    share every syscall is a round trip, so a per-directory stat added to a
    400-sample walk is 400 round trips that the user waits through. Following
    symlinks must therefore cost nothing at all when there are none, which is
    the ordinary case.

    Asserted as a count, not a duration: wall-clock time on a local disk hides
    exactly the cost that matters on a share.
    """
    from helspin.core import dataset_index as di

    root = tmp_path / "root"
    for i in range(12):
        _make_sample(root / f"grp{i // 4}", f"ABC-{900 + i}")

    calls = {"n": 0}
    original = di._link_identity

    def counted(entry):
        calls["n"] += 1
        return original(entry)

    di._link_identity = counted
    try:
        found, _ = di.discover_samples(str(root))
    finally:
        di._link_identity = original

    assert len(found) == 12
    assert calls["n"] == 0, (
        f"{calls['n']} stat calls on a tree with no symlinks; on a share that "
        "is one network round trip each, for nothing"
    )


def test_stat_cost_scales_with_links_not_with_directories(tmp_path):
    """The extra work must be per SYMLINK, never per directory.

    A root of a few linked instrument mounts is the realistic Linux and macOS
    arrangement. Paying one stat per link is fine -- there are a handful.
    Paying one per directory would not be, and the difference between those
    two is invisible until someone points the app at a real share.
    """
    if not _can_symlink(tmp_path):
        pytest.skip("filesystem does not support symlinks")

    from helspin.core import dataset_index as di

    real = tmp_path / "real"
    for i in range(20):
        _make_sample(real / f"inst{i % 4}", f"ABC-{950 + i}")

    root = tmp_path / "root"
    root.mkdir()
    for i in range(4):
        (root / f"spect{i}").symlink_to(real / f"inst{i}", target_is_directory=True)

    calls = {"n": 0}
    original = di._link_identity

    def counted(entry):
        calls["n"] += 1
        return original(entry)

    di._link_identity = counted
    try:
        found, _ = di.discover_samples(str(root))
    finally:
        di._link_identity = original

    assert len(found) == 20, "all linked data must be found"
    assert calls["n"] <= 4, (
        f"{calls['n']} stat calls for 4 symlinks over 20 samples: the cost is "
        "tracking directories, not links"
    )
