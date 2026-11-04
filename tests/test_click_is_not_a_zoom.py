"""0.9.12: clicking the plot must never change the x range.

Reported on 0.9.11: "when I click the canvas in 1D, the x range gives
nonsense -- it should keep the range last applied". Two causes, both pinned
here, driven with real Qt mouse, key and focus events (HANDOFF section 5:
drive it the way a user does):

* In X zoom a press-release was a box zoom unless both ends hit the SAME data
  point (a 1e-9 ppm guard), so a 1-2 pixel trackpad tremor zoomed into a
  0.02 ppm sliver.
* The ppm boxes applied on editingFinished, which Qt also emits when a box
  merely loses focus -- e.g. to a click on the plot -- so whatever the boxes
  held, half-typed or not, was applied.
"""

from __future__ import annotations

import math

import pytest
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QWidget

from helspin.ui.adjustment_bar import AdjustmentBar
from helspin.ui.spectrum_canvas import CLICK_SLOP_PIXELS, SpectrumCanvas

from .test_canvas_controls import FakeReader, item, mime_for
from .test_reported_issues_040 import _mime_2d, _Reader2D

pytestmark = pytest.mark.usefixtures("qapp")


# --------------------------------------------------------------- helpers


@pytest.fixture
def canvas(qtbot):
    widget = SpectrumCanvas(reader=FakeReader())
    qtbot.addWidget(widget)
    widget.resize(800, 500)
    widget.show()
    qtbot.waitExposed(widget)
    widget.handle_mime_data(mime_for(item("/d/0", "s/0"), item("/d/1", "s/1")))
    qtbot.waitUntil(lambda: len(widget.traces) == 2, timeout=3000)
    widget._canvas.draw()
    return widget


def at(canvas, x, y, axes=None):
    """Widget position of DATA point (x, y)."""
    mpl = canvas._canvas
    mpl.draw()
    axes = axes if axes is not None else canvas._axes
    dx, dy = axes.transData.transform((x, y))
    ratio = mpl.device_pixel_ratio
    return QPoint(round(dx / ratio), round((mpl.figure.bbox.height - dy) / ratio))


def centre(canvas, axes=None):
    axes = axes if axes is not None else canvas._axes
    (x0, x1), (y0, y1) = axes.get_xlim(), axes.get_ylim()
    return at(canvas, (x0 + x1) / 2, (y0 + y1) / 2, axes)


def gesture(canvas, start: QPoint, end: QPoint, steps: int = 4) -> None:
    """Press at start, move there in steps, release at end."""
    mpl = canvas._canvas
    QTest.mousePress(mpl, Qt.LeftButton, Qt.NoModifier, start)
    for i in range(1, steps + 1):
        QTest.mouseMove(mpl, QPoint(
            start.x() + (end.x() - start.x()) * i // steps,
            start.y() + (end.y() - start.y()) * i // steps,
        ))
    QTest.mouseRelease(mpl, Qt.LeftButton, Qt.NoModifier, end)
    QTest.qWait(1)   # let QTest forget this press before the next gesture


def offset(point: QPoint, dx: int, dy: int) -> QPoint:
    return QPoint(point.x() + dx, point.y() + dy)


class Counter:
    def __init__(self, signal):
        self.count = 0
        signal.connect(self._hit)

    def _hit(self, *args):
        self.count += 1


# ------------------------------------------------- X zoom: a click is a click


@pytest.mark.parametrize("jitter", [(0, 0), (1, 0), (2, 1), (-3, 2), (4, -4)])
def test_a_click_in_x_zoom_keeps_the_applied_range(canvas, jitter):
    """The reported fault: a click (with the tremor of a real one) zoomed."""
    canvas.set_ppm_range(8.0, 2.0)
    canvas.set_zoom_mode(True)
    undo_depth = len(canvas._undo)
    views = Counter(canvas.viewChanged)
    start = centre(canvas)
    gesture(canvas, start, offset(start, *jitter))
    assert canvas.ppm_range() == (8.0, 2.0)
    assert canvas.y_range() is None
    assert views.count == 0
    assert len(canvas._undo) == undo_depth
    assert canvas._zoom_band is None
    assert not [p for p in canvas._axes.patches if isinstance(p, Rectangle)]


