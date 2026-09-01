"""The centre panel: where the figure canvas belongs once rendering exists.

This is deliberately a placeholder, not a mockup pretending to be finished.
Milestone 3 (matplotlib rendering, see README) replaces this widget's content
with a real FigureCanvasQTAgg; nothing else in the shell needs to change when
that happens, since MainWindow only depends on this widget's public surface
(show_message / clear_message), not its internals.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

DEFAULT_MESSAGE = (
    "Drag spectra here once a figure is open.\n\n"
    "(This build has the dataset browser only -- "
    "the comparison canvas is not implemented yet.)"
)


class CanvasPlaceholder(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._label = QLabel(DEFAULT_MESSAGE)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet("color: palette(mid); font-size: 13px;")

        layout = QVBoxLayout(self)
        layout.addWidget(self._label)

    def show_message(self, text: str) -> None:
        self._label.setText(text)

    def clear_message(self) -> None:
        self._label.setText(DEFAULT_MESSAGE)
