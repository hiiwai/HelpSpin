"""The loaded-spectra panel.

Lists what is currently on the canvas so a specific spectrum can be selected
and adjusted -- the wheel and the y-scale box act on the SELECTED trace, so
there has to be a visible, obvious way to choose one. Also carries per-trace
visibility, colour, and removal, which otherwise have nowhere to live.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SpectrumListPanel(QWidget):
    """Selection + per-trace vertical scale for the loaded spectra."""

    selectionChanged = Signal(int)            # index, -1 for none
    yScaleChanged = Signal(int, float)        # index, scale
    visibilityToggled = Signal(int, bool)     # index, visible
    removeRequested = Signal(int)             # index
    colorChangeRequested = Signal(int, str)   # index, "#rrggbb"
    lineStyleChanged = Signal(int, str)       # index, matplotlib style

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False   # guards against feedback loops while syncing

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemChanged.connect(self._on_item_changed)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setDecimals(3)
        self._scale_spin.setRange(0.001, 1_000_000.0)
        self._scale_spin.setValue(1.0)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setToolTip(
            "Vertical scale of the selected spectrum.\n"
            "The scroll wheel over the plot does the same thing."
        )
        self._scale_spin.valueChanged.connect(self._on_scale_changed)

        from .spectrum_canvas import LINE_STYLES

        self._style_combo = QComboBox()
        for name, style in LINE_STYLES.items():
            self._style_combo.addItem(name, style)
        self._style_combo.setToolTip("Line style of the selected spectrum")
        self._style_combo.currentIndexChanged.connect(self._on_style_changed)

        self._color_button = QPushButton("Colour…")
        self._color_button.clicked.connect(self._on_color_clicked)

        self._remove_button = QPushButton("Remove")
        self._remove_button.clicked.connect(self._on_remove_clicked)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Y scale"))
        scale_row.addWidget(self._scale_spin, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self._color_button)
        button_row.addWidget(self._remove_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("Loaded spectra"))
        layout.addWidget(self._list, 1)
        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Line"))
        style_row.addWidget(self._style_combo, 1)

        layout.addLayout(scale_row)
        layout.addLayout(style_row)
        layout.addLayout(button_row)

        self._set_controls_enabled(False)

    # -- syncing from the canvas ---------------------------------------------

    def set_traces(self, traces, selected_index: int | None) -> None:
        """Rebuild the list from the canvas's traces.

        Rebuilding wholesale (rather than diffing) keeps this simple and the
        lists are short; _updating suppresses the signals that rebuilding
        would otherwise emit back at the canvas.
        """
        self._updating = True
        try:
            self._list.clear()
            for trace in traces:
                item = QListWidgetItem(trace.label)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if trace.visible else Qt.Unchecked)
                item.setForeground(_qcolor(trace.color))
                self._list.addItem(item)
            if selected_index is not None and 0 <= selected_index < len(traces):
                self._list.setCurrentRow(selected_index)
                self._scale_spin.setValue(traces[selected_index].y_scale)
                style = getattr(traces[selected_index], "line_style", "-")
                idx = self._style_combo.findData(style)
                if idx >= 0:
                    self._style_combo.setCurrentIndex(idx)
                self._set_controls_enabled(True)
            else:
                self._set_controls_enabled(False)
        finally:
            self._updating = False

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._scale_spin.setEnabled(enabled)
        self._style_combo.setEnabled(enabled)
        self._color_button.setEnabled(enabled)
        self._remove_button.setEnabled(enabled)

    # -- user actions ---------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        if self._updating:
            return
        self.selectionChanged.emit(row)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._updating:
            return
        row = self._list.row(item)
        self.visibilityToggled.emit(row, item.checkState() == Qt.Checked)

    def _on_scale_changed(self, value: float) -> None:
        if self._updating:
            return
        row = self._list.currentRow()
        if row >= 0:
            self.yScaleChanged.emit(row, float(value))

    def _on_style_changed(self, _index: int) -> None:
        if self._updating:
            return
        row = self._list.currentRow()
        if row >= 0:
            self.lineStyleChanged.emit(row, self._style_combo.currentData())

    def _on_color_clicked(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.colorChangeRequested.emit(row, color.name())

    def _on_remove_clicked(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.removeRequested.emit(row)


def _qcolor(name: str):
    from PySide6.QtGui import QColor

    color = QColor(name)
    return color if color.isValid() else QColor("#000000")
