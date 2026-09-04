"""CEST panel tests.

The panel builds every widget in __init__ and shows nothing itself, so these
drive it directly with no modal exec anywhere. HANDOFF.md records why:
QDialog.exec on a Shiboken-wrapped class cannot be monkeypatched reliably,
so construction and showing must stay separate.
"""

from __future__ import annotations

import pytest
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
    yield widget
    widget.deleteLater()


def _set_source(panel, value):
    index = panel._source.findData(value)
    assert index >= 0
    panel._source.setCurrentIndex(index)


def test_panel_builds_without_data(panel):
    assert panel._tabs.count() == 2
    assert "No experiment" in panel._path_label.text()


def test_fitting_without_data_reports_rather_than_crashes(panel):
    panel._fit_nutation()
    assert "Load an experiment first" in panel._n_result.toPlainText()
    panel._build_z()
    assert "Load an experiment first" in panel._z_result.toPlainText()


def test_loading_a_cest_set_selects_the_z_tab(panel, tmp_path):
    root = make_dataset(tmp_path / "z", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    assert panel._tabs.currentIndex() == 1
    assert "cest" in panel._path_label.text()


def test_loading_a_nutation_set_selects_the_calibration_tab(panel, tmp_path):
    root = make_dataset(tmp_path / "n", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    assert panel._tabs.currentIndex() == 0
    # The target field is seeded from CNST8 so the common case needs no typing.
    assert panel._n_target.value() == pytest.approx(100.0)


def test_truncated_pdata_warning_reaches_the_user(panel, tmp_path):
    root = make_dataset(tmp_path / "t", n_rows=N, si_f1=16, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    notes = panel._notes.toPlainText()
    assert "16 of" in notes and "raw ser" in notes


def test_offsets_are_summarised_after_loading(panel, tmp_path):
    root = make_dataset(tmp_path / "o", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    assert f"{N} saturation offsets" in panel._notes.toPlainText()


def test_z_spectrum_builds_and_exports(panel, tmp_path):
    root = make_dataset(tmp_path / "zz", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._pick_z_peak()
    panel._build_z()
    text = panel._z_result.toPlainText()
    assert "I0" in text and "Offsets" in text
    csv = panel.z_csv()
    assert csv.splitlines()[0].startswith("offset_hz,offset_ppm")
    assert len(csv.splitlines()) == N + 1


def test_z_export_is_empty_before_building(panel):
    assert panel.z_csv() == ""
    assert panel.nutation_record() == ""


def test_missing_offsets_explains_itself(panel, tmp_path):
    root = make_dataset(tmp_path / "nolist", n_rows=N, si_f1=N, offsets=None)
    panel.load(root)
    panel._build_z()
    assert "No saturation offsets" in panel._z_result.toPlainText()


def test_truncated_series_still_plots_the_offsets_it_has(panel, tmp_path):
    """F1QF steps the list in order, so row i IS offset i.

    A truncated series is therefore still correctly labelled for the rows it
    has; only the tail is absent. Plotting what exists beats refusing,
    provided the loss is stated.
    """
    root = make_dataset(tmp_path / "mm", n_rows=N, si_f1=16, offsets=DEFAULT_OFFSETS)
    _set_source(panel, "2rr")
    panel.load(root)
    panel._build_z()
    text = panel._z_result.toPlainText()
    assert text.startswith("PARTIAL:")
    assert "16 of 24" in text
    assert "Raw ser" in text
    # 16 rows plotted against the FIRST 16 offsets, in order.
    rows = panel.z_csv().splitlines()[1:]
    assert len(rows) == 16
    plotted = sorted(float(line.split(",")[0]) for line in rows)
    assert plotted == sorted(float(v) for v in DEFAULT_OFFSETS[:16])


def test_more_rows_than_offsets_is_still_refused(panel, tmp_path):
    """The recoverable direction is one way only.

    Extra rows have no offset to belong to, so any pairing would be invented.
    """
    root = make_dataset(tmp_path / "extra", n_rows=N, si_f1=N,
                        offsets=DEFAULT_OFFSETS[:5])
    panel.load(root)
    panel._build_z()
    text = panel._z_result.toPlainText()
    assert "no way to know" in text
    assert not text.startswith("PARTIAL")


def test_a_single_row_cannot_make_a_z_spectrum(panel, tmp_path):
    root = make_dataset(tmp_path / "one", n_rows=N, si_f1=1, offsets=DEFAULT_OFFSETS)
    _set_source(panel, "2rr")
    panel.load(root)
    panel._build_z()
    assert "at least two" in panel._z_result.toPlainText()


def test_nutation_fit_reports_a_field_and_a_power(panel, tmp_path):
    root = make_dataset(tmp_path / "nf", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._pick_nutation_peak()
    panel._n_half.setValue(0.5)
    panel._fit_nutation()
    text = panel._n_result.toPlainText()
    assert "B1 (fitted)" in text
    assert "PLW" in text
    record = panel.nutation_record()
    assert "Fitted B1" in record and "Correction factor" in record


def test_target_field_changes_the_corrected_power(panel, tmp_path):
    """The correction transfers between nominal fields; halving the target
    must quarter the power, since PLW goes as B1 squared."""
    root = make_dataset(tmp_path / "tg", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._n_half.setValue(0.5)

    def corrected_for(target):
        panel._n_target.setValue(target)
        panel._fit_nutation()
        for line in panel._n_result.toPlainText().splitlines():
            if "<-- target" in line:
                return float(line.split()[5])
        raise AssertionError("no target row in the settings table")

    assert corrected_for(50.0) == pytest.approx(corrected_for(100.0) / 4.0, rel=1e-3)


def test_measurement_mode_switch_is_honoured(panel, tmp_path):
    root = make_dataset(tmp_path / "mode", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    results = {}
    for mode in ("height", "integral"):
        panel._z_mode.setCurrentIndex(panel._z_mode.findData(mode))
        panel._build_z()
        results[mode] = panel.z_csv()
    assert results["height"] != results["integral"]


def test_loading_a_bad_directory_does_not_raise(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "helspin.ui.cest_panel.QMessageBox.warning",
        lambda *args, **kwargs: None,
    )
    panel.load(tmp_path / "does-not-exist")
    assert "Could not load" in panel._notes.toPlainText()


def test_result_gives_the_cnst_to_type_on_the_spectrometer(panel, tmp_path):
    """The panel must answer "what do I set?", not just "how wrong is it?".

    Both pulse programs derive pulse and power from a CNST, and 19f_cest
    recomputes plw25 unconditionally, so lowering CNST is the only
    adjustment available there.
    """
    root = make_dataset(tmp_path / "cn", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._n_half.setValue(0.5)
    panel._n_target.setValue(60.0)
    panel._fit_nutation()
    text = panel._n_result.toPlainText()
    assert "TO SET ON THE SPECTROMETER" in text
    assert "Calibration re-check: set CNST8" in text
    assert "CEST: set CNST25" in text

    ratio = None
    for line in text.splitlines():
        if line.startswith("Nominal (CNST)"):
            ratio = float(line.split("ratio")[1])
    assert ratio is not None

    # The quoted CNST must be the nominal divided by the measured excess.
    for line in text.splitlines():
        if "CEST: set CNST25" in line:
            quoted = float(line.split("=")[1].split()[0])
    assert quoted == pytest.approx(60.0 / ratio, rel=1e-3)


def test_quoted_power_matches_what_the_pulse_program_would_derive(panel, tmp_path):
    """The table's PLW must equal plw1*(p1/pulse)^2, not an independent guess."""
    from helspin.ui.cest_panel import plw1_for

    root = make_dataset(tmp_path / "pw", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._n_half.setValue(0.5)
    panel._n_target.setValue(60.0)
    panel._fit_nutation()
    for line in panel._n_result.toPlainText().splitlines():
        if line.strip().startswith("60.0 Hz"):
            parts = line.split()
            cnst, pulse, plw = float(parts[2]), float(parts[3]), float(parts[5])
            assert pulse == pytest.approx(1e6 / (4 * cnst), rel=1e-3)
            # p1 = 12 us, plw1 = 9.5121 W in the fixture.
            assert plw == pytest.approx(plw1_for(12.0, 9.5121, pulse), rel=1e-3)
            break
    else:
        raise AssertionError("no 60 Hz row in the table")


def test_record_sheet_carries_the_cnst_settings(panel, tmp_path):
    root = make_dataset(tmp_path / "rec", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    panel.load(root)
    panel._n_half.setValue(0.5)
    panel._fit_nutation()
    record = panel.nutation_record()
    assert "Probe delivers" in record
    assert "SET CNST" in record


def test_zoom_boxes_are_seeded_and_applied(panel, tmp_path):
    """A range control whose default is meaningless has to be discovered."""
    root = make_dataset(tmp_path / "zoom", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._build_z()
    assert panel._z_xmax.value() > panel._z_xmin.value()
    assert panel._z_ymax.value() > panel._z_ymin.value()

    panel._z_ymin.setValue(0.90)
    panel._z_ymax.setValue(1.05)
    assert panel._z_plot.axes.get_ylim() == pytest.approx((0.90, 1.05))

    panel._reset_z_limits()
    assert panel._z_plot.axes.get_ylim()[0] < 0.90


def test_x_axis_stays_inverted_when_zoomed(panel, tmp_path):
    """NMR convention: frequency increases leftwards.

    set_xlim silently undoes the inversion if given the pair the other way
    round, which would mirror the spectrum on any zoom.
    """
    root = make_dataset(tmp_path / "inv", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._build_z()
    panel._z_xmin.setValue(-500.0)
    panel._z_xmax.setValue(500.0)
    low, high = panel._z_plot.axes.get_xlim()
    assert low > high


def test_inverted_range_is_ignored(panel, tmp_path):
    """Setting max below min must leave the axis alone, not blank the plot.

    Boxes are edited one at a time, so an inverted pair is a normal
    intermediate state while the user types -- it has to be survivable.
    """
    root = make_dataset(tmp_path / "bad", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._build_z()
    before = panel._z_plot.axes.get_ylim()
    panel._z_ymax.setValue(panel._z_ymin.value() - 1.0)     # inverted
    assert panel._z_plot.axes.get_ylim() == pytest.approx(before)


def test_error_bars_can_be_switched_off(panel, tmp_path):
    root = make_dataset(tmp_path / "err", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._z_errors.setChecked(True)
    panel._build_z()
    with_errors = panel._z.error
    panel._z_errors.setChecked(False)
    panel._build_z()
    assert with_errors is not None
    assert panel._z.error is None


def test_candidate_dips_are_listed_with_significance(panel, tmp_path):
    root = make_dataset(tmp_path / "cand", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._build_z()
    text = panel._z_result.toPlainText()
    assert "Noise (1 sigma)" in text


def test_residual_view_announces_itself(panel, tmp_path):
    """Depths in the residual view are not comparable to the raw ones."""
    root = make_dataset(tmp_path / "res", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._z_residual.setChecked(True)
    panel._build_z()
    text = panel._z_result.toPlainText()
    if "Dip centre" in text:
        assert "SHOWING RESIDUAL" in text