def test_a_real_drag_zooms_to_the_box_and_is_undoable(canvas):
    canvas.set_ppm_range(8.0, 2.0)
    canvas.set_zoom_mode(True)
    views = Counter(canvas.viewChanged)
    gesture(canvas, at(canvas, 7.0, 40.0), at(canvas, 4.0, 60.0))
    high, low = canvas.ppm_range()
    assert high == pytest.approx(7.0, abs=0.05)
    assert low == pytest.approx(4.0, abs=0.05)
    assert views.count == 1
    assert canvas.undo()
    assert canvas.ppm_range() == (8.0, 2.0)


def test_a_flat_horizontal_drag_zooms(canvas):
    """The old rule needed a non-zero height too, so a perfectly horizontal
    drag -- the natural gesture for an x zoom -- did nothing."""
    canvas.set_zoom_mode(True)
    start = at(canvas, 8.0, 50.0)
    end = QPoint(at(canvas, 3.0, 50.0).x(), start.y())
    gesture(canvas, start, end)
    high, low = canvas.ppm_range()
    assert high == pytest.approx(8.0, abs=0.05)
    assert low == pytest.approx(3.0, abs=0.05)


def test_dragging_past_the_frame_zooms_to_the_edge(canvas):
    """Released outside the axes (or the widget), the box used to be
    cancelled; zooming up to an edge needs exactly that gesture."""
    canvas.set_ppm_range(8.0, 2.0)
    canvas.set_zoom_mode(True)
    start = at(canvas, 5.0, 50.0)
    gesture(canvas, start, QPoint(canvas._canvas.width() + 60, start.y() + 30))
    high, low = canvas.ppm_range()
    assert high == pytest.approx(5.0, abs=0.05)
    assert low == pytest.approx(2.0)          # clamped to the frame's edge


def test_x_box_zoom_keeps_an_explicit_y_zoom(canvas):
    """HANDOFF 4.s: only Fit Y, reset_y_zoom and Clear may drop _y_range.
    The box zoom used to set it to None."""
    canvas.set_y_range(10.0, 50.0)
    canvas.set_zoom_mode(True)
    gesture(canvas, at(canvas, 8.0, 20.0), at(canvas, 4.0, 40.0))
    assert canvas.ppm_range()[0] == pytest.approx(8.0, abs=0.05)
    assert canvas.y_range() == (10.0, 50.0)


def test_overlay_y_zoom_box_sets_both_axes(canvas):
    canvas.set_zoom_mode(True)
    canvas.set_y_zoom_mode(True)
    gesture(canvas, at(canvas, 8.0, 20.0), at(canvas, 4.0, 60.0))
    low, high = canvas.y_range()
    assert low == pytest.approx(20.0, abs=1.0)
    assert high == pytest.approx(60.0, abs=1.0)
    assert canvas.ppm_range()[1] == pytest.approx(4.0, abs=0.05)


def test_tall_thin_box_with_y_zoom_sets_only_y(canvas):
    canvas.set_ppm_range(8.0, 2.0)
    canvas.set_zoom_mode(True)
    canvas.set_y_zoom_mode(True)
    start = at(canvas, 5.0, 20.0)
    gesture(canvas, start, QPoint(start.x() + 2, at(canvas, 5.0, 70.0).y()))
    assert canvas.ppm_range() == (8.0, 2.0)
    low, high = canvas.y_range()
    assert low == pytest.approx(20.0, abs=1.0)
    assert high == pytest.approx(70.0, abs=1.0)


def test_tall_thin_box_without_y_zoom_is_a_click(canvas):
    canvas.set_ppm_range(8.0, 2.0)
    canvas.set_zoom_mode(True)
    undo_depth = len(canvas._undo)
    start = at(canvas, 5.0, 20.0)
    gesture(canvas, start, QPoint(start.x() + 2, at(canvas, 5.0, 70.0).y()))
    assert canvas.ppm_range() == (8.0, 2.0)
    assert canvas.y_range() is None
    assert len(canvas._undo) == undo_depth


def test_stacked_y_zoom_box_never_crops_the_lanes(canvas):
    """HANDOFF 4.r: an explicit vertical window in stacked mode cuts lanes
    off the frame; the box applies its width only."""
    canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
    canvas.set_zoom_mode(True)
    canvas.set_y_zoom_mode(True)
    axes = canvas._axes
    (y0, y1) = axes.get_ylim()
    gesture(canvas, at(canvas, 8.0, y0 + 0.2 * (y1 - y0)),
            at(canvas, 4.0, y0 + 0.6 * (y1 - y0)))
    assert canvas.y_range() is None
    assert canvas.stack_gain() == 1.0
    assert canvas.ppm_range()[1] == pytest.approx(4.0, abs=0.05)


