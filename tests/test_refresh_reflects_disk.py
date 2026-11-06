"""0.9.16: finding data quickly, and Refresh showing what is on disk.

Reported: "the explorer does not find data as quickly as before, and Refresh
does not really reflect the files -- is it because there are too many?"

* Refresh re-walked the root for new and removed SAMPLES, but never looked
  inside known samples it had not expanded. An experiment processed after it
  was indexed (pdata written inside the experiment folder, which leaves the
  sample folder's date untouched) stayed hidden however often Refresh ran.
* A root with more than 5000 samples stopped there, silently; the walk is
  sorted by name, so the newest (date-named) data was the part never found.
* Discovery checked directories one after another, one round trip each.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui import dataset_model as dm
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")

OLD = time.time() - 60 * 24 * 3600      # well outside RECENT_SECONDS


def idle(browser) -> bool:
    p = browser._populator
    return not p._inflight and not p._bg_queue and not p._bg_active


def session(qtbot, root: Path) -> DatasetBrowser:
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    qtbot.waitUntil(lambda: browser.model.root_nodes()[0].children is not None
                    and idle(browser), timeout=15000)
    return browser


def unprocessed_expno(sample: Path, expno: int) -> Path:
    """Acquired but not processed yet: acqus and fid, no pdata."""
    d = sample / str(expno)
    d.mkdir(parents=True)
    (d / "acqus").write_text("##$NUC1= <1H>\n")
    (d / "fid").write_bytes(b"\0" * 8)
    return d


def process(expno_dir: Path, keep_mtime_of: Path) -> None:
    """Process it the way TopSpin does -- inside the experiment folder, so
    the sample folder's date does not change."""
    stamp = os.stat(keep_mtime_of)
    procno = expno_dir / "pdata" / "1"
    procno.mkdir(parents=True)
    (procno / "1r").write_bytes(b"\0" * 8)
    os.utime(keep_mtime_of, (stamp.st_atime, stamp.st_mtime))


def shown(browser, sample_path: Path) -> list[str]:
    model = browser.model
    root = model.root_nodes()[0]
    node = next(c for c in root.children if Path(c.path) == sample_path)
    browser._request_populate(model._index_for(node))
    return [c.display_name for c in node.children or []]


def build(tmp_path, old=False):
    root = tmp_path / "nmr"
    sample = root / "261103_DNA"
    make_expno(sample, 1)
    pending = unprocessed_expno(sample, 2)
    for _ in range(3):
        make_expno(root / f"2610{_:02d}_OTHER", 1)
    if old:
        os.utime(sample, (OLD, OLD))
    return root, sample, pending


# ---------------------------------------------- Refresh reaches inside samples


def test_refresh_shows_an_experiment_processed_since_indexing(qtbot, tmp_path):
    root, sample, pending = build(tmp_path, old=True)
    first = session(qtbot, root)
    assert shown(first, sample) == ["1"]          # 2 has no pdata yet
    first.shutdown()

    process(pending, keep_mtime_of=sample)
    second = session(qtbot, root)
    assert shown(second, sample) == ["1"]         # old sample: not re-read at start
    second.refresh_all()
    qtbot.waitUntil(lambda: idle(second) and not second._populator._refreshing,
                    timeout=15000)
    assert shown(second, sample) == ["1", "2"]
    second.shutdown()


def test_refresh_of_a_collapsed_sample_needs_no_expansion_first(qtbot, tmp_path):
    root, sample, pending = build(tmp_path, old=True)
    first = session(qtbot, root)
    first.shutdown()
    process(pending, keep_mtime_of=sample)
    second = session(qtbot, root)
    second.refresh_all()                          # nothing expanded at all
    qtbot.waitUntil(lambda: idle(second) and not second._populator._refreshing,
                    timeout=15000)
    entry = second.model.cached_index(root).find(str(sample))
    assert [e.name for e in entry.expnos if e.displayable] == ["1", "2"]
    second.shutdown()


def test_a_recent_sample_is_rechecked_at_start_up_without_refresh(qtbot, tmp_path):
    root, sample, pending = build(tmp_path, old=False)
    first = session(qtbot, root)
    first.shutdown()
    process(pending, keep_mtime_of=sample)
    second = session(qtbot, root)
    assert shown(second, sample) == ["1", "2"]
    second.shutdown()


def test_old_settled_samples_are_not_reread_at_start_up(qtbot, tmp_path, monkeypatch):
    root, sample, _pending = build(tmp_path, old=True)
    first = session(qtbot, root)
    first.shutdown()
    scans = []
    real = di.scan_expnos
    monkeypatch.setattr(di, "scan_expnos", lambda p: scans.append(p) or real(p))
    second = session(qtbot, root)
    assert scans == []
    second.shutdown()


def test_a_relisted_expanded_sample_gains_rows_for_new_experiments(qtbot, tmp_path):
    root, sample, pending = build(tmp_path, old=True)
    browser = session(qtbot, root)
    assert shown(browser, sample) == ["1"]
    process(pending, keep_mtime_of=sample)
    entry = browser.model.cached_index(root).find(str(sample))
    entry.detailed = False                        # as a stale check decides
    browser._populator.start_background_index(browser.model.root_nodes()[0])
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    node = next(c for c in browser.model.root_nodes()[0].children
                if Path(c.path) == sample)
    assert [c.display_name for c in node.children] == ["1", "2"]
    browser.shutdown()


