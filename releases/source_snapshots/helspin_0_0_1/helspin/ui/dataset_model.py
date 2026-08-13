"""The dataset browser's tree model.

Deliberately NOT QFileSystemModel: tree nodes here are Bruker concepts (data
root, sample, expno), which do not map one-to-one onto directories. Structural
detection from domain.paths drives this model instead of path depth.

The real Bruker hierarchy is root/data/<user>/nmr/<sample>/<expno>/pdata/<procno>
-- seven levels, five of which are plumbing. This model shows two: data root,
then sample, then expno. pdata/<procno> is exposed only when a dataset actually
has more than one procno (handled by the browser widget, not this model, since
it is a per-row detail rather than a tree level in the common case).

Lazy population: a node's children are not read until the node is asked about
(hasChildren/fetchMore), and even then only one level deep. Directory listing
and acqus probing genuinely touch the filesystem -- often a network share -- so
production wiring should route calls through a worker (see DatasetPopulator
below) rather than call scan_for_datasets/probe on the GUI thread directly.
This module's own fetchMore is synchronous, which is correct and fully
testable in isolation; DatasetPopulator is the thin async wrapper used by the
real application.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import (
    QAbstractItemModel,
    QMimeData,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
)

from ..domain.paths import expnos_in, scan_for_datasets
from ..domain.ports import DataRoot, DatasetInfo
from ..domain.project import Dimensionality
from ..infrastructure.nmrglue_reader import NmrglueReader

MIME_DATASET = "application/x-helspin-dataset"

COLUMNS = ("Name", "PULPROG", "Nucleus", "Dim", "Date")
COL_NAME, COL_PULPROG, COL_NUCLEUS, COL_DIM, COL_DATE = range(5)


class NodeKind(Enum):
    DATA_ROOT = auto()
    SAMPLE = auto()
    EXPNO = auto()


@dataclass
class Node:
    kind: NodeKind
    path: Path
    display_name: str
    parent: "Node | None" = None
    children: "list[Node] | None" = None   # None = not yet fetched
    data_root: DataRoot | None = None      # set on DATA_ROOT nodes
    info: DatasetInfo | None = None        # set on EXPNO nodes once probed
    failed: bool = False                   # unreachable mount, permission, etc.
    error: str = ""

    @property
    def is_fetched(self) -> bool:
        return self.children is not None

    @property
    def row(self) -> int:
        if self.parent is None or self.parent.children is None:
            return 0
        return self.parent.children.index(self)


def _owning_root(node: Node) -> DataRoot | None:
    """Walk up to find the DataRoot a node belongs to, for barcode_key etc."""
    current: Node | None = node
    while current is not None:
        if current.kind is NodeKind.DATA_ROOT:
            return current.data_root
        current = current.parent
    return None


class DatasetTreeModel(QAbstractItemModel):
    """Flattened data-root -> sample -> expno tree.

    reader is injected so tests can swap in a fake without touching nmrglue,
    and so the model has no import-time dependency beyond the domain ports.
    """

    scanFailed = Signal(str, str)   # path, error message -- for a status bar

    def __init__(self, data_roots: list[DataRoot], reader=None, parent=None):
        super().__init__(parent)
        self._reader = reader or NmrglueReader()
        self._roots: list[Node] = [
            Node(
                kind=NodeKind.DATA_ROOT,
                path=root.path,
                display_name=root.name,
                data_root=root,
            )
            for root in data_roots
            if root.enabled
        ]

    # -- adding roots after construction -------------------------------------

    def add_data_root(self, root: DataRoot) -> Node:
        """Append a new data root, e.g. from a 'File > Add data root...' action.

        First-run configuration (handoff 4.3.0) requires exactly one thing:
        the data root. This is what that action calls.
        """
        node = Node(
            kind=NodeKind.DATA_ROOT,
            path=root.path,
            display_name=root.name,
            data_root=root,
        )
        self.beginInsertRows(QModelIndex(), len(self._roots), len(self._roots))
        self._roots.append(node)
        self.endInsertRows()
        return node

    def remove_data_root(self, row: int) -> None:
        if not (0 <= row < len(self._roots)):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._roots[row]
        self.endRemoveRows()

    def data_roots(self) -> list[DataRoot]:
        return [n.data_root for n in self._roots if n.data_root is not None]

    def refresh(self, node: Node) -> None:
        """Clear a node's cached children so it can be re-scanned.

        There is currently no automatic file-watching: is_fetched is set
        once and never expires, so a data root or sample scanned before new
        files appeared on disk stays stale until the app restarts. This is
        the fix -- called from a "Refresh" action, not automatically, since
        automatic watching (QFileSystemWatcher) is unreliable over the
        network shares Bruker data commonly lives on (inotify/FSEvents often
        do not fire for SMB/NFS mounts at all).

        Only clears the cache; the caller (DatasetBrowser) is responsible
        for triggering the actual re-scan afterwards via DatasetPopulator,
        the same as initial expansion does -- this method's job is only the
        correctly-signalled removal of stale rows.
        """
        if node.kind is NodeKind.EXPNO:
            return   # leaf: nothing to refresh (see handoff decision 13 for
                      # re-probing an existing expno's acqus separately)
        index = self._index_for(node)
        if node.children:
            self.beginRemoveRows(index, 0, len(node.children) - 1)
            node.children = None
            self.endRemoveRows()
        else:
            node.children = None
        node.failed = False
        node.error = ""

    # -- tree structure -------------------------------------------------

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        siblings = self._roots if not parent.isValid() else self._children_of(parent)
        if row >= len(siblings):
            return QModelIndex()
        return self.createIndex(row, column, siblings[row])

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node: Node = index.internalPointer()
        if node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return len(self._roots)
        node: Node = parent.internalPointer()
        if node.kind is NodeKind.EXPNO:
            return 0
        return len(node.children) if node.children is not None else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def _children_of(self, parent: QModelIndex) -> list[Node]:
        node: Node = parent.internalPointer()
        return node.children or []

    # -- lazy population --------------------------------------------------

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return bool(self._roots)
        node: Node = parent.internalPointer()
        if node.kind is NodeKind.EXPNO:
            return False
        if node.is_fetched:
            return len(node.children) > 0
        return not node.failed   # assume expandable until proven otherwise

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if not parent.isValid():
            return False
        node: Node = parent.internalPointer()
        return node.kind is not NodeKind.EXPNO and not node.is_fetched

    def fetchMore(self, parent: QModelIndex) -> None:
        """Synchronous population of exactly one node's immediate children.

        Correct and fully testable on its own. Production code should not
        call this directly on a network path from the GUI thread -- see
        DatasetPopulator, which runs the same scan on a worker and calls back
        into applyChildren().
        """
        if not parent.isValid():
            return
        node: Node = parent.internalPointer()
        try:
            children = self._scan_children(node)
        except OSError as exc:
            node.failed = True
            node.error = str(exc)
            node.children = []
            self.scanFailed.emit(str(node.path), str(exc))
            return
        self.applyChildren(node, children)

    def applyChildren(self, node: Node, children: list[Node]) -> None:
        """Install pre-scanned children under node, with correct model signals.

        Split out from fetchMore so an async worker can call this on the GUI
        thread once a background scan completes.
        """
        index = self._index_for(node)
        self.beginInsertRows(index, 0, max(len(children) - 1, 0))
        node.children = children
        node.failed = False
        self.endInsertRows()

    def _scan_children(self, node: Node) -> list[Node]:
        if node.kind is NodeKind.DATA_ROOT:
            return self._scan_data_root(node)
        if node.kind is NodeKind.SAMPLE:
            return self._scan_sample(node)
        return []

    def _scan_data_root(self, node: Node) -> list[Node]:
        """Sample directories under a root, found via a bounded scan.

        scan_for_datasets returns expnos, not sample dirs; the SAMPLE nodes
        this method returns are their parent directories, grouped. Bounded by
        the same depth and count limits as the underlying scan -- a data root
        with more samples than the limit shows fewer samples, not a stall.
        (An earlier draft of this method also did an unbounded rglob() as a
        fallback for samples the bounded scan missed; that defeated the whole
        point of bounding the scan and has been removed. If the bounded scan
        misses a sample, the fix is to raise the limit in preferences, not to
        walk the entire tree unconditionally.)
        """
        found = scan_for_datasets(node.path)
        samples: dict[Path, None] = {}
        for expno_path in found:
            samples.setdefault(expno_path.parent, None)
        return [
            Node(
                kind=NodeKind.SAMPLE,
                path=p,
                display_name=p.name,
                parent=node,
            )
            for p in sorted(samples.keys())
        ]

    def _scan_sample(self, node: Node) -> list[Node]:
        """Expnos under a sample, probed immediately so PULPROG etc. show
        without a second lazy step -- the handoff calls for reading acqus
        only for expnos that become visible, and these just became visible."""
        out = []
        for expno_path in expnos_in(node.path):
            child = Node(
                kind=NodeKind.EXPNO,
                path=expno_path,
                display_name=expno_path.name,
                parent=node,
            )
            try:
                child.info = self._reader.probe(
                    expno_path, data_root=_owning_root(node)
                )
            except Exception as exc:  # noqa: BLE001 -- never lose the row
                child.failed = True
                child.error = str(exc)
            out.append(child)
        return out

    # -- display ------------------------------------------------------------

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        node: Node = index.internalPointer()
        col = index.column()

        if role == Qt.ToolTipRole and node.failed:
            return node.error or "Could not read this location."

        if role != Qt.DisplayRole:
            return None

        if node.kind is not NodeKind.EXPNO:
            return node.display_name if col == COL_NAME else None

        info = node.info
        if col == COL_NAME:
            return node.display_name
        if info is None:
            return "\u2014" if node.failed else None
        if col == COL_PULPROG:
            return info.pulse_program
        if col == COL_NUCLEUS:
            return info.nucleus
        if col == COL_DIM:
            return "1D" if info.dimensionality is Dimensionality.ONE_D else "2D"
        if col == COL_DATE:
            return info.date
        return None

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        base = super().flags(index)
        if not index.isValid():
            return base
        node: Node = index.internalPointer()
        if node.kind is NodeKind.EXPNO and not node.failed:
            return base | Qt.ItemIsDragEnabled
        return base

    def _index_for(self, node: Node) -> QModelIndex:
        if node.parent is None:
            return QModelIndex()
        return self.createIndex(node.row, 0, node)

    # -- drag source ---------------------------------------------------------

    def mimeTypes(self) -> list[str]:
        return [MIME_DATASET]

    def mimeData(self, indexes) -> QMimeData:
        """Multi-select drag payload: one entry per selected expno row.

        Dimensionality is already known from each node's probed info, so the
        drop target can validate without any file read during the drag.
        """
        import json

        seen_rows = set()
        payload = []
        for index in indexes:
            if index.column() != 0:
                continue
            node: Node = index.internalPointer()
            if node.kind is not NodeKind.EXPNO or node.info is None:
                continue
            if id(node) in seen_rows:
                continue
            seen_rows.add(id(node))
            payload.append(
                {
                    "path": str(node.path),
                    "dimensionality": node.info.dimensionality.value,
                    "label": node.display_name,
                }
            )
        mime = QMimeData()
        mime.setData(MIME_DATASET, json.dumps(payload).encode("utf-8"))
        return mime


class _PopulateSignals(QObject):
    finished = Signal(object, list)   # node, children
    failed = Signal(object, str)


class _PopulateTask(QRunnable):
    """Runs a node's directory scan off the GUI thread."""

    def __init__(self, model: DatasetTreeModel, node: Node):
        super().__init__()
        self._model = model
        self._node = node
        self.signals = _PopulateSignals()

    def run(self) -> None:
        try:
            children = self._model._scan_children(self._node)
        except OSError as exc:
            self.signals.failed.emit(self._node, str(exc))
            return
        self.signals.finished.emit(self._node, children)


class DatasetPopulator(QObject):
    """Async wrapper: schedules fetchMore's scan on a QThreadPool and applies
    the result back on the GUI thread via applyChildren().

    Kept separate from DatasetTreeModel so the model's own logic stays
    synchronous and trivially testable; this class is what production wiring
    uses instead of calling model.fetchMore() directly.
    """

    def __init__(self, model: DatasetTreeModel, pool: QThreadPool | None = None):
        super().__init__()
        self._model = model
        self._pool = pool or QThreadPool.globalInstance()

    def populate(self, node: Node) -> None:
        task = _PopulateTask(self._model, node)
        task.signals.finished.connect(self._on_finished)
        task.signals.failed.connect(self._on_failed)
        self._pool.start(task)

    def _on_finished(self, node: Node, children: list[Node]) -> None:
        self._model.applyChildren(node, children)

    def _on_failed(self, node: Node, message: str) -> None:
        node.failed = True
        node.error = message
        self._model.applyChildren(node, [])
        self._model.scanFailed.emit(str(node.path), message)
