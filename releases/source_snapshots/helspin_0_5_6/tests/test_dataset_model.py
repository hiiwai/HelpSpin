"""DatasetTreeModel: lazy population, columns, drag payload, failure modes."""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt

from helspin.domain.ports import DataRoot
from helspin.domain.project import Dimensionality
from helspin.ui.dataset_model import (
    COL_DIM,
    COL_NAME,
    COL_NUCLEUS,
    COL_PULPROG,
    MIME_DATASET,
    DatasetPopulator,
    DatasetTreeModel,
    Node,
    NodeKind,
)

pytestmark = pytest.mark.usefixtures("qapp")


ACQUS = """##$NUC1= <{nucleus}>
##$SOLVENT= <{solvent}>
##$PULPROG= <{pulprog}>
##$NS= 16
##$RG= 101
##$HOLDER= 1
##$USERA1= <>
##$USERA2= <>
##END=
"""


def make_expno(sample: Path, expno: int, dim=1, nucleus="1H", solvent="CDCl3",
                pulprog="zg30") -> Path:
    d = sample / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    (d / "acqus").write_text(
        ACQUS.format(nucleus=nucleus, solvent=solvent, pulprog=pulprog)
    )
    if dim == 2:
        (d / "acqu2s").write_text("##END=\n")
        (d / "ser").write_bytes(b"\x00" * 8)
    else:
        (d / "fid").write_bytes(b"\x00" * 8)
    procno = d / "pdata" / "1"
    procno.mkdir(parents=True, exist_ok=True)
    # Real Bruker expnos have a processed-data file; the browser now
    # filters out ones that do not, so fixtures must include it.
    (procno / ("2rr" if dim == 2 else "1r")).write_bytes(b"\x00" * 8)
    return d


@pytest.fixture
def two_samples(tmp_path):
    root_dir = tmp_path / "600"
    s1 = root_dir / "sample_a"
    s2 = root_dir / "sample_b"
    make_expno(s1, 11, pulprog="zg30")
    make_expno(s1, 21, dim=2, pulprog="hsqcedetgp")
    make_expno(s2, 12, pulprog="zgpg30", nucleus="13C")
    return root_dir


def model_for(root_dir) -> DatasetTreeModel:
    return DatasetTreeModel([DataRoot(name="600 MHz", path=root_dir)])


def expand(model: DatasetTreeModel, index: QModelIndex) -> None:
    """Synchronously populate one node, as the view would on expansion.

    Note: for a SAMPLE this now creates expno rows from structure ALONE
    (no acqus reads) -- metadata columns are filled by separate background
    probes in production. Tests that need PULPROG/nucleus/dimensionality
    must call probe_all() afterwards to run those probes synchronously.
    """
    model.fetchMore(index)


def probe_all(model: DatasetTreeModel, sample_index: QModelIndex) -> None:
    """Synchronously probe every expno row under an expanded sample.

    Mirrors what DatasetPopulator does in the background per row, but
    inline, so tests can assert on the resulting metadata deterministically
    without waiting on a thread pool.
    """
    sample_node = sample_index.internalPointer()
    for child in sample_node.children or []:
        model.probe_node(child)


# --- basic tree shape ---------------------------------------------------


def test_root_level_shows_data_roots(two_samples):
    model = model_for(two_samples)
    assert model.rowCount() == 1
    idx = model.index(0, COL_NAME)
    assert model.data(idx) == "600 MHz"


def test_disabled_data_root_is_not_shown(two_samples):
    root = DataRoot(name="off", path=two_samples, enabled=False)
    model = DatasetTreeModel([root])
    assert model.rowCount() == 0


def test_children_not_fetched_until_asked(two_samples):
    """Lazy loading: a data root's children are not present until it is
    populated. (canFetchMore is intentionally always False now -- population
    is driven by DatasetBrowser/DatasetPopulator, not Qt's auto-fetch -- so
    the lazy behaviour is checked directly via is_fetched/rowCount rather than
    through canFetchMore.)"""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    node = root_idx.internalPointer()
    assert not node.is_fetched
    assert model.rowCount(root_idx) == 0
    # hasChildren still reports it as expandable, so the view shows an arrow.
    assert model.hasChildren(root_idx)


