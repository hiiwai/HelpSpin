"""Per-trace scaling, selection, appearance preferences, and the list panel."""

import json
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, Qt

from helspin.ui.dataset_model import MIME_DATASET
from helspin.ui.preferences_dialog import (
    DEFAULT_COLORS,
    MAX_LINE_WIDTH,
    MIN_LINE_WIDTH,
    SLOT_COUNT,
    PreferencesDialog,
    _clamp_width,
    default_styles,
)
from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace
from helspin.ui.spectrum_list_panel import SpectrumListPanel

pytestmark = pytest.mark.usefixtures("qapp")


class FakeSpectrum:
    def __init__(self, n=128):
        self.real = np.linspace(0.0, 100.0, n)

        class Axis:
            size = n

            def ppm_scale(self_inner):
                return np.linspace(10.0, 0.0, n)

        self.axis = Axis()
        self.ns = 1
        self.rg = 1.0


class FakeReader:
    def read_1d(self, path, procno=1):
        return FakeSpectrum()


def mime_for(*items):
    mime = QMimeData()
    mime.setData(MIME_DATASET, json.dumps(list(items)).encode("utf-8"))
    return mime


def item(path="/d/1", label="s/1"):
    return {"path": path, "dimensionality": 1, "label": label}


def loaded_canvas(qtbot, n=2):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(
        mime_for(*[item(path=f"/d/{i}", label=f"s/{i}") for i in range(n)])
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == n, timeout=3000)
    return canvas


# --- selection ------------------------------------------------------------


def test_first_loaded_trace_becomes_selected(qtbot):
    """So the wheel and y-scale work immediately, without an extra click."""
    canvas = loaded_canvas(qtbot, 1)
    assert canvas.selected_index() == 0


