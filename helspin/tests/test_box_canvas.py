"""BoxCanvas: figure-fraction geometry, and the actual drop mechanics.

Drop handling is tested via _handle_mime_data / _on_slot_dropped directly
with a real QMimeData, rather than constructing full QDropEvent objects --
that keeps the tests stable across Qt/PySide versions and focuses effort on
the logic that actually has bugs (dimensionality checks, sequencing),
not Qt's event-constructor boilerplate.
"""

import json

import pytest
from PySide6.QtCore import QMimeData

from helspin.domain.layout import NewFigureRequest, build_project
from helspin.domain.project import Arrangement, Dimensionality
from helspin.ui.box_canvas import MIME_DATASET, BoxCanvas, SlotChip

pytestmark = pytest.mark.usefixtures("qapp")


def mime_for(*items: dict) -> QMimeData:
    mime = QMimeData()
    mime.setData(MIME_DATASET, json.dumps(list(items)).encode("utf-8"))
    return mime


def item(path="/data/ABC/11", dim=1, label="ABC/11"):
    return {"path": path, "dimensionality": dim, "label": label}


def project_1d(count=3, arrangement=Arrangement.OVERLAY):
    return build_project(NewFigureRequest(count_1d=count, arrangement_1d=arrangement))


def project_2d(count=2):
    return build_project(
        NewFigureRequest(count_1d=0, count_2d=count, arrangement_2d=Arrangement.TILED)
    )


# --- geometry -----------------------------------------------------------


def test_single_box_fills_the_canvas_area():
    """canvas.resize() alone does not reliably deliver resizeEvent to a
    widget that is never shown -- real usage embeds this in a shown
    QMainWindow, where resizeEvent fires normally. _reposition() is called
    explicitly here to test the geometry math itself without depending on
    Qt's event-delivery timing for an unshown widget."""
    p = project_1d(3)
    canvas = BoxCanvas(p)
    canvas.resize(1000, 500)
    canvas._reposition()
    box = p.spectrum_boxes()[0]
    widget = canvas._box_widgets[box.id]
    left, bottom, bw, bh = box.rect
    assert widget.width() == pytest.approx(bw * 1000, abs=1)
    assert widget.height() == pytest.approx(bh * 500, abs=1)


def test_resize_repositions_widgets():
    p = project_1d(3)
    canvas = BoxCanvas(p)
    canvas.resize(1000, 500)
    canvas._reposition()
    box = p.spectrum_boxes()[0]
    w1 = canvas._box_widgets[box.id].width()
    canvas.resize(500, 250)
    canvas._reposition()
    w2 = canvas._box_widgets[box.id].width()
    assert w2 == pytest.approx(w1 / 2, rel=0.05)


def test_showing_the_canvas_triggers_a_layout_via_showEvent(qtbot):
    """The production fallback: resize() alone is not depended on. Showing
    the widget must also produce correct geometry, covering the window
    manager race showEvent guards against."""
    p = project_1d(3)
    canvas = BoxCanvas(p)
    canvas.resize(1000, 500)
    qtbot.addWidget(canvas)
    canvas.show()
    qtbot.waitExposed(canvas)
    box = p.spectrum_boxes()[0]
    widget = canvas._box_widgets[box.id]
    assert widget.width() > 1
    assert widget.height() > 1


def test_tiled_boxes_stack_without_overlap():
    p = build_project(NewFigureRequest(count_1d=3, arrangement_1d=Arrangement.TILED))
    canvas = BoxCanvas(p)
    canvas.resize(800, 600)
    canvas._reposition()
    widgets = [canvas._box_widgets[b.id] for b in p.spectrum_boxes()]
    tops = sorted(w.geometry().top() for w in widgets)
    bottoms_of_prior = sorted(w.geometry().top() + w.geometry().height() for w in widgets)[:-1]
    # Every top (after the first) should be at or below the previous box's
    # bottom edge -- i.e. no vertical overlap between stacked panels.
    for t, prior_bottom in zip(tops[1:], bottoms_of_prior):
        assert t >= prior_bottom - 2   # small tolerance for integer rounding


def test_minimum_size_never_collapses_to_zero():
    """A box must remain at least 1px even if the canvas is tiny -- a zero
    or negative geometry can make a widget vanish entirely."""
    p = project_1d(3)
    canvas = BoxCanvas(p)
    canvas.resize(1, 1)
    canvas._reposition()
    box = p.spectrum_boxes()[0]
    widget = canvas._box_widgets[box.id]
    assert widget.width() >= 1
    assert widget.height() >= 1


def test_difference_box_gets_a_widget():
    p = build_project(NewFigureRequest(count_1d=2, arrangement_1d=Arrangement.SUBTRACTED))
    canvas = BoxCanvas(p)
    canvas.resize(800, 600)
    canvas._reposition()
    assert len(canvas._box_widgets) == 2   # source box + difference box


# --- chip-level drop handling -------------------------------------------


def test_chip_ignores_mime_without_the_dataset_format():
    chip = SlotChip(0, "#E69F00", Dimensionality.ONE_D)
    received = []
    chip.datasetDropped.connect(lambda i, p: received.append((i, p)))
    other = QMimeData()
    other.setText("not a dataset")
    chip._handle_mime_data(other)
    assert received == []


