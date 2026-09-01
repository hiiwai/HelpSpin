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
    f1RangeChanged = Signal(float, float)   # 2D indirect dimension
    fullRequested = Signal()
    zoomModeChanged = Signal(bool)
    yZoomModeChanged = Signal(bool)

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
            "Left edge of the plot: the HIGHER ppm value, per NMR convention.\n"
            "Either order can be typed; the boxes normalise on Apply."
        )
        # Applied when editing finishes (Enter / tab out) as well as via the
        # Apply button. Both values are always read TOGETHER and normalised,
        # so a half-typed pair can never be applied in a wrong order.
        self._left_spin.editingFinished.connect(self._on_range_edited)

        self._right_spin = QDoubleSpinBox()
        self._right_spin.setRange(-1000.0, 1000.0)
        self._right_spin.setDecimals(3)
        self._right_spin.setSuffix(" ppm")
        self._right_spin.setToolTip(
            "Right edge of the plot: the LOWER ppm value."
        )
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
        layout.addWidget(self._full_button)
        self._ppm_label = QLabel("ppm  left")
        layout.addWidget(self._ppm_label)
        layout.addWidget(self._left_spin)
        layout.addWidget(QLabel("right"))
        layout.addWidget(self._right_spin)
        layout.addWidget(self._apply_button)
        layout.addStretch(1)
        layout.addWidget(self._recent_combo)

        # --- 2D controls: a second, independent range for the indirect
        # dimension. Hidden in 1D mode, because F1 is meaningless there.
        self._f1_left = QDoubleSpinBox()
        self._f1_right = QDoubleSpinBox()
        for spin in (self._f1_left, self._f1_right):
            spin.setRange(-1000.0, 1000.0)
            spin.setDecimals(3)
            spin.setSuffix(" ppm")
        # Zoom toggle, next to the range boxes it drives. Off by default so
        # the wheel keeps its existing meaning (scaling the selected trace);
        # on, the wheel zooms about the cursor and dragging draws a box to
        # zoom into. A toggle rather than a modifier key because the two uses
        # of the wheel are both wanted often, and a held key is awkward while
        # scrolling.
        self._zoom_toggle = QPushButton("X zoom")
        self._zoom_toggle.setCheckable(True)
        self._zoom_toggle.setToolTip(
            "Wheel zooms the ppm axis about the cursor;\n"
            "drag a box to zoom into it.\n"
            "Off: the wheel scales the selected spectrum instead."
        )
        self._zoom_toggle.toggled.connect(self.zoomModeChanged)

        # The vertical counterpart. Independent of X zoom, not exclusive with
        # it: with both on the wheel zooms both axes at once.
        self._y_zoom_toggle = QPushButton("Y zoom")
        self._y_zoom_toggle.setCheckable(True)
        self._y_zoom_toggle.setToolTip(
            "Wheel zooms the intensity axis about the cursor,\n"
            "moving ALL spectra together.\n"
            "Different from scaling one spectrum: this changes\n"
            "nothing between them.\n"
            "Fit Y returns to automatic framing."
        )
        self._y_zoom_toggle.toggled.connect(self.yZoomModeChanged)

        self._f1_apply = QPushButton("Apply F1")
        self._f1_apply.clicked.connect(self._on_f1_edited)
        # F1 is the INDIRECT dimension and is drawn vertically, so "left" and
        # "right" described the wrong axis entirely. The pair reads top-down,
        # matching the plot: F1 axes descend like every other ppm axis, so the
        # first box is the higher shift.
        self._f1_label = QLabel("F1  top")
        self._f1_right_label = QLabel("bottom")

        layout.addWidget(self._zoom_toggle)
        layout.addWidget(self._y_zoom_toggle)
        layout.addWidget(self._f1_label)
        layout.addWidget(self._f1_left)
        layout.addWidget(self._f1_right_label)
        layout.addWidget(self._f1_right)
        layout.addWidget(self._f1_apply)

        self.set_mode("1D")
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
            self._f1_left,
            self._f1_right,
            self._f1_apply,
        ):
            widget.setEnabled(enabled)

    def set_range(self, left: float, right: float) -> None:
        """Display a range, normalised so left is the higher ppm value."""
        """Update the displayed range without emitting rangeChanged --
        for reflecting a change that came from elsewhere (e.g. a canvas
        zoom), not for the user's own edits."""
        high, low = (left, right) if left > right else (right, left)
        self._show_range(high, low)

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

        # NMR convention, applied consistently everywhere: the LEFT box is the
        # LEFT edge of the plot and therefore the HIGHER ppm value. Typing the
        # pair in either order is accepted, but the boxes are then normalised
        # so what is displayed matches the axis exactly. Leaving the boxes in
        # an arbitrary order (an earlier attempt) meant the controls and the
        # plot disagreed, which is what kept reading as "reversed".
        high, low = (a, b) if a > b else (b, a)
        self._show_range(high, low)
        self._remember(high, low)
        self.rangeChanged.emit(high, low)

    def set_mode(self, mode: str) -> None:
        """Show the F1 controls only in 2D.

        In 2D the main pair is F2 (the direct dimension, on the x axis) and F1
        is the indirect dimension on the y axis -- they need separate ranges,
        which is why one pair of boxes is not enough.
        """
        two_d = mode == "2D"
        for widget in (
            self._f1_label, self._f1_left, self._f1_right_label,
            self._f1_right, self._f1_apply,
        ):
            widget.setVisible(two_d)
        self._ppm_label.setText("F2  left" if two_d else "ppm  left")

    def set_f1_range(self, left: float, right: float) -> None:
        """Fill the F1 boxes, TOP box first.

        F1 is drawn with the LOW ppm value at the top of the plot (matplotlib
        gets set_ylim(high, low), so high is the bottom). Putting the high
        value in the box labelled "top" therefore stated the opposite of what
        the plot showed -- 35 ppm under "top" while 35 sat along the bottom
        axis. The pair now reads down the plot: top box = what is at the top.

        The argument order is unchanged (high, low), because that is what the
        canvas needs for set_ylim; only the box each value lands in moved.
        """
        high, low = (left, right) if left > right else (right, left)
        for spin, value in ((self._f1_left, low), (self._f1_right, high)):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def f1_range(self) -> tuple:
        """(high, low) -- the order the canvas needs, not the box order."""
        return (self._f1_right.value(), self._f1_left.value())

    def _on_f1_edited(self) -> None:
        a, b = self._f1_left.value(), self._f1_right.value()
        if a == b:
            return
        high, low = (a, b) if a > b else (b, a)
        self.set_f1_range(high, low)
        self.f1RangeChanged.emit(high, low)

    def _show_range(self, high: float, low: float) -> None:
        """Put the pair in the boxes without re-triggering an apply."""
        self._left_spin.blockSignals(True)
        self._right_spin.blockSignals(True)
        self._left_spin.setValue(high)
        self._right_spin.setValue(low)
        self._left_spin.blockSignals(False)
        self._right_spin.blockSignals(False)

    def _remember(self, left: float, right: float) -> None:
        entry = (round(left, 3), round(right, 3))
        if entry in self._recent:
            self._recent.remove(entry)
        self._recent.insert(0, entry)
        self._recent = self._recent[:MAX_RECENT_RANGES]
        self._recent_combo.clear()
        # A remembered range is written the way a range is written -- from the
        # smaller number to the larger one ("0 -> 10 ppm"). The two BOXES keep
        # the axis order (left box = higher ppm), because they map onto the
        # plot edges; the list is a description, not a pair of edges.
        self._recent_combo.addItems(
            [f"{min(l, r):.2f} \u2192 {max(l, r):.2f} ppm" for l, r in self._recent]
        )

    def _on_recent_selected(self, index: int) -> None:
        if not (0 <= index < len(self._recent)):
            return
        left, right = self._recent[index]
        self.set_range(left, right)
        self.rangeChanged.emit(left, right)
