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

from ..domain.paths import expnos_with_data, scan_for_datasets
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
    probe_pending: bool = False            # EXPNO row shown, acqus not read yet
    fetch_in_flight: bool = False          # async child scan scheduled, not back yet
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


def _dataset_label(node: "Node") -> str:
    """Human-readable identity for an expno: "<sample>/<expno>".

    A bare expno number is meaningless in a plot legend -- "1" does not say
    which sample it came from, and every sample has an expno 1. Qualifying it
    with the parent sample name is what makes an overlay readable.
    """
    sample = node.parent.display_name if node.parent is not None else ""
    return f"{sample}/{node.display_name}" if sample else node.display_name


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
        self._fetch_scheduler = None   # set by DatasetBrowser for async loading
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
        """Re-scan a node and MERGE the result into its existing children.

        Adds children that newly appeared on disk and removes ones that
        vanished, but leaves existing child nodes -- and, crucially, their
        expansion state and any already-probed metadata -- untouched. This is
        what makes refresh non-destructive: an earlier version cleared all
        children and re-scanned from scratch, which collapsed every expanded
        folder because the tree's expansion state was keyed to nodes that no
        longer existed. Merging sidesteps that entirely.

        Refresh is explicit (a menu / toolbar action), never automatic:
        QFileSystemWatcher-style watching is unreliable over the SMB/NFS
        shares Bruker data commonly lives on (inotify/FSEvents frequently do
        not fire for network mounts).

        A not-yet-fetched node is simply left alone -- there is nothing to
        merge into, and it will scan fresh when first expanded. Leaves
        (expnos) have no children to refresh.
        """
        if node.kind is NodeKind.EXPNO:
            return
        if node.children is None:
            # Never fetched: nothing to merge, it will scan fresh on expand.
            # But still clear any stale failed state so a data root that failed
            # to scan once (e.g. mount was temporarily down) is not stuck
            # showing an error after a refresh -- the next expand retries.
            node.failed = False
            node.error = ""
            return

        parent_index = self._index_for(node)
        fresh = self._scan_children(node)
        fresh_by_path = {c.path: c for c in fresh}
        existing_by_path = {c.path: c for c in node.children}

        # Remove children whose directories are gone. Iterate high row to low
        # so each removal does not shift the rows still to be removed.
        for row in range(len(node.children) - 1, -1, -1):
            child = node.children[row]
            if child.path not in fresh_by_path:
                self.beginRemoveRows(parent_index, row, row)
                del node.children[row]
                self.endRemoveRows()

        # Append children that are new on disk, preserving the scan's order by
        # inserting each at the position it would occupy. Simpler and safe:
        # append new ones at the end (the scan order is not otherwise
        # guaranteed to be meaningful, and samples/expnos sort in the view).
        new_children = [c for c in fresh if c.path not in existing_by_path]
        if new_children:
            start = len(node.children)
            self.beginInsertRows(parent_index, start, start + len(new_children) - 1)
            node.children.extend(new_children)
            self.endInsertRows()

        node.failed = False
        node.error = ""
        return new_children

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

    def set_fetch_scheduler(self, scheduler) -> None:
        """Install an async fetcher, called as scheduler(node).

        When set (DatasetBrowser sets it to DatasetPopulator.populate),
        fetchMore hands the directory scan to it instead of running the scan
        on the GUI thread. When unset, fetchMore falls back to the synchronous
        scan, which is what the model's own tests use.
        """
        self._fetch_scheduler = scheduler

    def canFetchMore(self, parent: QModelIndex) -> bool:
        """True for a node whose children have not been loaded yet.

        This MUST report True for unfetched nodes. QSortFilterProxyModel
        derives hasChildren() from rowCount() when canFetchMore() is False,
        so returning False here left every unexpanded sample with rowCount 0
        and therefore no expander arrow in the view -- the tree could not be
        opened at all. (That was a real regression in 0.0.2.) The blocking
        problem it was trying to solve is handled in fetchMore instead, by
        delegating the scan to the async populator.
        """
        if not parent.isValid():
            return False
        node: Node = parent.internalPointer()
        if node is None or node.kind is NodeKind.EXPNO:
            return False
        return not node.is_fetched and not node.fetch_in_flight

    def fetchMore(self, parent: QModelIndex) -> None:
        """Load one node's immediate children.

        With a fetch scheduler installed (production), this returns
        immediately after handing the scan to a worker thread; the rows appear
        later via applyChildren, which also schedules the per-row metadata
        probes. Without one (tests), it performs the scan synchronously.
        """
        if not parent.isValid():
            return
        node: Node = parent.internalPointer()
        if node is None or node.is_fetched or node.fetch_in_flight:
            return

        if self._fetch_scheduler is not None:
            node.fetch_in_flight = True
            self._fetch_scheduler(node)
            return

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
        node.fetch_in_flight = False
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
        """Expno rows from directory STRUCTURE ALONE -- no acqus reads.

        This is the fix for "expanding a sample takes forever": the old
        version called reader.probe() (a full acqus file read) once per
        expno, sequentially, before returning a single row. Over a network
        share that is one blocking round-trip per experiment, so a sample
        with 30 expnos meant 30 sequential network reads before anything
        appeared.

        expnos_in() is a single directory listing -- it identifies which
        subdirectories are expnos structurally (integer name + acqus
        present) without opening acqus. Each row is marked probe_pending;
        the PULPROG / nucleus / date columns fill in afterwards as
        per-row background probes complete (see DatasetPopulator.probe_row).
        The rows themselves appear instantly.
        """
        out = []
        for expno_path in expnos_with_data(node.path):
            out.append(
                Node(
                    kind=NodeKind.EXPNO,
                    path=expno_path,
                    display_name=expno_path.name,
                    parent=node,
                    probe_pending=True,
                )
            )
        return out

    def probe_node(self, node: Node) -> None:
        """Probe one expno synchronously ON THE GUI THREAD, then refresh its row.

        This does two things that MUST happen on the GUI thread: it mutates
        model state (node.info / node.probe_pending) and it emits dataChanged.
        Qt item models are not thread-safe, so neither may happen on a worker.
        For the async path, the worker calls read_probe_result() (pure I/O, no
        model mutation) and this method is invoked on the GUI thread with the
        result via apply_probe_result(). Called directly, it is the simple
        synchronous version used by tests and any non-threaded caller.
        """
        if node.kind is not NodeKind.EXPNO or not node.probe_pending:
            return
        info, error = self.read_probe_result(node)
        self.apply_probe_result(node, info, error)

    def read_probe_result(self, node: Node):
        """Pure I/O: read one expno's acqus and return (info, error).

        SAFE TO CALL ON A WORKER THREAD -- it touches nothing on the model,
        only reads from disk via the reader. The returned value is handed
        back to the GUI thread by apply_probe_result(). This split is the fix
        for metadata columns never filling in: the previous version emitted
        dataChanged and mutated node state from the worker thread, which Qt
        models do not allow, so the view silently never updated.
        """
        try:
            info = self._reader.probe(node.path, data_root=_owning_root(node))
            return info, ""
        except Exception as exc:  # noqa: BLE001 -- never lose the row
            return None, str(exc)

    def apply_probe_result(self, node: Node, info, error: str) -> None:
        """Install a probe result and refresh the row. GUI THREAD ONLY.

        Mutates node state and emits dataChanged, both of which must happen on
        the GUI thread. Reached either directly (synchronous probe_node) or
        via the worker's done signal, which Qt delivers on the GUI thread
        because DatasetPopulator (a QObject living on the GUI thread) is the
        receiver.
        """
        if not node.probe_pending:
            return
        node.info = info
        node.failed = info is None
        node.error = error
        node.probe_pending = False

        index = self._index_for(node)
        if index.isValid():
            last_col = self.createIndex(index.row(), len(COLUMNS) - 1, node)
            self.dataChanged.emit(index, last_col)

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
            # Structure is known (the row is shown), but acqus has not been
            # read yet. A pending probe shows a subtle placeholder in the
            # metadata columns rather than blank -- so the user can see the
            # row exists and its details are loading, not that they are
            # absent. A failed probe shows an em dash.
            if node.failed:
                return "\u2014"
            if node.probe_pending and col != COL_NAME:
                return "\u2026"   # horizontal ellipsis: "loading"
            return None
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
        """The model index that points at `node`.

        A data-root node has no parent Node, but it is NOT the invisible root
        -- it is a real, visible top-level row living in self._roots. Returning
        an empty QModelIndex() for it (as an earlier version did) made every
        operation keyed on a data root's own index silently target the
        invisible root instead, which is exactly why refreshing a data root
        did nothing. Its index must be built from its row within self._roots.
        """
        if node.parent is None:
            try:
                root_row = self._roots.index(node)
            except ValueError:
                return QModelIndex()   # not a known root: treat as invisible root
            return self.createIndex(root_row, 0, node)
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
                    # A bare expno ("1", "30") is useless in a legend -- it does
                    # not say WHICH sample. Qualify it with the parent sample
                    # name, which is what the user actually recognises.
                    "label": _dataset_label(node),
                    "sample": node.parent.display_name if node.parent else "",
                    "expno": node.display_name,
                    "pulse_program": node.info.pulse_program or "",
                    "nucleus": node.info.nucleus or "",
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