def test_chip_ignores_malformed_json():
    chip = SlotChip(0, "#E69F00", Dimensionality.ONE_D)
    received = []
    chip.datasetDropped.connect(lambda i, p: received.append((i, p)))
    bad = QMimeData()
    bad.setData(MIME_DATASET, b"{not valid json")
    chip._handle_mime_data(bad)
    assert received == []


def test_chip_ignores_empty_payload_list():
    chip = SlotChip(0, "#E69F00", Dimensionality.ONE_D)
    received = []
    chip.datasetDropped.connect(lambda i, p: received.append((i, p)))
    chip._handle_mime_data(mime_for())
    assert received == []


def test_chip_emits_its_own_index_and_the_full_payload():
    chip = SlotChip(2, "#E69F00", Dimensionality.ONE_D)
    received = []
    chip.datasetDropped.connect(lambda i, p: received.append((i, p)))
    chip._handle_mime_data(mime_for(item(), item(path="/data/ABC/12")))
    assert received == [(2, [item(), item(path="/data/ABC/12")])]


def test_set_filled_none_shows_the_slot_number():
    chip = SlotChip(4, "#E69F00", Dimensionality.ONE_D)
    chip.set_filled(None)
    assert chip._label_widget.text() == "5"


def test_set_filled_with_label_shows_the_label():
    chip = SlotChip(0, "#E69F00", Dimensionality.ONE_D)
    chip.set_filled("ABC-124/11")
    assert chip._label_widget.text() == "ABC-124/11"


# --- canvas-level sequencing and validation ------------------------------


def test_single_item_drop_fills_the_target_slot():
    p = project_1d(3)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(box, 0, [item(label="ABC/11")])
    assert box.block.slots[0].dataset_id == "/data/ABC/11"
    assert canvas._box_widgets[box.id]._chips[0]._label_widget.text() == "ABC/11"


def test_multi_item_drop_fills_sequential_slots_from_the_target(qtbot):
    p = project_1d(3)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    with qtbot.waitSignal(canvas.figureChanged, timeout=500):
        canvas._on_slot_dropped(
            box, 0,
            [item(path="/a/1"), item(path="/a/2"), item(path="/a/3")],
        )
    assert [s.dataset_id for s in box.block.slots] == ["/a/1", "/a/2", "/a/3"]


def test_multi_item_drop_starts_at_the_dropped_on_index_not_zero():
    p = project_1d(4)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(box, 2, [item(path="/a/1"), item(path="/a/2")])
    ids = [s.dataset_id for s in box.block.slots]
    assert ids == [None, None, "/a/1", "/a/2"]


def test_drop_beyond_the_last_slot_fills_what_fits():
    p = project_1d(2)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(box, 1, [item(path="/a/1"), item(path="/a/2"), item(path="/a/3")])
    ids = [s.dataset_id for s in box.block.slots]
    assert ids == [None, "/a/1"]   # only one slot existed at/after index 1


def test_replacing_a_filled_slot_keeps_its_colour():
    """Dropping a new dataset onto an already-filled slot replaces it and
    keeps the slot's colour -- the intended way to swap a sample, not a
    reset to some new colour."""
    p = project_1d(2)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    original_color = box.block.slots[0].color

    canvas._on_slot_dropped(box, 0, [item(path="/a/1")])
    canvas._on_slot_dropped(box, 0, [item(path="/a/2")])

    assert box.block.slots[0].dataset_id == "/a/2"
    assert box.block.slots[0].color == original_color


def test_2d_item_dropped_on_a_1d_block_is_skipped_not_fatal():
    p = project_1d(2)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(box, 0, [item(dim=2)])
    assert box.block.slots[0].dataset_id is None


def test_1d_item_dropped_on_a_2d_block_is_skipped_not_fatal():
    p = project_2d(2)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(box, 0, [item(dim=1)])
    assert box.block.slots[0].dataset_id is None


def test_mixed_dimensionality_drop_accepts_only_the_matching_items():
    """The checklist case: multi-select spanning 1D and 2D dropped on a 1D
    block accepts only the 1D ones."""
    p = project_1d(3)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(
        box, 0,
        [item(dim=1, path="/a/1"), item(dim=2, path="/a/2"), item(dim=1, path="/a/3")],
    )
    ids = [s.dataset_id for s in box.block.slots]
    assert ids == ["/a/1", None, "/a/3"]


def test_dropping_onto_its_own_already_correct_slot_is_harmless():
    p = project_1d(2)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    canvas._on_slot_dropped(box, 0, [item(path="/a/1")])
    canvas._on_slot_dropped(box, 0, [item(path="/a/1")])
    assert box.block.slots[0].dataset_id == "/a/1"


def test_figure_changed_emits_on_every_successful_drop(qtbot):
    p = project_1d(2)
    canvas = BoxCanvas(p)
    box = p.spectrum_boxes()[0]
    with qtbot.waitSignal(canvas.figureChanged, timeout=500):
        canvas._on_slot_dropped(box, 0, [item()])
