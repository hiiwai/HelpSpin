"""0.9.6: drag to pan the Z-spectrum; use only the first N rows."""

from __future__ import annotations

import pytest
from matplotlib.backend_bases import MouseEvent
from PySide6.QtWidgets import QApplication

from helspin.ui.cest_panel import CestPanel

from .cest_fixtures import DEFAULT_OFFSETS, make_dataset

N = len(DEFAULT_OFFSETS)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    widget = CestPanel()
    widget._z_plot.resize(600, 400)
    yield widget
    widget.deleteLater()


def _cest(tmp_path, name="e"):
    return make_dataset(tmp_path / "set" / name, n_rows=N, si_f1=N,
                        offsets=DEFAULT_OFFSETS)


def _nut(tmp_path, rows=16):
    return make_dataset(tmp_path / "nut", n_rows=rows, si_f1=rows,
                        pulprog="19f_calib_nut.iw", offsets=None)


def _event(plot, kind, fx, fy, button=1, dblclick=False):
    x, y = plot.axes.transAxes.transform((fx, fy))
    return MouseEvent(kind, plot, x, y, button=button, dblclick=dblclick)


def _drag(plot, start, end, button=1):
    plot.callbacks.process("button_press_event",
                           _event(plot, "button_press_event", *start, button=button))
    plot.callbacks.process("motion_notify_event",
                           _event(plot, "motion_notify_event", *end, button=button))
    plot.callbacks.process("button_release_event",
                           _event(plot, "button_release_event", *end, button=button))


def _built(panel, tmp_path):
    panel.load(_cest(tmp_path))
    panel._build_z()
    panel._z_plot.draw()
    return panel._z_plot


# --------------------------------------------------------------------- pan

def test_drag_pans_both_axes_and_keeps_the_point_under_the_pointer(panel, tmp_path):
    plot = _built(panel, tmp_path)
    before_x, before_y = plot.axes.get_xlim(), plot.axes.get_ylim()
    grabbed = plot.axes.transAxes + plot.axes.transData.inverted()
    data_at_start = grabbed.transform((0.5, 0.5))
    _drag(plot, (0.5, 0.5), (0.7, 0.4))
    after_x, after_y = plot.axes.get_xlim(), plot.axes.get_ylim()
    assert after_x != before_x and after_y != before_y
    # span unchanged: a pan, not a zoom
    assert after_x[1] - after_x[0] == pytest.approx(before_x[1] - before_x[0])
    assert after_y[1] - after_y[0] == pytest.approx(before_y[1] - before_y[0])
    # the data point grabbed is now under the release position
    # (matplotlib rounds event positions to whole pixels, so allow one)
    now = (plot.axes.transAxes + plot.axes.transData.inverted()).transform((0.7, 0.4))
    inverse = plot.axes.transData.inverted()
    per_px = abs(inverse.transform((1, 1)) - inverse.transform((0, 0)))
    assert abs(now[0] - data_at_start[0]) <= 2 * per_px[0]
    assert abs(now[1] - data_at_start[1]) <= 2 * per_px[1]
    assert plot.axes.xaxis_inverted()


def test_pan_updates_the_range_boxes(panel, tmp_path):
    plot = _built(panel, tmp_path)
    _drag(plot, (0.5, 0.5), (0.3, 0.5))
    low, high = sorted(plot.axes.get_xlim())
    assert panel._z_xmin.value() == pytest.approx(low, abs=1e-3)
    assert panel._z_xmax.value() == pytest.approx(high, abs=1e-3)


def test_release_ends_the_pan(panel, tmp_path):
    plot = _built(panel, tmp_path)
    _drag(plot, (0.5, 0.5), (0.6, 0.5))
    assert plot._pan is None
    after = plot.axes.get_xlim()
    plot.callbacks.process("motion_notify_event",
                           _event(plot, "motion_notify_event", 0.2, 0.2))
    assert plot.axes.get_xlim() == after


@pytest.mark.parametrize("button, dblclick, start", [
    (3, False, (0.5, 0.5)),       # right button: the menu, not a pan
    (1, True, (0.5, 0.5)),        # double-click: reset, not a pan
    (1, False, (-0.2, 0.5)),      # outside the axes
])
def test_presses_that_do_not_pan(panel, tmp_path, button, dblclick, start):
    plot = _built(panel, tmp_path)
    plot.callbacks.process("button_press_event", _event(
        plot, "button_press_event", *start, button=button, dblclick=dblclick))
    assert plot._pan is None


