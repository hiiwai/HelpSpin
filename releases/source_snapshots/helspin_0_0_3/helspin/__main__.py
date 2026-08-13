"""Application entry point.

Shell layout, modelled on the reference screening-app screenshot the user
provided: a button toolbar across the top, an explorer down the left, the
main working area in the centre, and an adjustable control strip along the
bottom.

    +-----------------------------------------------------------+
    |  [Add Data Root]  [New Figure]  [Preferences]   [About]   |  <- toolbar
    +---------------+---------------------------------------------+
    |               |                                             |
    |   dataset     |            canvas placeholder               |
    |   browser     |     (milestone 3 replaces this content)     |
    |  (left panel) |                                             |
    |               |                                             |
    +---------------+---------------------------------------------+
    |  ppm  [Full]  [ left ] to [ right ]      recent ranges v    |  <- adjustment bar
    +-----------------------------------------------------------+

Everything left of "Preferences" and below the canvas is real and tested,
including "New Figure" now: it opens a real dialog, builds a real (tested)
Project via domain.layout, and swaps the placeholder for a working BoxCanvas
that accepts drags from the browser. Actually plotting a dropped spectrum is
still not implemented (see nmrglue_reader.py) -- a filled slot shows its
colour and the dataset's label, not a trace. "Preferences" remains a stub,
named honestly as not-yet-implemented rather than left silently absent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QWidget,
)
from PySide6.QtCore import Qt

from . import __version__
from .core.settings import load_data_roots, save_data_roots
from .domain.layout import build_project
from .domain.ports import DataRoot
from .ui.adjustment_bar import AdjustmentBar
from .ui.box_canvas import BoxCanvas
from .ui.browser import DatasetBrowser
from .ui.canvas_placeholder import CanvasPlaceholder
from .ui.new_figure_dialog import NewFigureDialog

APP_TITLE = "HelSpin"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {__version__}")
        self.resize(1000, 640)

        self._browser = DatasetBrowser(load_data_roots())
        self._canvas = CanvasPlaceholder()
        self._adjustment_bar = AdjustmentBar()

        # Left/centre split: browser vs. canvas. Centre gets the lion's
        # share of the space -- the browser only needs to be wide enough to
        # read sample names and PULPROG comfortably.
        self._horizontal_split = QSplitter(Qt.Horizontal)
        self._horizontal_split.addWidget(self._browser)
        self._horizontal_split.addWidget(self._canvas)
        self._horizontal_split.setStretchFactor(0, 0)
        self._horizontal_split.setStretchFactor(1, 1)
        self._horizontal_split.setSizes([260, 740])

        # That, plus the adjustment bar, stacked vertically.
        self._vertical_split = QSplitter(Qt.Vertical)
        self._vertical_split.addWidget(self._horizontal_split)
        self._vertical_split.addWidget(self._adjustment_bar)
        self._vertical_split.setStretchFactor(0, 1)
        self._vertical_split.setStretchFactor(1, 0)
        self.setCentralWidget(self._vertical_split)

        self._build_toolbar()
        self._build_menu()

        if not self._browser.data_roots():
            self.statusBar().showMessage(
                "Use \u201cAdd Data Root\u2026\u201d to point HelSpin at your "
                "Bruker data.",
                0,
            )

    # -- toolbar (the reference app's button row) ----------------------------

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        toolbar.addAction("Add Data Root\u2026", self._add_data_root)
        refresh_action = toolbar.addAction("Refresh All", self._browser.refresh_all)
        refresh_action.setShortcut("F5")   # the conventional refresh key
        toolbar.addAction("New Figure\u2026", self._new_figure)
        toolbar.addSeparator()
        toolbar.addAction("Preferences\u2026", self._preferences)
        toolbar.addSeparator()
        toolbar.addAction("About", self._about)

        # The reference screenshot this shell is modelled on has Quit as an
        # explicit, visible button at the far right of the toolbar -- not
        # only reachable through File. A stretch spacer pushes it there.
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        quit_action = toolbar.addAction("Quit", self.close)
        # Explicit role rather than relying on Qt's text-based heuristic:
        # on macOS an action literally named "Quit" is auto-relocated into
        # the application menu by default, which is fine for the File-menu
        # Quit but would also silently empty this toolbar button if it
        # applied here too. NoRole keeps this one exactly where it's put.
        quit_action.setMenuRole(QAction.MenuRole.NoRole)

        self.addToolBar(toolbar)

    def _build_menu(self) -> None:
        """A conventional menu bar as well as the toolbar -- both are cheap
        and different users reach for different ones; the toolbar mirrors
        the reference app, the menu is what most desktop users expect."""
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("&Add Data Root…", self._add_data_root)
        file_menu.addAction("&Refresh All", self._browser.refresh_all).setShortcut("F5")
        file_menu.addAction("&New Figure…", self._new_figure)
        file_menu.addSeparator()
        file_menu.addAction("&Preferences…", self._preferences)
        file_menu.addSeparator()
        file_menu.addAction("&Quit", self.close)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&About", self._about)

    # -- actions -------------------------------------------------------------

    def _add_data_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a Bruker data root"
        )
        if not directory:
            return
        name, ok = QInputDialog.getText(
            self, "Name this data root", "Display name (e.g. '600 MHz'):"
        )
        if not ok or not name.strip():
            return

        root = DataRoot(name=name.strip(), path=Path(directory))
        self._browser.add_data_root(root)
        save_data_roots(self._browser.data_roots())
        self.statusBar().clearMessage()

    def _new_figure(self) -> None:
        """Opens NewFigureDialog; on Create, builds the project (domain
        logic, already tested) and swaps CanvasPlaceholder for a real
        BoxCanvas showing the generated, empty, colour-assigned layout.

        Dropping datasets from the browser onto the resulting slots works
        (see box_canvas.py); actually plotting them does not yet, since
        read_1d/read_2d are not implemented. A filled slot shows its colour
        and the dropped dataset's label, not a spectrum -- that boundary is
        stated in the canvas's own placeholder text and in the README.
        """
        dialog = NewFigureDialog(self)
        if dialog.exec() != NewFigureDialog.Accepted:
            return

        project = build_project(dialog.request())
        canvas = BoxCanvas(project, self)
        self._replace_canvas(canvas)

    def _replace_canvas(self, new_canvas) -> None:
        """Swaps the centre panel.

        QSplitter recomputes each child's share from size hints whenever a
        widget is inserted/removed -- confirmed by measurement, not assumed:
        without reasserting sizes explicitly, a bare BoxCanvas (no layout of
        its own; children are positioned by hand in _reposition) reports a
        small size hint next to the browser's tree view, and the split
        flips from roughly 26/74 to 70/30, handing most of the window to the
        browser right when the canvas is what the user just asked to see.
        """
        old = self._canvas
        index = self._horizontal_split.indexOf(old)
        sizes_before = self._horizontal_split.sizes()

        self._horizontal_split.insertWidget(index, new_canvas)
        old.setParent(None)
        old.deleteLater()
        self._canvas = new_canvas

        self._horizontal_split.setSizes(sizes_before)
        self._adjustment_bar.set_enabled_for_figure(True)

    def _preferences(self) -> None:
        """Stub: today, data roots are the only preference (added via the
        toolbar/menu action above). A dedicated dialog for editing existing
        roots, barcode keys, and sample-name patterns is not built yet."""
        QMessageBox.information(
            self,
            "Not yet implemented",
            "A dedicated preferences dialog isn't built yet. For now, "
            "data roots are added via \u201cAdd Data Root\u2026\u201d and "
            "are remembered automatically between runs.",
        )

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_TITLE}",
            f"{APP_TITLE} {__version__}\n\n"
            "Compare Bruker NMR spectra and build publication figures.\n"
            "This build includes the dataset browser only; the comparison "
            "canvas is not yet implemented.",
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="helspin")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the installed version and exit, without opening a window",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv[1:])
    if args.version:
        # Deliberately no QApplication here: this must work even on a
        # machine with no display at all, e.g. over SSH, purely to answer
        # "which version is installed" for troubleshooting.
        print(f"{APP_TITLE} {__version__}")
        return 0

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    # On macOS, a window opened by a plain Python script (as opposed to a
    # signed .app bundle) frequently appears BEHIND other windows or in an
    # inactive state, with no visible signal that anything happened at all --
    # this is what "the app hangs with no window" usually turns out to be.
    # raise_()/activateWindow() force it to the front and grab focus.
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