def test_a_second_press_drops_the_first_box(canvas):
    """A release that never arrived must not leave an orphan rectangle."""
    class Press:
        inaxes = canvas._axes
        xdata, ydata = 5.0, 50.0

    canvas.set_zoom_mode(True)
    assert canvas._begin_zoom_band(Press())
    assert canvas._begin_zoom_band(Press())
    rects = [p for p in canvas._axes.patches if isinstance(p, Rectangle)]
    assert len(rects) == 1


def test_a_press_outside_any_axes_starts_nothing(canvas):
    class Outside:
        inaxes = None
        xdata = ydata = None

    assert canvas._begin_zoom_band(Outside()) is False
    assert canvas._zoom_band is None


def test_a_box_on_a_replaced_axes_is_dropped(canvas):
    """A redraw during the drag can replace the axes; its stale limits must
    not decide the zoom."""
    foreign = Figure().add_subplot()
    rect = Rectangle((0, 0), 0, 0)
    foreign.add_patch(rect)
    canvas._zoom_band = (foreign, 9.0, 10.0, rect)

    class Release:
        x, y = 400.0, 100.0

    canvas._finish_zoom_band(Release())
    assert canvas.ppm_range() is None
    assert canvas._zoom_band is None


def test_a_release_without_a_position_is_ignored(canvas):
    class Press:
        inaxes = canvas._axes
        xdata, ydata = 5.0, 50.0

    class NoPosition:
        x = y = None

    canvas._begin_zoom_band(Press())
    canvas._finish_zoom_band(NoPosition())
    assert canvas.ppm_range() is None
    assert canvas._zoom_band is None
    canvas._finish_zoom_band(NoPosition())     # nothing open: a no-op


