"""The dataset browser widget.

Tree view over DatasetTreeModel/DatasetFilterProxy, a filter box, and lazy
population wired to expansion. This is the primary input path (handoff 7A):
paste and the TopSpin bridge are conveniences layered on top of this.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QModelIndex, QPoint, QRegularExpression, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QPainter, QPixmap
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
from .dataset_model import (
    COL_DATE,
    COL_DIM,
    COL_NAME,
    COL_NUCLEUS,
    COL_PULPROG,
    COLUMNS,
    MIME_DATASET,
    DatasetPopulator,
    DatasetTreeModel,
    Node,
    NodeKind,
)

_DRAG_PIXMAP_SIZE = QSize(140, 28)


def _drag_label(row_count: int) -> str:
    return f"{row_count} dataset" + ("" if row_count == 1 else "s")


def _make_drag_pixmap(label: str) -> QPixmap:
    """A small, cheap, hand-drawn pixmap instead of Qt's default drag image.

    Qt's own docs describe the default behaviour: QTreeView.startDrag()
    renders the dragged row(s) -- across every column, in the real widget
    style -- into a pixmap once, at the moment the drag begins, before the
    drag can visually start at all. For a 5-column row under a native
    macOS style that render is a real, avoidable cost paid on every single
    drag start. This pixmap is a few characters of text on a flat
    background: strictly cheaper than the default in every case, so this
    change cannot make things worse even where it isn't the whole story.
    """
    pixmap = QPixmap(_DRAG_PIXMAP_SIZE)
    pixmap.fill(Qt.GlobalColor.white)
    painter = QPainter(pixmap)
    painter.setPen(Qt.GlobalColor.black)
    painter.drawRect(pixmap.rect().adjusted(0, 0, -1, -1))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, label)
    painter.end()
    return pixmap


class DatasetTreeView(QTreeView):
    """QTreeView is itself the drag source: dragging is native once
    setDragEnabled(True) and the model implements mimeData/mimeTypes/flags.

    startDrag() is overridden to supply a cheap custom pixmap rather than
    accept Qt's default per-row rendering -- see _make_drag_pixmap's
    docstring. The QDrag construction is split into _build_drag() so tests
    can inspect it directly without ever calling drag.exec(), which is a
    genuinely blocking native modal call (the same category of hang already
    hit twice elsewhere in this codebase with QDialog.exec()/QMenu.exec()).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def _build_drag(self, indexes: list[QModelIndex]) -> QDrag | None:
        mime = self.model().mimeData(indexes)
        if mime is None or not mime.hasFormat(MIME_DATASET):
            return None

        # mime.formats() is NOT sufficient on its own: the model's
        # mimeData() always calls setData(MIME_DATASET, ...), even when the
        # encoded payload is an empty list (e.g. every selected row was a
        # SAMPLE, not an EXPNO) -- so the format is present regardless of
        # whether anything draggable was actually selected. The decoded
        # content is what actually matters here.
        try:
            payload = json.loads(bytes(mime.data(MIME_DATASET)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not payload:
            return None

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(_make_drag_pixmap(_drag_label(len(payload))))
        return drag

    def startDrag(self, supportedActions) -> None:
        indexes = self.selectedIndexes()
        if not indexes:
            return
        drag = self._build_drag(indexes)
        if drag is None:
            return
        drag.exec(supportedActions)


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
        self._configure_columns()
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

    def _configure_columns(self) -> None:
        """Column sizing, fixing the reported bug: the Name column could not
        be widened and long sample names were clipped.

        The cause was `Stretch` on the Name column. Stretch makes a section
        fill leftover space, but a side effect is that a stretched section
        is NOT user-resizable -- so the handle did nothing, and whenever the
        metadata columns took their share, Name got squeezed below what the
        names needed.

        The fix:
          - Name is Interactive (the user can drag its edge freely) with a
            generous default width, wide enough for a typical Bruker sample
            name without truncation.
          - The metadata columns (PULPROG / Nucleus / Dim / Date) are
            Interactive too but default to ResizeToContents-derived widths,
            so they take only what they need and leave the rest for Name.
          - stretchLastSection is OFF: otherwise Date would expand to eat all
            leftover width, re-creating the squeeze on Name from the right.
        A horizontal scrollbar appears if the total exceeds the panel width,
        which is the correct behaviour when the user has widened Name past
        what fits, rather than silently clipping text.
        """
        header = self._tree.header()
        header.setStretchLastSection(False)
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.Interactive)

        # Give every column a deliberate starting width. The metadata columns
        # get fixed sensible defaults rather than ResizeToContents, because at
        # construction time there is no data in them yet (expnos are not even
        # probed until expanded), so content-sizing would collapse them to
        # just their header text and they would not re-expand on their own.
        # All remain user-adjustable afterwards.
        header.resizeSection(COL_NAME, 320)
        header.resizeSection(COL_PULPROG, 110)
        header.resizeSection(COL_NUCLEUS, 60)
        header.resizeSection(COL_DIM, 45)
        header.resizeSection(COL_DATE, 100)

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
        """Re-scan one node, merging in new files and dropping vanished ones.

        Because the model's refresh() merges in place rather than clearing and
        rebuilding, expansion state and already-loaded metadata are preserved
        automatically -- refreshing no longer collapses open folders. Any
        newly-appeared expno rows are probed so their metadata columns fill in,
        exactly as on first expansion.
        """
        node: Node = source_index.internalPointer()
        if node is None or node.kind is NodeKind.EXPNO:
            return
        new_children = self._model.refresh(node) or []
        for child in new_children:
            self._populator.probe_row(child)

    def refresh_all(self) -> None:
        """Refresh every data root (new/removed samples) and every sample that
        is currently fetched (new/removed expnos), all as in-place merges that
        preserve expansion. Walks fetched nodes only -- unfetched ones will
        scan fresh when first expanded, so there is nothing to refresh there.
        """
        for node in self._fetched_refreshable_nodes():
            index = self._model._index_for(node)
            self.refresh_node(index)

    def _fetched_refreshable_nodes(self) -> list:
        """Every data-root and sample node that has been fetched, so refresh
        reaches open samples' expnos too -- not just top-level samples. Order
        does not matter: each refresh is an independent in-place merge."""
        out = []
        for row in range(self._model.rowCount()):
            root_node = self._model.index(row, 0).internalPointer()
            if root_node is None:
                continue
            out.append(root_node)
            for child in root_node.children or []:
                if child.kind is NodeKind.SAMPLE and child.is_fetched:
                    out.append(child)
        return out

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

