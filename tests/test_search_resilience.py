"""0.9.20: nothing about one folder may hide, or stop, the search of others.

Reported (again, on 0.9.19): nothing under NMR600_2026/data/ETHZ is found on
a large shared volume, although `helspin --why` walks straight to it. --why
looks only at the folders on the way; the search lists every sibling too,
and two faults there could lose data silently:

* one entry whose type could not be read (PermissionError from is_dir() --
  a link into a folder without permission, an entry the share will not
  stat) made the WHOLE folder's listing come back empty;
* anything unexpected in one folder (a "²"-named folder: isdigit() but not
  int()) raised out of the search and ended it, so everything later in name
  order -- on a volume, the newest year -- was never reached. The failure
  went to a signal nothing listened to.
"""

from __future__ import annotations

import os

import pytest

from helspin.core import dataset_index as di
from helspin.domain import paths as dp
from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")

SAMPLE = "NMR600_2026/data/ETHZ/Shared/NRAS/20260821_NRAS_Hideo_Hari_700"


def volume(tmp_path):
    root = tmp_path / "Active_RD_NMR600"
    make_expno(root / SAMPLE, 5)
    make_expno(root / "NMR600_2025" / "data" / "UH" / "S2025", 1)
    (root / "NMR600_2026" / "data" / "AAA_restricted").mkdir(parents=True)
    return root


class _Entry:
    def __init__(self, real, bad_dir, bad_link):
        self._real, self._bad_dir, self._bad_link = real, bad_dir, bad_link
        self.name, self.path = real.name, real.path

    def is_dir(self, *a, **k):
        if self.name in self._bad_dir:
            raise PermissionError(13, "Permission denied", self.path)
        return self._real.is_dir(*a, **k)

    def is_symlink(self):
        if self.name in self._bad_link:
            raise OSError(5, "Input/output error", self.path)
        return self._real.is_symlink()

    def stat(self, *a, **k):
        return self._real.stat(*a, **k)


class _Listing:
    def __init__(self, real, bad_dir, bad_link):
        self._real = real
        self._bad = (bad_dir, bad_link)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._real.close()

    def __iter__(self):
        return (_Entry(e, *self._bad) for e in self._real)


@pytest.fixture
def odd_share(monkeypatch):
    """Make chosen entries unreadable, as a big shared volume does."""
    state = {"bad_dir": set(), "bad_link": set(), "fail_dirs": {}}
    real_scandir = os.scandir

    def scandir(path="."):
        failures = state["fail_dirs"]
        name = os.path.basename(os.fspath(path))
        if failures.get(name, 0) > 0:
            failures[name] -= 1
            raise OSError(5, "Input/output error", path)
        return _Listing(real_scandir(path), state["bad_dir"], state["bad_link"])

    monkeypatch.setattr(os, "scandir", scandir)
    return state


def test_an_unreadable_entry_hides_only_itself(tmp_path, odd_share):
    root = volume(tmp_path)
    odd_share["bad_dir"].add("AAA_restricted")      # beside ETHZ in data/
    problems = []
    found, _ = di.discover_samples(root, workers=8, problems=problems)
    assert str(root / SAMPLE) in found
    assert any("AAA_restricted" in p and "PermissionError" in e
               for p, e in problems)


def test_an_entry_whose_link_state_cannot_be_read_is_still_searched(tmp_path,
                                                                   odd_share):
    root = volume(tmp_path)
    odd_share["bad_link"].add("ETHZ")
    problems = []
    found, _ = di.discover_samples(root, workers=8, problems=problems)
    assert str(root / SAMPLE) in found
    assert any(p.endswith("ETHZ") for p, _e in problems)


def test_a_unicode_digit_folder_does_not_stop_the_search(tmp_path):
    root = volume(tmp_path)
    (root / "NMR600_2025" / "data" / "UH" / "weird" / "²").mkdir(parents=True)
    found, _ = di.discover_samples(root, workers=8)
    assert str(root / SAMPLE) in found      # searched AFTER the odd folder
    assert not dp.is_number_name("²") and dp.is_number_name("12")
    assert not dp.is_number_name("１２")       # full-width: not a Bruker number