def test_turning_zoom_off_mid_drag_cancels_the_box(canvas):
    canvas.set_zoom_mode(True)
    start = centre(canvas)
    QTest.mousePress(canvas._canvas, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(canvas._canvas, offset(start, 80, 40))
    canvas.set_zoom_mode(False)
    QTest.mouseRelease(canvas._canvas, Qt.LeftButton, Qt.NoModifier,
                       offset(start, 80, 40))
    assert canvas.ppm_range() is None
    assert canvas._zoom_band is None


def test_click_slop_follows_the_device_pixel_ratio(canvas, monkeypatch):
    """matplotlib's display units are physical pixels."""
    mpl = canvas._canvas
    monkeypatch.setattr(mpl, "_device_pixel_ratio", 2.0, raising=False)
    assert canvas._click_slop() == pytest.approx(CLICK_SLOP_PIXELS * 2.0)
    for bad in (0.0, -1.0, math.nan, math.inf):
        monkeypatch.setattr(mpl, "_device_pixel_ratio", bad, raising=False)
        assert canvas._click_slop() == CLICK_SLOP_PIXELS


# ------------------------------------------------------------------- 2D


@pytest.fixture
def canvas_2d(qtbot):
    widget = SpectrumCanvas(reader=_Reader2D())
    qtbot.addWidget(widget)
    widget.resize(800, 500)
    widget.show()
    qtbot.waitExposed(widget)
    widget.handle_mime_data(_mime_2d("/d/a"))
    qtbot.waitUntil(lambda: len(widget.traces) == 1, timeout=3000)
    widget._canvas.draw()
    return widget


def test_2d_click_keeps_both_ranges(canvas_2d):
    canvas_2d.set_zoom_mode(True)
    axes = canvas_2d._figure.axes[0]
    start = centre(canvas_2d, axes)
    gesture(canvas_2d, start, offset(start, 2, 2))
    assert canvas_2d.f2_range() is None and canvas_2d.f1_range() is None


def test_2d_box_sets_f2_and_f1(canvas_2d):
    canvas_2d.set_zoom_mode(True)
    axes = canvas_2d._figure.axes[0]
    gesture(canvas_2d, at(canvas_2d, 3.0, 30.0, axes),
            at(canvas_2d, 1.0, 10.0, axes))
    f2, f1 = canvas_2d.f2_range(), canvas_2d.f1_range()
    assert f2[0] == pytest.approx(3.0, abs=0.05)
    assert f2[1] == pytest.approx(1.0, abs=0.05)
    assert f1[0] == pytest.approx(30.0, abs=0.3)
    assert f1[1] == pytest.approx(10.0, abs=0.3)
    assert canvas_2d.undo()
    assert canvas_2d.f2_range() is None and canvas_2d.f1_range() is None


def test_2d_flat_box_sets_only_f2(canvas_2d):
    canvas_2d.set_zoom_mode(True)
    axes = canvas_2d._figure.axes[0]
    start = at(canvas_2d, 3.0, 20.0, axes)
    gesture(canvas_2d, start, QPoint(at(canvas_2d, 1.0, 20.0, axes).x(),
                                     start.y() + 1))
    assert canvas_2d.f2_range()[1] == pytest.approx(1.0, abs=0.05)
    assert canvas_2d.f1_range() is None


# ----------------------------------------- moving a spectrum: a click is a click


def test_a_click_never_nudges_the_selected_spectrum(canvas):
    canvas.select_trace(0)
    undo_depth = len(canvas._undo)
    changed = Counter(canvas.tracesChanged)
    start = centre(canvas)
    gesture(canvas, start, offset(start, 2, -2))
    assert canvas.traces[0].y_offset == 0.0
    assert len(canvas._undo) == undo_depth
    assert changed.count == 0


def test_a_real_drag_moves_it_from_the_press_point_as_one_undo_step(canvas):
    canvas.select_trace(0)
    undo_depth = len(canvas._undo)
    start, end = at(canvas, 5.0, 30.0), at(canvas, 5.0, 70.0)
    gesture(canvas, start, end, steps=8)
    # No lag from the slop: the whole travel is applied.
    assert canvas.traces[0].y_offset == pytest.approx(40.0, abs=1.0)
    assert canvas.traces[1].y_offset == 0.0
    assert len(canvas._undo) == undo_depth + 1
    assert canvas.undo()
    assert canvas.traces[0].y_offset == 0.0


def test_a_drag_whose_trace_vanished_does_nothing(canvas):
    canvas.select_trace(1)
    canvas._drag_start = (5.0, 30.0, 0.0)
    canvas._selected_index = 7                 # no such trace any more

    class Move:
        inaxes = canvas._axes
        xdata, ydata = 5.0, 90.0

    canvas._continue_drag(Move())
    assert [t.y_offset for t in canvas.traces] == [0.0, 0.0]


# ------------------------------------------------ the ppm boxes and focus


@pytest.fixture
def boxes(qtbot):
    host = QWidget()
    layout = QVBoxLayout(host)
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    elsewhere = QLineEdit()              # stands in for the plot
    layout.addWidget(bar)
    layout.addWidget(elsewhere)
    qtbot.addWidget(host)
    host.show()
    qtbot.waitExposed(host)
    host.activateWindow()
    qtbot.waitUntil(host.isActiveWindow)
    bar.set_range(10.0, 0.0)
    yield bar, elsewhere
    host.close()    # holding `host` until here keeps the window alive


def type_into(qtbot, spin, text):
    spin.setFocus()
    qtbot.waitUntil(spin.hasFocus)
    spin.selectAll()
    QTest.keyClicks(spin, text)


def test_enter_applies_the_pair(qtbot, boxes):
    bar, _ = boxes
    type_into(qtbot, bar._left_spin, "7")
    with qtbot.waitSignal(bar.rangeChanged, timeout=1000) as blocker:
        QTest.keyClick(bar._left_spin, Qt.Key_Return)
    assert blocker.args == [7.0, 0.0]


def test_moving_between_the_boxes_applies_nothing(qtbot, boxes):
    """Left -> Right used to apply a mixed pair (new left, old right)."""
    bar, _ = boxes
    hits = Counter(bar.rangeChanged)
    type_into(qtbot, bar._left_spin, "7")
    type_into(qtbot, bar._right_spin, "3")
    assert hits.count == 0
    assert bar.current_range() == (7.0, 3.0)
    with qtbot.waitSignal(bar.rangeChanged, timeout=1000) as blocker:
        QTest.keyClick(bar._right_spin, Qt.Key_Enter)
    assert blocker.args == [7.0, 3.0]


def test_tab_between_the_boxes_applies_nothing(qtbot, boxes):
    bar, _ = boxes
    hits = Counter(bar.rangeChanged)
    type_into(qtbot, bar._left_spin, "7")
    QTest.keyClick(bar._left_spin, Qt.Key_Tab)
    qtbot.waitUntil(lambda: not bar._left_spin.hasFocus())
    assert hits.count == 0
    assert bar._left_spin.value() == 7.0


def test_leaving_for_elsewhere_restores_the_range_on_the_plot(qtbot, boxes):
    bar, elsewhere = boxes
    hits = Counter(bar.rangeChanged)
    type_into(qtbot, bar._left_spin, "7")
    elsewhere.setFocus()
    qtbot.waitUntil(elsewhere.hasFocus)
    assert hits.count == 0
    assert bar.current_range() == (10.0, 0.0)


def test_restores_the_latest_range_pushed_in_from_the_plot(qtbot, boxes):
    bar, elsewhere = boxes
    bar.set_range(6.0, 1.0)                     # e.g. a wheel zoom
    type_into(qtbot, bar._left_spin, "4")
    elsewhere.setFocus()
    qtbot.waitUntil(elsewhere.hasFocus)
    assert bar.current_range() == (6.0, 1.0)


def test_apply_that_takes_focus_still_applies_the_typing(qtbot, boxes):
    """On Windows a push button takes focus on PRESS, before clicked fires;
    reverting on that focus change would make Apply apply the old range."""
    bar, _ = boxes
    bar._apply_button.setFocusPolicy(Qt.StrongFocus)
    type_into(qtbot, bar._left_spin, "7")
    with qtbot.waitSignal(bar.rangeChanged, timeout=1000) as blocker:
        QTest.mouseClick(bar._apply_button, Qt.LeftButton)
    assert blocker.args == [7.0, 0.0]


def test_another_window_keeps_the_typing(qtbot, boxes):
    bar, _ = boxes
    hits = Counter(bar.rangeChanged)
    type_into(qtbot, bar._left_spin, "7")
    other = QLineEdit()
    qtbot.addWidget(other)
    other.show()
    qtbot.waitExposed(other)
    other.activateWindow()
    other.setFocus()
    qtbot.waitUntil(other.hasFocus)
    assert hits.count == 0
    assert bar._left_spin.value() == 7.0
    other.close()


def test_a_recent_range_becomes_the_range_to_restore(qtbot, boxes):
    bar, elsewhere = boxes
    type_into(qtbot, bar._left_spin, "8")
    QTest.keyClick(bar._left_spin, Qt.Key_Return)
    bar.set_range(10.0, 0.0)
    bar._on_recent_selected(0)                  # 8 -> 0, just applied
    type_into(qtbot, bar._left_spin, "3")
    elsewhere.setFocus()
    qtbot.waitUntil(elsewhere.hasFocus)
    assert bar.current_range() == (8.0, 0.0)


def test_every_box_has_its_enter_wired(boxes):
    bar, _ = boxes
    for spin in (bar._left_spin, bar._right_spin):
        assert spin.findChild(QLineEdit) is not None


# ------------------------------------------- the reported gesture, end to end


@pytest.fixture
def window(qtbot, monkeypatch):
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    win = MainWindow()
    qtbot.addWidget(win)
    win.resize(1300, 850)
    win._canvas._reader = FakeReader()
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow)
    win._canvas.handle_mime_data(mime_for(item("/d/0", "s/0"), item("/d/1", "s/1")))
    qtbot.waitUntil(lambda: len(win._canvas.traces) == 2, timeout=3000)
    bar = win._adjustment_bar
    type_into(qtbot, bar._left_spin, "8")
    type_into(qtbot, bar._right_spin, "2")
    QTest.keyClick(bar._right_spin, Qt.Key_Return)
    assert win._canvas.ppm_range() == (8.0, 2.0)
    return win


