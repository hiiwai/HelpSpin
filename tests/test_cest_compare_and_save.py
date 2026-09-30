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