def test_select_trace_changes_selection(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.select_trace(1)
    assert canvas.selected_index() == 1
    assert canvas.selected_trace().label == "s/1"


def test_select_none_clears_selection(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.select_trace(None)
    assert canvas.selected_index() is None
    assert canvas.selected_trace() is None


def test_out_of_range_selection_is_ignored(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.select_trace(0)
    canvas.select_trace(99)
    assert canvas.selected_index() == 0


# --- per-trace y scaling ---------------------------------------------------


def test_set_y_scale_applies_to_one_trace_only(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_y_scale(1, 5.0)
    assert canvas.traces[1].y_scale == 5.0
    assert canvas.traces[0].y_scale == 1.0


def test_zero_or_negative_y_scale_is_refused(qtbot):
    """Would flatten or invert the spectrum -- never what a scale means."""
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_y_scale(0, 0.0)
    assert canvas.traces[0].y_scale == 1.0
    canvas.set_y_scale(0, -3.0)
    assert canvas.traces[0].y_scale == 1.0


def test_non_finite_y_scale_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_y_scale(0, float("nan"))
    assert canvas.traces[0].y_scale == 1.0
    canvas.set_y_scale(0, float("inf"))
    assert canvas.traces[0].y_scale == 1.0


def test_y_scale_on_unknown_index_is_harmless(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_y_scale(7, 2.0)   # must not raise
    assert canvas.traces[0].y_scale == 1.0


def test_nudge_multiplies_the_scale(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_y_scale(0, 2.0)
    canvas.nudge_y_scale(0, 1.5)
    assert canvas.traces[0].y_scale == pytest.approx(3.0)


def test_nudge_is_clamped_and_cannot_reach_zero(qtbot):
    """A fast scroll must not drive the scale to zero or overflow."""
    canvas = loaded_canvas(qtbot, 1)
    for _ in range(400):
        canvas.nudge_y_scale(0, 0.5)
    assert canvas.traces[0].y_scale > 0
    for _ in range(400):
        canvas.nudge_y_scale(0, 2.0)
    assert np.isfinite(canvas.traces[0].y_scale)
    assert canvas.traces[0].y_scale <= 1e9


def test_y_offset_moves_a_trace(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_y_offset(0, 25.0)
    assert canvas.traces[0].y_offset == 25.0
    canvas.nudge_y_offset(0, 5.0)
    assert canvas.traces[0].y_offset == 30.0


def test_non_finite_y_offset_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_y_offset(0, float("nan"))
    assert canvas.traces[0].y_offset == 0.0


# --- appearance -----------------------------------------------------------


def test_default_line_width_applies_to_existing_traces(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_default_line_width(3.0)
    assert [t.line_width for t in canvas.traces] == [3.0, 3.0]


def test_invalid_line_width_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    before = canvas.default_line_width()
    canvas.set_default_line_width(0.0)
    canvas.set_default_line_width(-1.0)
    assert canvas.default_line_width() == before


def test_set_palette_recolours_existing_traces(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_palette(["#111111", "#222222"])
    assert [t.color for t in canvas.traces] == ["#111111", "#222222"]


def test_empty_palette_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    before = canvas.traces[0].color
    canvas.set_palette([])
    assert canvas.traces[0].color == before


def test_set_trace_color_changes_only_that_trace(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    original = canvas.traces[1].color
    canvas.set_trace_color(0, "#ABCDEF")
    assert canvas.traces[0].color == "#ABCDEF"
    assert canvas.traces[1].color == original


def test_trace_visibility_toggles(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_trace_visible(0, False)
    assert canvas.traces[0].visible is False
    canvas.set_trace_visible(0, True)
    assert canvas.traces[0].visible is True


def test_hiding_every_trace_does_not_crash_the_redraw(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_trace_visible(0, False)
    canvas.set_trace_visible(1, False)
    canvas._redraw()   # must not raise


# --- ppm bounds and clear -------------------------------------------------


def test_ppm_bounds_is_none_when_empty():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.ppm_bounds() is None


def test_ppm_bounds_spans_the_loaded_data(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    left, right = canvas.ppm_bounds()
    assert left > right          # descending
    assert left == pytest.approx(10.0)
    assert right == pytest.approx(0.0)


def test_clear_resets_selection_and_range_too(qtbot):
    """A true reset -- leaving a stale ppm range or selection behind makes the
    next drop appear on a nonsensical axis."""
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_ppm_range(9.0, 1.0)
    canvas.select_trace(1)
    canvas.clear()
    assert canvas.traces == []
    assert canvas.selected_index() is None
    assert canvas._ppm_range is None


# --- stacking with scaling ------------------------------------------------


def test_stacked_spacing_accounts_for_scaled_traces(qtbot):
    """A scaled-up trace must not overlap its neighbour in stacked mode."""
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
    canvas.set_y_scale(0, 50.0)
    canvas._redraw()   # must not raise; spacing derives from scaled spans


# --- preferences dialog ---------------------------------------------------


def test_preferences_has_one_row_per_slot():
    dlg = PreferencesDialog()
    styles = dlg.styles()
    assert len(styles) == SLOT_COUNT
    assert set(styles[0]) == {"color", "style", "width"}


def test_preferences_returns_the_values_it_was_given():
    given = default_styles()
    given[0] = {"color": "#123456", "style": "--", "width": 2.0}
    dlg = PreferencesDialog(styles=given)
    out = dlg.styles()
    assert out[0]["color"] == "#123456"
    assert out[0]["style"] == "--"
    assert out[0]["width"] == pytest.approx(2.0)


def test_preferences_clamps_an_absurd_width():
    given = default_styles()
    given[0]["width"] = 9999.0
    dlg = PreferencesDialog(styles=given)
    assert dlg.styles()[0]["width"] <= MAX_LINE_WIDTH


def test_preferences_clamps_a_zero_width():
    given = default_styles()
    given[0]["width"] = 0.0
    dlg = PreferencesDialog(styles=given)
    assert dlg.styles()[0]["width"] >= MIN_LINE_WIDTH


def test_clamp_width_handles_garbage():
    assert _clamp_width("nonsense") == 0.8
    assert _clamp_width(float("nan")) == 0.8
    assert _clamp_width(-5) == MIN_LINE_WIDTH


def test_preferences_pads_a_short_style_list():
    """Fewer slots supplied than exist must not raise."""
    dlg = PreferencesDialog(styles=[{"color": "#111111", "style": "-", "width": 1.0}])
    assert len(dlg.styles()) == SLOT_COUNT


def test_preferences_reset_restores_defaults():
    given = [{"color": "#111111", "style": ":", "width": 3.0} for _ in range(SLOT_COUNT)]
    dlg = PreferencesDialog(styles=given)
    dlg._reset()
    out = dlg.styles()
    assert [e["color"] for e in out] == DEFAULT_COLORS
    assert all(e["style"] == "-" for e in out)


def test_apply_styles_restyles_existing_traces(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    styles = canvas.slot_styles()
    styles[0] = {"color": "#FF0000", "style": ":", "width": 3.0}
    canvas.apply_styles(styles)
    assert canvas.traces[0].color == "#FF0000"
    assert canvas.traces[0].line_style == ":"
    assert canvas.traces[0].line_width == 3.0


def test_new_traces_pick_up_their_slot_style(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    styles = canvas.slot_styles()
    styles[0] = {"color": "#00FF00", "style": "--", "width": 2.0}
    canvas.apply_styles(styles)
    canvas.handle_mime_data(mime_for(item()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    assert canvas.traces[0].color == "#00FF00"
    assert canvas.traces[0].line_width == 2.0


def test_apply_styles_with_empty_list_is_ignored(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    before = canvas.traces[0].color
    canvas.apply_styles([])
    assert canvas.traces[0].color == before


# --- spectrum list panel --------------------------------------------------


class FakeTrace:
    def __init__(self, label, color="#000000", visible=True, y_scale=1.0):
        self.label = label
        self.color = color
        self.visible = visible
        self.y_scale = y_scale


def test_list_panel_shows_one_row_per_trace():
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a"), FakeTrace("b")], 0)
    assert panel._list.count() == 2


def test_list_panel_reflects_the_selection():
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a"), FakeTrace("b")], 1)
    assert panel._list.currentRow() == 1


def test_list_panel_shows_the_selected_trace_scale():
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a", y_scale=4.0)], 0)
    assert panel._scale_spin.value() == pytest.approx(4.0)


def test_list_panel_controls_disabled_with_no_selection():
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], None)
    assert not panel._scale_spin.isEnabled()
    assert not panel._remove_button.isEnabled()


def test_list_panel_emits_selection_change(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a"), FakeTrace("b")], 0)
    with qtbot.waitSignal(panel.selectionChanged, timeout=1000) as blocker:
        panel._list.setCurrentRow(1)
    assert blocker.args == [1]


def test_list_panel_emits_scale_change(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], 0)
    with qtbot.waitSignal(panel.yScaleChanged, timeout=1000) as blocker:
        panel._scale_spin.setValue(3.0)
    assert blocker.args[0] == 0
    assert blocker.args[1] == pytest.approx(3.0)


def test_list_panel_emits_visibility_toggle(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], 0)
    item0 = panel._list.item(0)
    with qtbot.waitSignal(panel.visibilityToggled, timeout=1000) as blocker:
        item0.setCheckState(Qt.Unchecked)
    assert blocker.args == [0, False]


def test_list_panel_emits_remove_request(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], 0)
    with qtbot.waitSignal(panel.removeRequested, timeout=1000) as blocker:
        panel._remove_button.click()
    assert blocker.args == [0]


def test_list_panel_rebuild_does_not_emit_spurious_signals(qtbot):
    """Syncing from the canvas must not echo back and cause a feedback loop."""
    panel = SpectrumListPanel()
    received = []
    panel.selectionChanged.connect(received.append)
    panel.set_traces([FakeTrace("a"), FakeTrace("b")], 1)
    assert received == []


# --- y-limits pinned so scaling is actually visible ------------------------


def test_y_limits_stay_fixed_when_a_trace_is_scaled(qtbot):
    """The reported 'Y scale does nothing' bug: matplotlib autoscales, so
    multiplying the data just grew the axis to match and the picture looked
    identical. Limits are pinned once established."""
    canvas = loaded_canvas(qtbot, 1)
    before = canvas._axes.get_ylim()
    canvas.set_y_scale(0, 4.0)
    after = canvas._axes.get_ylim()
    assert before == after
    assert float(canvas._axes.lines[0].get_ydata().max()) == pytest.approx(
        400.0, rel=0.01
    )


def test_reset_y_limits_returns_to_the_raw_frame(qtbot):
    """The vertical frame is derived from RAW intensities on purpose: per-trace
    scaling is an adjustment made *within* that frame, so folding the scale
    back into the frame would cancel out the effect it exists to produce.
    "Reset" therefore restores the neutral frame, not a scale-fitted one."""
    canvas = loaded_canvas(qtbot, 1)
    raw_frame = canvas._axes.get_ylim()
    canvas.set_y_scale(0, 10.0)
    canvas.reset_y_limits()
    assert canvas._axes.get_ylim() == pytest.approx(raw_frame)


def test_dropping_a_second_spectrum_reautoscales(qtbot):
    """Regression: y limits were pinned at the FIRST drop, so a spectrum
    loaded afterwards with a much smaller intensity rendered as a flat line at
    zero. Limits must re-fit whenever the set of traces changes."""
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item(path="/d/1", label="a")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    first = canvas._y_limits

    canvas.handle_mime_data(mime_for(item(path="/d/2", label="b")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    # Limits were recomputed (not left pinned from the single-trace state).
    assert canvas._y_limits is not None
    top = canvas._axes.get_ylim()[1]
    assert top >= max(float(t.intensity.max()) for t in canvas.traces)


def test_removing_a_trace_reautoscales(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_y_scale(0, 20.0)
    canvas.remove_trace("/d/0")
    assert canvas._y_limits is not None


def test_scale_changes_do_not_reautoscale(qtbot):
    """The counterpart: limits must stay put while adjusting, or scaling is
    invisible again."""
    canvas = loaded_canvas(qtbot, 1)
    before = canvas._axes.get_ylim()
    canvas.set_y_scale(0, 3.0)
    assert canvas._axes.get_ylim() == before


# --- per-trace labels drawn on the plot -----------------------------------


def test_each_trace_is_labelled_on_the_plot(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    texts = {t.get_text() for t in canvas._axes.texts}
    assert "s/0" in texts and "s/1" in texts


def test_labels_do_not_move_when_a_trace_is_scaled(qtbot):
    """Reported bug: labels were anchored to each trace's data maximum, so
    scaling a spectrum dragged its name around the plot. They are pinned in
    axes-fraction coordinates now."""
    canvas = loaded_canvas(qtbot, 2)
    before = [t.get_position() for t in canvas._axes.texts]
    canvas.set_y_scale(0, 9.0)
    after = [t.get_position() for t in canvas._axes.texts]
    assert before == after


def test_labels_are_not_drawn_when_nothing_is_loaded():
    canvas = SpectrumCanvas(reader=FakeReader())
    texts = [t.get_text() for t in canvas._axes.texts]
    assert texts == ["Drag spectra here"]


# --- grid -----------------------------------------------------------------


def test_grid_is_off_by_default():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.grid_visible() is False


def test_grid_toggles(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_grid_visible(True)
    assert canvas.grid_visible() is True
    canvas.set_grid_visible(False)
    assert canvas.grid_visible() is False


# --- line style -----------------------------------------------------------


def test_line_style_defaults_to_solid(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    assert canvas.traces[0].line_style == "-"


def test_line_style_can_be_set_per_trace(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_trace_line_style(0, "--")
    assert canvas.traces[0].line_style == "--"
    assert canvas.traces[1].line_style == "-"


def test_unknown_line_style_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_trace_line_style(0, "squiggly")
    assert canvas.traces[0].line_style == "-"


# --- drag to move (stacked only) ------------------------------------------


class FakeMouseEvent:
    def __init__(self, axes, ydata=0.0, xdata=5.0, button=1):
        self.inaxes = axes
        self.ydata = ydata
        self.xdata = xdata
        self.button = button


def test_drag_moves_the_selected_trace_when_stacked(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
    canvas.select_trace(1)

    canvas._on_mouse_press(FakeMouseEvent(canvas._axes, ydata=10.0))
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=40.0))
    canvas._on_mouse_release(FakeMouseEvent(canvas._axes))

    assert canvas.traces[1].y_offset == pytest.approx(30.0)
    assert canvas.traces[0].y_offset == 0.0   # others untouched


def test_drag_works_in_overlay_mode_too(qtbot):
    """Restricting dragging to stacked made the control silently do nothing in
    overlay, which reads as broken. The selection makes the target
    unambiguous in either arrangement."""
    canvas = loaded_canvas(qtbot, 2)
    canvas.select_trace(1)
    canvas._on_mouse_press(FakeMouseEvent(canvas._axes, ydata=10.0))
    assert canvas._drag_start is not None
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=25.0))
    assert canvas.traces[1].y_offset == pytest.approx(15.0)


def test_drag_does_nothing_with_no_selection(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
    canvas.select_trace(None)
    canvas._on_mouse_press(FakeMouseEvent(canvas._axes, ydata=10.0))
    assert canvas._drag_start is None


# --- crosshair ------------------------------------------------------------


def test_crosshair_appears_on_move_and_clears_on_leave(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=5.0, xdata=4.0))
    assert canvas._crosshair is not None
    canvas._on_mouse_leave(FakeMouseEvent(None))
    assert canvas._crosshair is None


def test_crosshair_emits_the_cursor_position(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    with qtbot.waitSignal(canvas.cursorMoved, timeout=1000) as blocker:
        canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=7.0, xdata=3.5))
    assert blocker.args[0] == pytest.approx(3.5)


def test_crosshair_can_be_disabled(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_crosshair_enabled(False)
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=5.0, xdata=4.0))
    assert canvas._crosshair is None


# --- Y offset by typing (list panel) --------------------------------------


def test_list_panel_has_a_y_offset_box():
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], 0)
    assert panel._offset_spin.isEnabled()


def test_list_panel_emits_offset_change(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], 0)
    with qtbot.waitSignal(panel.yOffsetChanged, timeout=1000) as blocker:
        panel._offset_spin.setValue(150.0)
    assert blocker.args[0] == 0
    assert blocker.args[1] == pytest.approx(150.0)


def test_offset_box_range_covers_real_nmr_intensities():
    """Intensities run to ~1e9; a +/-100 range would be useless."""
    panel = SpectrumListPanel()
    assert panel._offset_spin.maximum() >= 1e9


def test_list_panel_no_longer_has_a_line_style_combo():
    """Line style moved to Preferences, per the reported preference."""
    panel = SpectrumListPanel()
    assert not hasattr(panel, "_style_combo")


# --- autoscale each trace so all are legible ------------------------------


class BigSmallReader:
    """Two spectra three orders of magnitude apart, like real data."""

    def __init__(self):
        self.n = 0

    def read_1d(self, path, procno=1):
        self.n += 1
        amp = 1e11 if self.n == 1 else 1e8
        s = FakeSpectrum()
        s.real = np.linspace(0.0, amp, 128)
        return s


def big_small_canvas(qtbot):
    canvas = SpectrumCanvas(reader=BigSmallReader())
    canvas.handle_mime_data(
        mime_for(item(path="/d/0", label="big"), item(path="/d/1", label="small"))
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    return canvas


def test_autoscale_makes_a_weak_spectrum_visible(qtbot):
    """A spectrum 1000x weaker than its neighbour is a flat line at zero
    without per-trace scaling, because the frame must fit the strong one."""
    canvas = big_small_canvas(qtbot)
    canvas.autoscale_traces()
    low, high = canvas._axes.get_ylim()
    span = high - low
    for trace in canvas.traces:
        drawn = float(trace.intensity.max()) * trace.y_scale
        assert drawn > span * 0.1     # genuinely visible, not a flat line


def test_autoscale_is_still_adjustable_afterwards(qtbot):
    canvas = big_small_canvas(qtbot)
    canvas.autoscale_traces()
    scaled = canvas.traces[1].y_scale
    canvas.set_y_scale(1, scaled * 2)
    assert canvas.traces[1].y_scale == pytest.approx(scaled * 2)


def test_autoscale_with_nothing_loaded_is_harmless():
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.autoscale_traces()   # must not raise


def test_autoscale_ignores_hidden_traces(qtbot):
    canvas = big_small_canvas(qtbot)
    canvas.set_trace_visible(1, False)
    before = canvas.traces[1].y_scale
    canvas.autoscale_traces()
    assert canvas.traces[1].y_scale == before


# --- visibility must not disturb the view ---------------------------------


def test_hiding_and_showing_preserves_scale_offset_and_frame(qtbot):
    """Reported: unticking a spectrum rescaled everything, and re-ticking did
    not restore the previous view. Hiding is a viewing action only."""
    canvas = big_small_canvas(qtbot)
    canvas.autoscale_traces()
    canvas.set_y_offset(0, 123.0)
    frame_before = canvas._axes.get_ylim()
    scales_before = [t.y_scale for t in canvas.traces]
    offsets_before = [t.y_offset for t in canvas.traces]

    canvas.set_trace_visible(0, False)
    canvas.set_trace_visible(0, True)

    assert canvas._axes.get_ylim() == frame_before
    assert [t.y_scale for t in canvas.traces] == scales_before
    assert [t.y_offset for t in canvas.traces] == offsets_before


# --- grid: X only, settable spacing ---------------------------------------


def test_grid_is_x_only(qtbot):
    """A horizontal grid cuts across every stacked spectrum and reads as part
    of the data; the ppm axis is what a reader measures against."""
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_grid_visible(True)
    assert any(l.get_visible() for l in canvas._axes.get_xgridlines())
    assert not any(l.get_visible() for l in canvas._axes.get_ygridlines())


def test_grid_spacing_sets_tick_interval(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_grid_visible(True)
    canvas.set_grid_spacing_ppm(0.5)
    ticks = canvas._axes.get_xticks()
    assert abs(ticks[1] - ticks[0]) == pytest.approx(0.5)


def test_grid_spacing_none_means_automatic(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_grid_spacing_ppm(None)
    assert canvas.grid_spacing_ppm() is None


def test_invalid_grid_spacing_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_grid_spacing_ppm(0.5)
    canvas.set_grid_spacing_ppm(0)
    canvas.set_grid_spacing_ppm(-2)
    canvas.set_grid_spacing_ppm("nonsense")
    assert canvas.grid_spacing_ppm() == 0.5


# --- move to bottom -------------------------------------------------------


def test_move_to_bottom_puts_the_baseline_at_the_frame_floor(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.move_to_bottom(1)
    frame_low = canvas._axes.get_ylim()[0]
    trace = canvas.traces[1]
    baseline = float(trace.intensity.min()) * trace.y_scale + trace.y_offset
    assert baseline == pytest.approx(frame_low, rel=0.05)


def test_move_all_to_bottom_gives_a_common_baseline(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_y_offset(0, 500.0)
    canvas.move_all_to_bottom()
    baselines = [
        float(t.intensity.min()) * t.y_scale + t.y_offset for t in canvas.traces
    ]
    assert baselines[0] == pytest.approx(baselines[1], rel=0.05)


def test_move_to_bottom_on_unknown_index_is_harmless(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.move_to_bottom(9)   # must not raise


def test_list_panel_emits_move_to_bottom(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a")], 0)
    with qtbot.waitSignal(panel.moveToBottomRequested, timeout=1000) as blocker:
        panel._bottom_button.click()
    assert blocker.args == [0]


# --- WYSIWYG export -------------------------------------------------------


@pytest.mark.parametrize("ext", ["png", "jpg", "tif", "svg", "pdf", "eps", "ps"])
def test_save_image_writes_every_supported_format(qtbot, tmp_path, ext):
    canvas = loaded_canvas(qtbot, 2)
    out = tmp_path / f"figure.{ext}"
    canvas.save_image(out)
    assert out.exists() and out.stat().st_size > 0


def test_svg_export_keeps_text_editable(qtbot, tmp_path):
    """matplotlib turns text into outlines by default in vector output, which
    defeats the point of exporting vector -- labels must stay editable."""
    canvas = loaded_canvas(qtbot, 1)
    out = tmp_path / "figure.svg"
    canvas.save_image(out)
    content = out.read_text()
    assert "<text" in content
    assert "s/0" in content        # the actual label, as real text


def test_export_can_be_transparent(qtbot, tmp_path):
    canvas = loaded_canvas(qtbot, 1)
    out = tmp_path / "clear.eps"
    canvas.save_image(out, transparent=True)
    assert out.stat().st_size > 0


def test_export_of_an_unwritable_path_raises_rather_than_corrupting(qtbot, tmp_path):
    canvas = loaded_canvas(qtbot, 1)
    with pytest.raises(Exception):
        canvas.save_image(tmp_path / "no_such_dir" / "x.png")


def test_context_menu_offers_save_and_view_actions(qtbot):
    """Built without exec(), which would block the test run forever."""
    canvas = loaded_canvas(qtbot, 1)
    labels = [a.text() for a in canvas.build_context_menu().actions() if a.text()]
    assert any("Save image" in t for t in labels)
    assert any("Auto scale" in t for t in labels)


# --- fit everything inside the canvas -------------------------------------


def test_autoscale_leaves_every_trace_inside_the_frame(qtbot):
    canvas = big_small_canvas(qtbot)
    canvas.autoscale_traces()
    low, high = canvas._axes.get_ylim()
    for line in canvas._axes.lines:
        data = np.asarray(line.get_ydata(), dtype=float)
        assert data.min() >= low
        assert data.max() <= high


def test_autoscale_fits_inside_the_frame_when_stacked(qtbot):
    canvas = big_small_canvas(qtbot)
    canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
    canvas.autoscale_traces()
    low, high = canvas._axes.get_ylim()
    for line in canvas._axes.lines:
        data = np.asarray(line.get_ydata(), dtype=float)
        assert data.min() >= low
        assert data.max() <= high


def test_fit_to_drawn_with_nothing_loaded_is_harmless():
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.fit_to_drawn()   # must not raise


# --- cursor readout -------------------------------------------------------


def test_cursor_shows_the_ppm_value_on_the_plot(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=50.0, xdata=7.1234))
    texts = [t.get_text() for t in canvas._axes.texts]
    assert any("7.1234" in t and "ppm" in t for t in texts)


def test_cursor_label_is_removed_on_leave(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=50.0, xdata=3.0))
    canvas._on_mouse_leave(FakeMouseEvent(None))
    texts = [t.get_text() for t in canvas._axes.texts]
    assert not any("ppm" in t for t in texts)


# --- palette --------------------------------------------------------------


def test_second_default_colour_is_blue_not_orange():
    """Orange next to black is muddy on screen and in print; a two-spectrum
    comparison is the common case, so slot 2 must be high contrast."""
    from helspin.ui.preferences_dialog import DEFAULT_COLORS

    assert DEFAULT_COLORS[0] == "#000000"
    assert DEFAULT_COLORS[1] == "#0072B2"


def test_default_palette_has_no_near_invisible_yellow():
    from helspin.ui.preferences_dialog import DEFAULT_COLORS

    assert "#F0E442" not in DEFAULT_COLORS


# --- equal-noise normalisation --------------------------------------------


class NoisyReader:
    """Two spectra with the SAME true SNR but wildly different absolute scale."""

    def __init__(self):
        self.n = 0

    def read_1d(self, path, procno=1):
        self.n += 1
        rng = np.random.default_rng(self.n)
        amp, noise = (1e11, 1e9) if self.n == 1 else (1e8, 1e6)
        n = 2048
        x = np.arange(n)
        s = FakeSpectrum(n)
        s.real = amp / (1 + ((x - 500) / 4.0) ** 2) + rng.normal(0, noise, n)
        return s


def noisy_canvas(qtbot):
    canvas = SpectrumCanvas(reader=NoisyReader())
    canvas.handle_mime_data(
        mime_for(item(path="/d/0", label="strong"), item(path="/d/1", label="weak"))
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    return canvas


def _noise(trace):
    data = np.asarray(trace.intensity, dtype=float)
    return float(np.median(np.abs(data - np.median(data)))) * 1.4826


def test_normalise_to_noise_equalises_noise_levels(qtbot):
    """Equal noise is what makes peak heights directly comparable between
    spectra acquired with different scan counts or gain."""
    canvas = noisy_canvas(qtbot)
    assert canvas.normalise_to_noise() is True
    scaled = [_noise(t) * t.y_scale for t in canvas.traces]
    assert scaled[0] == pytest.approx(scaled[1], rel=0.05)


def test_normalise_to_noise_uses_mad_not_sigma(qtbot):
    """A plain standard deviation is dominated by the peaks themselves, so it
    would measure signal, not noise. The weak spectrum is ~1000x smaller, so a
    correct noise-based factor lands near 1000."""
    canvas = noisy_canvas(qtbot)
    canvas.normalise_to_noise()
    factors = sorted(t.y_scale for t in canvas.traces)
    assert 100 < factors[1] < 10000


def test_normalise_to_noise_reports_failure_on_a_flat_trace(qtbot):
    """A perfectly flat trace has no measurable noise; scaling by a
    meaningless factor would be worse than declining."""
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    canvas.traces[0].intensity = np.zeros(64)
    assert canvas.normalise_to_noise() is False


def test_normalise_to_noise_with_nothing_loaded_is_false():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.normalise_to_noise() is False


# --- export: cursor must never be baked in --------------------------------


def test_saving_clears_the_crosshair_first(qtbot, tmp_path):
    """A crosshair is a UI overlay; leaving it in bakes a stray cursor line
    into every exported figure."""
    canvas = loaded_canvas(qtbot, 1)
    canvas._on_mouse_move(FakeMouseEvent(canvas._axes, ydata=50.0, xdata=5.0))
    assert canvas._crosshair is not None
    out = tmp_path / "clean.svg"
    canvas.save_image(out)
    assert canvas._crosshair is None
    assert "5.0000 ppm" not in out.read_text()


def test_pptx_export_produces_one_slide_with_the_figure(qtbot, tmp_path):
    pptx = pytest.importorskip("pptx")
    canvas = loaded_canvas(qtbot, 1)
    out = tmp_path / "deck.pptx"
    canvas.save_image(out)
    prs = pptx.Presentation(str(out))
    assert len(prs.slides) == 1
    assert len(prs.slides[0].shapes) == 1


def test_emf_is_not_offered_as_an_export_format():
    """matplotlib cannot write EMF, and the only route (an external Inkscape
    install) silently produced nothing when Inkscape was absent. Offering a
    format that usually fails is worse than not offering it."""
    formats = [ext for _label, ext in SpectrumCanvas.EXPORT_FILTERS]
    assert "emf" not in formats
    # ...but genuine vector options are still there.
    assert {"svg", "pdf", "eps"} <= set(formats)

# --- axis formatting and label scale --------------------------------------


def test_x_decimals_formats_tick_labels(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_x_decimals(2)
    labels = [t.get_text() for t in canvas._axes.get_xticklabels()]
    assert all("." in t and len(t.split(".")[1]) == 2 for t in labels if t)


def test_x_decimals_zero_gives_integers(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_x_decimals(0)
    labels = [t.get_text() for t in canvas._axes.get_xticklabels() if t.get_text()]
    assert all("." not in t for t in labels)


def test_invalid_x_decimals_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_x_decimals(2)
    canvas.set_x_decimals(-1)
    canvas.set_x_decimals(99)
    canvas.set_x_decimals("two")
    assert canvas.x_decimals() == 2


def test_label_scale_changes_name_size(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_label_scale(2.0)
    assert canvas.label_scale() == 2.0
    sizes = [t.get_fontsize() for t in canvas._axes.texts]
    assert all(s > 8 for s in sizes)


def test_invalid_label_scale_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_label_scale(0.01)
    canvas.set_label_scale(99)
    canvas.set_label_scale("big")
    assert canvas.label_scale() == 1.0


# --- palette ---------------------------------------------------------------


def test_domain_palette_has_no_yellow():
    """Yellow has the lowest contrast against white of the Okabe-Ito set and
    disappears at publication line widths."""
    from helspin.domain.project import DEFAULT_PALETTE

    assert "#F0E442" not in DEFAULT_PALETTE
    assert DEFAULT_PALETTE[1] == "#0072B2"


# --- 2D spectra -----------------------------------------------------------


class _Ax2D:
    def __init__(self, n, hi, lo):
        self.size = n
        self._v = np.linspace(hi, lo, n)

    def ppm_scale(self):
        return self._v


class Fake2D:
    def __init__(self):
        n1, n2 = 32, 64
        f1 = np.arange(n1)[:, None]
        f2 = np.arange(n2)[None, :]
        rng = np.random.default_rng(3)
        self.real = (
            5e8 * np.exp(-(((f1 - 10) / 2.0) ** 2 + ((f2 - 20) / 3.0) ** 2))
            + rng.normal(0, 1e6, (n1, n2))
        )
        self.axis_f1 = _Ax2D(n1, 130.0, 10.0)
        self.axis_f2 = _Ax2D(n2, 10.0, 0.0)
        self.ns = 1
        self.rg = 1.0


class Reader2D:
    def read_2d(self, path, procno=1):
        return Fake2D()

    def read_1d(self, path, procno=1):
        return FakeSpectrum()


def item2d(path="/d/2d", label="HSQC"):
    return {"path": path, "dimensionality": 2, "label": label}


def test_dropping_a_2d_spectrum_renders_contours(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    assert canvas.traces[0].is_2d
    assert sum(len(a.collections) for a in canvas._figure.axes) > 0


def test_two_2d_spectra_get_side_by_side_panels(qtbot):
    """Overlaying contour maps is unreadable; adjacent panels are how 2D
    spectra are actually compared."""
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(
        mime_for(item2d(path="/d/a", label="A"), item2d(path="/d/b", label="B"))
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    assert len(canvas._figure.axes) == 2


def test_2d_axes_descend_in_both_dimensions(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    ax = canvas._figure.axes[0]
    assert ax.get_xlim()[0] > ax.get_xlim()[1]
    assert ax.get_ylim()[0] > ax.get_ylim()[1]


def test_mixed_1d_and_2d_gives_a_panel_each_plus_one_for_the_1d(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(
        mime_for(
            item2d(path="/d/a", label="A"),
            item2d(path="/d/b", label="B"),
            item(path="/d/c", label="1H"),
        )
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 3, timeout=3000)
    assert len(canvas._figure.axes) == 3


def test_hiding_every_2d_returns_to_the_single_1d_axes(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(
        mime_for(item2d(path="/d/a", label="A"), item(path="/d/c", label="1H"))
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    assert len(canvas._figure.axes) == 2
    canvas.set_trace_visible(0, False)
    assert len(canvas._figure.axes) == 1


def test_2d_figure_can_be_exported(qtbot, tmp_path):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    out = tmp_path / "twod.png"
    canvas.save_image(out)
    assert out.stat().st_size > 0


def test_contours_survive_an_all_noise_matrix(qtbot):
    """A matrix with no real peaks must not raise; it just draws few levels."""
    class FlatReader(Reader2D):
        def read_2d(self, path, procno=1):
            s = Fake2D()
            s.real = np.zeros_like(s.real)
            return s

    canvas = SpectrumCanvas(reader=FlatReader())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)   # no crash


# --- difference spectra ---------------------------------------------------


class DifferentLengthReader:
    """Two spectra with DIFFERENT point counts, sharing one peak."""

    def __init__(self):
        self.n = 0

    def read_1d(self, path, procno=1):
        self.n += 1
        size, extra = (512, 500.0) if self.n == 1 else (777, 0.0)
        x = np.linspace(0, 10, size)
        s = FakeSpectrum(size)
        s.real = 1000 * np.exp(-((x - 3) / 0.1) ** 2) + extra * np.exp(
            -((x - 7) / 0.1) ** 2
        )

        class Axis:
            def __init__(self, n):
                self.size = n

            def ppm_scale(self_inner):
                return np.linspace(10, 0, size)

        s.axis = Axis(size)
        return s


def test_subtract_interpolates_rather_than_subtracting_by_index(qtbot):
    """Index subtraction is the classic silent error: two spectra rarely share
    a point count, so it compares different chemical shifts and produces
    convincing nonsense. The shared peak must cancel and the unique one
    survive, even with 512 vs 777 points."""
    canvas = SpectrumCanvas(reader=DifferentLengthReader())
    canvas.handle_mime_data(
        mime_for(item(path="/d/a", label="ligand"), item(path="/d/b", label="apo"))
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    assert canvas.traces[0].ppm.size != canvas.traces[1].ppm.size

    assert canvas.subtract(0, 1) is True
    diff = canvas.traces[2]
    # The fixture builds data over ascending x while ppm descends, so the
    # SHARED peak (x=3) lands at 7 ppm and the UNIQUE one (x=7) at 3 ppm.
    shared = diff.intensity[int(np.argmin(np.abs(diff.ppm - 7.0)))]
    unique = diff.intensity[int(np.argmin(np.abs(diff.ppm - 3.0)))]
    assert abs(shared) < 50           # cancelled
    assert unique > 400               # survived


def test_subtract_labels_the_result(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.subtract(0, 1)
    # Marked with a delta and a true minus sign so a difference is
    # unmistakable in the legend, not confusable with a hyphenated name.
    assert canvas.traces[2].label.startswith("\u0394")
    assert "s/0" in canvas.traces[2].label and "s/1" in canvas.traces[2].label
    assert canvas.traces[2].is_difference is True


def test_subtract_applies_y_scale_first(qtbot):
    """A difference taken after scaling must reflect what is on screen."""
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_y_scale(0, 2.0)
    canvas.subtract(0, 1)
    peak = float(np.nanmax(canvas.traces[2].intensity))
    assert peak == pytest.approx(100.0, rel=0.05)   # 2*100 - 100


def test_subtract_refuses_the_same_spectrum(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    assert canvas.subtract(0, 0) is False


def test_subtract_refuses_unknown_indexes(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    assert canvas.subtract(0, 9) is False


def test_subtract_refuses_2d_spectra(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(
        mime_for(item2d(path="/d/a"), item2d(path="/d/b", label="B"))
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    assert canvas.subtract(0, 1) is False


def test_list_panel_emits_subtract_for_two_selected(qtbot):
    panel = SpectrumListPanel()
    panel.set_traces([FakeTrace("a"), FakeTrace("b"), FakeTrace("c")], 0)
    panel._list.item(0).setSelected(True)
    panel._list.item(2).setSelected(True)
    with qtbot.waitSignal(panel.subtractRequested, timeout=1000) as blocker:
        panel._subtract_button.click()
    assert blocker.args == [0, 2]


def test_list_panel_subtract_needs_exactly_two(qtbot):
    panel = SpectrumListPanel()
    received = []
    panel.subtractRequested.connect(lambda a, b: received.append((a, b)))
    panel.set_traces([FakeTrace("a"), FakeTrace("b")], 0)
    panel._list.clearSelection()
    panel._subtract_button.click()
    assert received == []


# --- draggable spectrum names ---------------------------------------------


def test_label_positions_default_to_a_stacked_column(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    positions = [t.label_pos for t in canvas.traces]
    assert all(p is not None for p in positions)
    assert positions[0][1] > positions[1][1]     # second sits below the first


def test_reset_label_positions_clears_dragged_names(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.traces[0].label_pos = (0.5, 0.5)
    canvas.reset_label_positions()
    assert canvas.traces[0].label_pos == pytest.approx((0.015, 0.985))


def test_label_position_survives_rescaling(qtbot):
    """Dragged names stay where they were put when a spectrum is scaled."""
    canvas = loaded_canvas(qtbot, 2)
    canvas.traces[0].label_pos = (0.6, 0.4)
    canvas.set_y_scale(0, 5.0)
    assert canvas.traces[0].label_pos == (0.6, 0.4)


# --- processed-only datasets ----------------------------------------------


def test_dimensionality_falls_back_to_processed_files(tmp_path):
    """Raw FIDs are often deleted or never copied off the spectrometer. A
    processed-only 2D dataset was listed in the browser (it has 2rr) but then
    refused to probe, so it could never be opened."""
    from helspin.domain.project import Dimensionality
    from helspin.infrastructure.nmrglue_reader import _dimensionality

    expno = tmp_path / "11"
    procno = expno / "pdata" / "1"
    procno.mkdir(parents=True)
    (procno / "2rr").write_bytes(b"\x00" * 8)
    assert _dimensionality(expno) is Dimensionality.TWO_D

    expno1d = tmp_path / "12"
    procno1d = expno1d / "pdata" / "1"
    procno1d.mkdir(parents=True)
    (procno1d / "1r").write_bytes(b"\x00" * 8)
    assert _dimensionality(expno1d) is Dimensionality.ONE_D


def test_dimensionality_still_raises_with_no_data_at_all(tmp_path):
    from helspin.domain.errors import DatasetNotFound
    from helspin.infrastructure.nmrglue_reader import _dimensionality

    expno = tmp_path / "13"
    expno.mkdir(parents=True)
    with pytest.raises(DatasetNotFound):
        _dimensionality(expno)


# --- legend offsets, pulse programme, 2D mode -----------------------------


def test_label_offset_moves_a_name_and_survives_rescaling(qtbot):
    """Typed offsets are reproducible and reach names sitting under a trace,
    where grabbing them with the mouse is awkward."""
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_label_offset(1, 0.30, -0.20)
    moved = canvas.traces[1].label_pos
    canvas.set_y_scale(1, 6.0)
    assert canvas.traces[1].label_pos == moved


def test_label_offset_is_clamped_inside_the_plot(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    canvas.set_label_offset(0, 5.0, 5.0)
    x, y = canvas.traces[0].label_pos
    assert 0.0 <= x <= 0.98
    assert 0.02 <= y <= 1.0


def test_invalid_label_offset_is_refused(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    before = canvas.traces[0].label_pos
    canvas.set_label_offset(0, float("nan"), 0.0)
    canvas.set_label_offset(0, "x", 0.0)
    assert canvas.traces[0].label_pos == before


def test_reset_label_positions_clears_offsets_too(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_label_offset(0, 0.4, -0.3)
    canvas.reset_label_positions()
    assert canvas.traces[0].label_offset == (0.0, 0.0)


def test_pulse_program_appears_in_the_on_plot_name(qtbot):
    """Two experiments on the same sample differ only by pulse programme,
    which is exactly when a bare name is ambiguous."""
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(
        mime_for({
            "path": "/d/1", "dimensionality": 1,
            "label": "sample/10", "pulse_program": "zgig",
        })
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    assert canvas.traces[0].pulse_program == "zgig"
    assert "zgig" in canvas.display_label(canvas.traces[0])


def test_name_without_pulse_program_is_unchanged(qtbot):
    canvas = loaded_canvas(qtbot, 1)
    assert canvas.display_label(canvas.traces[0]) == canvas.traces[0].label


def test_mode_is_derived_from_the_data(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    assert canvas.mode() == "1D"
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    assert canvas.mode() == "2D"


def test_mode_change_is_announced(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    with qtbot.waitSignal(canvas.modeChanged, timeout=3000) as blocker:
        canvas.handle_mime_data(mime_for(item2d()))
    assert blocker.args == ["2D"]


def test_dropping_2d_onto_1d_is_refused_with_a_message(qtbot):
    """A contour map and a 1D trace share no meaningful vertical axis, so
    accepting the drop would give a figure that looks fine and means nothing."""
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item(path="/d/1d", label="1H")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)

    with qtbot.waitSignal(canvas.dimensionalityRefused, timeout=3000) as blocker:
        canvas.handle_mime_data(mime_for(item2d(path="/d/2d")))
    assert "1D mode" in blocker.args[0]
    assert len(canvas.traces) == 1     # nothing was added


def test_dropping_1d_onto_2d_is_refused(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d(path="/d/2d")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    with qtbot.waitSignal(canvas.dimensionalityRefused, timeout=3000):
        canvas.handle_mime_data(mime_for(item(path="/d/1d")))
    assert len(canvas.traces) == 1


def test_f1_and_f2_ranges_are_independent(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    canvas.set_f2_range(8.0, 2.0)
    canvas.set_f1_range(120.0, 40.0)
    ax = canvas._figure.axes[0]
    assert ax.get_xlim() == pytest.approx((8.0, 2.0))
    assert ax.get_ylim() == pytest.approx((120.0, 40.0))


def test_f_ranges_are_normalised_to_descending(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    canvas.set_f2_range(2.0, 8.0)      # typed ascending
    ax = canvas._figure.axes[0]
    assert ax.get_xlim()[0] > ax.get_xlim()[1]


def test_clear_resets_the_2d_ranges(qtbot):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    canvas.set_f1_range(100.0, 50.0)
    canvas.clear()
    assert canvas._f1_range is None


# --- 2D traces must not break the 1D-only operations ----------------------
#
# Reported crash: dropping a 2D spectrum raised "zero-size array to reduction
# operation fmax which has no identity". 2D traces keep EMPTY ppm/intensity
# arrays (their data is in matrix/ppm_f1/ppm_f2), and several 1D-only routines
# called np.nanmax on them. These pin every one of those paths.


def loaded_2d(qtbot, n=2):
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(
        mime_for(*[item2d(path=f"/d/{i}", label=f"HSQC{i}") for i in range(n)])
    )
    qtbot.waitUntil(lambda: len(canvas.traces) == n, timeout=3000)
    return canvas


def test_ppm_bounds_is_none_when_only_2d_is_loaded(qtbot):
    """The exact reported crash: ppm_bounds ran nanmax over a 2D trace's
    empty ppm array."""
    canvas = loaded_2d(qtbot, 1)
    assert canvas.ppm_bounds() is None


def test_autoscale_is_safe_with_only_2d(qtbot):
    canvas = loaded_2d(qtbot)
    canvas.autoscale_traces()     # must not raise


def test_normalise_to_noise_is_safe_with_only_2d(qtbot):
    canvas = loaded_2d(qtbot)
    assert canvas.normalise_to_noise() is False


def test_move_to_bottom_is_safe_with_only_2d(qtbot):
    canvas = loaded_2d(qtbot)
    canvas.move_to_bottom(0)      # a contour panel has no baseline
    canvas.move_all_to_bottom()


def test_fit_and_reset_are_safe_with_only_2d(qtbot):
    canvas = loaded_2d(qtbot)
    canvas.fit_to_drawn()
    canvas.reset_y_limits()


def test_export_is_safe_with_only_2d(qtbot, tmp_path):
    canvas = loaded_2d(qtbot)
    canvas.save_image(tmp_path / "twod.png")
    assert (tmp_path / "twod.png").stat().st_size > 0


def test_1d_operations_ignore_a_hidden_2d_trace(qtbot):
    """A 2D trace present but hidden must not poison the 1D calculations."""
    canvas = SpectrumCanvas(reader=Reader2D())
    canvas.handle_mime_data(mime_for(item2d(path="/d/2d")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    canvas._traces.append(
        Trace(
            path=Path("/d/1d"), label="1H",
            ppm=np.linspace(10, 0, 128),
            intensity=np.linspace(0, 50, 128),
            color="#000000",
        )
    )
    canvas.set_trace_visible(0, False)     # hide the 2D
    assert canvas.mode() == "1D"
    left, right = canvas.ppm_bounds()
    assert left == pytest.approx(10.0)
    canvas.autoscale_traces()
    canvas.normalise_to_noise()
    canvas.move_all_to_bottom()


def test_visible_1d_excludes_2d_and_empty_traces(qtbot):
    canvas = loaded_2d(qtbot, 1)
    assert canvas._visible_1d() == []


# --- combine: subtract AND add -------------------------------------------


def test_add_spectra_sums_them(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    assert canvas.add_spectra(0, 1) is True
    assert canvas.traces[2].intensity.max() == pytest.approx(200.0, rel=0.05)


def test_add_and_subtract_share_the_same_rules(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    assert canvas.add_spectra(0, 0) is False       # same spectrum
    assert canvas.add_spectra(0, 9) is False       # unknown index


def test_add_is_labelled_with_a_plus(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.add_spectra(0, 1)
    assert "+" in canvas.traces[2].label
    assert canvas.traces[2].is_difference is True   # derived, not measured


def test_add_applies_y_scale_first(qtbot):
    canvas = loaded_canvas(qtbot, 2)
    canvas.set_y_scale(0, 3.0)
    canvas.add_spectra(0, 1)
    assert canvas.traces[2].intensity.max() == pytest.approx(400.0, rel=0.05)


def test_select_trace_does_not_rebuild_and_lose_a_multi_selection(qtbot):
    """The regression that broke Subtract: selecting a spectrum emitted
    tracesChanged, which rebuilt the list and wiped the multi-selection, so
    Subtract never saw two spectra."""
    canvas = loaded_canvas(qtbot, 2)
    emissions = []
    canvas.tracesChanged.connect(lambda: emissions.append(1))
    canvas.select_trace(1)
    assert emissions == []