@pytest.mark.parametrize("x_zoom", [False, True])
def test_clicking_the_plot_keeps_the_last_applied_range(qtbot, window, x_zoom):
    canvas, bar = window._canvas, window._adjustment_bar
    if x_zoom:
        bar._zoom_toggle.setChecked(True)
    type_into(qtbot, bar._left_spin, "5")        # typed, NOT applied
    start = centre(canvas)
    gesture(canvas, start, offset(start, 2, 1))
    qtbot.waitUntil(lambda: not bar._left_spin.hasFocus())
    assert canvas.ppm_range() == (8.0, 2.0)
    assert bar.current_range() == (8.0, 2.0)


def test_a_real_box_zoom_still_updates_the_boxes(qtbot, window):
    canvas, bar = window._canvas, window._adjustment_bar
    bar._zoom_toggle.setChecked(True)
    gesture(canvas, at(canvas, 7.0, 40.0), at(canvas, 4.0, 60.0))
    high, low = bar.current_range()
    assert high == pytest.approx(7.0, abs=0.05)
    assert low == pytest.approx(4.0, abs=0.05)
    assert isinstance(bar._apply_button, QPushButton)


# ------------------- the boxes show the range just applied (the root cause)


def test_apply_shows_the_new_range_not_the_previous_one(qtbot, window, monkeypatch):
    """push_undo fires historyChanged BEFORE the change, and the window
    synced the boxes on it -- so every Apply that opened a new undo step left
    the PREVIOUS range in the boxes while the plot showed the new one. The
    next click on the plot then applied that stale pair."""
    canvas, bar = window._canvas, window._adjustment_bar
    # Every Apply opens a new undo step, as it does a second or more apart.
    monkeypatch.setattr(type(canvas), "COALESCE_SECONDS", 0.0)
    assert bar.current_range() == (8.0, 2.0)
    for left, right in (("7", "3"), ("6", "4")):
        type_into(qtbot, bar._left_spin, left)
        type_into(qtbot, bar._right_spin, right)
        QTest.keyClick(bar._right_spin, Qt.Key_Return)
        assert canvas.ppm_range() == (float(left), float(right))
        assert bar.current_range() == (float(left), float(right))
    start = centre(canvas)
    gesture(canvas, start, offset(start, 1, 1))
    assert canvas.ppm_range() == (6.0, 4.0)
    assert bar.current_range() == (6.0, 4.0)


