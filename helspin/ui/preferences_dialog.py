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
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..domain.project import palette_colours, palette_names, palette_styles

# Okabe-Ito: colour-blind safe, and the same cycle the domain uses.
# Ten slots now, not eight. The two extra colours continue the colour-blind
# safe set (a grey and a teal) so the default palette still works when ten
# spectra are overlaid.
DEFAULT_COLORS = [
    "#000000",   # black
    "#0072B2",   # blue        (was orange -- poor contrast against black on
                 #              screen and muddy in print)
    "#D55E00",   # vermillion
    "#009E73",   # bluish green
    "#CC79A7",   # reddish purple
    "#56B4E9",   # sky blue
    "#E69F00",   # orange
    "#8C564B",   # brown
    "#666666",   # grey
    "#117733",   # deep teal-green
]

LINE_STYLES = {
    "Solid": "-",
    "Dashed": "--",
    "Dotted": ":",
    "Dash-dot": "-.",
}

SLOT_COUNT = 10
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

    def __init__(self, styles=None, grid_spacing=None, x_decimals=None,
                 label_scale=1.0, opacity=1.0, cursor_decimals=2,
                 grid_spacing_y=None, palette=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")

        incoming = list(styles or [])
        defaults = default_styles()
        while len(incoming) < SLOT_COUNT:
            incoming.append(defaults[len(incoming)])

        grid = QGridLayout()
        # Palette chooser. Setting eight colours one at a time is tedious and
        # tends to produce a set that is not actually distinguishable; these
        # are published schemes designed for exactly that. Applying is an
        # explicit button press, so opening the dropdown to read the names
        # does not overwrite colours already chosen by hand.
        self._palette_box = QComboBox()
        self._palette_box.addItems(palette_names())
        if palette in palette_names():
            self._palette_box.setCurrentText(palette)
        self._palette_apply = QPushButton("Apply palette")
        self._palette_apply.clicked.connect(self._apply_palette)

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

        # A single width applied to every slot: setting eight boxes by hand
        # to the same value is the common case (a whole figure usually wants
        # one line weight), so it gets a dedicated one-click control.
        # A single width that applies to every slot at once -- setting the
        # same value eight times is exactly the tedium a global control exists
        # to remove. It writes into the per-slot boxes, so the per-slot values
        # stay authoritative and individually overridable afterwards.
        self._global_width = QDoubleSpinBox()
        self._global_width.setDecimals(2)
        self._global_width.setRange(MIN_LINE_WIDTH, MAX_LINE_WIDTH)
        self._global_width.setSingleStep(0.1)
        self._global_width.setValue(
            _clamp_width(incoming[0].get("width", DEFAULT_LINE_WIDTH))
        )
        apply_all = QPushButton("Apply width to all")
        apply_all.clicked.connect(self._apply_width_to_all)

        global_holder = QWidget()
        global_row = QHBoxLayout(global_holder)
        global_row.setContentsMargins(0, 0, 0, 0)
        global_row.addWidget(QLabel("All line widths"))
        global_row.addWidget(self._global_width)
        global_row.addWidget(apply_all)
        global_row.addSpacing(16)
        global_row.addWidget(QLabel("Palette"))
        global_row.addWidget(self._palette_box)
        global_row.addWidget(self._palette_apply)
        global_row.addStretch(1)

        # Grid spacing in ppm. 0 means "let matplotlib choose", which is the
        # sane default -- a fixed spacing only helps when you want gridlines at
        # known chemical-shift intervals.
        self._grid_spacing = QDoubleSpinBox()
        self._grid_spacing.setDecimals(2)
        self._grid_spacing.setRange(0.0, 100.0)
        self._grid_spacing.setSingleStep(0.5)
        self._grid_spacing.setSpecialValueText("Automatic")
        self._grid_spacing.setSuffix(" ppm")
        self._grid_spacing.setValue(float(grid_spacing or 0.0))
        self._grid_spacing.setToolTip(
            "Spacing between vertical gridlines. 0 = automatic."
        )

        grid_holder = QWidget()
        grid_row = QHBoxLayout(grid_holder)
        grid_row.setContentsMargins(0, 0, 0, 0)
        grid_row.addWidget(QLabel("Grid spacing"))
        grid_row.addWidget(self._grid_spacing)
        grid_row.addStretch(1)

        # ppm axis label precision: "1", "1.0", "1.00" ...
        self._x_decimals = QComboBox()
        self._x_decimals.addItem("Automatic", None)
        for n in range(0, 4):
            example = f"1.{'0' * n}" if n else "1"
            self._x_decimals.addItem(f"{example} ppm", n)
        idx = self._x_decimals.findData(x_decimals)
        self._x_decimals.setCurrentIndex(idx if idx >= 0 else 0)

        # Spectrum-name size on the plot. Larger also spaces them further
        # apart, which is what stops a long list overlapping the traces.
        # Opacity. Matters most for superimposed 2D maps: opaque contours
        # hide each other exactly where they cross, which is the place the
        # comparison is being made.
        self._opacity = QDoubleSpinBox()
        self._opacity.setRange(0.05, 1.00)
        self._opacity.setSingleStep(0.05)
        self._opacity.setDecimals(2)
        self._opacity.setToolTip(
            "1.00 is fully opaque. Lower values let overlapping spectra "
            "and contour maps show through each other."
        )
        self._opacity.setValue(float(opacity or 1.0))

        # Separate from the x spacing: in 2D the axes cover quite different
        # ranges -- 10 ppm of proton against 150 of carbon -- so one number
        # cannot suit both. 0 means "let matplotlib choose".
        self._grid_spacing_y = QDoubleSpinBox()
        self._grid_spacing_y.setRange(0.0, 1000.0)
        self._grid_spacing_y.setDecimals(3)
        self._grid_spacing_y.setSpecialValueText("auto")
        self._grid_spacing_y.setToolTip(
            "Horizontal grid spacing: F1 ppm in 2D. 0 = automatic."
        )
        self._grid_spacing_y.setValue(float(grid_spacing_y or 0.0))

        self._cursor_decimals = QSpinBox()
        self._cursor_decimals.setRange(0, 6)
        self._cursor_decimals.setToolTip(
            "Digits after the decimal point in the cursor and crosshair "
            "readouts. Two places a peak precisely enough and keeps the "
            "numbers short."
        )
        self._cursor_decimals.setValue(int(cursor_decimals or 2))

        self._label_scale = QDoubleSpinBox()
        self._label_scale.setDecimals(2)
        self._label_scale.setRange(0.3, 4.0)
        self._label_scale.setSingleStep(0.1)
        self._label_scale.setValue(float(label_scale or 1.0))

        axis_holder = QWidget()
        axis_row = QHBoxLayout(axis_holder)
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.addWidget(QLabel("ppm labels"))
        axis_row.addWidget(self._x_decimals)
        axis_row.addSpacing(12)
        axis_row.addWidget(QLabel("Name size"))
        axis_row.addWidget(self._label_scale)
        axis_row.addWidget(QLabel("Opacity"))
        axis_row.addWidget(self._opacity)
        axis_row.addWidget(QLabel("Cursor decimals"))
        axis_row.addWidget(self._cursor_decimals)
        axis_row.addWidget(QLabel("Grid Y"))
        axis_row.addWidget(self._grid_spacing_y)
        axis_row.addStretch(1)

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
        layout.addWidget(global_holder)
        layout.addLayout(grid)
        layout.addWidget(grid_holder)
        layout.addWidget(axis_holder)
        layout.addWidget(reset_button, 0, Qt.AlignLeft)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _apply_width_to_all(self) -> None:
        width = _clamp_width(self._global_width.value())
        for spin in self._width_spins:
            spin.setValue(width)

    def _reset(self) -> None:
        for i, entry in enumerate(default_styles()):
            self._swatches[i].set_color(entry["color"])
            idx = self._style_combos[i].findData(entry["style"])
            if idx >= 0:
                self._style_combos[i].setCurrentIndex(idx)
            self._width_spins[i].setValue(entry["width"])

    def grid_spacing(self):
        """Spacing in ppm, or None for automatic."""
        value = self._grid_spacing.value()
        return value if value > 0 else None

    def x_decimals(self):
        return self._x_decimals.currentData()

    def grid_spacing_y(self):
        value = float(self._grid_spacing_y.value())
        return value if value > 0 else None

    def _apply_palette(self) -> None:
        """Fill the colour swatches from the chosen scheme.

        Line styles are only touched when the palette needs them: the
        greyscale set is unusable without distinct dash patterns, since in a
        black-and-white figure the dash is the only thing telling two spectra
        apart. Every other palette leaves the styles as the user set them.
        """
        name = self._palette_box.currentText()
        colours = palette_colours(name)
        for index, swatch in enumerate(self._swatches):
            if index < len(colours):
                swatch.set_color(colours[index])
        styles = palette_styles(name)
        if styles:
            for index, combo in enumerate(self._style_combos):
                if index >= len(styles):
                    break
                position = combo.findData(styles[index])
                if position >= 0:
                    combo.setCurrentIndex(position)

    def palette(self) -> str:
        return self._palette_box.currentText()

    def cursor_decimals(self) -> int:
        return int(self._cursor_decimals.value())

    def opacity(self) -> float:
        return float(self._opacity.value())

    def label_scale(self) -> float:
        return float(self._label_scale.value())

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
