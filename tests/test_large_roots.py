"""0.9.17: data roots of up to 100,000 samples, without slowing small ones.

The limit was raised from 20,000 after measuring what a big root costs the
GUI thread. Three costs grew faster than the root and are pinned here:

* each streamed batch of 40 samples rebuilt a path -> row map of EVERY row
  (quadratic: 19 s of frozen window at 100,000 samples, now ~1 s);
* the index was serialised on the GUI thread every 4 s while indexing
  (1.6 s each at 100,000: a third of the time frozen);
* every filter keystroke re-tested every sample row (0.7 s at 100,000).

And because a root at the limit silently loses its newest data, reaching
(or nearing) the limit now says so, once, in a dialog.
"""

from __future__ import annotations

import dataclasses
import time

import pytest
from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui import dataset_model as dm
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")


def fake_model(n_root="/fake/root"):
    model = dm.DatasetTreeModel([DataRoot(name="R", path=n_root)])
    populator = dm.DatasetPopulator(model)
    populator.start_background_index = lambda *a, **k: None     # no I/O
    return model, populator, model.root_nodes()[0]


# ------------------------------------------------ streaming stays linear


def test_streaming_100000_samples_is_linear(qtbot):
    model, populator, root = fake_model()
    paths = [f"/fake/root/u{i % 50}/S{i:07d}" for i in range(100_000)]
    started = time.perf_counter()
    for i in range(0, len(paths), di.DISCOVER_BATCH):
        populator._on_sample_batch(None, root, paths[i:i + di.DISCOVER_BATCH])
    elapsed = time.perf_counter() - started
    assert len(root.children) == 100_000
    # Quadratic took ~19 s here; linear takes ~1-2 s. Generous for slow CI.
    assert elapsed < 8.0, f"{elapsed:.1f}s"
    populator.shutdown()


def test_appending_keeps_the_row_map_instead_of_dropping_it(qtbot):
    model, populator, root = fake_model()
    populator._on_sample_batch(None, root, ["/fake/root/a", "/fake/root/b"])
    model.child_map_of(root)
    populator._on_sample_batch(None, root, ["/fake/root/c"])
    assert root.child_map is not None and set(root.child_map) == {
        "/fake/root/a", "/fake/root/b", "/fake/root/c"}
    assert model.child_node(root, "/fake/root/c") is root.children[2]
    populator.shutdown()


def test_duplicates_within_and_across_batches_make_one_row(qtbot):
    model, populator, root = fake_model()
    populator._on_sample_batch(None, root, ["/fake/root/a", "/fake/root/a"])
    populator._on_sample_batch(None, root, ["/fake/root/a", "/fake/root/b"])
    assert [str(c.path) for c in root.children] == ["/fake/root/a", "/fake/root/b"]
    assert len(model.cached_index(root.path).samples) == 2
    populator.shutdown()


# ------------------------------------------------------ saves are paced


@pytest.mark.parametrize("seconds, expected", [
    (0.001, 4000),          # small root: as before
    (0.25, 5000),           # 20,000 samples
    (1.6, 32000),           # 100,000 samples
    (60.0, 120000),         # capped
])
def test_saves_are_spaced_by_how_long_serialising_takes(qtbot, seconds, expected):
    _model, populator, _root = fake_model()
    populator._pace_saves(seconds)
    assert populator._save_timer.interval() == expected
    populator.shutdown()


# --------------------------------------------- the limit, and warnings


def test_the_limit_is_100000():
    assert di.SAMPLE_LIMIT == 100_000
    assert 0 < di.NEAR_LIMIT_FRACTION < 1


def _root_with(tmp_path, n):
    root = tmp_path / "nmr"
    for i in range(n):
        make_expno(root / f"S{i:03d}", 1)
    return root