def test_undo_and_redo_still_move_the_boxes(qtbot, window, monkeypatch):
    canvas, bar = window._canvas, window._adjustment_bar
    monkeypatch.setattr(type(canvas), "COALESCE_SECONDS", 0.0)
    type_into(qtbot, bar._left_spin, "7")
    type_into(qtbot, bar._right_spin, "3")
    QTest.keyClick(bar._right_spin, Qt.Key_Return)
    window._undo()
    assert canvas.ppm_range() == (8.0, 2.0)
    assert bar.current_range() == (8.0, 2.0)
    window._redo()
    assert bar.current_range() == (7.0, 3.0)


def test_opening_a_session_shows_its_own_range(qtbot, window, tmp_path, monkeypatch):
    import json

    canvas, bar = window._canvas, window._adjustment_bar
    state = canvas.session_state()
    state["ppm_range"] = [6.5, 3.5]
    path = tmp_path / "s.helspin"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        "helspin.__main__.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: (str(path), "")),
    )
    monkeypatch.setattr(canvas, "restore_session",
                        lambda s: (setattr(canvas, "_ppm_range", (6.5, 3.5)), [])[1])
    window._open_session()
    assert bar.current_range() == (6.5, 3.5)


# ------------------------------------------------------ remaining edges


def test_2d_thin_tall_box_sets_only_f1(canvas_2d):
    canvas_2d.set_zoom_mode(True)
    axes = canvas_2d._figure.axes[0]
    start = at(canvas_2d, 2.0, 30.0, axes)
    gesture(canvas_2d, start, QPoint(start.x() + 1,
                                     at(canvas_2d, 2.0, 10.0, axes).y()))
    assert canvas_2d.f2_range() is None
    assert canvas_2d.f1_range()[1] == pytest.approx(10.0, abs=0.3)


def test_band_point_without_a_band_is_none(canvas):
    class Anywhere:
        x, y = 10.0, 10.0

    assert canvas._band_point(Anywhere()) is None


def test_a_degenerate_transform_gives_no_point(canvas):
    class Inverse:
        def transform(self, point):
            return (math.nan, math.nan)

    class Transform:
        def inverted(self):
            return Inverse()

    class BrokenAxes:
        transData = Transform()

    canvas._zoom_band = (BrokenAxes(), 1.0, 1.0, Rectangle((0, 0), 0, 0))

    class Anywhere:
        x, y = 10.0, 10.0

    assert canvas._band_point(Anywhere()) is None
    canvas._update_zoom_band(Anywhere())          # no point: nothing drawn
    canvas._zoom_band = None


def test_cancelling_a_box_whose_rectangle_is_already_gone(canvas):
    class Press:
        inaxes = canvas._axes
        xdata, ydata = 5.0, 50.0

    canvas._begin_zoom_band(Press())
    canvas._zoom_band[3].remove()                 # e.g. wiped by a redraw
    canvas._cancel_zoom_band()
    assert canvas._zoom_band is None


def test_a_drag_move_without_data_coordinates_is_ignored(canvas):
    canvas.select_trace(0)
    canvas._drag_start = (5.0, 30.0, 0.0)

    class Outside:
        inaxes = canvas._axes
        xdata = ydata = None

    canvas._continue_drag(Outside())
    assert canvas.traces[0].y_offset == 0.0
    assert canvas._drag_moved is False
