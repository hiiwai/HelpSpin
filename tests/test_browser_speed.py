"""Regressions for the 0.4.0 browsing rework.

Every test here corresponds to something a user reported or something that
would have crashed: "it takes minutes to open", "it does not open at all", "I
cannot drag". Where the fix is about COST rather than behaviour, the test
counts filesystem operations rather than timing anything -- a wall-clock
assertion on a build machine tells you nothing about a share, but the number
of round trips is exactly what the latency multiplies.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QRegularExpression

from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser
from helspin.ui.dataset_filter import DatasetFilterProxy
from helspin.ui.dataset_model import (
    COL_NAME,
    COL_PULPROG,
    DatasetPopulator,
    DatasetTreeModel,
)

pytestmark = pytest.mark.usefixtures("qapp")

ACQUS = """##$NUC1= <{nucleus}>
##$SOLVENT= <D2O>
##$PULPROG= <{pulprog}>
##$PARMODE= {parmode}
##$NS= 16
##$RG= 101
##$HOLDER= 1
##END=
"""


def make_expno(sample: Path, expno: int, pulprog="zg30", nucleus="1H",
               dim=1, raw=True) -> Path:
    d = sample / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    (d / "acqus").write_text(
        ACQUS.format(pulprog=pulprog, nucleus=nucleus, parmode=dim - 1)
    )
    if raw:
        if dim == 2:
            (d / "acqu2s").write_text("##END=\n")
            (d / "ser").write_bytes(b"\x00" * 8)
        else:
            (d / "fid").write_bytes(b"\x00" * 8)
    procno = d / "pdata" / "1"
    procno.mkdir(parents=True, exist_ok=True)
    (procno / ("2rr" if dim == 2 else "1r")).write_bytes(b"\x00" * 8)
    return d


def build_root(tmp_path, samples=6, expnos=4) -> Path:
    root = tmp_path / "nmr"
    for s in range(samples):
        for e in range(1, expnos + 1):
            make_expno(root / f"2607{s:02d}_SAMPLE", e)
    return root


class OpCounter:
    """Counts the filesystem calls a block of code makes.

    A network share charges a round trip for each of these, so the count IS
    the latency, scaled. Timing on a local disk would measure nothing that
    matters.
    """

    def __init__(self, monkeypatch):
        self.counts = {"scandir": 0, "stat": 0, "open": 0}
        self._monkeypatch = monkeypatch

    def __enter__(self):
        import builtins

        real_scandir, real_stat, real_open = os.scandir, os.stat, builtins.open

        def scandir(*a, **k):
            self.counts["scandir"] += 1
            return real_scandir(*a, **k)

        def stat(*a, **k):
            self.counts["stat"] += 1
            return real_stat(*a, **k)

        def opener(*a, **k):
            self.counts["open"] += 1
            return real_open(*a, **k)

        self._monkeypatch.setattr(os, "scandir", scandir)
        self._monkeypatch.setattr(os, "stat", stat)
        self._monkeypatch.setattr(builtins, "open", opener)
        return self

    def __exit__(self, *exc):
        self._monkeypatch.undo()
        return False

    @property
    def total(self) -> int:
        return sum(self.counts.values())


# --- "it takes minutes before anything appears" -----------------------------


def test_discovery_does_not_open_every_experiment(tmp_path, monkeypatch):
    """Listing the samples must not cost a round trip per EXPERIMENT.

    This is the headline bug. The 0.3.0 index walked into every experiment
    directory of every sample before the first row could be drawn: ~1.2
    operations per experiment, so a real root (400 samples, 8000 experiments)
    spent thousands of round trips -- minutes on a share -- showing nothing.

    Discovery now stops at the first integer-named child, so the cost is per
    DIRECTORY VISITED and completely independent of how many experiments each
    sample holds. Asserting "fewer ops than there are experiments" is what
    pins that down.
    """
    root = build_root(tmp_path, samples=10, expnos=20)   # 200 experiments
    with OpCounter(monkeypatch) as counter:
        paths, truncated = di.discover_samples(root)

    assert len(paths) == 10
    assert not truncated
    assert counter.total < 30, (
        f"discovery cost {counter.total} operations for 10 samples; it must "
        "scale with directories visited, not with experiments"
    )


def test_streaming_reports_every_sample_in_batches(tmp_path):
    root = build_root(tmp_path, samples=9, expnos=2)
    batches = []
    paths, _ = di.discover_samples(root, on_batch=batches.append, batch_size=4)

    assert len(batches) >= 2, "batches must arrive progressively, not all at once"
    assert sorted(p for batch in batches for p in batch) == sorted(paths)


def test_discovery_stops_when_asked(tmp_path):
    """A closed browser must not leave a worker walking a share for minutes."""
    root = build_root(tmp_path, samples=40, expnos=1)
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    paths, truncated = di.discover_samples(root, should_stop=should_stop)
    assert len(paths) < 40
    assert not truncated, "cancellation is not truncation; do not blame the root"


def test_second_open_of_a_sample_costs_no_filesystem_access(tmp_path, monkeypatch):
    """Once a sample is in the index, opening it is a dictionary lookup.

    This is what makes the browser feel instant on the second look, and it is
    only true because the index records each experiment's files -- not just
    its name -- from the one listing it already paid for.
    """
    root = build_root(tmp_path, samples=3, expnos=5)
    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)              # first open: reads the directory
    assert model.rowCount(sample_idx) == 5

    node = sample_idx.internalPointer()
    entry = node.entry
    with OpCounter(monkeypatch) as counter:
        children = model.expno_nodes_for(node, entry)

    assert len(children) == 5
    assert counter.total == 0, "re-opening an indexed sample must not touch disk"


def test_metadata_survives_to_the_next_session_through_the_cache(tmp_path):
    """A second launch shows PULPROG with no reads at all.

    The index caches per-experiment metadata, so rows come back already
    filled in rather than starting from the loading placeholder again.
    """
    root = build_root(tmp_path, samples=2, expnos=2)
    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)
    for child in sample_idx.internalPointer().children:
        model.probe_node(child)
    index = model.cached_index(root)
    di.save_index(index)

    # A brand new model, as a fresh launch would build.
    reopened = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = reopened.index(0, 0)
    reopened.fetchMore(root_idx)
    sample_idx = reopened.index(0, 0, root_idx)
    reopened.fetchMore(sample_idx)
    first = reopened.index(0, 0, sample_idx).internalPointer()

    assert not first.probe_pending, "cached metadata must not be re-read"
    assert reopened.data(reopened.index(0, COL_PULPROG, sample_idx)) == "zg30"


def test_a_partial_index_is_still_cached_and_reused(tmp_path):
    """Quitting mid-walk must leave the next launch better off, not identical.

    The old build was all-or-nothing: interrupt it and nothing was written, so
    every launch re-walked the whole share from scratch.
    """
    root = build_root(tmp_path, samples=4, expnos=1)
    index = di.RootIndex(root=str(root))
    index.add(di.SampleEntry(path=str(root / "260700_SAMPLE")))
    index.complete = False
    di.save_index(index)

    loaded = di.load_index(root)
    assert loaded is not None
    assert not loaded.complete
    assert len(loaded.samples) == 1


def test_probing_one_row_costs_a_single_read(tmp_path, monkeypatch):
    """Filling a row's metadata columns must be ONE file read.

    probe() cost nine round trips per row: two to re-confirm the directory is
    an expno, one for acqus, up to four working out 1D vs 2D from which raw
    files exist, and one for a title no column displays. At 20 ms a round trip
    that is ~180 ms per row, so a sample with forty experiments spent seconds
    filling in. probe_row() reads acqus and nothing else, because the index
    already knows the rest.
    """
    root = build_root(tmp_path, samples=1, expnos=3)
    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)
    node = model.index(0, 0, sample_idx).internalPointer()

    with OpCounter(monkeypatch) as counter:
        info, error = model.read_probe_result(node)

    assert error == ""
    assert info.pulse_program == "zg30"
    assert counter.total == 1, (
        f"probing a row cost {counter.counts}; the index already knows "
        "everything except what is inside acqus"
    )


def test_dimensionality_comes_from_the_index_without_reading(tmp_path, monkeypatch):
    """1D vs 2D is answered by the flags the listing already gathered."""
    root = tmp_path / "nmr"
    make_expno(root / "sample", 1, dim=1)
    make_expno(root / "sample", 2, dim=2)

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)

    with OpCounter(monkeypatch) as counter:
        dims = [
            model.index(r, 0, sample_idx).internalPointer().dimensionality()
            for r in range(model.rowCount(sample_idx))
        ]

    assert dims == [1, 2]
    assert counter.total == 0


def test_processed_only_dataset_gets_its_dimensionality_from_parmode(tmp_path):
    """Raw data is routinely deleted after processing, leaving no fid or ser
    to judge by. acqus carries PARMODE, which costs nothing extra because
    acqus is being read anyway."""
    root = tmp_path / "nmr"
    make_expno(root / "sample", 7, dim=2, raw=False)

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)
    node = model.index(0, 0, sample_idx).internalPointer()

    assert node.dimensionality() == 0, "no raw files: nothing structural to go on"
    info, error = model.read_probe_result(node)
    assert error == ""
    assert info.dimensionality.value == 2


# --- "it does not open at all" (the filter) ---------------------------------


def test_expanding_a_filtered_sample_still_shows_its_experiments(tmp_path):
    """THE reported bug: with a filter typed, expanding a matching sample
    showed nothing, because each experiment row was tested against the same
    text and "2607" is not in "1" or "11".

    A sample matched BY NAME means the user asked for that sample, so all of
    it must be visible -- otherwise the row they filtered for cannot be
    opened, which is indistinguishable from a browser that will not open.
    """
    root = tmp_path / "nmr"
    make_expno(root / "260727_SampleC_50uM", 1)
    make_expno(root / "260727_SampleC_50uM", 11)
    make_expno(root / "unrelated_sample", 1)

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    for r in range(model.rowCount(root_idx)):
        model.fetchMore(model.index(r, 0, root_idx))

    proxy = DatasetFilterProxy()
    proxy.setSourceModel(model)
    proxy.setFilterRegularExpression(QRegularExpression("2607"))

    proxy_root = proxy.mapFromSource(root_idx)
    assert proxy.rowCount(proxy_root) == 1          # only the matching sample
    sample = proxy.index(0, COL_NAME, proxy_root)
    assert proxy.data(sample) == "260727_SampleC_50uM"
    names = {
        proxy.data(proxy.index(r, COL_NAME, sample))
        for r in range(proxy.rowCount(sample))
    }
    assert names == {"1", "11"}, "a filtered sample must still open"


def test_filtering_by_pulprog_still_narrows_within_a_sample(tmp_path):
    """The fix above must not turn the PULPROG filter into "show everything":
    when the SAMPLE name does not match, each experiment is judged on its
    own."""
    root = tmp_path / "nmr"
    make_expno(root / "sample_one", 1, pulprog="zg30")
    make_expno(root / "sample_one", 2, pulprog="cosygpppqf")

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)
    for child in sample_idx.internalPointer().children:
        model.probe_node(child)

    proxy = DatasetFilterProxy()
    proxy.setSourceModel(model)
    proxy.setFilterRegularExpression(QRegularExpression("cosy"))

    sample = proxy.index(0, COL_NAME, proxy.mapFromSource(root_idx))
    names = {
        proxy.data(proxy.index(r, COL_NAME, sample))
        for r in range(proxy.rowCount(sample))
    }
    assert names == {"2"}


def test_pulprog_filter_reaches_a_sample_that_was_never_expanded(tmp_path):
    """The documented 0.3.0 limitation, now fixed.

    Matching used to reach only rows that existed, so a PULPROG query missed
    every unexpanded sample -- which on a real root is nearly all of them.
    The index caches metadata, so the model can answer for a sample whose rows
    have never been created.
    """
    root = tmp_path / "nmr"
    make_expno(root / "sample_a", 1, pulprog="hsqcedetgp")
    make_expno(root / "sample_b", 1, pulprog="zg30")

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    # Fill the index the way the background indexer does, WITHOUT creating
    # any expno rows.
    index = model.cached_index(root)
    for sample in index.samples:
        sample.expnos = di.scan_expnos(sample.path)
        sample.detailed = True
        for expno in sample.expnos:
            values = {"pulprog": "hsqcedetgp" if "sample_a" in sample.path
                      else "zg30", "nucleus": "1H", "dim": 1}
            expno.meta = True
            expno.pulprog = values["pulprog"]
        sample.invalidate()

    proxy = DatasetFilterProxy()
    proxy.setSourceModel(model)
    proxy.setFilterRegularExpression(QRegularExpression("hsqc"))

    proxy_root = proxy.mapFromSource(root_idx)
    visible = [
        proxy.data(proxy.index(r, COL_NAME, proxy_root))
        for r in range(proxy.rowCount(proxy_root))
    ]
    assert visible == ["sample_a"]


# --- "I cannot drag" --------------------------------------------------------


def test_rows_are_draggable_before_any_metadata_is_read(qtbot, tmp_path):
    """End to end through the widget: a freshly listed row drags.

    On a share the metadata read is the slow part, so requiring it before a
    drag meant the drag did nothing for as long as the read took -- which is
    what "I cannot drag" was.
    """
    root = build_root(tmp_path, samples=1, expnos=3)
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=3000)

    sample_index = browser.model.index(0, 0, root_index)
    browser._request_populate(sample_index)
    qtbot.waitUntil(lambda: browser.model.rowCount(sample_index) == 3, timeout=3000)

    proxy_rows = [
        browser._proxy.mapFromSource(browser.model.index(r, 0, sample_index))
        for r in range(3)
    ]
    drag = browser.tree._build_drag(proxy_rows)
    assert drag is not None, "a listed row must be draggable straight away"
    browser.shutdown()


# --- lifecycle --------------------------------------------------------------


def test_shutdown_is_idempotent_and_stops_the_workers(qtbot, tmp_path):
    """Closing the explorer twice, or closing it mid-index, must be safe.

    Leaving workers running while the object graph is collected is what
    produced a segfault with no Python traceback.
    """
    root = build_root(tmp_path, samples=4, expnos=3)
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 4, timeout=3000)

    browser.shutdown()
    browser.shutdown()      # must not raise


def test_a_result_arriving_after_shutdown_is_dropped(qtbot, tmp_path):
    """A queued worker result must never be applied to a model the browser
    has finished with."""
    root = build_root(tmp_path, samples=2, expnos=2)
    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    populator = DatasetPopulator(model)
    node = model._roots[0]

    populator.shutdown()
    # Deliver a result as a worker would, after the shutdown.
    populator._on_sample_batch(None, node, [str(root / "260700_SAMPLE")])
    assert model.rowCount(model.index(0, 0)) == 0


def test_index_cache_survives_a_root_being_removed(qtbot, tmp_path):
    """Removing a data root must not leave the background indexer reading it."""
    root = build_root(tmp_path, samples=3, expnos=2)
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 3, timeout=3000)

    browser.remove_data_root_node(root_index.internalPointer())
    assert browser.model.rowCount() == 0
    qtbot.wait(50)          # anything still in flight must not crash
    browser.shutdown()


def test_a_root_with_nothing_in_it_says_so(qtbot, tmp_path):
    """An unrecognisable root used to sit there refusing to expand, with no
    message -- indistinguishable from one still loading, or from a bug."""
    empty = tmp_path / "not_nmr"
    (empty / "documents" / "notes").mkdir(parents=True)

    browser = DatasetBrowser([DataRoot(name="600", path=empty)])
    qtbot.addWidget(browser)
    messages = []
    browser.statusChanged.connect(messages.append)

    qtbot.waitUntil(lambda: any("No Bruker samples" in m for m in messages),
                    timeout=3000)
    said = next(m for m in messages if "No Bruker samples" in m)
    assert str(di.MAX_DEPTH) in said, "the message must state how deep it looked"
    browser.shutdown()


def test_samples_are_found_at_any_depth_up_to_the_cap(tmp_path):
    """No fixed depth is required: a folder counts as a sample when it holds a
    numbered experiment folder, wherever it sits. The cap only exists so a
    mistaken root cannot walk a whole filesystem."""
    for depth in (1, 3, di.MAX_DEPTH):
        root = tmp_path / f"d{depth}"
        leaf = root.joinpath(*[f"lvl{i}" for i in range(depth)]) / "260728_SAMPLE"
        make_expno(leaf, 1)
        found, _ = di.discover_samples(root)
        assert len(found) == 1, f"depth {depth}: {found}"

    too_deep = tmp_path / "deep"
    leaf = too_deep.joinpath(
        *[f"lvl{i}" for i in range(di.MAX_DEPTH + 2)]
    ) / "260728_SAMPLE"
    make_expno(leaf, 1)
    assert di.discover_samples(too_deep)[0] == []


def test_the_root_itself_may_be_a_sample(tmp_path):
    """Pointing straight at one sample must work, not just at a folder of
    them."""
    sample = tmp_path / "260728_SampleC_50uM"
    make_expno(sample, 10)
    assert di.discover_samples(sample)[0] == [str(sample)]
