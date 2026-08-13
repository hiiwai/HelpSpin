"""Type-to-filter for the dataset browser.

Matches sample names and PULPROG. Three rules, and the second one is the whole
reason this file was rewritten:

1. a sample matches on its own name, or on the PULPROG/nucleus of any
   experiment in it -- so a query keeps the sample that contains the hit;
2. **an experiment under a sample that matched BY NAME is always shown.**
   Qt's recursive filtering only propagates matches UPWARDS: it keeps the
   ancestors of a matching row, and says nothing about the descendants of one.
   So filtering on a sample name kept the sample row and then tested each
   experiment row against the same text -- "2607" is not in "1", "11" or
   "21" -- and hid every child. Expanding a filtered sample showed nothing at
   all, which is indistinguishable from a browser that will not open;
3. a query that names a pulse programme still narrows within the sample, so
   "zg30" shows only the experiments that ran it.

Matching is done here rather than through QSortFilterProxyModel's recursive
mode because the model can answer "does this sample contain a match?" from the
cached index -- including for samples that have never been expanded, which
recursive filtering cannot reach (it reads rowCount, and an unfetched node
reports none). That is what makes a PULPROG query search the whole root.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt

from .dataset_model import COL_NAME, COL_PULPROG, NodeKind


class DatasetFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Our own filterAcceptsRow already consults descendants through the
        # index, so Qt's recursive pass would walk every fetched experiment of
        # every sample again for the same answer, on every keystroke.
        self.setRecursiveFilteringEnabled(False)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self._needle = ""

    # -- keep the lowercased needle in step with whatever set the filter -----

    def setFilterRegularExpression(self, pattern) -> None:
        # Called with a QRegularExpression or a plain string depending on the
        # caller; both have to end up setting _needle, or the filter silently
        # matches on a stale one.
        text = pattern if isinstance(pattern, str) else pattern.pattern()
        self._needle = (text or "").strip().lower()
        super().setFilterRegularExpression(pattern)

    def setFilterFixedString(self, text: str) -> None:
        self._needle = (text or "").strip().lower()
        super().setFilterFixedString(text)

    def needle(self) -> str:
        return self._needle

    # -- matching ------------------------------------------------------------

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        needle = self._needle
        if not needle:
            return True

        model = self.sourceModel()
        if model is None:
            return True
        index = model.index(source_row, COL_NAME, source_parent)
        node = index.internalPointer()
        if node is None:
            return True

        if node.kind is NodeKind.DATA_ROOT:
            return model.root_matches(node, needle)
        if node.kind is NodeKind.SAMPLE:
            return model.sample_matches(node, needle)

        # An experiment. Its sample matching BY NAME means the user asked for
        # that sample -- show all of it, or the row they filtered for cannot
        # be opened. Otherwise fall back to matching this row itself, which is
        # what makes a PULPROG query narrow within a sample.
        sample = node.parent
        if sample is not None and needle in sample.display_name.lower():
            return True
        if needle in node.display_name.lower():
            return True
        pulprog = model.data(model.index(source_row, COL_PULPROG, source_parent))
        return needle in (pulprog or "").lower()
