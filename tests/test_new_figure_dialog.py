"""NewFigureDialog: request assembly, live validation, Create button gating."""

import pytest
from PySide6.QtWidgets import QDialogButtonBox

from helspin.domain.project import Arrangement
from helspin.ui.new_figure_dialog import NewFigureDialog

pytestmark = pytest.mark.usefixtures("qapp")


def _ok_button(dlg):
    return dlg._buttons.button(QDialogButtonBox.Ok)


def test_default_request_is_zero_and_invalid():
    dlg = NewFigureDialog()
    req = dlg.request()
    assert req.count_1d == 0 and req.count_2d == 0
    assert req.validate()   # non-empty: "add at least one spectrum"
    assert not _ok_button(dlg).isEnabled()


def test_setting_1d_count_enables_create():
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(3)
    assert dlg.request().validate() == []
    assert _ok_button(dlg).isEnabled()


def test_default_1d_arrangement_is_overlay():
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(3)
    assert dlg.request().arrangement_1d is Arrangement.OVERLAY


def test_selecting_tiled_changes_the_request():
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(4)
    tiled_button = dlg._arrangement_1d_group.button(2)
    assert tiled_button.property("arrangement") is Arrangement.TILED
    tiled_button.setChecked(True)
    assert dlg.request().arrangement_1d is Arrangement.TILED


def test_subtracted_with_one_spectrum_is_invalid_and_shown(qtbot):
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(1)
    subtracted_button = dlg._arrangement_1d_group.button(3)
    assert subtracted_button.property("arrangement") is Arrangement.SUBTRACTED
    subtracted_button.click()
    assert "two" in dlg._problem_label.text()
    assert not _ok_button(dlg).isEnabled()


def test_subtracted_with_two_spectra_is_valid():
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(2)
    dlg._arrangement_1d_group.button(3).setChecked(True)
    dlg._revalidate()
    assert dlg.request().validate() == []
    assert _ok_button(dlg).isEnabled()


def test_2d_arrangement_group_has_no_stacked_or_subtracted_option():
    """2D cannot be stacked, and 2D differences are not supported yet --
    both must be structurally absent from the 2D radio group, not just
    validated against after the fact."""
    dlg = NewFigureDialog()
    offered = {
        dlg._arrangement_2d_group.button(i).property("arrangement")
        for i in range(dlg._arrangement_2d_group.buttons().__len__())
    }
    assert Arrangement.STACKED not in offered
    assert Arrangement.SUBTRACTED not in offered
    assert offered == {Arrangement.OVERLAY, Arrangement.TILED}


def test_1d_and_2d_together_is_valid():
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(2)
    dlg._count_2d.setValue(2)
    assert dlg.request().validate() == []


def test_problem_label_clears_once_fixed():
    dlg = NewFigureDialog()
    assert dlg._problem_label.text() != ""
    dlg._count_1d.setValue(1)
    assert dlg._problem_label.text() == ""


def test_request_reflects_both_counts_independently():
    dlg = NewFigureDialog()
    dlg._count_1d.setValue(3)
    dlg._count_2d.setValue(2)
    req = dlg.request()
    assert req.count_1d == 3
    assert req.count_2d == 2
