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
    yOffsetChanged = Signal(int, float)       # index, vertical offset
    visibilityToggled = Signal(int, bool)     # index, visible
    removeRequested = Signal(int)             # index
    moveToBottomRequested = Signal(int)       # index
    subtractRequested = Signal(int, int)      # index A, index B  (A - B)
    colorChangeRequested = Signal(int, str)   # index, "#rrggbb"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False   # guards against feedback loops while syncing

        self._list = QListWidget()
        # Extended so two spectra can be picked for a difference.
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
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

        self._offset_spin = QDoubleSpinBox()
        self._offset_spin.setDecimals(1)
        # Intensities are large, so the offset range must be too -- a range of
        # +/-100 would be useless against a 1e9 spectrum.
        self._offset_spin.setRange(-1e12, 1e12)
        self._offset_spin.setSingleStep(1.0)
        self._offset_spin.setToolTip(
            "Vertical position of the selected spectrum.\n"
            "Dragging the spectrum on the plot changes this too."
        )
        self._offset_spin.valueChanged.connect(self._on_offset_changed)

        self._color_button = QPushButton("Colour…")
        self._color_button.clicked.connect(self._on_color_clicked)

        self._bottom_button = QPushButton("To bottom")
        self._bottom_button.setToolTip(
            "Sit this spectrum's baseline on the bottom of the plot"
        )
        self._bottom_button.clicked.connect(self._on_bottom_clicked)

        self._subtract_button = QPushButton("Subtract")
        self._subtract_button.setToolTip(
            "Select exactly two spectra, then subtract the second from the "
            "first and add the result as a new spectrum"
        )
        self._subtract_button.clicked.connect(self._on_subtract_clicked)

        self._remove_button = QPushButton("Remove")
        self._remove_button.clicked.connect(self._on_remove_clicked)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Y scale"))
        scale_row.addWidget(self._scale_spin, 1)

        offset_row = QHBoxLayout()
        offset_row.addWidget(QLabel("Y offset"))
        offset_row.addWidget(self._offset_spin, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self._subtract_button)
        button_row.addWidget(self._bottom_button)
        button_row.addWidget(self._color_button)
        button_row.addWidget(self._remove_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("Loaded spectra"))
        layout.addWidget(self._list, 1)
        layout.addLayout(scale_row)
        layout.addLayout(offset_row)
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
                trace = traces[selected_index]
                # Step sized from the trace's own amplitude: a fixed step of
                # 1.0 is meaningless against intensities of ~1e9, making the
                # spin arrows useless.
                data = getattr(trace, "intensity", None)
                if data is not None and getattr(data, "size", 0):
                    import numpy as _np
                    span = float(_np.nanmax(data)) - float(_np.nanmin(data))
                    self._offset_spin.setSingleStep(max(span * 0.05, 1e-6))
                self._offset_spin.setValue(getattr(trace, "y_offset", 0.0))
                self._set_controls_enabled(True)
            else:
                self._set_controls_enabled(False)
        finally:
            self._updating = False

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._scale_spin.setEnabled(enabled)
        self._offset_spin.setEnabled(enabled)
        self._color_button.setEnabled(enabled)
        self._remove_button.setEnabled(enabled)
        self._bottom_button.setEnabled(enabled)

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

    def _on_offset_changed(self, value: float) -> None:
        if self._updating:
            return
        row = self._list.currentRow()
        if row >= 0:
            self.yOffsetChanged.emit(row, float(value))

    def _on_color_clicked(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        color = QColorDialog.getColor()
        if color.isValid():
            self.colorChangeRequested.emit(row, color.name())

    def _on_subtract_clicked(self) -> None:
        rows = sorted(i.row() for i in self._list.selectedIndexes())
        # Exactly two, and in the order shown: A - B.
        if len(rows) != 2:
            return
        self.subtractRequested.emit(rows[0], rows[1])

    def _on_bottom_clicked(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.moveToBottomRequested.emit(row)

    def _on_remove_clicked(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self.removeRequested.emit(row)


def _qcolor(name: str):
    from PySide6.QtGui import QColor

    color = QColor(name)
    return color if color.isValid() else QColor("#000000")
