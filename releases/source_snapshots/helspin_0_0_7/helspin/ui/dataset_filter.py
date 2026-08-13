"""Type-to-filter for the dataset browser.

Matches on sample name (any tree level's display name) and PULPROG (expno rows
only), recursively -- a match at the expno level keeps its parent sample
visible, which is what makes filtering usable in a tree rather than only in a
flat list.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt

from .dataset_model import COL_NAME, COL_PULPROG


class DatasetFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        pattern = self.filterRegularExpression().pattern()
        if not pattern:
            return True

        model = self.sourceModel()
        name_index = model.index(source_row, COL_NAME, source_parent)
        name = model.data(name_index) or ""
        pulprog_index = model.index(source_row, COL_PULPROG, source_parent)
        pulprog = model.data(pulprog_index) or ""

        needle = pattern.lower()
        return needle in name.lower() or needle in pulprog.lower()