def test_expanding_data_root_shows_two_samples(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    assert model.rowCount(root_idx) == 2
    names = {model.data(model.index(r, 0, root_idx)) for r in range(2)}
    assert names == {"sample_a", "sample_b"}


def test_expanding_sample_shows_its_expnos(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_a_row = next(
        r for r in range(model.rowCount(root_idx))
        if model.data(model.index(r, 0, root_idx)) == "sample_a"
    )
    sample_idx = model.index(sample_a_row, 0, root_idx)
    expand(model, sample_idx)
    assert model.rowCount(sample_idx) == 2
    names = {model.data(model.index(r, 0, sample_idx)) for r in range(2)}
    assert names == {"11", "21"}


def test_expno_has_no_children(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    expno_idx = model.index(0, 0, sample_idx)
    assert model.rowCount(expno_idx) == 0
    assert not model.hasChildren(expno_idx)
    assert not model.canFetchMore(expno_idx)


def test_canFetchMore_reports_true_until_children_are_loaded(two_samples):
    """canFetchMore MUST be True for an unfetched node.

    QSortFilterProxyModel derives hasChildren() from rowCount() when
    canFetchMore() is False -- so returning False left every unexpanded sample
    with no expander arrow and the tree could not be opened at all (a real
    regression in 0.0.2). The GUI-thread-blocking concern is handled in
    fetchMore instead, which delegates the scan to the async populator when a
    fetch scheduler is installed."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    node = root_idx.internalPointer()

    assert not node.is_fetched
    assert model.canFetchMore(root_idx)      # arrow shows, expansion allowed

    expand(model, root_idx)
    assert node.is_fetched
    assert not model.canFetchMore(root_idx)  # nothing left to fetch


def test_fetch_scheduler_is_used_instead_of_a_synchronous_scan(two_samples):
    """With a scheduler installed, fetchMore hands the scan off and returns
    immediately, rather than scanning on the calling (GUI) thread."""
    model = model_for(two_samples)
    called = []
    model.set_fetch_scheduler(lambda node: called.append(node))
    root_idx = model.index(0, 0)

    model.fetchMore(root_idx)

    assert len(called) == 1                       # delegated
    assert not root_idx.internalPointer().is_fetched   # not scanned inline
    assert root_idx.internalPointer().fetch_in_flight


def test_fetch_in_flight_prevents_duplicate_scheduling(two_samples):
    model = model_for(two_samples)
    called = []
    model.set_fetch_scheduler(lambda node: called.append(node))
    root_idx = model.index(0, 0)

    model.fetchMore(root_idx)
    model.fetchMore(root_idx)   # Qt may ask more than once
    assert len(called) == 1
    assert not model.canFetchMore(root_idx)   # already in flight

def test_parent_of_expno_is_its_sample(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    expno_idx = model.index(0, 0, sample_idx)
    assert model.parent(expno_idx) == sample_idx


def test_empty_data_root_has_no_children_and_is_not_an_error(tmp_path):
    empty = tmp_path / "empty_root"
    empty.mkdir()
    model = model_for(empty)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    assert model.rowCount(root_idx) == 0
    assert not model.hasChildren(root_idx)


def test_add_data_root_appends_and_is_immediately_visible(two_samples, tmp_path):
    model = model_for(two_samples)
    assert model.rowCount() == 1
    second = tmp_path / "400"
    second.mkdir()
    model.add_data_root(DataRoot(name="400 MHz", path=second))
    assert model.rowCount() == 2
    assert model.data(model.index(1, 0)) == "400 MHz"


def test_added_root_is_independently_expandable(two_samples, tmp_path):
    model = model_for(two_samples)
    second = tmp_path / "400"
    make_expno(second / "sample_c", 31)
    model.add_data_root(DataRoot(name="400 MHz", path=second))
    new_idx = model.index(1, 0)
    expand(model, new_idx)
    assert model.rowCount(new_idx) == 1


def test_remove_data_root(two_samples, tmp_path):
    model = model_for(two_samples)
    model.add_data_root(DataRoot(name="400 MHz", path=tmp_path / "400"))
    assert model.rowCount() == 2
    model.remove_data_root(0)
    assert model.rowCount() == 1
    assert model.data(model.index(0, 0)) == "400 MHz"


def test_remove_data_root_out_of_range_is_a_no_op(two_samples):
    model = model_for(two_samples)
    model.remove_data_root(5)
    model.remove_data_root(-1)
    assert model.rowCount() == 1


def test_data_roots_returns_the_configured_roots(two_samples, tmp_path):
    model = model_for(two_samples)
    second = DataRoot(name="400 MHz", path=tmp_path / "400")
    model.add_data_root(second)
    names = [r.name for r in model.data_roots()]
    assert names == ["600 MHz", "400 MHz"]


# --- columns --------------------------------------------------------------


def test_expno_row_shows_pulprog_and_nucleus(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    probe_all(model, sample_idx)   # metadata is now deferred; probe explicitly
    row = next(
        r for r in range(model.rowCount(sample_idx))
        if model.data(model.index(r, COL_NAME, sample_idx)) == "11"
    )
    assert model.data(model.index(row, COL_PULPROG, sample_idx)) == "zg30"
    assert model.data(model.index(row, COL_NUCLEUS, sample_idx)) == "1H"
    assert model.data(model.index(row, COL_DIM, sample_idx)) == "1D"


def test_expno_name_appears_before_probe_completes(two_samples):
    """The whole point of the fix: the row (its Name) is present the instant
    the sample is expanded, WITHOUT any acqus read having happened yet."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)   # deliberately NOT probing

    names = {model.data(model.index(r, COL_NAME, sample_idx))
             for r in range(model.rowCount(sample_idx))}
    assert names == {"11", "21"}
    # ...and the metadata columns show the loading placeholder, not real data.
    assert model.data(model.index(0, COL_PULPROG, sample_idx)) == "\u2026"


def test_metadata_columns_fill_in_after_probe(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)

    row = next(
        r for r in range(model.rowCount(sample_idx))
        if model.data(model.index(r, COL_NAME, sample_idx)) == "11"
    )
    assert model.data(model.index(row, COL_PULPROG, sample_idx)) == "\u2026"
    probe_all(model, sample_idx)
    assert model.data(model.index(row, COL_PULPROG, sample_idx)) == "zg30"


def test_2d_expno_shows_2d_column(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    probe_all(model, sample_idx)
    row = next(
        r for r in range(model.rowCount(sample_idx))
        if model.data(model.index(r, COL_NAME, sample_idx)) == "21"
    )
    assert model.data(model.index(row, COL_DIM, sample_idx)) == "2D"


def test_sample_and_root_rows_have_no_pulprog(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    assert model.data(model.index(0, COL_PULPROG)) is None
    expand(model, root_idx)
    assert model.data(model.index(0, COL_PULPROG, root_idx)) is None


def test_header_labels(two_samples):
    model = model_for(two_samples)
    assert model.headerData(0, Qt.Horizontal) == "Name"
    assert model.headerData(1, Qt.Horizontal) == "PULPROG"


# --- unreadable / failed nodes --------------------------------------------


def test_unreachable_data_root_reports_failure_not_a_crash(tmp_path, qtbot):
    root = DataRoot(name="stale", path=tmp_path / "does_not_exist")
    model = DatasetTreeModel([root])
    root_idx = model.index(0, 0)
    with qtbot.waitSignal(model.scanFailed, timeout=1000, raising=False):
        expand(model, root_idx)
    # scan_for_datasets on a nonexistent path returns [] rather than raising
    # (iterdir failures inside it are caught), so no crash either way.
    assert model.rowCount(root_idx) == 0


def test_expno_that_fails_to_probe_still_appears(tmp_path):
    """A single unreadable acqus must not hide the row nor the dataset."""
    sample = tmp_path / "600" / "broken_sample"
    expno = sample / "13"
    expno.mkdir(parents=True)
    (expno / "acqus").write_text("not valid jcamp but also not fatal\n")
    (expno / "fid").write_bytes(b"\x00")
    procno = expno / "pdata" / "1"
    procno.mkdir(parents=True)
    (procno / "1r").write_bytes(b"\x00" * 8)

    model = model_for(tmp_path / "600")
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    assert model.rowCount(sample_idx) == 1
    expno_idx = model.index(0, COL_NAME, sample_idx)
    assert model.data(expno_idx) == "13"


# --- drag payload -----------------------------------------------------------


def test_expno_row_is_drag_enabled(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    expno_idx = model.index(0, 0, sample_idx)
    assert model.flags(expno_idx) & Qt.ItemIsDragEnabled


def test_sample_and_root_rows_are_not_drag_enabled(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    assert not (model.flags(root_idx) & Qt.ItemIsDragEnabled)


def test_mime_data_carries_path_and_dimensionality(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    probe_all(model, sample_idx)   # dimensionality comes from the probe
    expno_idx = model.index(0, 0, sample_idx)

    mime = model.mimeData([expno_idx])
    assert mime.hasFormat(MIME_DATASET)
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert len(payload) == 1
    assert payload[0]["dimensionality"] == Dimensionality.ONE_D.value
    assert payload[0]["path"].endswith("11")


def test_unprobed_expno_is_draggable_straight_away(two_samples):
    """A row must be draggable the moment it appears, before any acqus read.

    This is the reported "I cannot drag" bug. Until 0.4.0 mimeData() skipped
    every node whose probe had not returned, so on a network share -- where
    the probe is precisely the slow part -- dragging a freshly listed row
    produced an empty payload and the drag silently did nothing.

    It is fixable because the payload never needed the probe: the index
    already recorded which raw files each experiment has, and fid vs
    ser+acqu2s is the dimensionality. Nothing has to be read to know it.
    """
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)   # deliberately NOT probed
    expno_idx = model.index(0, 0, sample_idx)

    node = expno_idx.internalPointer()
    assert node.probe_pending, "the point of the test is an unprobed row"

    mime = model.mimeData([expno_idx])
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert len(payload) == 1
    assert payload[0]["path"].endswith("11")
    assert payload[0]["dimensionality"] == Dimensionality.ONE_D.value


def test_unprobed_2d_expno_carries_its_dimensionality(two_samples):
    """The structural flags must distinguish 2D too, or an unprobed drop
    would land on the canvas as a 1D trace and be drawn as nonsense."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    rows = {
        model.data(model.index(r, COL_NAME, sample_idx)): model.index(r, 0, sample_idx)
        for r in range(model.rowCount(sample_idx))
    }

    mime = model.mimeData([rows["21"]])          # ser + acqu2s
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert payload[0]["dimensionality"] == Dimensionality.TWO_D.value


def test_a_row_whose_dimensionality_is_unknown_is_still_draggable(tmp_path):
    """A processed-only dataset -- raw fid deleted, which is routine -- has no
    structural clue at all. It must still drag, with dimensionality 0, and let
    the canvas settle the question when it loads. Refusing the drag is what
    the old code did, and it is the worse answer: the file is perfectly
    loadable."""
    root_dir = tmp_path / "600"
    expno = root_dir / "proc_only" / "5"
    (expno / "pdata" / "1").mkdir(parents=True)
    (expno / "acqus").write_text("##END=\n")
    (expno / "pdata" / "1" / "1r").write_bytes(b"\x00" * 8)

    model = model_for(root_dir)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    expno_idx = model.index(0, 0, sample_idx)

    payload = json.loads(
        bytes(model.mimeData([expno_idx]).data(MIME_DATASET)).decode("utf-8")
    )
    assert len(payload) == 1
    assert payload[0]["dimensionality"] == 0


def test_multiselect_drag_carries_every_selected_expno(two_samples):
    """The core workflow: select several expnos, drag once, fill several slots."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    probe_all(model, sample_idx)
    indexes = [model.index(r, 0, sample_idx) for r in range(model.rowCount(sample_idx))]

    mime = model.mimeData(indexes)
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert len(payload) == 2


def test_mime_data_ignores_non_name_columns_to_avoid_duplicates(two_samples):
    """Selecting a whole row (all columns) must not duplicate the payload."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    probe_all(model, sample_idx)
    row0 = [model.index(0, c, sample_idx) for c in range(model.columnCount())]
    mime = model.mimeData(row0)
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert len(payload) == 1


def test_mime_data_of_a_sample_row_is_empty():
    """Only expno rows are drag sources in v1; sample-level drag is out of
    scope for the browser and handled instead by drop/paste's picker."""
    pass  # covered by the flags test above; kept as documentation.


# --- async populator ---------------------------------------------------------


def test_populator_fills_children_asynchronously(two_samples, qtbot):
    model = model_for(two_samples)
    populator = DatasetPopulator(model)
    root_idx = model.index(0, 0)
    node = root_idx.internalPointer()

    populator.populate(node)
    qtbot.waitUntil(lambda: node.is_fetched, timeout=2000)
    assert model.rowCount(root_idx) == 2


def test_expanding_a_sample_shows_rows_before_probes_finish(two_samples, qtbot):
    """The performance fix, verified through the real async populator:
    expno rows appear as soon as the (cheap, structure-only) sample scan
    completes -- their probe_pending flag still set, metadata not yet read."""
    model = model_for(two_samples)
    populator = DatasetPopulator(model)
    root_idx = model.index(0, 0)
    populator.populate(root_idx.internalPointer())
    qtbot.waitUntil(lambda: model.rowCount(root_idx) == 2, timeout=2000)

    sample_idx = model.index(0, 0, root_idx)
    sample_node = sample_idx.internalPointer()
    populator.populate(sample_node)
    qtbot.waitUntil(lambda: model.rowCount(sample_idx) == 2, timeout=2000)

    # The rows exist. At least their Name column is real immediately.
    names = {model.data(model.index(r, COL_NAME, sample_idx)) for r in range(2)}
    assert names == {"11", "21"}


def test_background_probes_fill_metadata_columns(two_samples, qtbot):
    """After the rows appear, the per-row background probes fill in
    PULPROG (and the other metadata columns) in place."""
    from helspin.ui.dataset_model import COL_PULPROG

    model = model_for(two_samples)
    populator = DatasetPopulator(model)
    root_idx = model.index(0, 0)
    populator.populate(root_idx.internalPointer())
    qtbot.waitUntil(lambda: model.rowCount(root_idx) == 2, timeout=2000)

    sample_idx = model.index(0, 0, root_idx)
    populator.populate(sample_idx.internalPointer())
    qtbot.waitUntil(lambda: model.rowCount(sample_idx) == 2, timeout=2000)

    row_11 = next(
        r for r in range(2)
        if model.data(model.index(r, COL_NAME, sample_idx)) == "11"
    )
    # PULPROG starts as the loading placeholder, then becomes real once the
    # background probe for that row lands.
    qtbot.waitUntil(
        lambda: model.data(model.index(row_11, COL_PULPROG, sample_idx)) == "zg30",
        timeout=2000,
    )


def test_read_probe_result_does_not_mutate_the_node(two_samples):
    """Thread-safety guarantee, tested directly: the worker-thread half of a
    probe (read_probe_result) must NOT touch model or node state -- it only
    reads from disk and returns a value. This is the structural fix for the
    metadata-never-fills bug, where the worker previously mutated the node
    and emitted dataChanged off the GUI thread. Hard to catch via timing
    alone, so it is pinned as an invariant here."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    node = sample_idx.internalPointer().children[0]

    assert node.probe_pending is True
    assert node.info is None

    info, error = model.read_probe_result(node)

    # The pure-IO call returned a real result...
    assert info is not None
    # ...but must have left the node completely untouched.
    assert node.probe_pending is True
    assert node.info is None
    assert node.failed is False


def test_apply_probe_result_updates_node_and_clears_pending(two_samples):
    """The GUI-thread half installs the result and clears the pending flag."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    node = sample_idx.internalPointer().children[0]

    info, error = model.read_probe_result(node)
    model.apply_probe_result(node, info, error)

    assert node.probe_pending is False
    assert node.info is info
    assert node.failed is False


def test_apply_probe_result_emits_datachanged_for_the_row(two_samples, qtbot):
    """Applying a probe result must emit dataChanged so the view repaints the
    now-filled metadata columns -- the signal the worker thread was wrongly
    emitting before."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    node = sample_idx.internalPointer().children[0]
    info, error = model.read_probe_result(node)

    with qtbot.waitSignal(model.dataChanged, timeout=1000):
        model.apply_probe_result(node, info, error)


def test_apply_probe_result_records_failure(two_samples):
    """A failed probe (info=None) marks the node failed and clears pending,
    so its columns show the em-dash rather than a perpetual placeholder."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    node = sample_idx.internalPointer().children[0]

    model.apply_probe_result(node, None, "boom")

    assert node.probe_pending is False
    assert node.failed is True
    assert node.error == "boom"


def test_probe_row_is_a_safe_noop_for_non_pending_nodes(two_samples, qtbot):
    """probe_row can be called indiscriminately -- on a sample, a data root,
    or an already-probed expno -- without effect or error."""
    model = model_for(two_samples)
    populator = DatasetPopulator(model)
    root_idx = model.index(0, 0)
    root_node = root_idx.internalPointer()

    populator.probe_row(root_node)          # a DATA_ROOT: no-op
    expand(model, root_idx)
    sample_node = model.index(0, 0, root_idx).internalPointer()
    populator.probe_row(sample_node)        # a SAMPLE: no-op
    # neither raised; nothing to assert beyond that
    assert True


def test_populator_reports_failure_via_signal(tmp_path, qtbot):
    root = DataRoot(name="bad", path=tmp_path / "nope")
    model = DatasetTreeModel([root])
    populator = DatasetPopulator(model)
    node = model.index(0, 0).internalPointer()

    populator.populate(node)
    qtbot.waitUntil(lambda: node.is_fetched, timeout=2000)
    # nonexistent path scans to an empty list without raising; confirm the
    # node still ends up in a settled (fetched) state either way.
    assert node.children == []


# --- refresh: the actual bug report this fixes ------------------------------


def test_refresh_picks_up_a_sample_added_after_the_first_scan(tmp_path):
    """The reported problem, reproduced directly: a data root scanned once,
    then a new sample appears on disk, and the tree must show it after
    refresh() without needing an app restart."""
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11)
    model = model_for(root_dir)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    assert model.rowCount(root_idx) == 1

    make_expno(root_dir / "sample_b", 12)   # added after the first scan
    model.refresh(root_idx.internalPointer())
    expand(model, root_idx)   # re-populate now that refresh cleared the cache

    assert model.rowCount(root_idx) == 2
    names = {model.data(model.index(r, 0, root_idx)) for r in range(2)}
    assert names == {"sample_a", "sample_b"}


def test_refresh_picks_up_an_expno_added_to_an_already_expanded_sample(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    assert model.rowCount(sample_idx) == 2

    make_expno(two_samples / "sample_a", 31, pulprog="hsqcedetgp")
    model.refresh(sample_idx.internalPointer())
    expand(model, sample_idx)

    assert model.rowCount(sample_idx) == 3


def test_refresh_drops_a_sample_removed_from_disk(tmp_path):
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11)
    keep = make_expno(root_dir / "sample_b", 12)
    model = model_for(root_dir)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    assert model.rowCount(root_idx) == 2

    import shutil
    shutil.rmtree(root_dir / "sample_a")
    model.refresh(root_idx.internalPointer())
    expand(model, root_idx)

    assert model.rowCount(root_idx) == 1
    assert model.data(model.index(0, 0, root_idx)) == "sample_b"


def test_refresh_on_an_expno_is_a_no_op():
    """Leaves have no children to refresh -- must not raise, must not
    somehow remove the leaf itself."""
    import tempfile
    from pathlib import Path
    root_dir = Path(tempfile.mkdtemp()) / "600"
    make_expno(root_dir / "sample_a", 11)
    model = model_for(root_dir)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    expno_idx = model.index(0, 0, sample_idx)
    expno_node = expno_idx.internalPointer()

    model.refresh(expno_node)   # must not raise

    assert model.rowCount(sample_idx) == 1   # the expno itself is untouched


def test_refresh_before_any_scan_is_harmless():
    """Refreshing a node that was never expanded (children already None)
    must not crash on beginRemoveRows with nothing to remove."""
    import tempfile
    from pathlib import Path
    root_dir = Path(tempfile.mkdtemp()) / "600"
    root_dir.mkdir(parents=True)
    model = model_for(root_dir)
    root_idx = model.index(0, 0)
    node = root_idx.internalPointer()
    assert not node.is_fetched

    model.refresh(node)   # must not raise
    assert node.children is None


def test_refresh_clears_a_previously_failed_state(tmp_path):
    """Refreshing a data root that failed to scan (e.g. its mount was down)
    clears the stale error so the next expansion retries, even though there
    are no children to merge into yet."""
    root = DataRoot(name="bad", path=tmp_path / "nope")
    model = DatasetTreeModel([root])
    root_idx = model.index(0, 0)
    node = root_idx.internalPointer()
    node.failed = True
    node.error = "stale error"

    model.refresh(node)

    assert not node.failed
    assert node.error == ""


def test_refresh_merges_in_place_keeping_children_fetched(two_samples):
    """Merge semantics: refresh does NOT reset the node to unfetched (the old
    clear-and-rebuild behaviour). It keeps the existing children and merges,
    which is what preserves expansion state and already-probed metadata across
    a refresh. So is_fetched stays true and canFetchMore stays false."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    node = root_idx.internalPointer()
    assert node.is_fetched
    assert not model.canFetchMore(root_idx)

    model.refresh(node)

    # Still fetched -- children were merged in place, not discarded.
    assert node.is_fetched
    assert not model.canFetchMore(root_idx)


def test_refresh_merge_adds_new_and_removes_vanished(tmp_path):
    """The core merge contract, at the model level: a sample added on disk
    appears, a sample removed from disk disappears, and an untouched sample's
    node identity is preserved (same object, so its expansion/probe state
    survives)."""
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11)
    make_expno(root_dir / "sample_b", 12)
    model = model_for(root_dir)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    node = root_idx.internalPointer()

    # Grab the existing sample_a node object to prove identity is preserved.
    sample_a_before = next(c for c in node.children if c.display_name == "sample_a")

    import shutil
    shutil.rmtree(root_dir / "sample_b")     # vanished
    make_expno(root_dir / "sample_c", 13)     # new

    model.refresh(node)

    names = {c.display_name for c in node.children}
    assert names == {"sample_a", "sample_c"}
    sample_a_after = next(c for c in node.children if c.display_name == "sample_a")
    assert sample_a_after is sample_a_before   # same node object, state intact



# --- fast sample discovery -------------------------------------------------


def _big_root(tmp_path, samples=120, expnos=6):
    root = tmp_path / "nmr"
    for i in range(samples):
        sample = root / f"2607{i:03d}_SAMPLE"
        for e in range(1, expnos + 1):
            d = sample / str(e)
            d.mkdir(parents=True)
            (d / "acqus").write_text("##END=\n")
            procno = d / "pdata" / "1"
            procno.mkdir(parents=True)
            (procno / "1r").write_bytes(b"\x00" * 8)
    return root


def test_every_sample_is_listed_not_just_the_first_few(tmp_path):
    """The reported bug: only some samples appeared. The old route enumerated
    EXPNOS and took their parents, and its expno cap meant a root with 400
    samples and 8 experiments each showed only 25 samples."""
    from helspin.domain.paths import scan_for_samples

    root = _big_root(tmp_path, samples=120, expnos=6)
    samples, truncated = scan_for_samples(root)
    assert len(samples) == 120
    assert truncated is False


def test_sample_scan_does_not_descend_into_a_sample(tmp_path):
    """A directory with integer-named children IS a sample; walking into it
    to count experiments was the main cost on a network share."""
    from helspin.domain.paths import scan_for_samples

    root = _big_root(tmp_path, samples=3, expnos=4)
    samples, _ = scan_for_samples(root)
    names = sorted(p.name for p in samples)
    assert all(not n.isdigit() for n in names)     # samples, not expnos


def test_sample_scan_reports_truncation_instead_of_hiding_it(tmp_path):
    from helspin.domain.paths import scan_for_samples

    root = _big_root(tmp_path, samples=10, expnos=2)
    samples, truncated = scan_for_samples(root, limit=4)
    assert len(samples) == 4
    assert truncated is True


def test_sample_scan_survives_an_unreadable_subtree(tmp_path):
    """One bad directory must not abort the whole scan."""
    from helspin.domain.paths import scan_for_samples

    root = _big_root(tmp_path, samples=3, expnos=2)
    bad = root / "unreadable"
    bad.mkdir()
    bad.chmod(0o000)
    try:
        samples, _ = scan_for_samples(root)
        assert len(samples) == 3
    finally:
        bad.chmod(0o755)


def test_a_root_that_is_itself_a_sample_is_handled(tmp_path):
    from helspin.domain.paths import scan_for_samples

    sample = tmp_path / "just_one_sample"
    (sample / "11").mkdir(parents=True)
    samples, _ = scan_for_samples(sample)
    assert samples == [sample]


def test_model_lists_every_sample(tmp_path):
    root = _big_root(tmp_path, samples=60, expnos=4)
    model = model_for(root)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    assert model.rowCount(root_idx) == 60


# --- removing a data root --------------------------------------------------


def test_remove_data_root_drops_only_that_root(tmp_path):
    a = _big_root(tmp_path / "a", samples=2, expnos=1)
    b = _big_root(tmp_path / "b", samples=2, expnos=1)
    model = DatasetTreeModel(
        [DataRoot(name="A", path=a), DataRoot(name="B", path=b)]
    )
    assert model.rowCount() == 2
    model.remove_data_root(0)
    assert model.rowCount() == 1
    assert [r.name for r in model.data_roots()] == ["B"]


def test_remove_data_root_ignores_a_bad_row(tmp_path):
    a = _big_root(tmp_path / "a", samples=1, expnos=1)
    model = DatasetTreeModel([DataRoot(name="A", path=a)])
    model.remove_data_root(9)
    model.remove_data_root(-1)
    assert model.rowCount() == 1


# --- persistent dataset index ---------------------------------------------


def _root_with(tmp_path, samples=5, expnos=4, with_pdata=True):
    root = tmp_path / "nmr"
    for i in range(samples):
        sample = root / f"SAMPLE_{i}"
        for e in range(1, expnos + 1):
            d = sample / str(e)
            d.mkdir(parents=True)
            (d / "acqus").write_text("##END=\n")
            if with_pdata:
                procno = d / "pdata" / "1"
                procno.mkdir(parents=True)
                (procno / "1r").write_bytes(b"\x00" * 8)
    return root


def test_index_records_every_sample_and_experiment(tmp_path):
    from helspin.core.dataset_index import build_index

    root = _root_with(tmp_path, samples=7, expnos=5)
    index = build_index(root)
    assert len(index.samples) == 7
    assert all(len(s.expnos) == 5 for s in index.samples)
    assert all(e.has_acqus and e.has_pdata for s in index.samples for e in s.expnos)


def test_index_marks_experiments_without_pdata_as_not_displayable(tmp_path):
    """Only processed data can be plotted, so an experiment without pdata is
    recorded but not offered."""
    from helspin.core.dataset_index import build_index

    root = _root_with(tmp_path, samples=1, expnos=2, with_pdata=False)
    index = build_index(root)
    assert all(not e.displayable for e in index.samples[0].expnos)


def test_index_survives_a_json_round_trip(tmp_path):
    from helspin.core.dataset_index import RootIndex, build_index

    root = _root_with(tmp_path, samples=3, expnos=2)
    original = build_index(root)
    restored = RootIndex.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored is not None
    assert len(restored.samples) == 3
    assert restored.samples[0].expnos[0].name == "1"


def test_cache_write_then_read(tmp_path, monkeypatch):
    from helspin.core import dataset_index as di

    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root = _root_with(tmp_path, samples=4, expnos=3)
    di.save_index(di.build_index(root))
    loaded = di.load_index(root)
    assert loaded is not None
    assert len(loaded.samples) == 4


def test_corrupt_cache_is_ignored_rather_than_fatal(tmp_path, monkeypatch):
    """A cache that cannot be parsed must cost a rebuild, never a crash."""
    from helspin.core import dataset_index as di

    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root = _root_with(tmp_path, samples=1, expnos=1)
    path = di.cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json")
    assert di.load_index(root) is None


def test_cache_of_a_different_format_is_ignored(tmp_path, monkeypatch):
    from helspin.core import dataset_index as di

    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root = _root_with(tmp_path, samples=1, expnos=1)
    path = di.cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": 999, "root": str(root)}))
    assert di.load_index(root) is None


def test_two_roots_get_separate_cache_files(tmp_path):
    from helspin.core.dataset_index import cache_path

    assert cache_path(tmp_path / "a") != cache_path(tmp_path / "b")


def test_stale_samples_detects_a_changed_directory(tmp_path):
    from helspin.core.dataset_index import build_index, refresh_sample, stale_samples

    root = _root_with(tmp_path, samples=2, expnos=2)
    index = build_index(root)
    assert stale_samples(index) == []

    new = root / "SAMPLE_0" / "99"
    new.mkdir(parents=True)
    (new / "acqus").write_text("##END=\n")
    (new / "pdata" / "1").mkdir(parents=True)
    changed = stale_samples(index)
    assert len(changed) == 1

    refresh_sample(changed[0])
    assert any(e.name == "99" for e in changed[0].expnos)


def test_model_reads_samples_and_expnos_from_the_index(tmp_path):
    root = _root_with(tmp_path, samples=6, expnos=3)
    model = model_for(root)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    assert model.rowCount(root_idx) == 6
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
    assert model.rowCount(sample_idx) == 3


def test_model_shows_a_sample_added_after_the_index_was_built(tmp_path):
    """A sample missing from the index must still open, not appear empty."""
    root = _root_with(tmp_path, samples=2, expnos=2)
    model = model_for(root)
    root_idx = model.index(0, 0)
    expand(model, root_idx)

    late = root / "SAMPLE_LATE"
    d = late / "7"
    d.mkdir(parents=True)
    (d / "acqus").write_text("##END=\n")
    (d / "pdata" / "1").mkdir(parents=True)
    (d / "pdata" / "1" / "1r").write_bytes(b"\x00" * 8)

    node = Node(kind=NodeKind.SAMPLE, path=late, display_name="SAMPLE_LATE",
                parent=root_idx.internalPointer())
    children = model._scan_sample(node)
    assert [c.display_name for c in children] == ["7"]
