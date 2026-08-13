"""The dataset browser's tree model.

Deliberately NOT QFileSystemModel: tree nodes here are Bruker concepts (data
root, sample, expno), which do not map one-to-one onto directories. Structural
detection from domain.paths drives this model instead of path depth.

The real Bruker hierarchy is root/data/<user>/nmr/<sample>/<expno>/pdata/<procno>
-- seven levels, five of which are plumbing. This model shows two: data root,
then sample, then expno.

Where the data comes from
-------------------------
Rows are built from `core.dataset_index`, never from a fresh directory listing,
because a data root is normally a network share where every listing is a round
trip. The index has three tiers (see that module) and this model consumes them
in the same order:

* sample rows come from discovery, which is streamed -- `appendChildren` adds
  each batch as it arrives, so the tree fills while the walk is still running;
* an expno row needs only its sample's directory listing, so opening a sample
  whose detail is already cached costs NO filesystem access at all;
* the metadata columns come from cached per-expno values when the index has
  them, and otherwise from background probes (`probe_row` -> one file read).

A row is therefore drawn, and is draggable, before its metadata exists. That is
deliberate: waiting for metadata is what made the browser feel broken.

Threading rules, learned the hard way:
* never mutate the model or emit `dataChanged` from a worker thread;
* workers return plain data (`read_probe_result`, `scan_expnos`), and the GUI
  thread applies it (`apply_probe_result`, `apply_sample_detail`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor

from ..core import dataset_index as index_store
from ..domain.paths import expnos_with_data
from ..domain.ports import DataRoot, DatasetInfo
from ..domain.project import Dimensionality
from ..infrastructure.nmrglue_reader import NmrglueReader

MIME_DATASET = "application/x-helspin-dataset"

COLUMNS = ("Name", "PULPROG", "Nucleus", "Dim", "Date")
COL_NAME, COL_PULPROG, COL_NUCLEUS, COL_DIM, COL_DATE = range(5)

PENDING_TEXT = "\u2026"      # horizontal ellipsis: "being read"
FAILED_TEXT = "\u2014"       # em dash: "was read, nothing there"


class NodeKind(Enum):
    DATA_ROOT = auto()
    SAMPLE = auto()
    EXPNO = auto()


@dataclass(eq=False)
class Node:
    """One row.

    `eq=False` is load-bearing, not tidiness. The generated dataclass __eq__
    compares every field, and `parent`/`children` make the structure cyclic:
    comparing two nodes with the same path recursed through the parent, into
    its children, back into the node -- unbounded recursion, reachable by
    adding the same data root twice. Identity is also what a tree node
    actually means here, and it makes `children.index(node)` -- called for
    every row refresh -- a pointer comparison instead of a deep one.
    """

    kind: NodeKind
    path: Path
    display_name: str
    parent: Node | None = None
    children: list[Node] | None = None   # None = not yet fetched
    data_root: DataRoot | None = None      # set on DATA_ROOT nodes
    info: DatasetInfo | None = None        # set on EXPNO nodes once probed
    entry: object | None = None            # SampleEntry / ExpnoEntry from the index
    probe_pending: bool = False            # EXPNO row shown, metadata not read yet
    probe_in_flight: bool = False          # a metadata read is already queued
    fetch_in_flight: bool = False          # async child scan scheduled, not back yet
    failed: bool = False                   # unreachable mount, permission, etc.
    truncated: bool = False                # scan hit its sample limit
    error: str = ""
    # path -> child Node, rebuilt whenever the child count changes. Purely a
    # lookup accelerator; every mutation of `children` clears it.
    child_map: dict | None = field(default=None, repr=False)

    @property
    def is_fetched(self) -> bool:
        return self.children is not None

    @property
    def row(self) -> int:
        if self.parent is None or self.parent.children is None:
            return 0
        try:
            return self.parent.children.index(self)
        except ValueError:
            return 0

    # -- metadata, from the probe if it ran and the cached index if not ------

    @property
    def has_meta(self) -> bool:
        if self.info is not None:
            return True
        entry = self.entry
        return bool(entry is not None and getattr(entry, "meta", False))

    def meta_pulprog(self) -> str:
        if self.info is not None:
            return self.info.pulse_program
        return getattr(self.entry, "pulprog", "") or ""

    def meta_nucleus(self) -> str:
        if self.info is not None:
            return self.info.nucleus
        return getattr(self.entry, "nucleus", "") or ""

    def meta_date(self) -> str:
        if self.info is not None:
            return self.info.date
        return getattr(self.entry, "date", "") or ""

    @property
    def loadable(self) -> bool:
        """False only once the index has LOOKED and found no processed data.

        A row that cannot be plotted must say so before it is dragged, not
        after: dropping it produced a failure message that the status bar
        immediately overwrote, so the spectrum simply did not appear and
        nothing explained why.
        """
        entry = self.entry
        if entry is None:
            return True
        return getattr(entry, "loadable", True)

    def load_note(self) -> str:
        """Why this row is dimmed, in a sentence, or ""."""
        if self.failed and self.error:
            return self.error
        entry = self.entry
        if entry is not None and not self.loadable:
            return getattr(entry, "processed_note", "") or (
                "No processed spectrum here (no 1r or 2rr under pdata)."
            )
        return ""

    def dimensionality(self) -> int:
        """1, 2 or 0 when nothing yet knows.

        Structural flags from the index answer this for any dataset that still
        has its raw data, which is why a row can be dragged the moment it
        appears rather than only once its acqus has been read.
        """
        if self.info is not None:
            return (
                2 if self.info.dimensionality is Dimensionality.TWO_D else 1
            )
        entry = self.entry
        if entry is None:
            return 0
        return int(getattr(entry, "best_dim", 0) or 0)


def _dataset_label(node: Node) -> str:
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


def _root_node(node: Node) -> Node:
    current = node
    while current.parent is not None:
        current = current.parent
    return current


class DatasetTreeModel(QAbstractItemModel):
    """Flattened data-root -> sample -> expno tree.

    reader is injected so tests can swap in a fake without touching nmrglue,
    and so the model has no import-time dependency beyond the domain ports.
    """

    scanFailed = Signal(str, str)      # path, error message -- for a status bar
    scanTruncated = Signal(str, int)   # path, how many samples were kept

    def __init__(self, data_roots: list[DataRoot], reader=None, parent=None):
        super().__init__(parent)
        self._reader = reader or NmrglueReader()
        self._fetch_scheduler = None   # set by DatasetBrowser for async loading
        # One index per data root, loaded from the on-disk cache when
        # available. This is what makes a second open instant.
        self._indexes: dict[str, index_store.RootIndex] = {}
        self._dirty_roots: set[str] = set()
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
        """Append a new data root, e.g. from a 'File > Add data root...' action."""
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
        node = self._roots[row]
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._roots[row]
        self.endRemoveRows()
        # Drop the in-memory index too: keeping it would silently resurrect
        # the old sample list if the same root were added back, and it can be
        # tens of megabytes for a big share.
        self._indexes.pop(str(node.path), None)
        self._dirty_roots.discard(str(node.path))

    def data_roots(self) -> list[DataRoot]:
        return [n.data_root for n in self._roots if n.data_root is not None]

    def root_nodes(self) -> list[Node]:
        return list(self._roots)

    # -- the index -----------------------------------------------------------

    def index_for(self, root_path, rebuild: bool = False):
        """The index for a data root: memory, else cache, else a full build.

        The full build walks every experiment directory, which is minutes on a
        large share -- the application avoids it entirely by streaming
        discovery through `install_index`/`append_samples` instead. This
        remains the synchronous path used by tests and by any caller that
        genuinely wants the whole thing in one call.
        """
        key = str(root_path)
        if not rebuild and key in self._indexes:
            return self._indexes[key]
        idx = None if rebuild else index_store.load_index(root_path)
        if idx is None:
            idx = index_store.build_index(root_path)
            index_store.save_index(idx)
        self._indexes[key] = idx
        return idx

    def cached_index(self, root_path):
        """The in-memory index, or None. Never touches the disk."""
        return self._indexes.get(str(root_path))

    def install_index(self, root_path, index) -> None:
        """Adopt an index built on a worker thread. GUI THREAD ONLY."""
        self._indexes[str(root_path)] = index

    def mark_dirty(self, root_path) -> None:
        """Note that this root's index has changed and should be re-cached."""
        self._dirty_roots.add(str(root_path))

    def take_dirty_roots(self) -> list[str]:
        dirty, self._dirty_roots = list(self._dirty_roots), set()
        return dirty

    def entry_for(self, node: Node):
        """The index entry behind a SAMPLE node, or None."""
        if node.kind is not NodeKind.SAMPLE:
            return None
        if node.entry is not None:
            return node.entry
        idx = self.cached_index(_root_node(node).path)
        if idx is None:
            return None
        node.entry = idx.find(str(node.path))
        return node.entry

    @property
    def reader(self):
        """The injected SpectrumReader. Worker tasks need it and reaching into
        a private attribute from another class is how that gets renamed out
        from under them."""
        return self._reader

    def is_live(self, node: Node) -> bool:
        """Is this node still part of the tree the view is showing?

        Every asynchronous result must ask. A walk of a share can easily
        outlive the data root that started it -- the user removes the root, or
        refreshes it, while the worker is still running -- and applying that
        result would insert rows under a node the model no longer owns.
        _index_for returns an INVALID index for an unknown root, and
        beginInsertRows on an invalid parent inserts at the INVISIBLE root, so
        stale results would have appeared as phantom top-level rows.
        """
        current = node
        seen = 0
        while current.parent is not None:
            current = current.parent
            seen += 1
            if seen > 8:            # defensive: a cycle must not hang the GUI
                return False
        if current not in self._roots:
            return False
        # A node whose parent has since dropped it (a refresh removed the
        # sample) is not live either, even though its root still is.
        walk = node
        while walk.parent is not None:
            siblings = walk.parent.children
            if siblings is None or walk not in siblings:
                return False
            walk = walk.parent
        return True

    # -- refresh -------------------------------------------------------------

    def refresh(self, node: Node) -> list[Node] | None:
        """Re-scan a node and MERGE the result into its existing children.

        Adds children that newly appeared on disk and removes ones that
        vanished, but leaves existing child nodes -- and, crucially, their
        expansion state and any already-probed metadata -- untouched. An
        earlier version cleared all children and re-scanned, which collapsed
        every expanded folder because expansion state was keyed to nodes that
        no longer existed.

        Refresh is explicit (a menu / toolbar action), never automatic:
        QFileSystemWatcher-style watching is unreliable over the SMB/NFS
        shares Bruker data commonly lives on.
        """
        if node.kind is NodeKind.EXPNO:
            return None
        if node.kind is NodeKind.DATA_ROOT:
            # A refresh is the user saying the cache is out of date, so it is
            # rebuilt rather than trusted.
            self.index_for(node.path, rebuild=True)
        elif node.kind is NodeKind.SAMPLE:
            idx = self.cached_index(_root_node(node).path)
            entry = idx.find(str(node.path)) if idx is not None else None
            if entry is not None:
                index_store.refresh_sample(entry)
                self.mark_dirty(_root_node(node).path)

        if node.children is None:
            # Never fetched: nothing to merge, it will scan fresh on expand.
            # Still clear stale failure state so a root that failed once (a
            # mount that was down) is not stuck showing an error.
            node.failed = False
            node.error = ""
            return None

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
        node.child_map = None

        new_children = [c for c in fresh if c.path not in existing_by_path]
        if new_children:
            start = len(node.children)
            self.beginInsertRows(parent_index, start, start + len(new_children) - 1)
            node.children.extend(new_children)
            node.child_map = None
            self.endInsertRows()

        # Existing rows keep their identity but adopt the refreshed index
        # entry, so metadata read after this point lands in the cached object
        # the filter and the next session read from.
        for child in node.children:
            fresh_child = fresh_by_path.get(child.path)
            if fresh_child is not None and fresh_child.entry is not None:
                child.entry = fresh_child.entry

        node.failed = False
        node.error = ""
        return new_children

    # -- tree structure -------------------------------------------------

    def index(self, row: int, column: int,
              parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        siblings = self._roots if not parent.isValid() else self._children_of(parent)
        if row >= len(siblings):
            return QModelIndex()
        return self.createIndex(row, column, siblings[row])

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:
        """The parent index.

        Built through _index_for rather than createIndex(node.parent.row, ...)
        because a DATA_ROOT node has no parent Node, so its `row` property
        reports 0 whatever its real position in self._roots is. With two data
        roots configured, that handed Qt row 0 for every sample's parent, and
        the proxy mapped the second root's children onto the first.
        """
        if not index.isValid():
            return QModelIndex()
        node: Node = index.internalPointer()
        if node is None or node.parent is None:
            return QModelIndex()
        return self._index_for(node.parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        if not parent.isValid():
            return len(self._roots)
        node: Node = parent.internalPointer()
        if node is None or node.kind is NodeKind.EXPNO:
            return 0
        return len(node.children) if node.children is not None else 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(COLUMNS)

    def _children_of(self, parent: QModelIndex) -> list[Node]:
        node: Node = parent.internalPointer()
        if node is None:
            return []
        return node.children or []

    # -- lazy population --------------------------------------------------

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        if not parent.isValid():
            return bool(self._roots)
        node: Node = parent.internalPointer()
        if node is None or node.kind is NodeKind.EXPNO:
            return False
        if node.is_fetched:
            return len(node.children) > 0
        return not node.failed   # assume expandable until proven otherwise

    def set_fetch_scheduler(self, scheduler) -> None:
        """Install an async fetcher, called as scheduler(node).

        When set (DatasetBrowser sets it to DatasetPopulator.populate),
        fetchMore hands the work to it instead of running it on the GUI
        thread. When unset, fetchMore falls back to the synchronous scan,
        which is what the model's own tests use.
        """
        self._fetch_scheduler = scheduler

    def canFetchMore(self, parent: QModelIndex) -> bool:
        """True for a node whose children have not been loaded yet.

        This MUST report True for unfetched nodes. QSortFilterProxyModel
        derives hasChildren() from rowCount() when canFetchMore() is False,
        so returning False here left every unexpanded sample with rowCount 0
        and therefore no expander arrow -- the tree could not be opened at
        all. (A real regression in 0.0.2.) The blocking problem it was trying
        to solve is handled in fetchMore instead, by delegating to the async
        populator.
        """
        if not parent.isValid():
            return False
        node: Node = parent.internalPointer()
        if node is None or node.kind is NodeKind.EXPNO:
            return False
        return not node.is_fetched and not node.fetch_in_flight

    def fetchMore(self, parent: QModelIndex) -> None:
        """Load one node's children.

        With a fetch scheduler installed (production), this returns
        immediately after handing the work to the populator; rows appear later
        via applyChildren/appendChildren. Without one (tests), it scans
        synchronously.
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

        Split out from fetchMore so an async worker's result can be applied on
        the GUI thread.
        """
        index = self._index_for(node)
        if children:
            self.beginInsertRows(index, 0, len(children) - 1)
            node.children = children
            node.child_map = None
            node.failed = False
            node.fetch_in_flight = False
            self.endInsertRows()
            return

        # No children. beginInsertRows(index, 0, 0) here (as an earlier
        # version did unconditionally, via max(len-1, 0)) announces a row that
        # is never inserted, leaving the view's idea of the row count one
        # ahead of the model's.
        node.children = []
        node.child_map = None
        node.failed = False
        node.fetch_in_flight = False
        if index.isValid():
            # Nudge the view to drop the expander arrow it drew on the
            # assumption that an unfetched node has children.
            self.dataChanged.emit(index, index)

    def appendChildren(self, node: Node, children: list[Node]) -> None:
        """Add another batch of children to an already-populated node.

        Discovery streams samples in batches so the tree fills while the walk
        is still running; this is what puts each batch on screen.
        """
        if not children:
            return
        if node.children is None:
            self.applyChildren(node, children)
            return
        index = self._index_for(node)
        start = len(node.children)
        self.beginInsertRows(index, start, start + len(children) - 1)
        node.children.extend(children)
        node.child_map = None
        self.endInsertRows()

    # -- building rows from the index ---------------------------------------

    def make_sample_nodes(self, root_node: Node, paths) -> list[Node]:
        """Sample rows for freshly discovered paths, recorded in the index.

        Paths already present are skipped, so this is safe to call with
        overlapping batches (a re-discovery pass over a partially cached
        root does exactly that).
        """
        idx = self.cached_index(root_node.path)
        if idx is None:
            idx = index_store.RootIndex(root=str(root_node.path))
            self.install_index(root_node.path, idx)

        known = {str(c.path) for c in (root_node.children or [])}
        out: list[Node] = []
        for path in paths:
            key = str(path)
            entry = idx.find(key)
            if entry is None:
                entry = index_store.SampleEntry(path=key)
                idx.add(entry)
            if key in known:
                continue
            out.append(
                Node(
                    kind=NodeKind.SAMPLE,
                    path=Path(key),
                    display_name=Path(key).name,
                    parent=root_node,
                    entry=entry,
                )
            )
        return out

    def expno_nodes_for(self, sample_node: Node, entry) -> list[Node]:
        """Expno rows for one sample's index entry -- no filesystem access.

        Experiments without a pdata directory are omitted: only processed data
        can be displayed, and listing the rest only produces "cannot find"
        errors on drop.
        """
        return [
            Node(
                kind=NodeKind.EXPNO,
                path=Path(entry.path) / expno.name,
                display_name=expno.name,
                parent=sample_node,
                entry=expno,
                # Cached metadata means the columns are already filled, so
                # the row needs no probe at all -- that is what makes a
                # second session cost zero reads.
                probe_pending=not expno.meta,
            )
            for expno in entry.expnos
            if expno.displayable
        ]

    def merge_expnos(self, entry, expnos: list, sample_path: str) -> None:
        """Adopt a fresh directory listing for one sample's index entry.

        Metadata costs a file read each and does not become wrong because an
        experiment appeared next door, so values already read are carried
        across to the new listing instead of being discarded and read again.
        """
        previous = {e.name: e for e in entry.expnos}
        for expno in expnos:
            old = previous.get(expno.name)
            if old is not None and old.meta and not expno.meta:
                expno.meta = True
                expno.pulprog = old.pulprog
                expno.nucleus = old.nucleus
                expno.solvent = old.solvent
                expno.date = old.date
                expno.dim = old.dim
                expno.error = old.error
                # has_processed is NOT carried over -- see refresh_sample.
        entry.expnos = expnos
        entry.detailed = True
        entry.mtime = index_store.sample_mtime(sample_path)
        entry.invalidate()

    def apply_sample_detail(self, node: Node, expnos: list) -> list[Node]:
        """Install a worker's directory listing for one sample. GUI THREAD."""
        root = _root_node(node)
        idx = self.cached_index(root.path)
        entry = idx.find(str(node.path)) if idx is not None else None
        if entry is None:
            entry = index_store.SampleEntry(path=str(node.path))
            if idx is not None:
                idx.add(entry)
        self.merge_expnos(entry, expnos, str(node.path))
        node.entry = entry
        self.mark_dirty(root.path)
        return self.expno_nodes_for(node, entry)

    def merge_sample_children(self, node: Node) -> list[Node]:
        """Reconcile an expanded sample's rows with its (just re-read) entry.

        Returns the rows that are new. Existing rows keep their identity, so
        selection, expansion and already-read metadata survive a refresh --
        the same reason the model's synchronous refresh() merges rather than
        rebuilds.
        """
        entry = node.entry
        if entry is None or node.children is None:
            return []
        parent_index = self._index_for(node)
        wanted = {e.name: e for e in entry.expnos if e.displayable}

        for row in range(len(node.children) - 1, -1, -1):
            child = node.children[row]
            if child.display_name not in wanted:
                self.beginRemoveRows(parent_index, row, row)
                del node.children[row]
                self.endRemoveRows()
        node.child_map = None

        existing = {c.display_name for c in node.children}
        for child in node.children:
            expno = wanted.get(child.display_name)
            if expno is not None:
                child.entry = expno
                if expno.meta:
                    child.probe_pending = False

        fresh = [
            Node(
                kind=NodeKind.EXPNO,
                path=Path(entry.path) / expno.name,
                display_name=expno.name,
                parent=node,
                entry=expno,
                probe_pending=not expno.meta,
            )
            for name, expno in wanted.items()
            if name not in existing
        ]
        if fresh:
            start = len(node.children)
            self.beginInsertRows(parent_index, start, start + len(fresh) - 1)
            node.children.extend(fresh)
            self.endInsertRows()
            node.child_map = None
        return fresh

    def drop_missing_samples(self, root_node: Node, keep: set) -> None:
        """Remove sample rows whose directories are gone. GUI THREAD.

        Only ever called after a COMPLETE walk: a cancelled or truncated one
        saying "I did not see it" is not the same as the sample being gone,
        and removing rows on that basis would make a big root flicker.
        """
        if root_node.children is None:
            return
        index = self._index_for(root_node)
        cached = self.cached_index(root_node.path)
        for row in range(len(root_node.children) - 1, -1, -1):
            child = root_node.children[row]
            if str(child.path) in keep:
                continue
            self.beginRemoveRows(index, row, row)
            del root_node.children[row]
            self.endRemoveRows()
            if cached is not None:
                cached.drop(str(child.path))
        root_node.child_map = None

    def child_node(self, parent: Node, path: str) -> Node | None:
        """One child by path, via a lazily built map.

        The background indexer asks this once per sample it finishes, and a
        linear scan of 5000 sample rows each time is the kind of quiet
        quadratic cost that only shows up on the roots that matter.
        """
        children = parent.children
        if not children:
            return None
        if parent.child_map is None or len(parent.child_map) != len(children):
            parent.child_map = {str(c.path): c for c in children}
        return parent.child_map.get(str(path))

    def refresh_sample_rows(self, root_node: Node, sample_path: str,
                            entry) -> None:
        """Push freshly indexed metadata into rows already on screen.

        Only samples the user expanded have Node objects at all, so this
        touches very little -- but without it a row the background pass
        reached first would show the loading placeholder for ever, because
        nothing else would ever clear its pending flag.
        """
        sample_node = self.child_node(root_node, sample_path)
        if sample_node is None or not sample_node.is_fetched:
            return
        by_name = {e.name: e for e in entry.expnos}
        for row in sample_node.children or []:
            expno = by_name.get(row.display_name)
            if expno is None:
                continue
            row.entry = expno
            if expno.meta and row.probe_pending:
                row.probe_pending = False
                row.failed = bool(expno.error)
                row.error = expno.error
                index = self._index_for(row)
                if index.isValid():
                    last = self.createIndex(index.row(), len(COLUMNS) - 1, row)
                    self.dataChanged.emit(index, last)

    # -- synchronous scanning (fallback / tests) -----------------------------

    def _scan_children(self, node: Node) -> list[Node]:
        if node.kind is NodeKind.DATA_ROOT:
            return self._scan_data_root(node)
        if node.kind is NodeKind.SAMPLE:
            return self._scan_sample(node)
        return []

    def _scan_data_root(self, node: Node) -> list[Node]:
        """Sample directories under a root, from the index."""
        idx = self.index_for(node.path)
        node.truncated = bool(getattr(idx, "truncated", False))
        if node.truncated:
            self.scanTruncated.emit(str(node.path), len(idx.samples))
        return [
            Node(
                kind=NodeKind.SAMPLE,
                path=Path(sample.path),
                display_name=Path(sample.path).name,
                parent=node,
                entry=sample,
            )
            for sample in idx.samples
        ]

    def _scan_sample(self, node: Node) -> list[Node]:
        """Experiments in a sample, from the index -- no filesystem access.

        The index already recorded which experiments exist and whether each
        has processed data, so opening a sample is a dictionary lookup. It
        used to be a directory listing plus two calls per experiment, which is
        why samples took seconds to open on a share.
        """
        idx = self.index_for(_root_node(node).path)
        entry = idx.find(str(node.path))
        if entry is None:
            # Not in the index (added since it was built): read it directly
            # rather than showing an empty sample.
            return [
                Node(
                    kind=NodeKind.EXPNO,
                    path=expno_path,
                    display_name=expno_path.name,
                    parent=node,
                    probe_pending=True,
                )
                for expno_path in expnos_with_data(node.path)
            ]
        if not entry.detailed:
            entry.expnos = index_store.scan_expnos(entry.path)
            entry.detailed = True
            entry.mtime = index_store.sample_mtime(entry.path)
            entry.invalidate()
            self.mark_dirty(_root_node(node).path)
        node.entry = entry
        return self.expno_nodes_for(node, entry)

    # -- probing -------------------------------------------------------------

    def probe_node(self, node: Node) -> None:
        """Probe one expno synchronously ON THE GUI THREAD, then refresh its row.

        Mutates model state and emits dataChanged, so it must not run on a
        worker. The async path splits it: read_probe_result() is pure I/O and
        worker-safe, apply_probe_result() does the mutation here.
        """
        if node.kind is not NodeKind.EXPNO or not node.probe_pending:
            return
        info, error = self.read_probe_result(node)
        self.apply_probe_result(node, info, error)

    def read_probe_result(self, node: Node):
        """Pure I/O: read one expno's acqus and return (info, error).

        SAFE TO CALL ON A WORKER THREAD -- it touches nothing on the model.
        Uses probe_row rather than probe: the index already established that
        this is an expno and which raw files it has, so the row costs one file
        read instead of nine round trips.
        """
        return probe_expno(
            self._reader, node.path, node.entry, _owning_root(node)
        )

    def apply_probe_result(self, node: Node, info, error: str) -> None:
        """Install a probe result and refresh the row. GUI THREAD ONLY."""
        if not node.probe_pending:
            return
        node.info = info
        node.failed = info is None
        node.error = error
        node.probe_pending = False
        self._record_meta(node, info, error)

        index = self._index_for(node)
        if index.isValid():
            last_col = self.createIndex(index.row(), len(COLUMNS) - 1, node)
            self.dataChanged.emit(index, last_col)

    def apply_meta(self, node: Node, values: dict) -> None:
        """Apply a background metadata read (a plain dict from a worker).

        Kept separate from apply_probe_result because the background pass
        deliberately does not build a DatasetInfo per row: on a big root that
        is thousands of objects held for rows nobody is looking at. The cached
        entry carries everything the columns, the filter and the drag payload
        need.
        """
        if not node.probe_pending:
            return
        node.probe_pending = False
        node.probe_in_flight = False
        node.failed = bool(values.get("error"))
        node.error = values.get("error", "")
        if node.entry is not None:
            _write_meta(node.entry, values)
        self._invalidate_sample_haystack(node)
        index = self._index_for(node)
        if index.isValid():
            last_col = self.createIndex(index.row(), len(COLUMNS) - 1, node)
            self.dataChanged.emit(index, last_col)

    def _record_meta(self, node: Node, info, error: str) -> None:
        """Write a probe result back into the cached index entry."""
        entry = node.entry
        if entry is None:
            return
        entry.meta = True
        entry.error = error
        if entry.has_processed is None and not entry.processed_note:
            # The batched worker path records this in meta_values(); the
            # synchronous path has to do it here or a never-processed
            # experiment stays marked droppable. One listing, and only on the
            # synchronous path -- which in the running application is the
            # fallback, not the route the browser takes.
            entry.has_processed, entry.processed_note = (
                index_store.inspect_processed(node.path)
            )
        if info is not None:
            entry.pulprog = info.pulse_program or ""
            entry.nucleus = info.nucleus or ""
            entry.solvent = info.solvent or ""
            entry.date = info.date or ""
            entry.dim = 2 if info.dimensionality is Dimensionality.TWO_D else 1
        self._invalidate_sample_haystack(node)
        root = _root_node(node)
        if root is not node:
            self.mark_dirty(root.path)

    def _invalidate_sample_haystack(self, node: Node) -> None:
        sample = node.parent
        if sample is not None and getattr(sample, "entry", None) is not None:
            invalidate = getattr(sample.entry, "invalidate", None)
            if callable(invalidate):
                invalidate()

    def mark_expno_failed(self, path, message: str) -> bool:
        """Record that this dataset could not be loaded, and show it.

        Closes the loop on a failed drop: the row the user dragged goes grey,
        carries the reason in its tooltip, and stops being draggable. Without
        this the only feedback was a status-bar line that the cursor readout
        wiped on the next mouse move -- which is why "it just does not open"
        was the reported symptom rather than any error.
        """
        wanted = Path(path)
        for root in self._roots:
            sample = self.child_node(root, str(wanted.parent))
            if sample is None or not sample.is_fetched:
                continue
            row = self.child_node(sample, str(wanted))
            if row is None:
                continue
            row.failed = True
            row.error = message
            if row.entry is not None:
                row.entry.error = message
            index = self._index_for(row)
            if index.isValid():
                last = self.createIndex(index.row(), len(COLUMNS) - 1, row)
                self.dataChanged.emit(index, last)
            return True
        return False

    # -- filtering support ---------------------------------------------------

    def sample_matches(self, node: Node, needle: str) -> bool:
        """Does this sample, or any experiment in it, match the filter text?

        Reads the cached index entry, so a sample that has never been expanded
        still matches on PULPROG once its metadata has been indexed -- the
        limitation the 0.3.0 filter documented. The entry keeps the haystack
        string, so this is one substring test per sample rather than a walk
        over its experiments on every keystroke.
        """
        if needle in node.display_name.lower():
            return True
        entry = self.entry_for(node)
        if entry is not None:
            return needle in entry.haystack()
        for child in node.children or []:
            if needle in child.display_name.lower():
                return True
            if needle in child.meta_pulprog().lower():
                return True
        return False

    def root_matches(self, node: Node, needle: str) -> bool:
        if needle in node.display_name.lower():
            return True
        for child in node.children or []:
            if self.sample_matches(child, needle):
                return True
        return False

    # -- display ------------------------------------------------------------

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        node: Node = index.internalPointer()
        if node is None:
            return None
        col = index.column()

        if role == Qt.ToolTipRole:
            if node.failed:
                return node.error or "Could not read this location."
            if node.kind is NodeKind.EXPNO and not node.loadable:
                return node.load_note()

        if role == Qt.ForegroundRole and node.kind is NodeKind.EXPNO:
            if node.failed or not node.loadable:
                # Dimmed rather than hidden: an experiment that exists but has
                # not been processed is information, and silently omitting it
                # leaves the user wondering where expno 3 went.
                return QBrush(QColor(150, 150, 150))
            return None

        if role != Qt.DisplayRole:
            return None

        if node.kind is not NodeKind.EXPNO:
            return node.display_name if col == COL_NAME else None

        if col == COL_NAME:
            return node.display_name
        if not node.has_meta:
            # Structure is known (the row is shown, and can be dragged), but
            # the metadata has not been read. A pending read shows a subtle
            # placeholder rather than blank, so the user can see the details
            # are coming rather than absent; a failed one shows an em dash.
            if node.failed:
                return FAILED_TEXT
            return PENDING_TEXT if node.probe_pending else None
        if col == COL_PULPROG:
            return node.meta_pulprog()
        if col == COL_NUCLEUS:
            return node.meta_nucleus()
        if col == COL_DIM:
            dim = node.dimensionality()
            return f"{dim}D" if dim else None
        if col == COL_DATE:
            return node.meta_date()
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
        if node is not None and node.kind is NodeKind.EXPNO:
            return base | Qt.ItemIsDragEnabled
        return base

    def _index_for(self, node: Node) -> QModelIndex:
        """The model index that points at `node`.

        A data-root node has no parent Node, but it is NOT the invisible root
        -- it is a real, visible top-level row living in self._roots.
        Returning an empty QModelIndex() for it (as an earlier version did)
        made every operation keyed on a data root's own index silently target
        the invisible root, which is why refreshing a data root did nothing.
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

        A row is draggable as soon as it exists. Requiring a completed probe
        (0.3.0) meant that on a share -- where the probe is exactly the slow
        part -- dragging a freshly listed row produced an empty payload and
        the drag silently did nothing, which reads as a broken app.

        `dimensionality` is 0 when nothing has established it yet; the canvas
        resolves that at load time instead of refusing the drop.
        """
        seen_rows = set()
        payload = []
        for index in indexes:
            if index.column() != 0:
                continue
            node: Node = index.internalPointer()
            if node is None or node.kind is not NodeKind.EXPNO:
                continue
            if id(node) in seen_rows:
                continue
            seen_rows.add(id(node))
            payload.append(
                {
                    "path": str(node.path),
                    "dimensionality": node.dimensionality(),
                    # A bare expno ("1", "30") is useless in a legend -- it
                    # does not say WHICH sample. Qualify it with the parent
                    # sample name, which is what the user recognises.
                    "label": _dataset_label(node),
                    "sample": node.parent.display_name if node.parent else "",
                    "expno": node.display_name,
                    "pulse_program": node.meta_pulprog(),
                    "nucleus": node.meta_nucleus(),
                }
            )
        mime = QMimeData()
        mime.setData(MIME_DATASET, json.dumps(payload).encode("utf-8"))
        return mime


# --- worker-side helpers (no Qt, no model access) ----------------------------


def probe_expno(reader, path, entry, data_root):
    """Read one experiment's metadata. SAFE ON A WORKER THREAD.

    Returns (DatasetInfo | None, error string).
    """
    # hasattr is checked up front, not caught as an AttributeError from the
    # call: an AttributeError raised INSIDE probe_row is a real bug, and
    # silently retrying with the slow path would hide it for ever.
    if hasattr(reader, "probe_row"):
        try:
            return reader.probe_row(
                Path(path), data_root=data_root, structure=entry
            ), ""
        except Exception as exc:      # noqa: BLE001 -- never lose the row
            return None, str(exc)
    try:
        return reader.probe(Path(path), data_root=data_root), ""
    except Exception as exc:          # noqa: BLE001 -- never lose the row
        return None, str(exc)


def _write_meta(entry, values: dict) -> None:
    """Copy a metadata read into a cached index entry.

    One writer for both paths -- the per-row probe and the background indexer
    -- because two of them drifted apart once already and the symptom was a
    column that filled in when a sample was expanded and stayed blank when the
    indexer got there first.
    """
    entry.meta = True
    entry.pulprog = values.get("pulprog", "")
    entry.nucleus = values.get("nucleus", "")
    entry.solvent = values.get("solvent", "")
    entry.date = values.get("date", "")
    entry.dim = int(values.get("dim", 0) or 0)
    entry.error = values.get("error", "")
    if "has_processed" in values:
        entry.has_processed = values["has_processed"]
        entry.processed_note = values.get("processed_note", "")


def meta_values(reader, path, entry, data_root) -> dict:
    """The metadata a browser row needs, as a plain dict. WORKER-SAFE."""
    info, error = probe_expno(reader, path, entry, data_root)
    processed, note = index_store.inspect_processed(path)
    if info is None:
        return {"error": error or "could not read",
                "has_processed": processed, "processed_note": note}
    return {
        "has_processed": processed,
        "processed_note": note,
        "pulprog": info.pulse_program or "",
        "nucleus": info.nucleus or "",
        "solvent": info.solvent or "",
        "date": info.date or "",
        "dim": 2 if info.dimensionality is Dimensionality.TWO_D else 1,
        "error": "",
    }


class CancelToken:
    """Cooperative cancellation for the worker pools.

    Set on the GUI thread, read on workers. A plain bool attribute is
    sufficient: CPython guarantees the assignment is atomic, and the only
    transition is False -> True, so a worker either sees the old value and
    does one more file read, or the new one and stops.
    """

    __slots__ = ("stopped",)

    def __init__(self) -> None:
        self.stopped = False

    def cancel(self) -> None:
        self.stopped = True

    def __bool__(self) -> bool:
        return self.stopped


# --- async plumbing ----------------------------------------------------------
#
# Every task emits ITSELF as the first argument of its result signal. Qt's
# thread pool keeps no Python reference to a running QRunnable, so the
# populator holds one; without the task in the payload the handler has to
# guess which one finished, and an earlier attempt at guessing (matching on
# the first node in the batch) dropped the wrong entry and let a live task be
# collected mid-flight.


class _DiscoverSignals(QObject):
    cached = Signal(object, object, object)         # task, node, RootIndex
    batch = Signal(object, object, list)            # task, node, [sample paths]
    finished = Signal(object, object, list, bool, bool)
    failed = Signal(object, object, str)            # task, node, message


class _DiscoverTask(QRunnable):
    """Finds a data root's samples, streaming them as they are found.

    Order matters: the on-disk cache is consulted FIRST and, when it holds a
    complete index, that is the whole job -- one local file read instead of a
    walk over the share. Only then does it fall back to discovery, which
    reports batches so the tree fills as the walk proceeds rather than after
    it. A refresh skips the cache: the user asking for a refresh is the user
    saying the cache is out of date.
    """

    def __init__(self, node: Node, token: CancelToken, limit: int = 5000,
                 use_cache: bool = True):
        super().__init__()
        # Qt owns a QRunnable by default and deletes it as soon as run()
        # returns -- while this object is still in the populator's _inflight
        # set. Dropping that reference then deletes the C++ object a SECOND
        # time, and pool.clear() deletes queued ones out from under it too.
        # The result is a double free: a segfault with no Python traceback,
        # arriving whenever a shutdown happened to race real work. Python owns
        # these tasks; Qt must not.
        self.setAutoDelete(False)
        self.node = node
        self._token = token
        self._limit = limit
        self._use_cache = use_cache
        self.signals = _DiscoverSignals()

    def run(self) -> None:
        node = self.node
        try:
            if self._use_cache:
                cached = index_store.load_index(node.path)
                if cached is not None and cached.samples:
                    self.signals.cached.emit(self, node, cached)
                    if cached.complete:
                        self.signals.finished.emit(
                            self, node, [s.path for s in cached.samples],
                            cached.truncated, True,
                        )
                        return
                    # A partial cache (an interrupted first run) is shown at
                    # once and then completed by a real walk, which re-reports
                    # the samples it already knows -- make_sample_nodes drops
                    # the duplicates.
            paths, truncated = index_store.discover_samples(
                node.path,
                limit=self._limit,
                on_batch=lambda found: self.signals.batch.emit(self, node, found),
                should_stop=lambda: self._token.stopped,
            )
        except Exception as exc:      # noqa: BLE001 -- a walk must not kill the app
            self.signals.failed.emit(self, node, str(exc))
            return
        self.signals.finished.emit(
            self, node, paths, truncated, not self._token.stopped
        )


class _DetailSignals(QObject):
    done = Signal(object, object, list)     # task, node, [ExpnoEntry]
    failed = Signal(object, object, str)


class _DetailTask(QRunnable):
    """Lists one sample's experiments: one directory read plus one per expno."""

    def __init__(self, node: Node, token: CancelToken):
        super().__init__()
        # Qt owns a QRunnable by default and deletes it as soon as run()
        # returns -- while this object is still in the populator's _inflight
        # set. Dropping that reference then deletes the C++ object a SECOND
        # time, and pool.clear() deletes queued ones out from under it too.
        # The result is a double free: a segfault with no Python traceback,
        # arriving whenever a shutdown happened to race real work. Python owns
        # these tasks; Qt must not.
        self.setAutoDelete(False)
        self.node = node
        self._token = token
        self.signals = _DetailSignals()

    def run(self) -> None:
        if self._token.stopped:
            self.signals.done.emit(self, self.node, [])
            return
        try:
            expnos = index_store.scan_expnos(str(self.node.path))
        except Exception as exc:      # noqa: BLE001
            self.signals.failed.emit(self, self.node, str(exc))
            return
        self.signals.done.emit(self, self.node, expnos)


class _MetaSignals(QObject):
    done = Signal(object, list)     # task, [(node, values dict)]


class _MetaTask(QRunnable):
    """Reads acqus for a BATCH of rows.

    One runnable per row costs a QObject, a signal connection and a thread hop
    for a single ~1 ms read; batching amortises all three while staying small
    enough that the first rows still fill in promptly.
    """

    def __init__(self, reader, nodes: list, data_root, token: CancelToken):
        super().__init__()
        # Qt owns a QRunnable by default and deletes it as soon as run()
        # returns -- while this object is still in the populator's _inflight
        # set. Dropping that reference then deletes the C++ object a SECOND
        # time, and pool.clear() deletes queued ones out from under it too.
        # The result is a double free: a segfault with no Python traceback,
        # arriving whenever a shutdown happened to race real work. Python owns
        # these tasks; Qt must not.
        self.setAutoDelete(False)
        self._reader = reader
        self._nodes = nodes
        self._data_root = data_root
        self._token = token
        self.signals = _MetaSignals()

    def run(self) -> None:
        results = []
        for node in self._nodes:
            if self._token.stopped:
                break
            results.append(
                (node, meta_values(self._reader, node.path, node.entry,
                                   self._data_root))
            )
        self.signals.done.emit(self, results)


class _IndexSignals(QObject):
    done = Signal(object, object, list)   # task, root node, results


class _IndexTask(QRunnable):
    """Background indexer: details and reads metadata for whole samples.

    This is what makes the SECOND look at a root instant and the PULPROG
    filter work across samples that were never expanded. It runs on its own
    small pool so it can never delay anything the user is waiting for.
    """

    def __init__(self, reader, root_node: Node, jobs: list,
                 token: CancelToken):
        super().__init__()
        # Qt owns a QRunnable by default and deletes it as soon as run()
        # returns -- while this object is still in the populator's _inflight
        # set. Dropping that reference then deletes the C++ object a SECOND
        # time, and pool.clear() deletes queued ones out from under it too.
        # The result is a double free: a segfault with no Python traceback,
        # arriving whenever a shutdown happened to race real work. Python owns
        # these tasks; Qt must not.
        self.setAutoDelete(False)
        self._reader = reader
        self.root_node = root_node
        self._jobs = jobs        # [(sample_path, needs_detail, [expno names])]
        self._token = token
        self.signals = _IndexSignals()

    def run(self) -> None:
        data_root = self.root_node.data_root
        out = []
        for sample_path, needs_detail, expno_names in self._jobs:
            if self._token.stopped:
                break
            expnos = (
                index_store.scan_expnos(sample_path) if needs_detail else None
            )
            if expnos is not None:
                names = [e.name for e in expnos if e.displayable and not e.meta]
                by_name = {e.name: e for e in expnos}
            else:
                names = list(expno_names)
                by_name = {}
            metadata = []
            for name in names:
                if self._token.stopped:
                    break
                metadata.append(
                    (name, meta_values(self._reader,
                                       Path(sample_path) / name,
                                       by_name.get(name), data_root))
                )
            out.append((sample_path, expnos, metadata))
        self.signals.done.emit(self, self.root_node, out)


class _StaleSignals(QObject):
    done = Signal(object, object, list)   # task, root node, [changed sample paths]


class _StaleTask(QRunnable):
    """One stat per indexed sample, to catch experiments added since.

    Cheap enough to run quietly after the background index finishes: it is
    what makes an experiment that finished while HelSpin was open appear
    without the user thinking to press Refresh, without ever re-walking the
    share.
    """

    def __init__(self, root_node: Node, entries: list, token: CancelToken):
        super().__init__()
        # Qt owns a QRunnable by default and deletes it as soon as run()
        # returns -- while this object is still in the populator's _inflight
        # set. Dropping that reference then deletes the C++ object a SECOND
        # time, and pool.clear() deletes queued ones out from under it too.
        # The result is a double free: a segfault with no Python traceback,
        # arriving whenever a shutdown happened to race real work. Python owns
        # these tasks; Qt must not.
        self.setAutoDelete(False)
        self.root_node = root_node
        self._entries = entries    # [(path, known mtime)]
        self._token = token
        self.signals = _StaleSignals()

    def run(self) -> None:
        changed = []
        for path, known in self._entries:
            if self._token.stopped:
                break
            current = index_store.sample_mtime(path)
            if current > known:
                changed.append(path)
        self.signals.done.emit(self, self.root_node, changed)


class _SaveTask(QRunnable):
    """Writes one root's index cache. Pure file I/O over a plain dict, so it
    shares nothing mutable with the GUI thread."""

    def __init__(self, root: str, payload: dict):
        super().__init__()
        # Qt owns a QRunnable by default and deletes it as soon as run()
        # returns -- while this object is still in the populator's _inflight
        # set. Dropping that reference then deletes the C++ object a SECOND
        # time, and pool.clear() deletes queued ones out from under it too.
        # The result is a double free: a segfault with no Python traceback,
        # arriving whenever a shutdown happened to race real work. Python owns
        # these tasks; Qt must not.
        self.setAutoDelete(False)
        self._root = root
        self._payload = payload

    def run(self) -> None:
        index_store.save_payload(self._root, self._payload)


# Every populator with work in flight, held STRONGLY until it is shut down.
#
# Without this the whole graph -- browser, populator, in-flight tasks, and the
# Node objects those tasks are reading -- can become unreachable at once, and
# CPython's cyclic collector then frees it in an order of its choosing WHILE a
# pool thread is still walking a Path out of one of those nodes. The crash is a
# segfault inside pathlib, on the GC's thread, arbitrarily far from the code
# that caused it. Nothing may be collectable until the pools have stopped, and
# a strong reference is the only way to promise that.
_LIVE_POPULATORS: set = set()


def shutdown_all_populators() -> None:
    """Stop every populator. For application teardown and test hygiene."""
    for populator in list(_LIVE_POPULATORS):
        populator.shutdown()


class DatasetPopulator(QObject):
    """Async wrapper around the model's population and probing.

    Two private pools, deliberately:

    * ``interactive`` serves what the user is waiting for -- expanding a
      sample, filling in the rows on screen;
    * ``background`` serves the indexer, which works through the whole root.

    With one shared pool the background work queues ahead of the click that
    just happened, and the browser feels frozen while it runs. Neither pool is
    the global instance, because that is where the canvas loads spectra: a
    drop must never wait behind a queue of directory listings. (It did, and
    that alone could add half a minute to a drag on a slow share.)
    """

    probeError = Signal(str, str)          # path, message
    populated = Signal(object)             # node whose children were applied
    indexProgress = Signal(str, int, int)  # root path, done, total
    discovering = Signal(str, int)         # root path, samples found so far
    rootEmpty = Signal(str)                # root path with no samples in it

    INTERACTIVE_THREADS = 8    # I/O bound: concurrency is what hides latency
    BACKGROUND_THREADS = 3
    META_BATCH = 8
    INDEX_BATCH = 4            # samples per background task
    INDEX_INFLIGHT = 3

    def __init__(self, model: DatasetTreeModel, pool: QThreadPool | None = None,
                 background_pool: QThreadPool | None = None):
        super().__init__()
        self._model = model
        self._pool = pool or self._make_pool(self.INTERACTIVE_THREADS)
        self._bg_pool = background_pool or self._make_pool(self.BACKGROUND_THREADS)
        self._token = CancelToken()
        # QThreadPool keeps no Python-side reference to the QRunnables it runs,
        # and each task owns the QObject carrying its result signal. Without a
        # reference held here that object can be collected before the signal is
        # delivered, and the result is silently lost.
        self._inflight: set = set()
        self._bg_queue: list = []
        self._bg_root: Node | None = None
        self._bg_active = 0
        self._bg_total = 0
        self._bg_done = 0
        self._stale_checked: set = set()
        self._refreshing: set = set()
        self._found: dict = {}
        # A result that arrives after this populator has been shut down must
        # be dropped, not applied: applying it would call createIndex() and
        # dataChanged.emit() on a model the browser has finished with, and if
        # Qt has already deleted the C++ object that is a SEGFAULT rather than
        # an exception. In the application the shape of this is "close the
        # explorer while the background indexer is still running".
        #
        # The flag is set by shutdown(), NOT by connecting to the model's
        # destroyed signal: that connection was tried and crashed on teardown
        # in PySide6 6.11. It is also unnecessary, because this object holds a
        # strong reference to the model -- the model therefore cannot be
        # destroyed while the populator is alive, and _LIVE_POPULATORS keeps
        # the populator alive until something shuts it down.
        self._alive = True
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(4000)
        self._save_timer.timeout.connect(self._flush_index)
        _LIVE_POPULATORS.add(self)

    def _make_pool(self, threads: int) -> QThreadPool:
        pool = QThreadPool()
        pool.setMaxThreadCount(threads)
        # Idle threads are cheap; re-creating them for every burst of reads is
        # not. Ten seconds comfortably outlives a series of expansions.
        pool.setExpiryTimeout(10000)
        return pool

    # -- lifecycle -----------------------------------------------------------

    def _drain(self) -> None:
        """Drop queued tasks and wait for running ones to notice the token.

        Bounded: workers check the token between files, so the wait is one
        file read, not one walk of the share. The timeout is a backstop for a
        mount that has gone away mid-read, where a single read can hang for as
        long as the network stack allows -- a slow quit is far better than a
        crash, but neither should be unbounded.
        """
        for pool in (self._pool, self._bg_pool):
            try:
                pool.clear()
                pool.waitForDone(5000)
            except RuntimeError:      # pragma: no cover - already torn down
                pass
        self._inflight.clear()

    def __del__(self) -> None:      # pragma: no cover - GC ordering
        """Last line of defence for a populator nobody shut down.

        A worker part-way through `signals.done.emit(...)` is emitting from a
        QObject this instance owns. Freeing that object underneath the worker
        is a use-after-free, so cancel and let the pools unwind first.
        """
        try:
            self._token.cancel()
            self._drain()
        except (RuntimeError, AttributeError):
            pass    # already torn down by Qt; nothing left to wait for

    def shutdown(self) -> None:
        """Stop everything and persist what has been learned.

        Queued tasks are dropped and running ones stop at their next file
        boundary, so quitting during a walk of a slow share does not hang the
        close. The final save is synchronous: handing it to a pool that is
        about to be torn down is how a session's indexing gets silently thrown
        away.
        """
        self._token.cancel()
        self._save_timer.stop()
        self._bg_queue = []
        self._bg_root = None
        if self._alive:
            self._flush_index(synchronous=True)
        self._alive = False
        self._drain()
        _LIVE_POPULATORS.discard(self)

    def cancel_background(self) -> None:
        self._bg_queue = []
        self._bg_root = None

    # -- population ----------------------------------------------------------

    def populate(self, node: Node) -> None:
        if node.kind is NodeKind.DATA_ROOT:
            self._populate_root(node)
        elif node.kind is NodeKind.SAMPLE:
            self._populate_sample(node)
        else:
            self._model.applyChildren(node, [])

    def refresh(self, node: Node) -> None:
        """Re-read a node from disk, merging the result into the live tree.

        Asynchronous, unlike the model's own refresh(): a data root's refresh
        is a full walk of the share, and running that on the GUI thread is a
        multi-minute freeze on exactly the setups this release exists to fix.
        """
        if node.kind is NodeKind.DATA_ROOT:
            if id(node) in self._refreshing:
                return
            self._refreshing.add(id(node))
            self._found[id(node)] = set()
            self._stale_checked.discard(id(node))
            task = _DiscoverTask(node, self._token, use_cache=False)
            task.signals.cached.connect(self._on_cached_index)
            task.signals.batch.connect(self._on_sample_batch)
            task.signals.finished.connect(self._on_discovery_finished)
            task.signals.failed.connect(self._on_failed)
            self._inflight.add(task)
            self._pool.start(task)
        elif node.kind is NodeKind.SAMPLE:
            task = _DetailTask(node, self._token)
            task.signals.done.connect(self._on_sample_refreshed)
            task.signals.failed.connect(self._on_failed)
            self._inflight.add(task)
            self._pool.start(task)

    def _populate_root(self, node: Node) -> None:
        cached = self._model.cached_index(node.path)
        if cached is not None and cached.complete and cached.samples:
            # Already in memory (a second expand, or a root re-added): no
            # thread hop at all.
            self._apply_root_index(node, cached)
            children = self._model.make_sample_nodes(
                node, [s.path for s in cached.samples]
            )
            self._model.applyChildren(node, children)
            self.populated.emit(node)
            self.start_background_index(node)
            return
        task = _DiscoverTask(node, self._token)
        task.signals.cached.connect(self._on_cached_index)
        task.signals.batch.connect(self._on_sample_batch)
        task.signals.finished.connect(self._on_discovery_finished)
        task.signals.failed.connect(self._on_failed)
        self._inflight.add(task)
        self._pool.start(task)

    def _populate_sample(self, node: Node) -> None:
        entry = self._model.entry_for(node)
        if entry is not None and entry.detailed:
            # The index already knows this sample's contents, so opening it is
            # a dictionary lookup -- no thread hop just to discover there was
            # nothing to do.
            children = self._model.expno_nodes_for(node, entry)
            self._model.applyChildren(node, children)
            self._after_children(node, children)
            return
        task = _DetailTask(node, self._token)
        task.signals.done.connect(self._on_sample_detail)
        task.signals.failed.connect(self._on_failed)
        self._inflight.add(task)
        self._pool.start(task)

    def _apply_root_index(self, node: Node, index) -> None:
        self._model.install_index(node.path, index)
        node.truncated = bool(getattr(index, "truncated", False))

    # -- results, all on the GUI thread --------------------------------------

    def _on_cached_index(self, task, node: Node, index) -> None:
        if not self._alive:
            return
        if not self._model.is_live(node):
            self._inflight.discard(task)
            return
        self._apply_root_index(node, index)
        self._add_samples(node, [s.path for s in index.samples])

    def _on_sample_batch(self, task, node: Node, paths: list) -> None:
        if not self._alive:
            return
        if not self._model.is_live(node):
            self._inflight.discard(task)
            return
        found = self._found.get(id(node))
        if found is not None:
            found.update(str(p) for p in paths)
        self._add_samples(node, paths)
        self.discovering.emit(
            str(node.path), len(node.children or [])
        )

    def _add_samples(self, node: Node, paths) -> None:
        children = self._model.make_sample_nodes(node, paths)
        if node.children is None:
            self._model.applyChildren(node, children)
        else:
            self._model.appendChildren(node, children)
        self.populated.emit(node)

    def _on_discovery_finished(self, task, node: Node, paths: list,
                               truncated: bool, complete: bool) -> None:
        if not self._alive:
            return
        self._inflight.discard(task)
        was_refresh = id(node) in self._refreshing
        self._refreshing.discard(id(node))
        self._found.pop(id(node), None)
        if not self._model.is_live(node):
            return
        node.truncated = truncated
        if node.children is None:
            self._model.applyChildren(node, [])
        node.fetch_in_flight = False
        if was_refresh and complete:
            # Only a refresh may remove rows: a cancelled or partial walk
            # saying "I did not see it" is not the same as it being gone.
            self._model.drop_missing_samples(node, {str(p) for p in paths})
        index = self._model.cached_index(node.path)
        if index is not None:
            index.truncated = truncated
            index.complete = complete
            self._model.mark_dirty(node.path)
            self._schedule_save()
        if truncated:
            self._model.scanTruncated.emit(
                str(node.path), len(index.samples) if index else 0
            )
        elif complete and not node.children:
            # A root that contains nothing recognisable used to sit there
            # refusing to expand, with no message -- indistinguishable from a
            # root that is still loading, or from a bug. Say so, and say what
            # was actually looked for.
            self.rootEmpty.emit(str(node.path))
        self.populated.emit(node)
        self.start_background_index(node)

    def _on_sample_detail(self, task, node: Node, expnos: list) -> None:
        if not self._alive:
            return
        self._inflight.discard(task)
        if not self._model.is_live(node):
            return
        children = self._model.apply_sample_detail(node, expnos)
        self._model.applyChildren(node, children)
        self._schedule_save()
        self._after_children(node, children)

    def _on_sample_refreshed(self, task, node: Node, expnos: list) -> None:
        """A refreshed sample MERGES: rows keep their identity, so expansion
        state and probed metadata survive."""
        if not self._alive:
            return
        self._inflight.discard(task)
        if not self._model.is_live(node):
            return
        self._model.apply_sample_detail(node, expnos)
        new_children = self._model.merge_sample_children(node)
        self._schedule_save()
        self.probe_rows(new_children)
        self.populated.emit(node)

    EAGER_ROWS = 16

    def _after_children(self, node: Node, children: list) -> None:
        # Rows are visible now; fill their metadata columns in the background.
        # Only the first screenful or so is read eagerly: a sample with 200
        # experiments would otherwise queue 200 reads ahead of the rows the
        # user is actually looking at. The browser probes what is on screen as
        # it scrolls, and the background indexer collects the rest.
        self.probe_rows(children[:self.EAGER_ROWS])
        # Let the browser react now that this node's children exist -- e.g.
        # re-expand children that were open before a refresh. Deterministic
        # (fires after applyChildren), unlike racing rowsInserted.
        self.populated.emit(node)

    def _on_failed(self, task, node: Node, message: str) -> None:
        if not self._alive:
            return
        self._inflight.discard(task)
        self._refreshing.discard(id(node))
        if not self._model.is_live(node):
            return
        node.failed = True
        node.error = message
        node.fetch_in_flight = False
        if node.children is None:
            self._model.applyChildren(node, [])
        self._model.scanFailed.emit(str(node.path), message)

    # -- metadata ------------------------------------------------------------

    def probe_row(self, node: Node) -> None:
        """Schedule one expno's metadata read.

        A no-op for anything that is not a pending expno probe, so it is safe
        to call indiscriminately -- e.g. for every visible row -- without
        checking each one first.
        """
        self.probe_rows([node])

    def probe_rows(self, nodes) -> None:
        """Schedule metadata reads for a group of rows, in batches."""
        pending = [
            n for n in nodes
            if n is not None and n.kind is NodeKind.EXPNO and n.probe_pending
            and not n.probe_in_flight
        ]
        if not pending:
            return
        for start in range(0, len(pending), self.META_BATCH):
            batch = pending[start:start + self.META_BATCH]
            for node in batch:
                node.probe_in_flight = True
            task = _MetaTask(
                self._model.reader, batch, _owning_root(batch[0]), self._token
            )
            task.signals.done.connect(self._on_meta_done)
            self._inflight.add(task)
            self._pool.start(task)

    def _on_meta_done(self, task, results: list) -> None:
        if not self._alive:
            return
        self._inflight.discard(task)
        for node in task._nodes:
            node.probe_in_flight = False
        dirty_root = None
        for node, values in results:
            if not self._model.is_live(node):
                continue
            self._model.apply_meta(node, values)
            if values.get("error"):
                self.probeError.emit(str(node.path), values["error"])
            dirty_root = _root_node(node)
        if dirty_root is not None:
            self._model.mark_dirty(dirty_root.path)
            self._schedule_save()

    # -- background indexing -------------------------------------------------

    def start_background_index(self, root_node: Node) -> None:
        """Index the rest of a root quietly, once its samples are listed.

        This is the part that makes the app fast rather than merely
        responsive: every sample's experiment list and metadata is read once,
        in the background, and cached. The next session opens any sample with
        no filesystem access at all, and the PULPROG filter reaches samples
        the user never expanded.
        """
        if self._token.stopped:
            return
        if self._bg_root is root_node and (self._bg_queue or self._bg_active):
            return      # already working on this root; queuing again doubles the I/O
        index = self._model.cached_index(root_node.path)
        if index is None:
            return
        jobs = []
        for sample in index.samples:
            if not sample.detailed:
                jobs.append((sample.path, True, []))
            elif not sample.meta_complete:
                jobs.append(
                    (sample.path, False,
                     [e.name for e in sample.expnos
                      if e.displayable and not e.meta])
                )
        if not jobs:
            self.indexProgress.emit(str(root_node.path), 0, 0)
            self._check_staleness(root_node)
            return
        self._bg_root = root_node
        self._bg_queue = jobs
        self._bg_total = len(jobs)
        self._bg_done = 0
        self.indexProgress.emit(str(root_node.path), 0, self._bg_total)
        self._pump_background()

    def _pump_background(self) -> None:
        while self._bg_active < self.INDEX_INFLIGHT and self._bg_queue:
            if self._token.stopped or self._bg_root is None:
                return
            batch = self._bg_queue[:self.INDEX_BATCH]
            del self._bg_queue[:self.INDEX_BATCH]
            task = _IndexTask(
                self._model.reader, self._bg_root, batch, self._token
            )
            task.signals.done.connect(self._on_index_batch)
            self._inflight.add(task)
            self._bg_active += 1
            self._bg_pool.start(task)

    def _on_index_batch(self, task, root_node: Node, results: list) -> None:
        if not self._alive:
            return
        self._inflight.discard(task)
        self._bg_active = max(0, self._bg_active - 1)
        if not self._model.is_live(root_node):
            return
        index = self._model.cached_index(root_node.path)
        if index is None:
            return
        for sample_path, expnos, metadata in results:
            entry = index.find(sample_path)
            if entry is None:
                continue
            if expnos is not None:
                self._model.merge_expnos(entry, expnos, sample_path)
            by_name = {e.name: e for e in entry.expnos}
            for name, values in metadata:
                expno = by_name.get(name)
                if expno is None:
                    continue
                _write_meta(expno, values)
            entry.invalidate()
            self._model.refresh_sample_rows(root_node, sample_path, entry)
            self._bg_done += 1
        self._model.mark_dirty(root_node.path)
        self.indexProgress.emit(str(root_node.path), self._bg_done, self._bg_total)
        self._schedule_save()
        self._pump_background()
        if not self._bg_queue and not self._bg_active:
            self._check_staleness(root_node)

    def _check_staleness(self, root_node: Node) -> None:
        """Once per root per session: stat the indexed samples and re-read the
        ones that changed. One stat each, in the background, so an experiment
        that appeared while HelSpin was open shows up on its own."""
        if id(root_node) in self._stale_checked or self._token.stopped:
            return
        index = self._model.cached_index(root_node.path)
        if index is None:
            return
        entries = [(s.path, s.mtime) for s in index.samples if s.detailed]
        if not entries:
            return
        self._stale_checked.add(id(root_node))
        task = _StaleTask(root_node, entries, self._token)
        task.signals.done.connect(self._on_stale_checked)
        self._inflight.add(task)
        self._bg_pool.start(task)

    def _on_stale_checked(self, task, root_node: Node, changed: list) -> None:
        if not self._alive:
            return
        self._inflight.discard(task)
        if not changed or not self._model.is_live(root_node):
            return
        index = self._model.cached_index(root_node.path)
        if index is None:
            return
        for path in changed:
            entry = index.find(path)
            if entry is not None:
                entry.detailed = False      # re-list it on the next pass
        self._bg_root = None                # allow a fresh pass for this root
        self.start_background_index(root_node)

    # -- cache persistence ---------------------------------------------------

    def _schedule_save(self) -> None:
        if not self._save_timer.isActive():
            self._save_timer.start()

    def _flush_index(self, synchronous: bool = False) -> None:
        """Write changed indexes to the cache.

        Serialising on the GUI thread is deliberate: the index is mutated here
        too, and handing a live object to a worker to serialise is how a
        "dictionary changed size during iteration" crash gets shipped. Only
        the file write -- the part that touches a disk -- goes to a worker.
        """
        for key in self._model.take_dirty_roots():
            index = self._model.cached_index(key)
            if index is None:
                continue
            try:
                payload = index.to_dict()
            except (TypeError, ValueError):      # pragma: no cover - defensive
                continue
            if synchronous:
                index_store.save_payload(key, payload)
                continue
            task = _SaveTask(key, payload)
            self._inflight.add(task)
            self._bg_pool.start(task)