def test_an_unexpected_error_in_one_folder_skips_only_that_folder(tmp_path,
                                                                  monkeypatch):
    root = volume(tmp_path)
    real = os.scandir

    def flaky(path="."):
        if os.fspath(path).endswith("UH"):
            raise RuntimeError("something odd")       # not even an OSError
        return real(path)

    monkeypatch.setattr(os, "scandir", flaky)
    problems = []
    found, _ = di.discover_samples(root, workers=8, problems=problems)
    assert str(root / SAMPLE) in found
    assert any(p.endswith("UH") and "something odd" in e for p, e in problems)


def test_a_listing_that_fails_once_is_retried(tmp_path, odd_share):
    root = volume(tmp_path)
    odd_share["fail_dirs"]["data"] = 1          # every "data" fails once
    problems = []
    found, _ = di.discover_samples(root, problems=problems)
    assert str(root / SAMPLE) in found and problems == []


def test_a_listing_that_keeps_failing_is_recorded_and_skipped(tmp_path, odd_share):
    root = volume(tmp_path)
    odd_share["fail_dirs"]["NMR600_2025"] = 99
    problems = []
    found, _ = di.discover_samples(root, problems=problems)
    assert found == [str(root / SAMPLE)]
    assert any(p.endswith("NMR600_2025") for p, _e in problems)


def test_problems_are_capped(tmp_path):
    problems = []
    for i in range(di.MAX_PROBLEMS + 50):
        di._note(problems, f"/x/{i}", OSError("no"))
    assert len(problems) == di.MAX_PROBLEMS
    di._note(None, "/x", OSError("ignored without a list"))


def test_numbered_subfolders_keep_what_they_could_read(tmp_path, odd_share):
    sample = tmp_path / "S"
    make_expno(sample, 1)
    make_expno(sample, 2)
    odd_share["bad_dir"].add("1")
    assert [e.name for e in dp.numbered_subfolders(str(sample))] == ["2"]
    assert dp.holds_experiments(str(sample))


# ---------------------------------------- the explorer reports what it skipped


def test_the_explorer_finds_it_and_reports_skipped_folders(qtbot, tmp_path,
                                                           odd_share):
    root = volume(tmp_path)
    odd_share["bad_dir"].add("AAA_restricted")
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    messages = []
    browser.statusChanged.connect(messages.append)
    node = browser.model.root_nodes()[0]
    qtbot.waitUntil(lambda: any("could not be read" in m for m in messages),
                    timeout=10000)
    assert str(root / SAMPLE) in {str(c.path) for c in node.children}
    index = browser.model.cached_index(node.path)
    assert index.problems and "AAA_restricted" in index.problems[0][0]
    browser.shutdown()
    assert "could not be read" in di.explain_cache(root, root / "missing")


def test_a_failed_search_is_no_longer_silent(qtbot, tmp_path):
    browser = DatasetBrowser([DataRoot(name="600", path=tmp_path / "nmr")])
    qtbot.addWidget(browser)
    messages = []
    browser.statusChanged.connect(messages.append)
    browser.model.scanFailed.emit("/data/600", "share went away")
    assert "Could not search /data/600: share went away" in messages
    browser.shutdown()


# ------------------------------------------------------- --why and --scan


def test_why_says_when_the_search_cannot_see_a_folder(tmp_path, odd_share):
    root = volume(tmp_path)
    odd_share["fail_dirs"]["Shared"] = 99       # Shared's listing always fails
    text = "\n".join(di.explain_path(root, root / SAMPLE))
    assert "NOT IN THE SEARCH'S LISTING of Shared" in text
    assert "problem:" in text


