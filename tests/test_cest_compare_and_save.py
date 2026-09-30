"""0.9.3: save the CEST plot as an image, and compare experiments on one plot.

The overlay list and its buttons existed before 0.9.3 but were never placed
in the window, so the comparison feature could not be reached. The first
tests here pin that they are now actually visible in the layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from helspin.domain.cest import CestError
from helspin.ui.cest_panel import CestPanel

from .cest_fixtures import DEFAULT_OFFSETS, make_dataset

N = len(DEFAULT_OFFSETS)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    widget = CestPanel()
    yield widget
    widget.deleteLater()


def _cest(tmp_path, name, n_rows=N):
    return make_dataset(tmp_path / "set" / name, n_rows=n_rows, si_f1=n_rows,
                        offsets=DEFAULT_OFFSETS)


def _built(panel, tmp_path, name):
    root = _cest(tmp_path, name)
    panel.load(root)
    panel._build_z()
    return root


def _inside_window(panel, widget) -> bool:
    parent = widget
    while parent is not None:
        if parent is panel:
            return True
        parent = parent.parentWidget()
    return False


# ------------------------------------------------------------------ layout

def test_overlay_and_peak_widgets_are_in_the_window(panel):
    for widget in (panel._overlay_list, panel._peak_list, panel._peak_snr):
        assert _inside_window(panel, widget)
    labels = {b.text() for b in panel.findChildren(QPushButton)}
    assert {"Add experiment\u2026", "Keep on plot", "Remove", "Clear all",
            "Save image\u2026"} <= labels


# -------------------------------------------------------------- save image

@pytest.mark.parametrize("suffix", [".png", ".pdf", ".svg", ".tif", ".tiff"])
def test_z_plot_saves_in_every_offered_format(panel, tmp_path, suffix):
    _built(panel, tmp_path, "img")
    written = panel.save_image(tmp_path / f"out{suffix}", dpi=72)
    assert written.suffix == suffix
    assert written.stat().st_size > 0


def test_png_signature_and_missing_suffix_gets_png(panel, tmp_path):
    _built(panel, tmp_path, "img2")
    written = panel.save_image(tmp_path / "noext", dpi=72)
    assert written.name == "noext.png"
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_unsupported_suffix_is_kept_and_png_appended(panel, tmp_path):
    _built(panel, tmp_path, "img3")
    written = panel.save_image(tmp_path / "fig.v2", dpi=72)
    assert written.name == "fig.v2.png"


def test_saving_an_empty_tab_is_refused(panel, tmp_path):
    with pytest.raises(CestError, match="nothing is plotted"):
        panel.save_image(tmp_path / "x.png")
    panel._tabs.setCurrentIndex(0)
    with pytest.raises(CestError):
        panel.save_image(tmp_path / "y.png")


def test_unwritable_path_raises_cest_error(panel, tmp_path):
    _built(panel, tmp_path, "img4")
    with pytest.raises(CestError, match="could not write"):
        panel.save_image(tmp_path / "missing_dir" / "a.png", dpi=72)


def test_nutation_tab_saves_its_own_plot(panel, tmp_path):
    root = make_dataset(tmp_path / "nut", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._fit_nutation()
    assert panel._tabs.currentIndex() == 0
    written = panel.save_image(tmp_path / "nut.png", dpi=72)
    assert written.stat().st_size > 0
    assert "nutation" in panel.default_image_name()


def test_saving_does_not_change_the_view(panel, tmp_path):
    _built(panel, tmp_path, "img5")
    axes = panel._z_plot.axes
    before = (axes.get_xlim(), axes.get_ylim(), panel._z_plot.figure.dpi)
    panel.save_image(tmp_path / "v.png", dpi=150)
    assert (axes.get_xlim(), axes.get_ylim(), panel._z_plot.figure.dpi) == before


def test_default_image_name_is_filesystem_safe(panel, tmp_path):
    assert panel.default_image_name() == "cest_nutation.png"   # empty panel: tab 0
    root = make_dataset(tmp_path / 'we:ird "name"' / "40", n_rows=N, si_f1=N,
                        offsets=DEFAULT_OFFSETS)
    panel.load(root)
    name = panel.default_image_name()
    assert name.endswith("_40_zspectrum.png")
    assert not set('<>:"/\\|?* ') & set(name)


def test_loading_new_data_clears_the_old_plots(panel, tmp_path):
    """Save must never write the previous experiment under the new name."""
    _built(panel, tmp_path, "old")
    panel.load(_cest(tmp_path, "new"))
    assert panel._z_display is None
    with pytest.raises(CestError):
        panel.save_image(tmp_path / "stale.png")


# ----------------------------------------------------------------- compare

def test_add_experiment_overlays_without_replacing_current(panel, tmp_path):
    first = _built(panel, tmp_path, "a")
    current = panel._z
    message = panel.add_overlay_from(_cest(tmp_path, "b"))
    assert message.startswith("added")
    assert panel._z is current and Path(panel._data_path) == first
    assert len(panel._overlays) == 1 and panel._overlay_list.count() == 1
    # current curve (points) + the overlay are both drawn
    labels = [t.get_text() for t in panel._z_plot.axes.get_legend().get_texts()]
    assert any("b" in text for text in labels)


def test_overlay_uses_current_settings(panel, tmp_path):
    _built(panel, tmp_path, "c")
    panel.add_overlay_from(_cest(tmp_path, "c2"))
    kept = panel._overlays[0][1]
    # Same synthetic data measured the same way -> same curve.
    assert kept.intensity == pytest.approx(panel._z.intensity, abs=1e-9)


def test_add_experiment_with_nothing_loaded_draws_overlay_alone(panel, tmp_path):
    message = panel.add_overlay_from(_cest(tmp_path, "solo"))
    assert message.startswith("added")
    assert panel._z_display is None
    assert panel._z_plot.axes.lines
    written = panel.save_image(tmp_path / "solo.png", dpi=72)
    assert written.exists()
    # Full range works with overlays only.
    panel._z_xmin.setValue(-1.0)
    panel._reset_z_limits()
    assert panel._z_xmin.value() < -1.0


def test_non_cest_and_bad_paths_are_reported_not_raised(panel, tmp_path):
    nut = make_dataset(tmp_path / "nut", n_rows=16, si_f1=16,
                       pulprog="19f_calib_nut.iw", offsets=None)
    assert "not a CEST" in panel.add_overlay_from(nut)
    assert "could not load" in panel.add_overlay_from(tmp_path / "nowhere")
    no_list = make_dataset(tmp_path / "nolist", n_rows=N, si_f1=N, offsets=None)
    assert "no FQ1LIST" in panel.add_overlay_from(no_list)
    assert panel._overlays == []


def test_more_rows_than_offsets_is_refused_for_overlays(panel, tmp_path):
    root = make_dataset(tmp_path / "many", n_rows=N + 4, si_f1=N + 4,
                        offsets=DEFAULT_OFFSETS)
    assert "not overlaid" in panel.add_overlay_from(root)
    assert panel._overlays == []


def test_partial_overlay_is_flagged(panel, tmp_path):
    root = make_dataset(tmp_path / "part", n_rows=N, si_f1=N - 4,
                        offsets=DEFAULT_OFFSETS)
    _set_source(panel, "2rr")
    message = panel.add_overlay_from(root)
    assert "PARTIAL" in message
    assert panel._overlays[0][1].offsets_hz.size == N - 4


def _set_source(panel, value):
    panel._source.setCurrentIndex(panel._source.findData(value))


def test_duplicate_labels_stay_unique(panel, tmp_path):
    root = _cest(tmp_path, "dup")
    for _ in range(3):
        panel.add_overlay_from(root)
    names = [name for name, _ in panel._overlays]
    assert len(set(names)) == 3


def test_remove_selected_overlays_and_empty_selection(panel, tmp_path):
    _built(panel, tmp_path, "r")
    for name in ("r1", "r2", "r3"):
        panel.add_overlay_from(_cest(tmp_path, name))
    panel._drop_overlay()                     # nothing selected: no change
    assert len(panel._overlays) == 3
    panel._overlay_list.item(0).setSelected(True)
    panel._overlay_list.item(2).setSelected(True)
    panel._drop_overlay()
    assert [n.split("/")[1].split()[0] for n, _ in panel._overlays] == ["r2"]
    assert panel._overlay_list.count() == 1


def test_removing_last_overlay_without_data_empties_the_plot(panel, tmp_path):
    panel.add_overlay_from(_cest(tmp_path, "only"))
    panel._clear_overlays()
    assert not panel._z_plot.axes.lines
    with pytest.raises(CestError):
        panel.save_image(tmp_path / "e.png")


def test_keep_does_not_refit_or_rewrite_result(panel, tmp_path):
    _built(panel, tmp_path, "k")
    text = panel._z_result.toPlainText()
    fitted = panel._z_fitted
    panel._keep_overlay()
    assert panel._z_result.toPlainText() == text
    assert panel._z_fitted is fitted


def test_compare_loads_first_and_overlays_rest(panel, tmp_path):
    roots = [_cest(tmp_path, f"s{i}") for i in range(3)]
    panel.compare(roots + [roots[1]])          # duplicate ignored
    assert Path(panel._data_path) == roots[0]
    assert panel._z is not None
    assert len(panel._overlays) == 2
    assert "added" in panel._notes.toPlainText()
    panel.compare(roots)                        # re-run does not stack
    assert len(panel._overlays) == 2
    panel.compare([])                           # harmless
    assert len(panel._overlays) == 2


def test_units_toggle_reseeds_ranges_and_redraws(panel, tmp_path):
    _built(panel, tmp_path, "u")
    panel.add_overlay_from(_cest(tmp_path, "u2"))
    ppm_span = panel._z_xmax.value() - panel._z_xmin.value()
    panel._z_ppm.setChecked(False)
    hz_span = panel._z_xmax.value() - panel._z_xmin.value()
    assert hz_span > 10 * ppm_span
    assert "(Hz)" in panel._z_plot.axes.get_xlabel()
    low, high = panel._z_plot.axes.get_xlim()
    assert low > high                           # still inverted
    panel._z_ppm.setChecked(True)
    assert "(ppm)" in panel._z_plot.axes.get_xlabel()


def test_error_toggle_and_fit_toggle_with_overlays(panel, tmp_path):
    _built(panel, tmp_path, "t")
    panel.add_overlay_from(_cest(tmp_path, "t2"))
    panel._z_errors.setChecked(False)
    panel._show_fits.setChecked(False)
    panel._show_fits.setChecked(True)
    assert len(panel._overlays) == 1
    assert panel._z_plot.axes.lines


# ------------------------------------------------- 0.9.4: right-click, drop

import json as _json  # noqa: E402

from PySide6.QtCore import QMimeData, Qt  # noqa: E402

from helspin.ui import cest_panel as _cp  # noqa: E402
from helspin.ui.dataset_model import MIME_DATASET  # noqa: E402


def _actions(menu):
    return {a.text(): a for a in menu.actions() if a.text()}


def test_plots_request_a_right_click_menu(panel):
    for plot in (panel._n_plot, panel._z_plot):
        assert plot.contextMenuPolicy() == Qt.CustomContextMenu


def test_z_menu_entries_and_enabled_states(panel, tmp_path):
    empty = _actions(panel.build_plot_menu(panel._z_plot))
    assert not empty["Save image\u2026"].isEnabled()
    assert empty["Add CEST experiment to plot\u2026"].isEnabled()
    assert not empty["Keep current Z-spectrum on plot"].isEnabled()
    assert not empty["Remove all overlays"].isEnabled()
    _built(panel, tmp_path, "menu")
    panel._keep_overlay()
    ready = _actions(panel.build_plot_menu(panel._z_plot))
    assert all(a.isEnabled() for text, a in ready.items()
               if text not in MARKER_ENTRIES)   # no click point yet
    ready["Remove all overlays"].trigger()
    assert panel._overlays == []


MARKER_ENTRIES = ["Add vertical marker here", "Add horizontal marker here",
                  "Remove nearest marker", "Clear all markers"]


def test_nutation_menu_offers_save_and_markers(panel, tmp_path):
    menu = _actions(panel.build_plot_menu(panel._n_plot))
    assert list(menu) == ["Save image\u2026", *MARKER_ENTRIES]
    assert not any(a.isEnabled() for a in menu.values())


def test_menu_save_writes_the_plot_it_was_opened_on(panel, tmp_path, monkeypatch):
    _built(panel, tmp_path, "ms")
    panel._tabs.setCurrentIndex(0)          # other tab showing
    target = tmp_path / "from_menu"
    monkeypatch.setattr(
        _cp.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "PDF vector (*.pdf)")),
    )
    _actions(panel.build_plot_menu(panel._z_plot))["Save image\u2026"].trigger()
    assert (tmp_path / "from_menu.pdf").read_bytes()[:4] == b"%PDF"


def test_header_button_click_does_not_pass_checked_as_plot(panel, tmp_path,
                                                           monkeypatch):
    _built(panel, tmp_path, "btn")
    target = tmp_path / "btn.png"
    monkeypatch.setattr(
        _cp.QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "PNG image (*.png)")),
    )
    panel._save_image_button.click()
    assert target.exists()


@pytest.mark.parametrize("suffix,magic", [
    (".jpg", b"\xff\xd8"), (".eps", b"%!PS"), (".ps", b"%!PS"),
    (".pptx", b"PK"),
])
def test_main_canvas_formats_are_also_offered(panel, tmp_path, suffix, magic):
    _built(panel, tmp_path, "fmt")
    written = panel.save_image(tmp_path / f"f{suffix}", dpi=72)
    assert written.read_bytes()[:len(magic)] == magic


def _mime(paths):
    mime = QMimeData()
    payload = [{"path": str(p), "label": Path(p).name} for p in paths]
    mime.setData(MIME_DATASET, _json.dumps(payload).encode("utf-8"))
    return mime


def test_drop_on_empty_panel_loads_first_and_overlays_rest(panel, tmp_path):
    roots = [_cest(tmp_path, f"d{i}") for i in range(3)]
    assert panel.handle_mime_data(_mime(roots))
    assert Path(panel._data_path) == roots[0]
    assert panel._z is not None
    assert len(panel._overlays) == 2
    assert panel._tabs.currentIndex() == 1


def test_drop_with_data_loaded_overlays_everything(panel, tmp_path):
    _built(panel, tmp_path, "base")
    roots = [_cest(tmp_path, f"e{i}") for i in range(2)]
    assert panel.handle_mime_data(_mime(roots))
    assert len(panel._overlays) == 2


def test_drop_on_loaded_but_unbuilt_cest_builds_it_first(panel, tmp_path):
    panel.load(_cest(tmp_path, "unbuilt"))
    assert panel._z is None
    panel.handle_mime_data(_mime([_cest(tmp_path, "o1")]))
    assert panel._z is not None and len(panel._overlays) == 1


def test_bad_drops_are_rejected(panel):
    assert not panel.handle_mime_data(QMimeData())
    bad = QMimeData()
    bad.setData(MIME_DATASET, b"not json")
    assert not panel.handle_mime_data(bad)
    empty = QMimeData()
    empty.setData(MIME_DATASET, b"[]")
    assert not panel.handle_mime_data(empty)
    nokey = QMimeData()
    nokey.setData(MIME_DATASET, b'[{"label": "x"}]')
    assert not panel.handle_mime_data(nokey)


def test_drop_of_non_cest_is_reported(panel, tmp_path):
    _built(panel, tmp_path, "b2")
    nut = make_dataset(tmp_path / "nutd", n_rows=16, si_f1=16,
                       pulprog="19f_calib_nut.iw", offsets=None)
    assert panel.handle_mime_data(_mime([nut]))
    assert panel._overlays == []
    assert "not a CEST" in panel._notes.toPlainText()


def test_panel_accepts_drops(panel):
    assert panel.acceptDrops()


def test_right_click_signal_reaches_the_menu_for_that_plot(panel, monkeypatch):
    from PySide6.QtCore import QPoint

    seen = []
    monkeypatch.setattr(panel, "_on_plot_menu", lambda plot, pos: seen.append(plot))
    panel._z_plot.customContextMenuRequested.emit(QPoint(5, 5))
    panel._n_plot.customContextMenuRequested.emit(QPoint(5, 5))
    assert seen == [panel._z_plot, panel._n_plot]


# ----------------------------------------------------------------- markers

def test_menus_match_the_main_canvas_wording(panel, app):
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    main = SpectrumCanvas()
    try:
        main_entries = list(_actions(main.build_context_menu()))
    finally:
        main.deleteLater()
    for plot in (panel._n_plot, panel._z_plot):
        entries = list(_actions(panel.build_plot_menu(plot)))
        assert entries[0] == main_entries[0] == "Save image\u2026"
        for text in MARKER_ENTRIES:
            assert text in entries and text in main_entries


def _centre(plot):
    from PySide6.QtCore import QPoint

    plot.resize(600, 400)
    plot.draw()
    bbox = plot.axes.bbox
    ratio = float(plot.devicePixelRatioF() or 1.0)
    x = (bbox.x0 + bbox.x1) / 2 / ratio
    y = (plot.figure.bbox.height - (bbox.y0 + bbox.y1) / 2) / ratio
    return QPoint(int(x), int(y))


def test_right_click_position_maps_into_the_axes(panel, tmp_path):
    _built(panel, tmp_path, "pos")
    plot = panel._z_plot
    point = plot.data_point_at(_centre(plot))
    assert point is not None
    low, high = sorted(plot.axes.get_xlim())
    assert low <= point[0] <= high
    from PySide6.QtCore import QPoint

    assert plot.data_point_at(QPoint(1, 1)) is None      # margin


def test_menu_adds_and_removes_markers_at_the_click(panel, tmp_path, monkeypatch):
    _built(panel, tmp_path, "mk")
    plot = panel._z_plot
    monkeypatch.setattr(_cp.CestPanel, "_exec_menu", staticmethod(lambda m, w: None))
    panel._on_plot_menu(plot, _centre(plot))
    assert plot.context_point is not None
    menu = _actions(panel.build_plot_menu(plot))
    assert menu["Add vertical marker here"].isEnabled()
    assert not menu["Remove nearest marker"].isEnabled()
    menu["Add vertical marker here"].trigger()
    menu["Add horizontal marker here"].trigger()
    assert [o for o, _ in plot.markers] == ["vertical", "horizontal"]
    assert plot.markers[0][1] == pytest.approx(plot.context_point[0])
    texts = [t.get_text() for t in plot.axes.texts]
    assert any(t.endswith("ppm") for t in texts)
    _actions(panel.build_plot_menu(plot))["Remove nearest marker"].trigger()
    assert len(plot.markers) == 1
    _actions(panel.build_plot_menu(plot))["Clear all markers"].trigger()
    assert plot.markers == []
    assert not plot.axes.texts


def test_markers_survive_rebuild_overlay_and_saving(panel, tmp_path):
    _built(panel, tmp_path, "keep")
    plot = panel._z_plot
    plot.add_marker("vertical", -0.5)
    plot.add_marker("horizontal", 0.9)
    panel._build_z()
    panel.add_overlay_from(_cest(tmp_path, "keep2"))
    panel._show_fits.setChecked(False)
    assert len(plot.markers) == 2
    lines = [ln for ln in plot.axes.lines if ln.get_linestyle() == "--"]
    assert len(lines) >= 2
    assert panel.save_image(tmp_path / "m.png", dpi=72).exists()


def test_marker_without_click_point_is_refused(panel, tmp_path):
    _built(panel, tmp_path, "none")
    plot = panel._z_plot
    plot.context_point = None
    assert plot.add_marker("vertical") is False
    assert plot.remove_nearest_marker() is False
    assert plot.add_marker("vertical", float("nan")) is False
    with pytest.raises(ValueError):
        plot.add_marker("diagonal", 1.0)


def test_right_click_outside_axes_greys_marker_entries(panel, tmp_path, monkeypatch):
    from PySide6.QtCore import QPoint

    _built(panel, tmp_path, "grey")
    plot = panel._z_plot
    plot.add_marker("vertical", 0.0)
    monkeypatch.setattr(_cp.CestPanel, "_exec_menu", staticmethod(lambda m, w: None))
    panel._on_plot_menu(plot, QPoint(1, 1))
    menu = _actions(panel.build_plot_menu(plot))
    assert not menu["Add vertical marker here"].isEnabled()
    assert not menu["Remove nearest marker"].isEnabled()
    assert menu["Clear all markers"].isEnabled()


def test_vertical_markers_follow_the_unit_switch(panel, tmp_path):
    _built(panel, tmp_path, "unit")
    plot = panel._z_plot
    sfo1 = panel._z.sfo1_mhz
    plot.add_marker("vertical", -0.5)
    plot.add_marker("horizontal", 0.9)
    panel._z_ppm.setChecked(False)
    assert plot.markers[0][1] == pytest.approx(-0.5 * sfo1)
    assert plot.markers[1][1] == pytest.approx(0.9)
    assert any(t.get_text().endswith("Hz") for t in plot.axes.texts)
    panel._z_ppm.setChecked(True)
    assert plot.markers[0][1] == pytest.approx(-0.5)


def test_unit_switch_with_nothing_plotted_drops_vertical_markers(panel):
    plot = panel._z_plot
    plot.markers = [("vertical", 1.0), ("horizontal", 0.5)]
    panel._z_ppm.setChecked(False)
    assert plot.markers == [("horizontal", 0.5)]


def test_nutation_markers(panel, tmp_path):
    root = make_dataset(tmp_path / "nm", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._fit_nutation()
    plot = panel._n_plot
    assert plot.add_marker("vertical", 1.25)
    assert any(t.get_text() == "1.25 ms" for t in plot.axes.texts)
    panel._show_fits.setChecked(False)          # redraw keeps it
    assert any(t.get_text() == "1.25 ms" for t in plot.axes.texts)


def test_marker_style_applies_to_both_plots(panel, tmp_path):
    _built(panel, tmp_path, "style")
    panel._z_plot.add_marker("horizontal", 0.8)
    panel.set_marker_style({"color": "#ff0000", "width": 2.0, "dash": ":",
                            "alpha": None})
    for plot in (panel._n_plot, panel._z_plot):
        assert plot.marker_style["color"] == "#ff0000"
        assert plot.marker_style["alpha"] == 0.8          # None ignored
    line = [ln for ln in panel._z_plot.axes.lines if ln.get_linewidth() == 2.0]
    assert line and line[0].get_color() == "#ff0000"


def test_main_window_passes_its_marker_style(qtbot, monkeypatch):
    from helspin.__main__ import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window._canvas.set_marker_style(color="#123456")
    monkeypatch.setattr(window, "_selected_expnos", lambda: [])
    window._show_cest()
    panel = window._cest_panel
    try:
        assert panel._z_plot.marker_style["color"] == "#123456"
    finally:
        panel.hide()
