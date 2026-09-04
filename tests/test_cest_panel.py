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


def test_row_offset_mismatch_is_refused_with_advice(panel, tmp_path):
    root = make_dataset(tmp_path / "mm", n_rows=N, si_f1=16, offsets=DEFAULT_OFFSETS)
    _set_source(panel, "2rr")
    panel.load(root)
    panel._build_z()
    text = panel._z_result.toPlainText()
    assert "mismatch" in text.lower()
    assert "Raw ser" in text


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
            if line.startswith("For a ") and "PLW8 becomes" in line:
                return float(line.split()[-2])
            if line.startswith("PLW8 corrected"):
                fallback = float(line.split()[2])
        return fallback

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