class _ProbeSignals(QObject):
    # Carries the pure-IO result back to the GUI thread. object fields so
    # DatasetInfo | None and str pass through without Qt meta-type fuss.
    done = Signal(object, object, str)   # node, info, error


class _ProbeTask(QRunnable):
    """Reads one expno's acqus OFF the GUI thread -- I/O only, no model touch.

    Critically, this worker does NOT mutate the model or emit model signals.
    It only reads from disk (read_probe_result) and emits its own `done`
    signal carrying the result. That signal is delivered on the GUI thread
    (the receiver, DatasetPopulator, lives there), where apply_probe_result
    then safely mutates node state and emits dataChanged. The earlier version
    mutated the node and emitted dataChanged from here, on the worker thread,
    which Qt item models forbid -- and the symptom was metadata columns that
    never filled in.
    """

    def __init__(self, model: DatasetTreeModel, node: Node):
        super().__init__()
        self._model = model
        self._node = node
        self.signals = _ProbeSignals()

    def run(self) -> None:
        info, error = self._model.read_probe_result(self._node)
        self.signals.done.emit(self._node, info, error)


class DatasetPopulator(QObject):
    """Async wrapper: schedules fetchMore's scan on a QThreadPool and applies
    the result back on the GUI thread via applyChildren().

    Kept separate from DatasetTreeModel so the model's own logic stays
    synchronous and trivially testable; this class is what production wiring
    uses instead of calling model.fetchMore() directly.

    Also schedules the per-row background probes that fill an expno's
    metadata columns after its (instant, structure-only) row appears.
    """

    probeError = Signal(str, str)   # path, message
    populated = Signal(object)      # node whose children were just applied

    def __init__(self, model: DatasetTreeModel, pool: QThreadPool | None = None):
        super().__init__()
        self._model = model
        self._pool = pool or QThreadPool.globalInstance()
        # QThreadPool does not keep Python-side references to the QRunnables it
        # runs, and a _ProbeTask owns the QObject that carries its result
        # signal. Without a reference held here, that signals object can be
        # garbage-collected before `done` is delivered, and the result is lost
        # -- another way the columns would silently never fill in. Tasks are
        # dropped from this set once their result has been applied.
        self._inflight: set[_ProbeTask] = set()

    def populate(self, node: Node) -> None:
        task = _PopulateTask(self._model, node)
        task.signals.finished.connect(self._on_finished)
        task.signals.failed.connect(self._on_failed)
        self._pool.start(task)

    def probe_row(self, node: Node) -> None:
        """Schedule a single expno's acqus read on the worker pool.

        No-op for anything that is not a pending expno probe, so it is safe
        to call indiscriminately (e.g. for every visible row) without
        checking first. The worker reads the file; the result is applied to
        the model on the GUI thread via _on_probe_done (Qt delivers the signal
        there because this populator lives on the GUI thread).
        """
        if node.kind is not NodeKind.EXPNO or not node.probe_pending:
            return
        task = _ProbeTask(self._model, node)
        task.signals.done.connect(self._on_probe_done)
        self._inflight.add(task)
        self._pool.start(task)

    def _on_probe_done(self, node: Node, info, error: str) -> None:
        # Runs on the GUI thread: safe to mutate the model and emit signals.
        self._model.apply_probe_result(node, info, error)
        self._inflight = {t for t in self._inflight if t._node is not node}

    def _on_finished(self, node: Node, children: list[Node]) -> None:
        self._model.applyChildren(node, children)
        # A sample's children are structure-only expno rows; kick off their
        # metadata probes now that they are visible. Each runs independently,
        # so columns fill in as reads complete rather than all-or-nothing.
        for child in children:
            self.probe_row(child)
        # Let the browser react now that this node's children exist -- e.g.
        # re-expand children that were open before a refresh. Deterministic
        # (fires after applyChildren), unlike racing rowsInserted.
        self.populated.emit(node)

    def _on_failed(self, node: Node, message: str) -> None:
        node.failed = True
        node.error = message
        node.fetch_in_flight = False
        self._model.applyChildren(node, [])
        self._model.scanFailed.emit(str(node.path), message)