def test_why_reports_links(tmp_path):
    root = tmp_path / "vol"
    outside = tmp_path / "elsewhere"
    make_expno(outside / "S", 1)
    root.mkdir()
    os.symlink(outside, root / "linked")
    os.symlink(tmp_path / "missing", root / "broken")
    assert "a link to" in "\n".join(di.explain_path(root, root / "linked" / "S"))
    text = "\n".join(di.explain_path(root, root / "broken" / "S"))
    assert "not a folder" in text or "cannot be read" in text


def test_scan_report(tmp_path, odd_share):
    root = volume(tmp_path)
    odd_share["bad_dir"].add("AAA_restricted")
    lines = []
    seen = []
    n = di.scan_report(root, find=root / SAMPLE / "5" / "pdata" / "1",
                       out=lines.append, progress=lambda c, last: seen.append(c))
    text = "\n".join(lines)
    assert n == 2 and seen
    assert "2 samples" in text and "NMR600_2025: 1" in text and "NMR600_2026: 1" in text
    assert "FOUND as sample" in text and "AAA_restricted" in text
    lines.clear()
    di.scan_report(root, find=root / "nope", out=lines.append)
    assert "NOT found" in "\n".join(lines)


def test_scan_command_line(tmp_path, monkeypatch, capsys):
    import helspin.__main__ as main_module

    root = volume(tmp_path)
    monkeypatch.setattr(main_module.sys, "argv",
                        ["helspin", "--scan", str(root), "--find", str(root / SAMPLE)])
    assert main_module.main() == 0
    out = capsys.readouterr().out
    assert "FOUND as sample" in out and "Every folder could be read." in out
    monkeypatch.setattr(main_module.sys, "argv",
                        ["helspin", "--scan", str(tmp_path / "nowhere")])
    assert main_module.main() == 2


# ------------------------------------------- a search too big for one sitting
#
# Reported data, 0.9.19: the saved search of /Volumes/Active_RD_NMR600 held
# 5000 samples (the old limit), was incomplete, and the last sample by name
# was inside "Fragment libraries" -- before NMR600_2026. Every later start
# searched the whole volume again from the top, and a search cut short by
# closing HelSpin saved nothing, so the end of the volume was never reached.


def big_volume(tmp_path):
    root = tmp_path / "vol"
    for lib in range(3):
        for s in range(5):
            make_expno(root / "Fragment libraries" / f"lib{lib}" / f"Z{s:03d}", 1)
    make_expno(root / SAMPLE, 5)
    make_expno(root / "NMR600_2026" / "data" / "UH" / "S1", 1)
    make_expno(root / "zz_last" / "S", 1)
    return root


@pytest.mark.parametrize("workers", [1, 8])
@pytest.mark.parametrize("at", [0, 4, 7, 14, 16])
def test_a_resumed_search_finds_exactly_the_rest(tmp_path, workers, at):
    root = big_volume(tmp_path)
    full, _ = di.discover_samples(root, workers=workers)
    tail, truncated = di.discover_samples(root, workers=workers,
                                          resume_after=full[at])
    assert tail == full[at + 1:] and not truncated


def test_resuming_after_a_sample_that_is_gone(tmp_path):
    root = big_volume(tmp_path)
    full, _ = di.discover_samples(root)
    gone = str(root / "Fragment libraries" / "lib1" / "Z002x")   # sorts after Z002
    tail, _ = di.discover_samples(root, resume_after=gone)
    assert tail == full[full.index(str(root / "Fragment libraries" / "lib1" / "Z003")):]


def test_resuming_outside_the_root_searches_everything(tmp_path):
    root = big_volume(tmp_path)
    full, _ = di.discover_samples(root)
    assert di.discover_samples(root, resume_after="/elsewhere/S")[0] == full


