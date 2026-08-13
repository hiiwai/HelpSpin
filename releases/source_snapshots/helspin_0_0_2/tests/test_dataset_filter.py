"""DatasetFilterProxy: recursive match on sample name and PULPROG."""

from pathlib import Path

import pytest
from PySide6.QtCore import QRegularExpression

from helspin.domain.ports import DataRoot
from helspin.ui.dataset_filter import DatasetFilterProxy
from helspin.ui.dataset_model import COL_NAME, DatasetTreeModel

pytestmark = pytest.mark.usefixtures("qapp")

ACQUS = """##$NUC1= <1H>
##$SOLVENT= <CDCl3>
##$PULPROG= <{pulprog}>
##$NS= 16
##$RG= 101
##$HOLDER= 1
##END=
"""


def make_expno(sample: Path, expno: int, pulprog: str) -> Path:
    d = sample / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    (d / "acqus").write_text(ACQUS.format(pulprog=pulprog))
    (d / "fid").write_bytes(b"\x00" * 8)
    return d


@pytest.fixture
def populated(tmp_path):
    root_dir = tmp_path / "600"
    make_expno(root_dir / "260728_PXR-SRC-1_26-1_FT2", 11, "zg30")
    make_expno(root_dir / "260728_PXR-SRC-1_26-1_FT2", 21, "cosygpppqf")
    make_expno(root_dir / "other_sample", 12, "zgpg30")

    model = DatasetTreeModel([DataRoot(name="600", path=root_dir)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    for r in range(model.rowCount(root_idx)):
        sample_idx = model.index(r, 0, root_idx)
        model.fetchMore(sample_idx)
        # Expno metadata (incl. PULPROG) is loaded lazily in the background
        # now; probe synchronously here so the PULPROG-filter tests have data
        # to match against, mirroring what DatasetPopulator does per row.
        for child in sample_idx.internalPointer().children or []:
            model.probe_node(child)
    return model


def proxy_for(model, text=""):
    proxy = DatasetFilterProxy()
    proxy.setSourceModel(model)
    proxy.setFilterRegularExpression(QRegularExpression(text))
    return proxy


def visible_names(proxy, parent=None):
    parent = parent if parent is not None else proxy.index(-1, -1).__class__()
    out = []

    def walk(index):
        for r in range(proxy.rowCount(index)):
            child = proxy.index(r, COL_NAME, index)
            out.append(proxy.data(child))
            walk(child)

    from PySide6.QtCore import QModelIndex
    walk(QModelIndex())
    return out


def test_empty_filter_shows_everything(populated):
    proxy = proxy_for(populated, "")
    names = visible_names(proxy)
    assert "260728_PXR-SRC-1_26-1_FT2" in names
    assert "other_sample" in names
    assert {"11", "21", "12"} <= set(names)


def test_filter_by_sample_name_substring(populated):
    proxy = proxy_for(populated, "PXR-SRC")
    names = visible_names(proxy)
    assert "260728_PXR-SRC-1_26-1_FT2" in names
    assert "other_sample" not in names


def test_filter_is_case_insensitive(populated):
    proxy = proxy_for(populated, "pxr-src")
    assert "260728_PXR-SRC-1_26-1_FT2" in visible_names(proxy)


def test_filter_by_pulprog_keeps_the_parent_sample_visible(populated):
    """A match at the expno level must keep its ancestor sample visible --
    this is the whole point of recursive filtering in a tree."""
    proxy = proxy_for(populated, "cosygpppqf")
    names = visible_names(proxy)
    assert "260728_PXR-SRC-1_26-1_FT2" in names
    assert "21" in names
    assert "11" not in names
    assert "other_sample" not in names


def test_filter_by_pulprog_excludes_non_matching_expnos_in_the_same_sample(populated):
    proxy = proxy_for(populated, "zg30")
    names = visible_names(proxy)
    assert "11" in names
    assert "21" not in names


def test_filter_matching_nothing_shows_nothing(populated):
    proxy = proxy_for(populated, "totally-unrelated-xyz")
    assert visible_names(proxy) == []


def test_filter_only_searches_already_fetched_nodes(tmp_path):
    """Known v1 limitation: an un-expanded sample's expnos have not been
    probed, so a PULPROG-only query cannot match them yet. Documented so this
    boundary is a tested fact, not a silent gap -- see the module docstring
    and README for the full-index follow-up."""
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_x", 99, "hsqcedetgp")
    model = DatasetTreeModel([DataRoot(name="600", path=root_dir)])
    # Deliberately not fetching anything.
    proxy = proxy_for(model, "hsqcedetgp")
    assert visible_names(proxy) == []
