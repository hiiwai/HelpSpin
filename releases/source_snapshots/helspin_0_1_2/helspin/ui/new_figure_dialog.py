"""The New Figure dialog.

Two numbers and a radio button produce a complete figure (handoff 4.3.0).
This is the UI half of domain.layout.NewFigureRequest/build_project, which
already exist and are fully tested -- this dialog's only job is collecting
the request and showing validation problems live, before Create is even
clickable on a bad combination.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
)

from ..domain.layout import NewFigureRequest
from ..domain.project import Arrangement


def _arrangement_group(*allowed: Arrangement) -> tuple[QGroupBox, QButtonGroup]:
    box = QGroupBox()
    group = QButtonGroup(box)
    layout = QVBoxLayout(box)
    labels = {
        Arrangement.OVERLAY: "Overlay",
        Arrangement.STACKED: "Stacked",
        Arrangement.TILED: "Tiled",
        Arrangement.SUBTRACTED: "Subtracted",
    }
    for i, arrangement in enumerate(allowed):
        button = QRadioButton(labels[arrangement])
        button.setProperty("arrangement", arrangement)
        group.addButton(button, i)
        layout.addWidget(button)
    group.button(0).setChecked(True)
    return box, group


class NewFigureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Figure")

        self._count_1d = QSpinBox()
        self._count_1d.setRange(0, 32)
        self._count_1d.setValue(0)
        self._count_1d.valueChanged.connect(self._revalidate)

        self._arrangement_1d_box, self._arrangement_1d_group = _arrangement_group(
            Arrangement.OVERLAY, Arrangement.STACKED, Arrangement.TILED,
            Arrangement.SUBTRACTED,
        )
        self._arrangement_1d_group.idClicked.connect(self._revalidate)

        self._count_2d = QSpinBox()
        self._count_2d.setRange(0, 32)
        self._count_2d.setValue(0)
        self._count_2d.valueChanged.connect(self._revalidate)

        self._arrangement_2d_box, self._arrangement_2d_group = _arrangement_group(
            Arrangement.OVERLAY, Arrangement.TILED,
        )
        self._arrangement_2d_group.idClicked.connect(self._revalidate)

        one_d_row = QHBoxLayout()
        one_d_row.addWidget(self._count_1d)
        one_d_row.addWidget(self._arrangement_1d_box)

        two_d_row = QHBoxLayout()
        two_d_row.addWidget(self._count_2d)
        two_d_row.addWidget(self._arrangement_2d_box)

        form = QFormLayout()
        form.addRow("1D spectra", one_d_row)
        form.addRow("2D spectra", two_d_row)

        self._problem_label = QLabel()
        self._problem_label.setWordWrap(True)
        self._problem_label.setStyleSheet("color: #b03030;")

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok
        )
        self._buttons.button(QDialogButtonBox.Ok).setText("Create")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self._problem_label)
        layout.addWidget(self._buttons)

        self._revalidate()

    # -- validation ----------------------------------------------------------

    def _current_arrangement(self, group: QButtonGroup) -> Arrangement:
        return group.checkedButton().property("arrangement")

    def request(self) -> NewFigureRequest:
        return NewFigureRequest(
            count_1d=self._count_1d.value(),
            arrangement_1d=self._current_arrangement(self._arrangement_1d_group),
            count_2d=self._count_2d.value(),
            arrangement_2d=self._current_arrangement(self._arrangement_2d_group),
        )

    def _revalidate(self, *_args) -> None:
        problems = self.request().validate()
        self._problem_label.setText("  ".join(problems))
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(not problems)
