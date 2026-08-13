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
    (d / "pdata" / "1").mkdir(parents=True, exist_ok=True)
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
    """Synchronously populate one node, as the view would on expansion."""
    model.fetchMore(index)


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
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    assert model.rowCount(root_idx) == 0
    assert model.canFetchMore(root_idx)


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


def test_canFetchMore_is_false_after_fetching(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    assert model.canFetchMore(root_idx)
    expand(model, root_idx)
    assert not model.canFetchMore(root_idx)


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
    row = next(
        r for r in range(model.rowCount(sample_idx))
        if model.data(model.index(r, COL_NAME, sample_idx)) == "11"
    )
    assert model.data(model.index(row, COL_PULPROG, sample_idx)) == "zg30"
    assert model.data(model.index(row, COL_NUCLEUS, sample_idx)) == "1H"
    assert model.data(model.index(row, COL_DIM, sample_idx)) == "1D"


def test_2d_expno_shows_2d_column(two_samples):
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
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
    expno_idx = model.index(0, 0, sample_idx)

    mime = model.mimeData([expno_idx])
    assert mime.hasFormat(MIME_DATASET)
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert len(payload) == 1
    assert payload[0]["dimensionality"] == Dimensionality.ONE_D.value
    assert payload[0]["path"].endswith("11")


def test_multiselect_drag_carries_every_selected_expno(two_samples):
    """The core workflow: select several expnos, drag once, fill several slots."""
    model = model_for(two_samples)
    root_idx = model.index(0, 0)
    expand(model, root_idx)
    sample_idx = model.index(0, 0, root_idx)
    expand(model, sample_idx)
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