def test_relisting_rereads_only_new_or_unsettled_experiments(qtbot, tmp_path,
                                                             monkeypatch):
    root = tmp_path / "nmr"
    sample = root / "S"
    for e in range(1, 6):
        make_expno(sample, e)
    browser = session(qtbot, root)
    make_expno(sample, 6)                         # one new experiment
    reads = []
    real = dm.meta_values
    monkeypatch.setattr(dm, "meta_values",
                        lambda r, path, e, d: reads.append(Path(path).name)
                        or real(r, path, e, d))
    entry = browser.model.cached_index(root).find(str(sample))
    entry.detailed = False
    browser._populator.start_background_index(browser.model.root_nodes()[0])
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    assert reads == ["6"]
    assert all(e.meta and e.has_processed for e in entry.expnos)
    browser.shutdown()


def test_unsettled_and_settled_are_told_apart():
    sample = di.SampleEntry(path="/x", detailed=True, expnos=[
        di.ExpnoEntry(name="1", has_acqus=True, has_pdata=True, meta=True,
                      has_processed=True),
    ])
    assert not sample.unsettled and sample.settled_names() == {"1"}
    for change in ({"has_pdata": False}, {"has_processed": False},
                   {"has_acqus": False}):
        expno = di.ExpnoEntry(name="2", **{"has_acqus": True, "has_pdata": True,
                                           "meta": True, "has_processed": True,
                                           **change})
        sample.expnos.append(expno)
        assert sample.unsettled
        assert "2" not in sample.settled_names()
        sample.expnos.pop()
    errored = di.ExpnoEntry(name="3", has_acqus=True, has_pdata=True, meta=True,
                            has_processed=True, error="bad acqus")
    sample.expnos.append(errored)
    assert "3" not in sample.settled_names()
    assert not di.SampleEntry(path="/y").unsettled      # never listed


def test_merge_carries_processed_only_once_true(qtbot, tmp_path):
    model = dm.DatasetTreeModel([])
    old = di.SampleEntry(path=str(tmp_path), detailed=True, expnos=[
        di.ExpnoEntry(name="1", has_pdata=True, meta=True, has_processed=True,
                      processed_note="ok"),
        di.ExpnoEntry(name="2", has_pdata=True, meta=True, has_processed=False),
    ])
    fresh = [di.ExpnoEntry(name="1", has_pdata=True),
             di.ExpnoEntry(name="2", has_pdata=True)]
    model.merge_expnos(old, fresh, str(tmp_path))
    by = {e.name: e for e in old.expnos}
    assert by["1"].has_processed is True and by["1"].processed_note == "ok"
    assert by["2"].has_processed is None          # looked at again


# ------------------------------------------------------- finding data quickly


def _layout(tmp_path):
    root = tmp_path / "share"
    for g in range(4):
        for s in range(12):
            make_expno(root / f"user{g}" / f"2610{s:02d}_x{g}", 1)
    make_expno(root / "deep" / "a" / "b" / "S_deep", 1)
    (root / "empty_dir").mkdir()
    os.symlink(root / "user0", root / "zz_link_back")   # loop into the root
    return root


@pytest.mark.parametrize("workers", [2, 8, 32])
def test_parallel_discovery_finds_the_same_in_the_same_order(tmp_path, workers):
    root = _layout(tmp_path)
    serial, t1 = di.discover_samples(root, workers=1)
    parallel, t2 = di.discover_samples(root, workers=workers)
    assert parallel == serial and (t1, t2) == (False, False)
    assert len(serial) == 4 * 12 + 1


def test_parallel_discovery_streams_batches_and_respects_the_limit(tmp_path):
    root = _layout(tmp_path)
    batches = []
    found, truncated = di.discover_samples(root, limit=10, workers=8,
                                           on_batch=batches.append, batch_size=4)
    serial, _ = di.discover_samples(root, workers=1)
    assert truncated and found == serial[:10]
    assert sum(batches, []) == found


def test_parallel_discovery_stops_when_asked(tmp_path):
    root = _layout(tmp_path)
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 3

    found, truncated = di.discover_samples(root, workers=8, should_stop=stop)
    assert not truncated and len(found) < 49


def test_a_root_that_is_itself_a_sample_with_workers(tmp_path):
    sample = tmp_path / "S"
    make_expno(sample, 1)
    assert di.discover_samples(sample, workers=8) == ([str(sample)], False)


def test_the_app_searches_up_to_the_new_limit_with_parallel_checks(monkeypatch,
                                                                   tmp_path):
    seen = {}
    real = di.discover_samples

    def spy(root, **kwargs):
        seen.update(kwargs)
        return real(root, **kwargs)

    monkeypatch.setattr(di, "discover_samples", spy)
    make_expno(tmp_path / "nmr" / "S", 1)
    node = dm.Node(kind=dm.NodeKind.DATA_ROOT, path=tmp_path / "nmr",
                   display_name="600")
    task = dm._DiscoverTask(node, dm.CancelToken(), use_cache=False)
    task.run()
    assert di.SAMPLE_LIMIT >= 20000           # 100,000 since 0.9.17
    assert seen["limit"] == di.SAMPLE_LIMIT
    assert seen["workers"] == di.DISCOVER_WORKERS > 1


def test_a_truncated_root_says_so_on_its_row(qtbot, tmp_path):
    from PySide6.QtCore import Qt

    root = tmp_path / "nmr"
    make_expno(root / "S", 1)
    browser = session(qtbot, root)
    node = browser.model.root_nodes()[0]
    index = browser.model._index_for(node)
    assert browser.model.data(index, Qt.DisplayRole) == "600"
    node.truncated = True
    label = browser.model.data(index, Qt.DisplayRole)
    assert label.startswith("600") and str(di.SAMPLE_LIMIT) in label
    assert "more specific folder" in browser.model.data(index, Qt.ToolTipRole)
    browser.shutdown()
