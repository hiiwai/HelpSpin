"""Preferences: plot appearance.

Replaces the earlier honest stub. Scope is deliberately what actually affects
the figure right now -- line width and the colour cycle -- rather than a large
settings surface that mostly does nothing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Okabe-Ito: colour-blind safe, and the same cycle the domain uses.
DEFAULT_COLORS = [
    "#000000", "#E69F00", "#56B4E9", "#009E73",
    "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
]

MIN_LINE_WIDTH = 0.1
MAX_LINE_WIDTH = 10.0


class _ColorSwatch(QPushButton):
    """One palette slot; clicking it opens a colour picker."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(28, 22)
        self.clicked.connect(self._pick)
        self._apply()

    def _apply(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._color}; border: 1px solid #888888;"
        )
        self.setToolTip(self._color)

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._color), self)
        if chosen.isValid():
            self._color = chosen.name()
            self._apply()

    def color(self) -> str:
        return self._color


class PreferencesDialog(QDialog):
    """Line width and colour cycle.

    Reads current values in, hands new ones back via line_width() / colors();
    the caller applies them. Kept free of side effects so Cancel genuinely
    changes nothing.
    """

    def __init__(self, line_width: float = 0.8, colors=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")

        self._width_spin = QDoubleSpinBox()
        self._width_spin.setDecimals(2)
        self._width_spin.setRange(MIN_LINE_WIDTH, MAX_LINE_WIDTH)
        self._width_spin.setSingleStep(0.1)
        self._width_spin.setValue(_clamp_width(line_width))
        self._width_spin.setToolTip(
            "Line width for every spectrum, in points. "
            "Thin lines (0.5-1.0) suit publication figures."
        )

        start_colors = list(colors) if colors else list(DEFAULT_COLORS)
        if not start_colors:
            start_colors = list(DEFAULT_COLORS)
        self._swatches = [_ColorSwatch(c) for c in start_colors]

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(4)
        for swatch in self._swatches:
            swatch_row.addWidget(swatch)
        swatch_row.addStretch(1)
        swatch_holder = QWidget()
        swatch_holder.setLayout(swatch_row)

        reset_button = QPushButton("Reset to default colours")
        reset_button.clicked.connect(self._reset_colors)

        form = QFormLayout()
        form.addRow("Line width", self._width_spin)
        form.addRow("Colour cycle", swatch_holder)

        note = QLabel(
            "Colours are applied to spectra in the order they were loaded."
        )
        note.setStyleSheet("color: palette(mid); font-size: 11px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(reset_button, 0, Qt.AlignLeft)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _reset_colors(self) -> None:
        for swatch, default in zip(self._swatches, DEFAULT_COLORS):
            swatch._color = default
            swatch._apply()

    def line_width(self) -> float:
        return _clamp_width(self._width_spin.value())

    def colors(self) -> list[str]:
        return [s.color() for s in self._swatches]


def _clamp_width(value: float) -> float:
    """A zero or negative width would make lines invisible; clamp rather than
    letting a bad value through to matplotlib."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.8
    if value != value:   # NaN
        return 0.8
    return max(MIN_LINE_WIDTH, min(value, MAX_LINE_WIDTH))
