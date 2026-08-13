"""The main canvas: drop spectra straight onto it and see them.

Deliberately simple, replacing the earlier layout-first flow (define a figure
with N slots, then fill the slots). Here you just drag one or more datasets
from the browser onto the canvas and they are loaded and drawn. Arrangement
(overlay vs stacked) is a control you flip afterwards, not a decision you have
to make up front.

Loading happens on a worker thread: reading processed data off a network share
is slow enough to freeze the GUI if done inline. Each spectrum is drawn as it
arrives, so dropping several does not block on the slowest one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..domain.project import DEFAULT_PALETTE as PALETTE
from .dataset_model import MIME_DATASET

# Okabe-Ito is already the domain palette; reuse it so on-screen colours match
# whatever an eventual export produces.
_FALLBACK_PALETTE = [
    "#000000", "#E69F00", "#56B4E9", "#009E73",
    "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
]


def _palette() -> list[str]:
    try:
        return list(PALETTE) or _FALLBACK_PALETTE
    except Exception:  # noqa: BLE001 - palette shape is not load-bearing here
        return _FALLBACK_PALETTE


@dataclass
class Trace:
    """One loaded 1D spectrum on the canvas."""

    path: Path
    label: str
    ppm: np.ndarray
    intensity: np.ndarray
    color: str
    visible: bool = True


class _LoadSignals(QObject):
    loaded = Signal(object, object, object, str)   # path, ppm, intensity, label
    failed = Signal(object, str)                   # path, message


class _LoadTask(QRunnable):
    """Reads one spectrum off the GUI thread.

    Pure I/O plus numpy: touches no Qt model state, so it is safe on a worker.
    The result is handed back by signal and applied on the GUI thread.
    """

    def __init__(self, reader, path: Path, label: str, dimensionality: int):
        super().__init__()
        self._reader = reader
        self._path = path
        self._label = label
        self._dim = dimensionality
        self.signals = _LoadSignals()

    def run(self) -> None:
        try:
            if self._dim != 1:
                # 2D contour rendering is not implemented yet; say so plainly
                # rather than silently dropping the file.
                self.signals.failed.emit(
                    self._path, "2D display is not implemented yet"
                )
                return
            spec = self._reader.read_1d(self._path)
            ppm = np.asarray(spec.axis.ppm_scale(), dtype=np.float64)
            intensity = np.asarray(spec.real, dtype=np.float64)
            self.signals.loaded.emit(self._path, ppm, intensity, self._label)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill the drop
            self.signals.failed.emit(self._path, str(exc))


class SpectrumCanvas(QWidget):
    """A matplotlib canvas that accepts dataset drops and plots them.

    Public surface kept small on purpose: drop things on it, call
    set_arrangement / set_ppm_range / clear. No figure-layout model, no slots.
    """

    spectrumAdded = Signal(str)      # label
    loadFailed = Signal(str, str)    # path, message
    tracesChanged = Signal()

    ARRANGEMENT_OVERLAY = "overlay"
    ARRANGEMENT_STACKED = "stacked"

    def __init__(self, reader=None, pool: QThreadPool | None = None, parent=None):
        super().__init__(parent)
        if reader is None:
            from ..infrastructure.nmrglue_reader import NmrglueReader

            reader = NmrglueReader()
        self._reader = reader
        self._pool = pool or QThreadPool.globalInstance()
        self._traces: list[Trace] = []
        self._arrangement = self.ARRANGEMENT_OVERLAY
        self._ppm_range: tuple[float, float] | None = None
        self._inflight: set = set()

        self._figure = Figure(figsize=(6, 4), tight_layout=False)
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._axes = self._figure.add_subplot(111)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

        self.setAcceptDrops(True)
        self._redraw()

    # -- public state --------------------------------------------------------

    @property
    def traces(self) -> list[Trace]:
        return list(self._traces)

    def arrangement(self) -> str:
        return self._arrangement

    def set_arrangement(self, arrangement: str) -> None:
        if arrangement not in (self.ARRANGEMENT_OVERLAY, self.ARRANGEMENT_STACKED):
            return
        self._arrangement = arrangement
        self._redraw()

    def set_ppm_range(self, left: float, right: float) -> None:
        """ppm axes descend, so left must be the HIGHER value."""
        if left <= right:
            return
        self._ppm_range = (left, right)
        self._redraw()

    def full_range(self) -> None:
        """Union of every loaded trace's ppm span (not the intersection) --
        show everything rather than only the overlap."""
        self._ppm_range = None
        self._redraw()

    def clear(self) -> None:
        self._traces.clear()
        self._ppm_range = None
        self._redraw()
        self.tracesChanged.emit()

    def remove_trace(self, path: Path) -> None:
        before = len(self._traces)
        self._traces = [t for t in self._traces if t.path != Path(path)]
        if len(self._traces) != before:
            self._redraw()
            self.tracesChanged.emit()

    # -- loading -------------------------------------------------------------

    def add_dataset(self, path, label: str, dimensionality: int = 1) -> None:
        """Queue one dataset for loading and drawing."""
        path = Path(path)
        if any(t.path == path for t in self._traces):
            return   # already shown; dropping twice is a no-op, not a duplicate
        task = _LoadTask(self._reader, path, label, dimensionality)
        task.signals.loaded.connect(self._on_loaded)
        task.signals.failed.connect(self._on_failed)
        # Hold a reference: QThreadPool does not keep the Python object alive,
        # and losing it mid-flight would silently discard the result.
        self._inflight.add(task)
        self._pool.start(task)

    def _on_loaded(self, path, ppm, intensity, label: str) -> None:
        path = Path(path)
        self._inflight = {t for t in self._inflight if t._path != path}
        if any(t.path == path for t in self._traces):
            return
        palette = _palette()
        color = palette[len(self._traces) % len(palette)]
        self._traces.append(
            Trace(path=path, label=label, ppm=ppm, intensity=intensity, color=color)
        )
        self._redraw()
        self.spectrumAdded.emit(label)
        self.tracesChanged.emit()

    def _on_failed(self, path, message: str) -> None:
        path = Path(path)
        self._inflight = {t for t in self._inflight if t._path != path}
        self.loadFailed.emit(str(path), message)

    # -- drawing -------------------------------------------------------------

    def _redraw(self) -> None:
        self._axes.clear()

        if not self._traces:
            self._axes.set_xticks([])
            self._axes.set_yticks([])
            for spine in self._axes.spines.values():
                spine.set_visible(False)
            self._axes.text(
                0.5, 0.5,
                "Drag spectra here",
                ha="center", va="center", fontsize=13, color="#999999",
                transform=self._axes.transAxes,
            )
            self._canvas.draw_idle()
            return

        for spine in self._axes.spines.values():
            spine.set_visible(True)

        visible = [t for t in self._traces if t.visible]
        offset_step = 0.0
        if self._arrangement == self.ARRANGEMENT_STACKED and visible:
            # Stack by a fraction of the tallest trace so spacing is stable
            # regardless of absolute intensity.
            tallest = max(
                float(np.nanmax(t.intensity)) - float(np.nanmin(t.intensity))
                for t in visible
            )
            offset_step = tallest * 1.05 if tallest > 0 else 1.0

        for i, trace in enumerate(visible):
            y = trace.intensity + (offset_step * i)
            self._axes.plot(
                trace.ppm, y, color=trace.color, linewidth=0.8, label=trace.label
            )

        # ppm axes are conventionally DESCENDING (high ppm on the left).
        if self._ppm_range is not None:
            left, right = self._ppm_range
        else:
            left = max(float(np.nanmax(t.ppm)) for t in visible)
            right = min(float(np.nanmin(t.ppm)) for t in visible)
        self._axes.set_xlim(left, right)

        nucleus_hint = ""
        self._axes.set_xlabel(f"{nucleus_hint}ppm".strip())
        if self._arrangement == self.ARRANGEMENT_STACKED:
            self._axes.set_yticks([])
        if len(visible) > 1:
            self._axes.legend(loc="upper right", fontsize=8, frameon=False)

        self._figure.tight_layout()
        self._canvas.draw_idle()

    # -- drag and drop -------------------------------------------------------
    #
    # dragEnterEvent MUST call acceptProposedAction() or dropEvent never fires
    # -- the single most common Qt drag-and-drop mistake.

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_DATASET):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(MIME_DATASET):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if self.handle_mime_data(event.mimeData()):
            event.acceptProposedAction()

    def handle_mime_data(self, mime) -> bool:
        """Decode a dataset payload and queue every entry.

        Separate from dropEvent so tests can exercise it with a plain
        QMimeData, without constructing QDropEvent objects whose signature
        varies between Qt versions.
        """
        if not mime.hasFormat(MIME_DATASET):
            return False
        try:
            payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return False
        if not payload:
            return False
        for item in payload:
            self.add_dataset(
                item["path"],
                item.get("label") or Path(item["path"]).name,
                int(item.get("dimensionality", 1)),
            )
        return True
