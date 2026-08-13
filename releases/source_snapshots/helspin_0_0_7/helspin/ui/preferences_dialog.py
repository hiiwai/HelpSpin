"""Preferences: per-spectrum plot appearance.

All appearance settings live here, not on the canvas panel: colour, line
style, and line width, each configurable independently for up to eight
spectra. Slot N applies to the Nth spectrum loaded, so the settings are
predictable before anything is dropped rather than only editable afterwards.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

# Okabe-Ito: colour-blind safe, and the same cycle the domain uses.
DEFAULT_COLORS = [
    "#000000", "#E69F00", "#56B4E9", "#009E73",
    "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
]

LINE_STYLES = {
    "Solid": "-",
    "Dashed": "--",
    "Dotted": ":",
    "Dash-dot": "-.",
}

SLOT_COUNT = 8
MIN_LINE_WIDTH = 0.1
MAX_LINE_WIDTH = 10.0
DEFAULT_LINE_WIDTH = 0.8


def _clamp_width(value) -> float:
    """A zero or negative width would make a line invisible; clamp rather than
    letting a bad value reach matplotlib."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LINE_WIDTH
    if value != value:   # NaN
        return DEFAULT_LINE_WIDTH
    return max(MIN_LINE_WIDTH, min(value, MAX_LINE_WIDTH))


def default_styles() -> list[dict]:
    """The out-of-the-box appearance for all eight slots."""
    return [
        {"color": DEFAULT_COLORS[i], "style": "-", "width": DEFAULT_LINE_WIDTH}
        for i in range(SLOT_COUNT)
    ]


class _ColorSwatch(QPushButton):
    """One slot's colour; clicking opens a picker."""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(30, 22)
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
            self.set_color(chosen.name())

    def set_color(self, color: str) -> None:
        self._color = color
        self._apply()

    def color(self) -> str:
        return self._color


class PreferencesDialog(QDialog):
    """Colour, line style, and line width for each of eight spectrum slots.

    Free of side effects: values are read back by the caller via styles() on
    accept, so Cancel genuinely changes nothing.
    """

    def __init__(self, styles=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")

        incoming = list(styles or [])
        defaults = default_styles()
        while len(incoming) < SLOT_COUNT:
            incoming.append(defaults[len(incoming)])

        grid = QGridLayout()
        grid.addWidget(QLabel("<b>Spectrum</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Colour</b>"), 0, 1)
        grid.addWidget(QLabel("<b>Line</b>"), 0, 2)
        grid.addWidget(QLabel("<b>Width</b>"), 0, 3)

        self._swatches: list[_ColorSwatch] = []
        self._style_combos: list[QComboBox] = []
        self._width_spins: list[QDoubleSpinBox] = []

        for i in range(SLOT_COUNT):
            entry = incoming[i]

            swatch = _ColorSwatch(entry.get("color", DEFAULT_COLORS[i]))

            combo = QComboBox()
            for name, style in LINE_STYLES.items():
                combo.addItem(name, style)
            idx = combo.findData(entry.get("style", "-"))
            if idx >= 0:
                combo.setCurrentIndex(idx)

            spin = QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(MIN_LINE_WIDTH, MAX_LINE_WIDTH)
            spin.setSingleStep(0.1)
            spin.setValue(_clamp_width(entry.get("width", DEFAULT_LINE_WIDTH)))

            grid.addWidget(QLabel(f"{i + 1}"), i + 1, 0)
            grid.addWidget(swatch, i + 1, 1)
            grid.addWidget(combo, i + 1, 2)
            grid.addWidget(spin, i + 1, 3)

            self._swatches.append(swatch)
            self._style_combos.append(combo)
            self._width_spins.append(spin)

        reset_button = QPushButton("Reset to defaults")
        reset_button.clicked.connect(self._reset)

        note = QLabel(
            "Settings apply to spectra in the order they are loaded: "
            "row 1 to the first spectrum, row 2 to the second, and so on."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 11px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.Cancel | QDialogButtonBox.Ok
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addWidget(reset_button, 0, Qt.AlignLeft)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _reset(self) -> None:
        for i, entry in enumerate(default_styles()):
            self._swatches[i].set_color(entry["color"])
            idx = self._style_combos[i].findData(entry["style"])
            if idx >= 0:
                self._style_combos[i].setCurrentIndex(idx)
            self._width_spins[i].setValue(entry["width"])

    def styles(self) -> list[dict]:
        """One dict per slot: color, style, width."""
        return [
            {
                "color": self._swatches[i].color(),
                "style": self._style_combos[i].currentData(),
                "width": _clamp_width(self._width_spins[i].value()),
            }
            for i in range(SLOT_COUNT)
        ]
