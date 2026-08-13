"""Renders a Project's boxes and slots, and is the drop target for the
dataset browser's drag payload.

Box rects are figure-fraction coordinates (handoff 4.3): this widget just
maps those fractions onto its own current pixel size on every resize, so the
layout is resolution-independent the same way the eventual matplotlib export
will be. Slots are drawn as coloured chips; an empty chip is the drop target
for dataset_model.MIME_DATASET.

What this deliberately does NOT do: render actual spectra. read_1d/read_2d
are not implemented yet (see nmrglue_reader.py), so a "filled" slot shows its
colour and the dropped dataset's label, not a plotted trace. That is the
honest boundary of this slice -- the layout and drop mechanics are real, the
plotting is not.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QMimeData, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..domain.project import (
    Block1D,
    Dimensionality,
    DifferenceBox as DomainDifferenceBox,
    LegendBox as DomainLegendBox,
    Project,
    SpectrumBox as DomainSpectrumBox,
)
from .dataset_model import MIME_DATASET


class SlotChip(QFrame):
    """One slot: an empty, coloured, numbered drop target, or a filled
    coloured chip showing the dropped dataset's label.

    Emits datasetDropped(slot_index, payload_dict) and lets the canvas decide
    whether the drop is actually valid -- this widget only knows its own
    dimensionality, not the wider rules (e.g. "don't grow a full block").
    """

    datasetDropped = Signal(int, list)

    def __init__(self, index: int, color: str, dimensionality: Dimensionality, parent=None):
        super().__init__(parent)
        self.index = index
        self.dimensionality = dimensionality
        self._label_widget = QLabel(str(index + 1))
        self._label_widget.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self._label_widget)

        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Box)
        self._empty_color = color
        self.set_filled(None)

    # -- state -----------------------------------------------------------

    def set_filled(self, label: str | None) -> None:
        if label is None:
            self._label_widget.setText(str(self.index + 1))
            self.setStyleSheet(
                f"background-color: white; border: 2px dashed {self._empty_color};"
            )
        else:
            self._label_widget.setText(label)
            self.setStyleSheet(
                f"background-color: {self._empty_color}; color: white; "
                f"border: 1px solid {self._empty_color};"
            )

    # -- drag and drop -----------------------------------------------------
    #
    # Two easy-to-miss Qt requirements (handoff 8.3): setAcceptDrops(True) on
    # THIS widget specifically (done in __init__, not on some ancestor), and
    # dragEnterEvent MUST call acceptProposedAction() or dropEvent never
    # fires at all -- the single most common Qt drag-and-drop bug.
    #
    # The actual decoding/emitting logic lives in _handle_mime_data, a plain
    # method taking a QMimeData, so tests can call it directly without
    # fighting QDropEvent's constructor across Qt/PySide versions. The event
    # handlers below are thin wrappers and are not where the real logic (or
    # the real bugs) live.

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(MIME_DATASET):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(MIME_DATASET):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self._handle_mime_data(event.mimeData())

    def _handle_mime_data(self, mime: QMimeData) -> None:
        if not mime.hasFormat(MIME_DATASET):
            return
        try:
            payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if not payload:
            return
        # Emits the WHOLE payload list. Sequencing across several slots for a
        # multi-select drag happens one level up, in BoxCanvas, since only
        # the canvas can see the rest of the block this chip belongs to.
        self.datasetDropped.emit(self.index, payload)


class SpectrumBoxWidget(QFrame):
    """One SpectrumBox: a titled frame containing its slots' chips."""

    datasetDropped = Signal(int, list)   # slot index (within this box), payload list

    def __init__(self, block, parent=None):
        super().__init__(parent)
        self.block = block
        self.setFrameShape(QFrame.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        dimensionality = (
            Dimensionality.ONE_D if isinstance(block, Block1D) else Dimensionality.TWO_D
        )
        self._chips: list[SlotChip] = []
        for i, slot in enumerate(block.slots):
            color = slot.color if isinstance(block, Block1D) else slot.color_positive
            chip = SlotChip(i, color, dimensionality)
            chip.datasetDropped.connect(self.datasetDropped)
            self._chips.append(chip)
            layout.addWidget(chip)

    def slot_filled(self, index: int, label: str) -> None:
        self._chips[index].set_filled(label)


class BoxCanvas(QWidget):
    """Positions SpectrumBoxWidgets (and, eventually, difference/legend
    boxes) according to their figure-fraction rects, remapped on every
    resize -- the same resolution-independence the eventual export relies on.
    """

    figureChanged = Signal()

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self._project = project
        self._box_widgets: dict[str, QWidget] = {}

        for box in project.boxes:
            if isinstance(box, DomainSpectrumBox) and box.block is not None:
                widget = SpectrumBoxWidget(box.block, self)
                widget.datasetDropped.connect(
                    lambda slot_idx, payload, b=box: self._on_slot_dropped(b, slot_idx, payload)
                )
                self._box_widgets[box.id] = widget
            elif isinstance(box, DomainDifferenceBox):
                widget = QFrame(self)
                widget.setFrameShape(QFrame.StyledPanel)
                inner = QVBoxLayout(widget)
                inner.addWidget(QLabel("difference"))
                self._box_widgets[box.id] = widget
            elif isinstance(box, DomainLegendBox):
                widget = QFrame(self)
                widget.setFrameShape(QFrame.NoFrame)
                self._box_widgets[box.id] = widget

        self._reposition()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition()

    def showEvent(self, event) -> None:
        """Belt-and-braces: on some window managers the first resizeEvent
        can race with the widget actually becoming visible, so the layout is
        also recomputed the moment it's shown, not only on resize."""
        super().showEvent(event)
        self._reposition()

    def _reposition(self) -> None:
        w, h = self.width(), self.height()
        for box in self._project.boxes:
            widget = self._box_widgets.get(box.id)
            if widget is None:
                continue
            left, bottom, bw, bh = box.rect
            # Figure-fraction rects are bottom-up (matplotlib convention);
            # Qt widget coordinates are top-down, so the y axis flips here.
            x = int(left * w)
            top = int((1.0 - bottom - bh) * h)
            widget.setGeometry(x, top, max(1, int(bw * w)), max(1, int(bh * h)))

    # -- drop handling: sequential fill for multi-item drags ------------------

    def _on_slot_dropped(self, box, start_index: int, payload: list[dict]) -> None:
        """A multi-select drag fills several slots in one gesture (handoff
        7A.5): starting at the chip the user dropped onto, each subsequent
        payload item advances one slot index. Items beyond the end of the
        block are silently dropped -- the same "fills what fits" rule as a
        multi-line paste with more lines than empty slots.

        Dropping the first item onto an already-filled slot REPLACES it
        (keeping the slot's colour, since colour is instance-bound, not
        derived from the dataset) -- this is the intended way to swap a
        sample, not an error.
        """
        block = box.block
        widget = self._box_widgets[box.id]
        block_dim = (
            Dimensionality.ONE_D if isinstance(block, Block1D) else Dimensionality.TWO_D
        )

        for offset, item in enumerate(payload):
            target_index = start_index + offset
            if target_index >= len(block.slots):
                break   # fits what fits; extras are silently dropped

            if Dimensionality(item["dimensionality"]) is not block_dim:
                # A mismatched item in the middle of a multi-select drag is
                # skipped, not fatal to the rest of the drop -- one wrong
                # selection should not lose the others.
                continue

            slot = block.slots[target_index]
            slot.dataset_id = item["path"]
            widget.slot_filled(target_index, item.get("label", item["path"]))

        self.figureChanged.emit()
