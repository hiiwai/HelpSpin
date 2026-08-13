"""The dataset browser widget.

Tree view over DatasetTreeModel/DatasetFilterProxy, a filter box, and lazy
population wired to expansion. This is the primary input path (handoff 7A):
paste and the TopSpin bridge are conveniences layered on top of this.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QRegularExpression, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QMenu,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..domain.ports import DataRoot
from .dataset_filter import DatasetFilterProxy
from .dataset_model import COL_NAME, DatasetPopulator, DatasetTreeModel, Node, NodeKind


class DatasetTreeView(QTreeView):
    """QTreeView is itself the drag source: dragging is native once
    setDragEnabled(True) and the model implements mimeData/mimeTypes/flags."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)


class DatasetBrowser(QWidget):
    """Filter box over a tree view of one or more configured data roots.

    Population is asynchronous via DatasetPopulator: expanding a node starts a
    worker-thread scan rather than blocking the GUI thread, which matters
    because a data root is frequently a network share.

    There is no automatic file-watching (see DatasetTreeModel.refresh's
    docstring for why: inotify/FSEvents are unreliable over the network
    shares Bruker data commonly lives on). Refreshing is explicit, via the
    tree's right-click menu or refresh_all() below.
    """

    scanFailed = Signal(str, str)

    def __init__(self, data_roots: list[DataRoot], reader=None, parent=None):
        super().__init__(parent)
        self._model = DatasetTreeModel(data_roots, reader=reader)
        self._populator = DatasetPopulator(self._model)
        self._proxy = DatasetFilterProxy()
        self._proxy.setSourceModel(self._model)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter by sample name or PULPROG…")
        self._filter_edit.textChanged.connect(self._on_filter_changed)

        self._tree = DatasetTreeView()
        self._tree.setModel(self._proxy)
        self._tree.header().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self._tree.expanded.connect(self._on_expanded)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._filter_edit)
        layout.addWidget(self._tree)

        self._model.scanFailed.connect(self.scanFailed)

        # Data roots are usually few; fetching them eagerly (not their
        # samples) makes the top level browsable and filterable immediately,
        # without the unbounded cost of expanding every sample underneath.
        for row in range(self._model.rowCount()):
            self._request_populate(self._model.index(row, 0))

    # -- population -----------------------------------------------------

    def _on_expanded(self, proxy_index: QModelIndex) -> None:
        source_index = self._proxy.mapToSource(proxy_index)
        self._request_populate(source_index)

    def _request_populate(self, source_index: QModelIndex) -> None:
        node: Node = source_index.internalPointer()
        if node is None or node.is_fetched:
            return
        self._populator.populate(node)

    def _on_filter_changed(self, text: str) -> None:
        self._proxy.setFilterRegularExpression(QRegularExpression(text))
        # See DatasetFilterProxy's docstring / the module's known-limitation
        # test: matching only reaches already-fetched nodes. Data roots are
        # pre-fetched at construction (above), so filtering by sample name
        # works immediately; PULPROG matching requires the sample to have
        # been expanded at least once first. A full background index is the
        # documented follow-up, not part of this slice.

    def add_data_root(self, root: DataRoot) -> None:
        """Add a root and start populating it immediately.

        The counterpart to the model's add_data_root: this is what the
        application's 'Add data root...' action calls, so the new root
        appears populated without the user having to expand it once first.
        """
        self._model.add_data_root(root)
        new_row = self._model.rowCount() - 1
        self._request_populate(self._model.index(new_row, 0))

    def remove_data_root(self, row: int) -> None:
        self._model.remove_data_root(row)

    def data_roots(self) -> list[DataRoot]:
        return self._model.data_roots()

    # -- refresh -----------------------------------------------------------

    def refresh_node(self, source_index: QModelIndex) -> None:
        """Re-scan one node: picks up files added to (or removed from) that
        exact directory since it was last populated. A data root's refresh
        finds new or deleted SAMPLE directories; a sample's refresh finds new
        or deleted EXPNOS within it -- refreshing a data root does not, by
        itself, refresh the samples already open beneath it, since those are
        separate nodes with their own fetch state. Right-click the specific
        folder you added files to."""
        node: Node = source_index.internalPointer()
        if node is None or node.kind is NodeKind.EXPNO:
            return
        self._model.refresh(node)
        self._populator.populate(node)

    def refresh_all(self) -> None:
        """Re-scans every configured data root for new or removed samples.

        Does not descend into already-expanded samples (see refresh_node's
        docstring) -- that would mean discarding and recreating the very
        sample nodes whose children we would also want to refresh, which is
        a real ordering hazard, not just extra work. Right-click a specific
        sample to refresh its experiments.
        """
        for row in range(self._model.rowCount()):
            self.refresh_node(self._model.index(row, 0))

    def _build_context_menu(self, source_index: QModelIndex | None) -> QMenu:
        """Menu construction only -- does not call exec(). Split out so
        tests can inspect and trigger actions directly without ever invoking
        the real, blocking modal exec() call at all. (Monkeypatching .exec
        on QMenu itself -- a built-in Shiboken-wrapped class, not a Python
        subclass -- turned out not to reliably override real dispatch the
        way it does for a custom QDialog subclass elsewhere in this
        codebase; this split sidesteps the question entirely rather than
        depending on it.)
        """
        menu = QMenu(self._tree)
        if source_index is not None and source_index.isValid():
            node: Node = source_index.internalPointer()
            if node is not None and node.kind is not NodeKind.EXPNO:
                label = "Refresh" if node.kind is NodeKind.SAMPLE else "Refresh Data Root"
                menu.addAction(label, lambda: self.refresh_node(source_index))
                menu.addSeparator()
        menu.addAction("Refresh All", self.refresh_all)
        return menu

    def _on_context_menu(self, pos: QPoint) -> None:
        proxy_index = self._tree.indexAt(pos)
        source_index = self._proxy.mapToSource(proxy_index) if proxy_index.isValid() else None
        menu = self._build_context_menu(source_index)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    # -- selection --------------------------------------------------------

    def selected_source_indexes(self) -> list[QModelIndex]:
        proxy_indexes = self._tree.selectionModel().selectedRows(COL_NAME)
        return [self._proxy.mapToSource(i) for i in proxy_indexes]

    @property
    def model(self) -> DatasetTreeModel:
        return self._model

    @property
    def tree(self) -> DatasetTreeView:
        return self._tree