def test_the_explorer_continues_where_the_last_search_stopped(qtbot, tmp_path,
                                                             monkeypatch):
    root = big_volume(tmp_path)
    full, _ = di.discover_samples(root)
    done = full[:8]                         # what an earlier session reached
    cached = di.RootIndex(root=str(root), complete=False, resume_after=done[-1])
    for path in done:
        cached.add(di.SampleEntry(path=path))
    di.save_index(cached)

    seen = {}
    real = di.discover_samples

    def spy(where, **kwargs):
        seen.update(kwargs)
        return real(where, **kwargs)

    monkeypatch.setattr(di, "discover_samples", spy)
    browser = DatasetBrowser([DataRoot(name="vol", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    p = browser._populator
    qtbot.waitUntil(lambda: node.children is not None and len(node.children) == len(full)
                    and not p._inflight and not p._bg_queue and not p._bg_active,
                    timeout=15000)
    assert seen["resume_after"] == done[-1]
    assert seen["limit"] == di.SAMPLE_LIMIT - len(done)
    assert [str(c.path) for c in node.children] == full     # nothing dropped
    assert str(root / SAMPLE) in {str(c.path) for c in node.children}
    index = browser.model.cached_index(node.path)
    assert index.complete and index.resume_after == ""
    browser.shutdown()
    assert di.load_index(root).complete


def test_progress_is_saved_while_the_search_runs(qtbot, tmp_path):
    root = big_volume(tmp_path)
    full, _ = di.discover_samples(root)
    browser = DatasetBrowser([DataRoot(name="vol", path=tmp_path / "empty")])
    qtbot.addWidget(browser)
    p, model = browser._populator, browser.model
    p.shutdown()                            # stop its own search; drive by hand
    p._alive = True
    node = model.root_nodes()[0]
    node.path = root
    model.install_index(root, di.RootIndex(root=str(root), complete=False))
    p._on_sample_batch(None, node, full[:5])
    index = model.cached_index(root)
    assert index.resume_after == full[4]
    assert str(root) in model._dirty_roots
    p._flush_index(synchronous=True)        # what closing HelSpin does
    saved = di.load_index(root)
    assert saved.resume_after == full[4] and not saved.complete
    assert len(saved.samples) == 5
    p._alive = False


def test_a_complete_index_records_no_resume_point(qtbot, tmp_path):
    root = big_volume(tmp_path)
    browser = DatasetBrowser([DataRoot(name="vol", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    p = browser._populator
    qtbot.waitUntil(lambda: node.children is not None and not p._inflight
                    and not p._bg_queue and not p._bg_active, timeout=15000)
    browser.shutdown()
    saved = di.load_index(root)
    assert saved.complete and saved.resume_after == ""


# ------------------------------------------------- round trips per search


class _CountingEntry:
    def __init__(self, real, counter):
        self._real, self._counter = real, counter
        self.name, self.path = real.name, real.path

    def is_dir(self, *a, **k):
        self._counter["is_dir"] += 1        # a round trip where types are unknown
        return self._real.is_dir(*a, **k)

    def is_symlink(self):
        return self._real.is_symlink()

    def stat(self, *a, **k):
        return self._real.stat(*a, **k)


def test_experiment_folders_are_never_type_checked(tmp_path, monkeypatch):
    """0.9.18-0.9.20 type-checked every numbered entry of every sample: on a
    share that does not report entry types, one round trip per EXPERIMENT
    (2297 round trips for 150 samples where 0.9.17 needed 497)."""
    root = tmp_path / "vol"
    for s in range(6):
        for e in range(1, 31):
            make_expno(root / "nmr" / f"S{s}", e)
    counter = {"is_dir": 0, "scandir": 0}
    real = os.scandir

    class Listing:
        def __init__(self, it):
            self._it = it

        def __enter__(self):
            return self

        def __exit__(self, *x):
            self._it.close()

        def __iter__(self):
            return (_CountingEntry(e, counter) for e in self._it)

    def scandir(path="."):
        counter["scandir"] += 1
        return Listing(real(path))

    monkeypatch.setattr(os, "scandir", scandir)
    found, _ = di.discover_samples(root, workers=8)
    assert len(found) == 6
    # Type checks only for the folders above the samples (vol: 1, nmr: 6).
    assert counter["is_dir"] <= 1 + 6
    # Each folder listed ONCE (vol, nmr, 6 samples), plus one experiment per
    # sample looked into.
    assert counter["scandir"] <= 2 + 6 + 6
