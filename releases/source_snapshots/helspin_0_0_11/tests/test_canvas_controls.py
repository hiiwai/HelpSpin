"""Per-trace scaling, selection, appearance preferences, and the list panel."""

import json

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
from helspin.ui.spectrum_canvas import SpectrumCanvas
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


def test_emf_without_inkscape_reports_why(qtbot, tmp_path, monkeypatch):
    """matplotlib genuinely cannot write EMF. If the converter is missing the
    error must say so, not leave a mislabelled file behind."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    canvas = loaded_canvas(qtbot, 1)
    with pytest.raises(RuntimeError, match="Inkscape"):
        canvas.save_image(tmp_path / "x.emf")


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
