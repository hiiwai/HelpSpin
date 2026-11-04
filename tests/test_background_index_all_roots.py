"""0.9.15: every data root is indexed, not just the last one to start.

Reported: "browsing is slow on data roots I added before; removing a root and
adding it again makes it fast". The background indexer kept ONE current
root and replaced its queue whenever another root started. At start-up every
configured root starts within milliseconds, so all but the last lost their
indexing, every session: they never became fully cached, each launch redid
part of the work, and opening a sample there meant listing it over the share
again. Re-adding a root made it the last one, which is why that "helped".

As elsewhere in this suite, cost is asserted as a COUNT of filesystem calls
(the thing a share's latency multiplies), not as wall-clock time.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui import dataset_model as dm
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import OpCounter, make_expno

pytestmark = pytest.mark.usefixtures("qapp")

ROOTS, SAMPLES, EXPNOS = 4, 6, 3


def build_roots(tmp_path, roots=ROOTS):
    out = []
    for r in range(roots):
        base = tmp_path / f"root{r}"
        for s in range(SAMPLES):
            for e in range(1, EXPNOS + 1):
                make_expno(base / f"group{s % 2}" / f"S{r}_{s:02d}", e)
        out.append(DataRoot(name=f"R{r}", path=base))
    return out


def idle(browser) -> bool:
    p = browser._populator
    return not p._inflight and not p._bg_queue and not p._bg_active


def open_browser(qtbot, roots):
    browser = DatasetBrowser(roots)
    qtbot.addWidget(browser)
    qtbot.waitUntil(
        lambda: all(n.children for n in browser.model.root_nodes()), timeout=5000
    )
    return browser


def fully_indexed(root) -> bool:
    index = di.load_index(root.path)
    return (index is not None and len(index.samples) == SAMPLES
            and all(s.detailed and s.meta_complete for s in index.samples))


def test_every_root_is_indexed_in_one_session(qtbot, tmp_path):
    roots = build_roots(tmp_path)
    browser = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    browser.shutdown()
    assert [fully_indexed(r) for r in roots] == [True] * ROOTS


def test_the_next_session_reads_nothing_but_one_stat_per_sample(
        qtbot, tmp_path, monkeypatch):
    roots = build_roots(tmp_path)
    first = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(first), timeout=15000)
    first.shutdown()

    calls = {"scan": 0, "meta": 0}
    real_scan, real_meta = di.scan_expnos, dm.meta_values
    monkeypatch.setattr(di, "scan_expnos",
                        lambda *a: (calls.__setitem__("scan", calls["scan"] + 1),
                                    real_scan(*a))[1])
    monkeypatch.setattr(dm, "meta_values",
                        lambda *a: (calls.__setitem__("meta", calls["meta"] + 1),
                                    real_meta(*a))[1])
    with OpCounter(monkeypatch) as ops:
        second = open_browser(qtbot, roots)
        qtbot.waitUntil(lambda: idle(second), timeout=15000)
        second.shutdown()
    assert calls == {"scan": 0, "meta": 0}
    # The staleness check: one stat per sample per root, nothing per expno.
    assert ops.counts["scandir"] == 0
    assert ops.counts["stat"] <= ROOTS * SAMPLES + 10


def test_opening_a_sample_in_any_root_needs_no_io_next_session(
        qtbot, tmp_path, monkeypatch):
    roots = build_roots(tmp_path)
    first = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(first), timeout=15000)
    first.shutdown()

    second = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(second), timeout=15000)
    model = second.model
    with OpCounter(monkeypatch) as ops:
        for root_node in model.root_nodes():          # the FIRST root too
            sample = root_node.children[0]
            second._request_populate(model._index_for(sample))
            assert sample.children
            assert all(not c.probe_pending for c in sample.children)
    assert ops.total == 0
    second.shutdown()


def test_asking_twice_never_queues_a_sample_twice(qtbot, tmp_path):
    roots = build_roots(tmp_path, roots=1)
    browser = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    populator = browser._populator
    node = browser.model.root_nodes()[0]
    for sample in browser.model.cached_index(node.path).samples:
        sample.detailed = False                     # as a stale check does
    populator.INDEX_INFLIGHT = 0                    # hold everything queued
    populator.start_background_index(node)
    queued = len(populator._bg_queue)
    populator.start_background_index(node)
    assert len(populator._bg_queue) == queued
    assert populator._bg_pending[id(node)] == queued
    populator.INDEX_INFLIGHT = 3
    populator._pump_background()
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    assert not populator._bg_scheduled and not populator._bg_pending
    browser.shutdown()


def test_a_removed_root_loses_its_queued_work_not_the_others(qtbot, tmp_path,
                                                              monkeypatch):
    roots = build_roots(tmp_path, roots=2)
    browser = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    populator = browser._populator
    populator.INDEX_INFLIGHT = 0
    for node in browser.model.root_nodes():
        index = browser.model.cached_index(node.path)
        for sample in index.samples:
            sample.detailed = False
        populator.start_background_index(node)
    gone, kept = browser.model.root_nodes()
    browser.remove_data_root_node(gone)

    seen = []
    real = dm._IndexTask.__init__

    def spy(self, reader, root_node, jobs, token):
        seen.append(root_node)
        real(self, reader, root_node, jobs, token)

    monkeypatch.setattr(dm._IndexTask, "__init__", spy)
    # Only the removed root's work went: removing one root used to cancel
    # every root's indexing.
    assert len(populator._bg_queue) == SAMPLES
    assert all(owner is kept for owner, _job in populator._bg_queue)
    populator.INDEX_INFLIGHT = 3
    populator._pump_background()
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    assert gone not in seen and kept in seen
    assert id(gone) not in populator._bg_pending
    index = browser.model.cached_index(kept.path)
    assert all(s.detailed for s in index.samples)
    browser.shutdown()


def test_progress_counts_all_roots_and_ends_cleared(qtbot, tmp_path):
    roots = build_roots(tmp_path)
    browser = DatasetBrowser(roots)
    qtbot.addWidget(browser)
    reports = []
    browser._populator.indexProgress.connect(
        lambda root, done, total: reports.append((done, total))
    )
    qtbot.waitUntil(lambda: all(n.children for n in browser.model.root_nodes()),
                    timeout=5000)
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    assert reports, "no progress was reported"
    assert max(total for _done, total in reports) >= ROOTS * SAMPLES - SAMPLES
    assert all(done <= total or total == 0 for done, total in reports)
    assert reports[-1] == (0, 0)
    browser.shutdown()


def test_a_save_task_is_released_after_writing(qtbot, tmp_path):
    """Each save used to stay in _inflight for the whole session, holding a
    full copy of the index -- megabytes per save on a big share."""
    roots = build_roots(tmp_path, roots=1)
    browser = open_browser(qtbot, roots)
    qtbot.waitUntil(lambda: idle(browser), timeout=15000)
    populator = browser._populator
    populator._model.mark_dirty(roots[0].path)
    populator._flush_index()
    saves = [t for t in populator._inflight if isinstance(t, dm._SaveTask)]
    assert saves
    qtbot.waitUntil(lambda: not any(isinstance(t, dm._SaveTask)
                                    for t in populator._inflight), timeout=5000)
    assert all(t._payload is None for t in saves)
    browser.shutdown()


# ------------------------------------------- the same folder added twice


def test_adding_the_same_folder_twice_is_refused(qtbot, tmp_path, monkeypatch):
    import helspin.__main__ as main_module

    folder = tmp_path / "600"
    folder.mkdir()
    monkeypatch.setattr(main_module, "load_data_roots",
                        lambda: [DataRoot(name="600 MHz", path=folder)])
    window = main_module.MainWindow()
    qtbot.addWidget(window)
    asked = []
    monkeypatch.setattr(main_module.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(folder) + "/"))
    monkeypatch.setattr(main_module.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: asked.append(1) or ("x", True)))
    window._add_data_root()
    assert asked == []
    assert len(window._browser.data_roots()) == 1
    assert "already a data root" in window.statusBar().currentMessage()
    window._browser.shutdown()
    QSettings().clear()


def test_a_different_folder_is_still_added(qtbot, tmp_path, monkeypatch):
    import helspin.__main__ as main_module

    first, second = tmp_path / "600", tmp_path / "800"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(main_module, "load_data_roots",
                        lambda: [DataRoot(name="600 MHz", path=first)])
    window = main_module.MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(main_module.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(second)))
    monkeypatch.setattr(main_module.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("800 MHz", True)))
    window._add_data_root()
    assert [r.name for r in window._browser.data_roots()] == ["600 MHz", "800 MHz"]
    window._browser.shutdown()
    QSettings().clear()
