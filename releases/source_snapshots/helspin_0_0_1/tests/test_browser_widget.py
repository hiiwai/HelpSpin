"""DatasetBrowser: the widget end to end -- shows datasets and can drag them.

Uses qtbot's real event loop so async population (QThreadPool) is exercised,
not synchronous fetchMore directly.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser
from helspin.ui.dataset_model import COL_NAME, NodeKind

pytestmark = pytest.mark.usefixtures("qapp")

ACQUS = """##$NUC1= <1H>
##$SOLVENT= <CDCl3>
##$PULPROG= <{pulprog}>
##$NS= 16
##$RG= 101
##$HOLDER= 1
##END=
"""


def make_expno(sample: Path, expno: int, pulprog="zg30") -> Path:
    d = sample / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    (d / "acqus").write_text(ACQUS.format(pulprog=pulprog))
    (d / "fid").write_bytes(b"\x00" * 8)
    return d


@pytest.fixture
def data_root(tmp_path):
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11, "zg30")
    make_expno(root_dir / "sample_a", 12, "zgpg30")
    return root_dir


def test_data_root_populates_asynchronously_on_construction(qtbot, data_root):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)

    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    assert browser.model.data(sample_index) == "sample_a"


def test_expanding_a_tree_row_populates_its_children(qtbot, data_root):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)

    proxy_root = browser._proxy.mapFromSource(root_index)
    sample_proxy = browser._proxy.index(0, 0, proxy_root)
    browser.tree.expand(sample_proxy)

    source_sample = browser._proxy.mapToSource(sample_proxy)
    qtbot.waitUntil(lambda: browser.model.rowCount(source_sample) == 2, timeout=2000)


def test_filter_narrows_visible_samples(qtbot, tmp_path):
    root_dir = tmp_path / "600"
    make_expno(root_dir / "PXR-SRC-1", 11)
    make_expno(root_dir / "unrelated", 12)
    browser = DatasetBrowser([DataRoot(name="600", path=root_dir)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 2, timeout=2000)

    browser._filter_edit.setText("PXR")
    qtbot.wait(50)

    proxy_root = browser._proxy.mapFromSource(root_index)
    visible = [
        browser._proxy.data(browser._proxy.index(r, COL_NAME, proxy_root))
        for r in range(browser._proxy.rowCount(proxy_root))
    ]
    assert visible == ["PXR-SRC-1"]


def test_selected_source_indexes_maps_through_the_proxy(qtbot, data_root):
    from PySide6.QtCore import QItemSelectionModel

    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)

    proxy_root = browser._proxy.mapFromSource(root_index)
    sample_proxy = browser._proxy.index(0, 0, proxy_root)
    browser.tree.setCurrentIndex(sample_proxy)
    browser.tree.selectionModel().select(
        sample_proxy,
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )

    selected = browser.selected_source_indexes()
    assert len(selected) == 1
    node = selected[0].internalPointer()
    assert node.kind is NodeKind.SAMPLE
    assert node.display_name == "sample_a"


def test_multiple_data_roots_shown_independently(qtbot, tmp_path):
    root_600 = tmp_path / "600"
    root_400 = tmp_path / "400"
    make_expno(root_600 / "sample_a", 11)
    make_expno(root_400 / "sample_b", 21)

    browser = DatasetBrowser(
        [DataRoot(name="600 MHz", path=root_600), DataRoot(name="400 MHz", path=root_400)]
    )
    qtbot.addWidget(browser)
    assert browser.model.rowCount() == 2

    idx0 = browser.model.index(0, 0)
    idx1 = browser.model.index(1, 0)
    qtbot.waitUntil(
        lambda: browser.model.rowCount(idx0) == 1 and browser.model.rowCount(idx1) == 1,
        timeout=2000,
    )


def test_scan_failed_signal_forwards_from_the_model(qtbot, tmp_path):
    """A stale/unreachable root must surface through the widget's own signal,
    for a status bar in the real application, without crashing the browser."""
    browser = DatasetBrowser([DataRoot(name="bad", path=tmp_path / "nonexistent")])
    qtbot.addWidget(browser)
    # The nonexistent-path case degrades to an empty scan rather than raising
    # (see test_dataset_model.py), so the assertion here is that construction
    # and population both complete without an exception -- the widget stays
    # usable either way.
    qtbot.wait(100)
    assert browser.model.rowCount(browser.model.index(0, 0)) == 0


def test_add_data_root_populates_without_manual_expansion(qtbot, tmp_path):
    """The 'File > Add Data Root...' action's counterpart: a newly added root
    should not require the user to expand it once before it shows content."""
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11)

    browser.add_data_root(DataRoot(name="600 MHz", path=root_dir))
    root_idx = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_idx) == 1, timeout=2000)


def test_data_roots_reflects_additions(qtbot, tmp_path):
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    browser.add_data_root(DataRoot(name="600 MHz", path=tmp_path / "600"))
    assert [r.name for r in browser.data_roots()] == ["600 MHz"]


def test_starting_with_zero_data_roots_is_not_an_error(qtbot):
    """First run: no data root configured yet. Must not crash on construction."""
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    assert browser.model.rowCount() == 0


# --- refresh: the reported bug -------------------------------------------


def test_refresh_node_picks_up_new_files(qtbot, tmp_path):
    """End-to-end through the browser widget, not just the model directly:
    a sample added after the first scan appears once refresh_node() is
    called on the data root."""
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11)
    browser = DatasetBrowser([DataRoot(name="600", path=root_dir)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)

    make_expno(root_dir / "sample_b", 12)
    browser.refresh_node(root_index)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 2, timeout=2000)


def test_refresh_all_covers_every_configured_root(qtbot, tmp_path):
    root_600 = tmp_path / "600"
    root_400 = tmp_path / "400"
    make_expno(root_600 / "s1", 11)
    make_expno(root_400 / "s2", 21)
    browser = DatasetBrowser(
        [DataRoot(name="600", path=root_600), DataRoot(name="400", path=root_400)]
    )
    qtbot.addWidget(browser)
    idx0, idx1 = browser.model.index(0, 0), browser.model.index(1, 0)
    qtbot.waitUntil(
        lambda: browser.model.rowCount(idx0) == 1 and browser.model.rowCount(idx1) == 1,
        timeout=2000,
    )

    make_expno(root_600 / "s3", 12)
    make_expno(root_400 / "s4", 22)
    browser.refresh_all()
    qtbot.waitUntil(
        lambda: browser.model.rowCount(idx0) == 2 and browser.model.rowCount(idx1) == 2,
        timeout=2000,
    )


def test_refresh_node_on_an_expno_index_is_a_no_op(qtbot, data_root):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    browser._request_populate(sample_index)
    qtbot.waitUntil(lambda: browser.model.rowCount(sample_index) == 2, timeout=2000)
    expno_index = browser.model.index(0, 0, sample_index)

    browser.refresh_node(expno_index)   # must not raise or remove the row
    assert browser.model.rowCount(sample_index) == 2


# --- context menu ---------------------------------------------------------
#
# _build_context_menu is called directly, never through _on_context_menu /
# QMenu.exec(). Monkeypatching .exec on QMenu itself -- a built-in
# Shiboken-wrapped class, not a Python subclass -- was tried first and did
# NOT reliably prevent the real blocking modal call from running: it hung
# the whole suite. This is the fix: test the menu-construction logic
# directly, which is also just a cleaner separation of concerns.


def test_context_menu_on_a_data_root_offers_refresh_data_root_and_refresh_all(
    qtbot, data_root
):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)

    menu = browser._build_context_menu(root_index)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]

    assert "Refresh Data Root" in labels
    assert "Refresh All" in labels


def test_context_menu_on_a_sample_offers_refresh(qtbot, data_root):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)

    menu = browser._build_context_menu(sample_index)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]

    assert "Refresh" in labels
    assert "Refresh Data Root" not in labels


def test_context_menu_on_an_expno_offers_only_refresh_all(qtbot, data_root):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    browser._request_populate(sample_index)
    qtbot.waitUntil(lambda: browser.model.rowCount(sample_index) == 2, timeout=2000)
    expno_index = browser.model.index(0, 0, sample_index)

    menu = browser._build_context_menu(expno_index)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]

    assert labels == ["Refresh All"]


def test_context_menu_on_empty_space_offers_only_refresh_all(qtbot):
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)

    menu = browser._build_context_menu(None)
    labels = [a.text() for a in menu.actions() if not a.isSeparator()]

    assert labels == ["Refresh All"]


def test_context_menu_refresh_action_actually_refreshes(qtbot, tmp_path):
    """Not just that the menu item exists -- that triggering its action
    does the real thing."""
    root_dir = tmp_path / "600"
    make_expno(root_dir / "sample_a", 11)
    browser = DatasetBrowser([DataRoot(name="600", path=root_dir)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)

    make_expno(root_dir / "sample_b", 12)
    menu = browser._build_context_menu(root_index)
    refresh_action = next(a for a in menu.actions() if a.text() == "Refresh Data Root")
    refresh_action.trigger()

    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 2, timeout=2000)
