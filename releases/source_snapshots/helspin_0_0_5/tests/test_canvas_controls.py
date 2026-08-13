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
    PreferencesDialog,
    _clamp_width,
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


def test_new_traces_use_the_current_default_width(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.set_default_line_width(2.0)
    canvas.handle_mime_data(mime_for(item()))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)
    assert canvas.traces[0].line_width == 2.0


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


def test_preferences_returns_its_values():
    dlg = PreferencesDialog(line_width=1.5, colors=["#010101", "#020202"])
    assert dlg.line_width() == 1.5
    assert dlg.colors() == ["#010101", "#020202"]


def test_preferences_clamps_an_absurd_width():
    dlg = PreferencesDialog(line_width=9999.0)
    assert dlg.line_width() <= MAX_LINE_WIDTH


def test_preferences_clamps_a_zero_width():
    dlg = PreferencesDialog(line_width=0.0)
    assert dlg.line_width() >= MIN_LINE_WIDTH


def test_clamp_width_handles_garbage():
    assert _clamp_width("nonsense") == 0.8
    assert _clamp_width(float("nan")) == 0.8
    assert _clamp_width(-5) == MIN_LINE_WIDTH


def test_preferences_falls_back_to_default_colours_when_given_none():
    dlg = PreferencesDialog(colors=[])
    assert dlg.colors() == DEFAULT_COLORS


def test_preferences_reset_restores_defaults():
    dlg = PreferencesDialog(colors=["#111111"] * len(DEFAULT_COLORS))
    dlg._reset_colors()
    assert dlg.colors() == DEFAULT_COLORS


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