def test_reaching_the_limit_is_reported(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(di, "SAMPLE_LIMIT", 10)
    browser = DatasetBrowser([DataRoot(name="600", path=_root_with(tmp_path, 12))])
    qtbot.addWidget(browser)
    with qtbot.waitSignal(browser.sampleLimit, timeout=5000) as blocker:
        pass
    path, found, reached = blocker.args
    assert reached is True and found == 10
    assert browser.model.root_nodes()[0].truncated
    browser.shutdown()


def test_nearing_the_limit_is_reported(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(di, "SAMPLE_LIMIT", 10)
    browser = DatasetBrowser([DataRoot(name="600", path=_root_with(tmp_path, 9))])
    qtbot.addWidget(browser)
    with qtbot.waitSignal(browser.sampleLimit, timeout=5000) as blocker:
        pass
    assert blocker.args[1:] == [9, False]
    assert not browser.model.root_nodes()[0].truncated
    browser.shutdown()


def test_a_root_well_under_the_limit_says_nothing(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(di, "SAMPLE_LIMIT", 10)
    browser = DatasetBrowser([DataRoot(name="600", path=_root_with(tmp_path, 5))])
    qtbot.addWidget(browser)
    said = []
    browser.sampleLimit.connect(lambda *a: said.append(a))
    qtbot.waitUntil(lambda: bool(browser.model.root_nodes()[0].children), timeout=5000)
    qtbot.wait(100)
    assert said == []
    browser.shutdown()


def test_the_window_warns_once_per_root_and_kind(qtbot, monkeypatch):
    import helspin.__main__ as main_module

    monkeypatch.setattr(main_module, "load_data_roots", lambda: [])
    window = main_module.MainWindow()
    qtbot.addWidget(window)
    shown = []
    monkeypatch.setattr(main_module.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    window._on_sample_limit("/data/600", 90_000, False)
    window._on_sample_limit("/data/600", 90_000, False)      # same: silent
    window._on_sample_limit("/data/600", 100_000, True)      # now reached
    window._on_sample_limit("/data/600", 100_000, True)
    window._on_sample_limit("/data/800", 100_000, True)      # another root
    assert len(shown) == 3
    assert "close to" in shown[0] and "NEWEST" in shown[1]
    window._browser.shutdown()


# ------------------------------------------------ filtering a big root


def test_small_roots_filter_at_once(qtbot, tmp_path):
    browser = DatasetBrowser([DataRoot(name="600", path=_root_with(tmp_path, 3))])
    qtbot.addWidget(browser)
    qtbot.waitUntil(lambda: bool(browser.model.root_nodes()[0].children), timeout=5000)
    browser._filter_edit.setText("S001")
    assert browser._proxy.filterRegularExpression().pattern() == "S001"
    browser.shutdown()


def test_big_roots_filter_once_typing_pauses(qtbot, tmp_path, monkeypatch):
    browser = DatasetBrowser([DataRoot(name="600", path=_root_with(tmp_path, 3))])
    qtbot.addWidget(browser)
    qtbot.waitUntil(lambda: bool(browser.model.root_nodes()[0].children), timeout=5000)
    monkeypatch.setattr(browser, "FILTER_INSTANT_ROWS", 0)
    applied = []
    real = browser._proxy.setFilterRegularExpression
    monkeypatch.setattr(browser._proxy, "setFilterRegularExpression",
                        lambda rx: applied.append(rx.pattern()) or real(rx))
    for text in ("S", "S0", "S00", "S001"):
        browser._filter_edit.setText(text)
    assert applied == []
    qtbot.waitUntil(lambda: applied == ["S001"], timeout=2000)
    browser.shutdown()


# ------------------------------------------------ the cache rows themselves


def test_every_flag_combination_survives_the_cache_round_trip():
    for flags in range(512):
        entry = di.ExpnoEntry("7", *di._decode_flags(flags), "note", "zg",
                              "19F", "D2O", "2026", 2, "")
        back = di.ExpnoEntry.from_row(entry.as_row())
        assert dataclasses.astuple(back) == dataclasses.astuple(entry), flags


def test_older_row_forms_still_load():
    assert di.ExpnoEntry.from_row(["3", True, False]).has_acqus is True
    short = di.ExpnoEntry.from_row(["4", 2 | 64])
    assert short.has_pdata and short.meta and short.pulprog == ""
    seven = di.ExpnoEntry.from_row(["5", 2, "zg", "1H", "D2O", "d", 1])
    assert seven.pulprog == "zg" and seven.processed_note == ""


def test_entries_use_slots():
    """A million ExpnoEntry objects for a 100,000-sample root: no per-object
    attribute dict."""
    assert not hasattr(di.ExpnoEntry("1"), "__dict__")
    assert not hasattr(di.SampleEntry(path="/x"), "__dict__")
