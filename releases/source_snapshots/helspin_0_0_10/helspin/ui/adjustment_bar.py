"""The bottom adjustment bar.

Permanently visible ppm range control (handoff 4.3.0): "Full" fits to the
union of loaded spectra, two typed fields set an exact range, and recent
ranges are remembered. Disabled until a figure with at least one linked group
exists -- there is nothing to adjust yet, and a live control with nothing
behind it invites confusion rather than preventing it.

Deliberately independent of any actual Project/canvas wiring in this slice:
the ppm math already exists in domain/spectrum.py (union_ppm_range) and
domain/project.py (LinkGroup.set_x's left>right validation); this widget is
the view half, built now so the shell layout is real rather than a mockup.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

MAX_RECENT_RANGES = 8


class AdjustmentBar(QWidget):
    """ppm  [ Full ]  [ left ] to [ right ]     recent ranges ▾

    Emits rangeChanged(left, right) when the user confirms a typed range or
    presses Full. Never emits an invalid (left <= right) range -- the same
    rule as LinkGroup.set_x, enforced here too so a bad value never even
    reaches the domain layer.
    """

    rangeChanged = Signal(float, float)
    fullRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recent: list[tuple[float, float]] = []

        self._full_button = QPushButton("Full")
        self._full_button.clicked.connect(self._on_full_clicked)

        self._left_spin = QDoubleSpinBox()
        self._left_spin.setRange(-1000.0, 1000.0)
        self._left_spin.setDecimals(3)
        self._left_spin.setSuffix(" ppm")
        self._left_spin.setToolTip(
            "One edge of the range. Either order works -- the plot always\n"
            "shows high ppm on the left, per NMR convention."
        )
        # Applied when editing finishes (Enter / tab out) as well as via the
        # Apply button. Both values are always read TOGETHER and normalised,
        # so a half-typed pair can never be applied in a wrong order.
        self._left_spin.editingFinished.connect(self._on_range_edited)

        self._right_spin = QDoubleSpinBox()
        self._right_spin.setRange(-1000.0, 1000.0)
        self._right_spin.setDecimals(3)
        self._right_spin.setSuffix(" ppm")
        self._right_spin.editingFinished.connect(self._on_range_edited)

        self._recent_combo = QComboBox()
        self._recent_combo.setPlaceholderText("Recent ranges")
        self._recent_combo.activated.connect(self._on_recent_selected)

        self._apply_button = QPushButton("Apply")
        self._apply_button.setToolTip(
            "Apply the typed range. Either order works: 0 to 10 and\n"
            "10 to 0 both show 0-10 ppm."
        )
        self._apply_button.clicked.connect(self._on_range_edited)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(QLabel("ppm range"))
        layout.addWidget(self._full_button)
        layout.addWidget(self._left_spin)
        layout.addWidget(QLabel("to"))
        layout.addWidget(self._right_spin)
        layout.addWidget(self._apply_button)
        layout.addStretch(1)
        layout.addWidget(self._recent_combo)

        self.set_enabled_for_figure(False)

    # -- state -------------------------------------------------------------

    def set_enabled_for_figure(self, enabled: bool) -> None:
        """Disabled entirely until a figure with a link group exists."""
        for widget in (
            self._full_button,
            self._left_spin,
            self._right_spin,
            self._apply_button,
            self._recent_combo,
        ):
            widget.setEnabled(enabled)

    def set_range(self, left: float, right: float) -> None:
        """Update the displayed range without emitting rangeChanged --
        for reflecting a change that came from elsewhere (e.g. a canvas
        zoom), not for the user's own edits."""
        self._left_spin.setValue(left)
        self._right_spin.setValue(right)

    def current_range(self) -> tuple[float, float]:
        return (self._left_spin.value(), self._right_spin.value())

    # -- interaction ---------------------------------------------------------

    def _on_full_clicked(self) -> None:
        self.fullRequested.emit()

    def _on_range_edited(self) -> None:
        """Accept the two values in EITHER order and normalise.

        ppm axes are displayed descending (high ppm on the left), but a user
        naturally types a range as "0 to 12" as often as "12 to 0". Rejecting
        the ascending form -- as an earlier version did -- just looked broken.
        The pair is swapped into descending order instead, and the boxes are
        updated so what is shown matches what is applied.
        """
        a, b = self._left_spin.value(), self._right_spin.value()
        if a == b:
            return   # zero-width range: nothing sensible to show
        # The PLOT always follows the NMR convention (high ppm on the left).
        # The BOXES are left exactly as typed: silently rewriting what someone
        # entered was the confusing part -- type "0 to 12" and the boxes
        # flipped to "12 to 0" under you. Interpretation happens here instead.
        high, low = (a, b) if a > b else (b, a)
        self._remember(high, low)
        self.rangeChanged.emit(high, low)

    def _remember(self, left: float, right: float) -> None:
        entry = (round(left, 3), round(right, 3))
        if entry in self._recent:
            self._recent.remove(entry)
        self._recent.insert(0, entry)
        self._recent = self._recent[:MAX_RECENT_RANGES]
        self._recent_combo.clear()
        self._recent_combo.addItems(
            [f"{l:.2f} \u2013 {r:.2f}" for l, r in self._recent]
        )

    def _on_recent_selected(self, index: int) -> None:
        if not (0 <= index < len(self._recent)):
            return
        left, right = self._recent[index]
        self.set_range(left, right)
        self.rangeChanged.emit(left, right)