def test_empty_plot_does_not_pan(panel):
    plot = panel._z_plot
    plot.callbacks.process("button_press_event",
                           _event(plot, "button_press_event", 0.5, 0.5))
    assert plot._pan is None


def test_nutation_plot_has_no_pan(panel, tmp_path):
    panel.load(_nut(tmp_path))
    panel._fit_nutation()
    plot = panel._n_plot
    before = plot.axes.get_xlim()
    plot.draw()
    _drag(plot, (0.5, 0.5), (0.8, 0.5))
    assert plot.axes.get_xlim() == before


def test_double_click_after_pan_restores_full_range(panel, tmp_path):
    plot = _built(panel, tmp_path)
    full = plot.axes.get_xlim()
    _drag(plot, (0.5, 0.5), (0.8, 0.5))
    plot.callbacks.process("button_press_event", _event(
        plot, "button_press_event", 0.5, 0.5, dblclick=True))
    assert plot.axes.get_xlim() == pytest.approx(full)


# --------------------------------------------------------------- row limit

def test_default_uses_all_rows(panel, tmp_path):
    panel.load(_cest(tmp_path))
    assert panel._row_limit.text() == "all"
    assert panel._data.n_rows == N and panel._offsets.size == N


def test_limit_cuts_rows_and_offsets_and_rebuilds(panel, tmp_path):
    panel.load(_cest(tmp_path))
    panel._build_z()
    panel._row_limit.setValue(8)
    assert panel._data.n_rows == 8
    assert panel._offsets.size == 8
    assert panel._data_full.n_rows == N            # original kept
    assert panel._z is not None and panel._z.offsets_hz.size == 8
    assert f"8 of {N} rows" in panel._path_label.text()
    assert "PARTIAL" not in panel._z_result.toPlainText()
    panel._row_limit.setValue(0)
    assert panel._data.n_rows == N and panel._z.offsets_hz.size == N
    assert f"{N} rows from" in panel._path_label.text()


def test_limit_above_row_count_means_all(panel, tmp_path):
    panel._row_limit.setValue(N + 50)
    panel.load(_cest(tmp_path))
    assert panel._data.n_rows == N
    assert panel._data is panel._data_full


def test_limit_set_before_loading_applies_at_load(panel, tmp_path):
    panel._row_limit.setValue(10)
    panel.load(_cest(tmp_path))
    assert panel._data.n_rows == 10 and panel._offsets.size == 10
    assert f"10 of {N} rows" in panel._path_label.text()


def test_limit_without_built_z_does_not_build_it(panel, tmp_path):
    panel.load(_cest(tmp_path))
    panel._row_limit.setValue(8)
    assert panel._z is None and panel._z_display is None


def test_limit_with_nothing_loaded_is_harmless(panel):
    panel._row_limit.setValue(4)
    assert panel._data is None


def test_limit_refits_nutation(panel, tmp_path):
    panel.load(_nut(tmp_path, rows=24))
    panel._fit_nutation()
    assert panel._fit.n_points == 24
    panel._row_limit.setValue(16)
    assert panel._fit is not None and panel._fit.n_points == 16
    t, _ = panel._n_series
    assert t.size == 16


def test_too_few_rows_reports_instead_of_crashing(panel, tmp_path):
    panel.load(_nut(tmp_path, rows=16))
    panel._fit_nutation()
    panel._row_limit.setValue(2)
    assert panel._fit is None
    assert "failed" in panel._n_result.toPlainText().lower()


def test_overlays_use_the_cut_and_say_so(panel, tmp_path):
    panel._row_limit.setValue(8)
    message = panel.add_overlay_from(_cest(tmp_path, "o"))
    assert message.startswith("added")
    name, curve = panel._overlays[0]
    assert "[first 8]" in name and curve.offsets_hz.size == 8


def test_changing_the_limit_leaves_existing_overlays_alone(panel, tmp_path):
    panel.load(_cest(tmp_path))
    panel._build_z()
    panel.add_overlay_from(_cest(tmp_path, "o2"))
    kept = panel._overlays[0][1]
    panel._row_limit.setValue(8)
    assert panel._overlays[0][1] is kept
    assert kept.offsets_hz.size == N
