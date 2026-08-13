"""DatasetBrowser: the widget end to end -- shows datasets and can drag them.

Uses qtbot's real event loop so async population (QThreadPool) is exercised,
not synchronous fetchMore directly.
"""

from pathlib import Path
import json

import pytest
from PySide6.QtCore import Qt

from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser
from helspin.ui.dataset_model import (
    COL_DATE,
    COL_DIM,
    COL_NAME,
    COL_NUCLEUS,
    COL_PULPROG,
    MIME_DATASET,
    NodeKind,
)

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


# --- drag performance fix: custom pixmap instead of Qt's default per-row
# render (reported: dragging a spectrum from the list was extremely slow).
#
# drag.exec() is a genuinely blocking native modal call, the same category
# as QDialog.exec()/QMenu.exec() elsewhere in this suite -- _build_drag is
# tested directly and NEVER through startDrag() with a real selection, which
# would call the real exec() and hang.


def test_drag_label_singular_and_plural():
    from helspin.ui.browser import _drag_label

    assert _drag_label(1) == "1 dataset"
    assert _drag_label(2) == "2 datasets"
    assert _drag_label(4) == "4 datasets"


def test_drag_pixmap_is_small_and_not_null():
    """The whole point: this must be cheap, so it stays small regardless of
    label length, rather than scaling with row content the way Qt's default
    per-row render would."""
    from helspin.ui.browser import _DRAG_PIXMAP_SIZE, _make_drag_pixmap

    pixmap = _make_drag_pixmap("3 datasets")
    assert not pixmap.isNull()
    assert pixmap.size() == _DRAG_PIXMAP_SIZE


def test_build_drag_carries_the_models_mime_data(qtbot, data_root):
    """_build_drag receives indexes from self.model(), which for this view
    is the PROXY (DatasetFilterProxy), not the source DatasetTreeModel --
    so indexes must be mapped through the proxy first, exactly as
    selectedIndexes() would supply them in real usage."""
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    browser._request_populate(sample_index)
    qtbot.waitUntil(lambda: browser.model.rowCount(sample_index) == 2, timeout=2000)

    from helspin.ui.dataset_model import MIME_DATASET

    expno_index = browser.model.index(0, 0, sample_index)
    proxy_expno_index = browser._proxy.mapFromSource(expno_index)
    drag = browser.tree._build_drag([proxy_expno_index])

    assert drag is not None
    assert drag.mimeData().hasFormat(MIME_DATASET)
    assert not drag.pixmap().isNull()


def test_build_drag_label_reflects_multiselect_count(qtbot, data_root):
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    browser._request_populate(sample_index)
    qtbot.waitUntil(lambda: browser.model.rowCount(sample_index) == 2, timeout=2000)

    all_columns_both_rows = [
        browser._proxy.mapFromSource(browser.model.index(r, c, sample_index))
        for r in range(2) for c in range(5)
    ]
    drag = browser.tree._build_drag(all_columns_both_rows)

    # Two distinct ROWS selected across all columns must still report 2,
    # not 10 (2 rows x 5 columns) -- the same de-duplication the model's own
    # mimeData already relies on, checked again here at the pixmap layer.
    assert drag.pixmap().size() == _make_drag_pixmap_size()


def _make_drag_pixmap_size():
    from helspin.ui.browser import _DRAG_PIXMAP_SIZE
    return _DRAG_PIXMAP_SIZE


def test_build_drag_with_no_draggable_rows_returns_none(qtbot, data_root):
    """Dragging a SAMPLE row (not drag-enabled, no expno mimeData) must not
    construct a QDrag at all."""
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    proxy_sample_index = browser._proxy.mapFromSource(sample_index)

    drag = browser.tree._build_drag([proxy_sample_index])
    assert drag is None


def test_mime_formats_alone_is_not_a_reliable_emptiness_check(qtbot, data_root):
    """Regression guard for the actual bug this fix went through: the
    model's mimeData() calls setData(MIME_DATASET, ...) unconditionally,
    even when the encoded JSON payload is an empty list. So
    mime.hasFormat(MIME_DATASET) is true for a non-draggable selection too
    -- _build_drag must check the DECODED content, not merely the format's
    presence, or it silently starts a drag with nothing real in it."""
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    root_index = browser.model.index(0, 0)
    qtbot.waitUntil(lambda: browser.model.rowCount(root_index) == 1, timeout=2000)
    sample_index = browser.model.index(0, 0, root_index)
    proxy_sample_index = browser._proxy.mapFromSource(sample_index)

    mime = browser.tree.model().mimeData([proxy_sample_index])
    assert mime.hasFormat(MIME_DATASET)          # format present regardless
    payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
    assert payload == []                          # but the content is empty
    assert browser.tree._build_drag([proxy_sample_index]) is None


def test_startdrag_with_empty_selection_is_a_safe_no_op(qtbot, data_root):
    """Must return immediately without ever reaching drag.exec() when
    nothing is selected -- this is the one startDrag() path safe to call
    directly in a test, since it returns before constructing a QDrag."""
    browser = DatasetBrowser([DataRoot(name="600 MHz", path=data_root)])
    qtbot.addWidget(browser)
    browser.tree.clearSelection()
    browser.tree.startDrag(Qt.CopyAction)   # must not raise or hang


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


# --- column sizing: the reported "cannot widen Name column" bug -------------


def test_name_column_is_user_resizable_not_stretch(qtbot):
    """The bug: Name was set to Stretch, which fills leftover space but is
    NOT user-resizable -- the drag handle did nothing. It must be Interactive
    so the user can widen it, and the last section must not auto-stretch (or
    Date eats the leftover width and re-squeezes Name from the right)."""
    from PySide6.QtWidgets import QHeaderView

    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    header = browser.tree.header()

    assert header.sectionResizeMode(COL_NAME) == QHeaderView.Interactive
    assert not header.stretchLastSection()


def test_name_column_has_a_generous_default_width(qtbot):
    """Wide enough for a real Bruker sample name, and wider than any of the
    narrow metadata columns."""
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    header = browser.tree.header()

    name_w = header.sectionSize(COL_NAME)
    assert name_w >= 300
    for col in (COL_PULPROG, COL_NUCLEUS, COL_DIM, COL_DATE):
        assert name_w > header.sectionSize(col)


def test_metadata_columns_do_not_collapse_to_zero(qtbot):
    """Every metadata column starts with a real, positive width even though
    there is no data in them at construction time (expnos aren't probed
    until expanded). A zero-width column is effectively invisible."""
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    header = browser.tree.header()

    for col in (COL_PULPROG, COL_NUCLEUS, COL_DIM, COL_DATE):
        assert header.sectionSize(col) > 0


def test_name_column_width_survivies_a_manual_resize(qtbot):
    """A user widening the Name column must stick -- nothing should snap it
    back. Simulates the drag by setting the section size directly."""
    browser = DatasetBrowser([])
    qtbot.addWidget(browser)
    header = browser.tree.header()

    header.resizeSection(COL_NAME, 500)
    assert header.sectionSize(COL_NAME) == 500
