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
    QDockWidget,
    QSplitter,
    QToolBar,
    QWidget,
)
from PySide6.QtCore import Qt

from . import __version__
from .core.settings import (
    load_data_roots,
    load_slot_styles,
    save_data_roots,
    save_slot_styles,
)
from .domain.ports import DataRoot
from .ui.adjustment_bar import AdjustmentBar
from .ui.browser import DatasetBrowser
from .ui.preferences_dialog import PreferencesDialog
from .ui.spectrum_canvas import SpectrumCanvas
from .ui.spectrum_list_panel import SpectrumListPanel

APP_TITLE = "HelSpin"


class _ExplorerDock(QDockWidget):
    """Dock whose close button returns it to the main window.

    Qt's default is to HIDE a closed dock. For a floating explorer that means
    the window disappears with no obvious way to get it back -- the reported
    problem. Intercepting closeEvent and re-docking instead keeps it always
    reachable. visibilityChanged was tried first and proved unreliable: it can
    fire before isFloating() reflects the new state.
    """

    def __init__(self, title, owner):
        super().__init__(title, owner)
        self._owner = owner

    def closeEvent(self, event):
        if self.isFloating():
            event.ignore()
            self._owner._dock_browser()
            return
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {__version__}")
        self.resize(1000, 640)

        self._browser = DatasetBrowser(load_data_roots())
        # The canvas is a real, drop-ready spectrum view from the start --
        # no 'New Figure' step required. Drag datasets straight onto it.
        self._canvas = SpectrumCanvas()
        # Appearance saved from a previous run becomes the default.
        _saved_styles = load_slot_styles()
        if _saved_styles:
            self._canvas.apply_styles(_saved_styles)
        self._adjustment_bar = AdjustmentBar()

        # Left/centre split: browser vs. canvas. Centre gets the lion's
        # share of the space -- the browser only needs to be wide enough to
        # read sample names and PULPROG comfortably.
        self._spectrum_list = SpectrumListPanel()
        # The browser lives in a DOCK so it can be undocked into its own
        # window and widened independently -- Bruker sample names are long
        # enough that a fixed side panel truncates them badly.
        self._browser_dock = _ExplorerDock("Data explorer", self)
        self._browser_dock.setObjectName("browser_dock")
        self._browser_dock.setWidget(self._browser)
        self._browser_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self._browser_dock.setFeatures(
            QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetClosable
        )
        self.addDockWidget(Qt.LeftDockWidgetArea, self._browser_dock)
        self._browser_dock.topLevelChanged.connect(
            lambda _floating: self._sync_dock_button()
        )
        self._browser_dock.visibilityChanged.connect(
            self._on_dock_visibility_changed
        )

        self._horizontal_split = QSplitter(Qt.Horizontal)
        self._horizontal_split.addWidget(self._canvas)
        self._horizontal_split.addWidget(self._spectrum_list)
        self._horizontal_split.setStretchFactor(0, 1)
        self._horizontal_split.setStretchFactor(1, 0)
        self._horizontal_split.setSizes([760, 220])
        # Force the canvas to take every pixel the window can give it.
        # Without an explicit expanding policy the central area could keep a
        # stale size after the explorer dock was floated, leaving dead grey
        # space around the plot.
        self._canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas.setMinimumSize(200, 150)
        self._spectrum_list.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Expanding
        )
        self._spectrum_list.setMaximumWidth(320)
        self._horizontal_split.setCollapsible(0, False)

        # That, plus the adjustment bar, stacked vertically.
        self._vertical_split = QSplitter(Qt.Vertical)
        self._vertical_split.addWidget(self._horizontal_split)
        self._vertical_split.addWidget(self._adjustment_bar)
        self._vertical_split.setStretchFactor(0, 1)
        self._vertical_split.setStretchFactor(1, 0)
        self.setCentralWidget(self._vertical_split)

        self._build_toolbar()
        self._build_menu()

        # The canvas is live immediately, so the ppm controls are usable from
        # the start rather than gated behind creating a figure first.
        self._adjustment_bar.set_enabled_for_figure(True)
        self._adjustment_bar.rangeChanged.connect(self._canvas.set_ppm_range)
        self._adjustment_bar.fullRequested.connect(self._on_full_range)
        saved_styles = load_slot_styles()
        if saved_styles:
            self._canvas.apply_styles(saved_styles)
        self._canvas.loadFailed.connect(self._on_load_failed)
        self._canvas.imageSaved.connect(
            lambda p: self.statusBar().showMessage(f"Saved {p}", 6000)
        )
        self._canvas.spectrumAdded.connect(self._on_spectrum_added)
        self._canvas.tracesChanged.connect(self._sync_spectrum_list)
        self._spectrum_list.selectionChanged.connect(self._on_list_selection)
        self._spectrum_list.yScaleChanged.connect(self._canvas.set_y_scale)
        self._spectrum_list.yOffsetChanged.connect(self._canvas.set_y_offset)
        self._spectrum_list.visibilityToggled.connect(
            self._canvas.set_trace_visible
        )
        self._spectrum_list.colorChangeRequested.connect(
            self._canvas.set_trace_color
        )
        self._spectrum_list.removeRequested.connect(self._on_remove_requested)
        self._spectrum_list.moveToBottomRequested.connect(
            self._canvas.move_to_bottom
        )
        self._canvas.cursorMoved.connect(self._on_cursor_moved)
        self._sync_spectrum_list()

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
        self._detach_action = toolbar.addAction(
            "Detach Explorer", self._toggle_browser_window
        )
        self._detach_action.setCheckable(True)
        self._detach_action.setToolTip(
            "Open the data explorer as its own resizable window"
        )
        refresh_action.setShortcut("F5")   # the conventional refresh key
        # Two checkable buttons, the ACTIVE one checked (and so highlighted
        # by the platform style). A single button labelled with the current
        # mode was reported as reading backwards -- it was ambiguous whether
        # the label named the current state or the action. Two buttons where
        # exactly one is lit removes the ambiguity entirely.
        self._overlay_action = toolbar.addAction("Overlay", self._use_overlay)
        self._overlay_action.setCheckable(True)
        self._overlay_action.setChecked(True)
        self._overlay_action.setToolTip("Show all spectra on the same axis")
        self._stacked_action = toolbar.addAction("Stacked", self._use_stacked)
        self._stacked_action.setCheckable(True)
        self._stacked_action.setToolTip("Offset each spectrum vertically")
        # "Auto Y" scales each spectrum individually so they are all legible;
        # "Fit Y" only re-fits the frame. With intensities differing by orders
        # of magnitude, the frame alone cannot make a weak spectrum visible.
        toolbar.addAction("Auto Y", self._auto_scale)
        toolbar.addAction("Same noise", self._normalise_noise)
        toolbar.addAction("Fit Y", self._canvas.reset_y_limits)
        toolbar.addAction("Bottom All", self._canvas.move_all_to_bottom)
        self._grid_action = toolbar.addAction("Grid", self._toggle_grid)
        self._grid_action.setCheckable(True)
        self._grid_action.setToolTip("Faint reference grid")
        toolbar.addAction("Clear", self._clear_canvas)
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
        file_menu.addAction("&Clear Canvas", self._clear_canvas)
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

    def _use_overlay(self) -> None:
        self._canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_OVERLAY)
        self._sync_arrangement_buttons()

    def _use_stacked(self) -> None:
        self._canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
        self._sync_arrangement_buttons()

    def _sync_arrangement_buttons(self) -> None:
        """Exactly one button checked, always matching the canvas."""
        overlay = (
            self._canvas.arrangement() == SpectrumCanvas.ARRANGEMENT_OVERLAY
        )
        self._overlay_action.setChecked(overlay)
        self._stacked_action.setChecked(not overlay)

    def _toggle_grid(self) -> None:
        self._canvas.set_grid_visible(self._grid_action.isChecked())

    def _on_cursor_moved(self, ppm: float, intensity: float) -> None:
        """Live readout, so the crosshair is informative and not just decorative."""
        self.statusBar().showMessage(f"{ppm:.4f} ppm    {intensity:.4g}")

    def _clear_canvas(self) -> None:
        """Full reset: traces, selection, ppm range, and the range boxes."""
        self._canvas.clear()
        self._adjustment_bar.set_range(0.0, 0.0)
        self._sync_spectrum_list()
        self.statusBar().showMessage("Canvas cleared", 3000)

    def _on_spectrum_added(self, label: str) -> None:
        """Seed the ppm boxes from real data the first time something loads.

        They start at 0.000/0.000, which is not a usable range -- typing into
        them before anything is loaded produced the degenerate near-zero-width
        view seen in testing. Filling them from the loaded spectra makes the
        controls meaningful the moment there is something to adjust.
        """
        self.statusBar().showMessage(f"Loaded {label}", 4000)
        bounds = self._canvas.ppm_bounds()
        if bounds is not None:
            self._adjustment_bar.set_range(*bounds)
        # With more than one spectrum loaded, intensities can differ by
        # orders of magnitude; auto-scale so a weak one is not invisible
        # the moment it arrives. Still adjustable afterwards.
        if len(self._canvas.traces) > 1:
            self._canvas.autoscale_traces()

    def _on_full_range(self) -> None:
        """Fit to all data AND put those numbers in the boxes, so the controls
        always show the range actually on screen rather than a stale pair."""
        self._canvas.full_range()
        bounds = self._canvas.ppm_bounds()
        if bounds is not None:
            self._adjustment_bar.set_range(*bounds)
        # With more than one spectrum loaded, intensities can differ by
        # orders of magnitude; auto-scale so a weak one is not invisible
        # the moment it arrives. Still adjustable afterwards.
        if len(self._canvas.traces) > 1:
            self._canvas.autoscale_traces()

    def _toggle_browser_window(self) -> None:
        """Float the explorer into its own window, or dock it back.

        Re-docking previously failed because the button's label was the only
        record of the state: dragging the dock out by its title bar, or
        closing the floating window, left the label and the real state
        disagreeing, after which the button did the wrong thing. State is now
        read from the dock itself and kept in sync by _sync_dock_button.
        """
        if self._browser_dock.isFloating():
            self._dock_browser()
        else:
            self._browser_dock.setFloating(True)
            self._browser_dock.show()
            self._browser_dock.resize(640, 700)
        self._sync_dock_button()

    def _dock_browser(self) -> None:
        """Put the explorer back in the main window and restore the layout.

        setFloating(False) alone was not enough in practice: a dock that has
        been dragged out keeps a floating geometry, and simply clearing the
        flag left it mis-sized with the central area not re-laid-out. Removing
        and re-adding it forces Qt to rebuild the dock area properly.
        """
        self.removeDockWidget(self._browser_dock)
        self._browser_dock.setFloating(False)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._browser_dock)
        self._browser_dock.setVisible(True)
        self._browser_dock.show()
        # Give it a sane width again -- a floated dock can come back at its
        # floating size and squeeze the plot to nothing.
        self.resizeDocks([self._browser_dock], [300], Qt.Horizontal)
        self._sync_dock_button()

    def _sync_dock_button(self) -> None:
        """Label always reflects what a click will DO, from the real state."""
        floating = self._browser_dock.isFloating()
        self._detach_action.setChecked(floating)
        self._detach_action.setText(
            "Dock Explorer" if floating else "Detach Explorer"
        )

    def _on_dock_visibility_changed(self, visible: bool) -> None:
        """A dock closed while floating must still be reachable: re-dock it
        rather than leaving it invisible with no way back."""
        if not visible and self._browser_dock.isFloating():
            # Closing the floating window must bring the explorer home, not
            # leave it invisible with no way to get it back.
            self._dock_browser()
            return
        self._sync_dock_button()

    def _normalise_noise(self) -> None:
        """Equalise noise levels so peak heights are directly comparable."""
        if self._canvas.normalise_to_noise():
            self.statusBar().showMessage(
                "Scaled to equal noise - peak heights are now comparable", 5000
            )
        else:
            self.statusBar().showMessage(
                "Could not estimate noise (need at least one real spectrum)",
                5000,
            )

    def _auto_scale(self) -> None:
        self._canvas.autoscale_traces()
        self.statusBar().showMessage("Auto-scaled each spectrum", 3000)

    def _on_list_selection(self, row: int) -> None:
        self._canvas.select_trace(row if row >= 0 else None)

    def _on_remove_requested(self, row: int) -> None:
        traces = self._canvas.traces
        if 0 <= row < len(traces):
            self._canvas.remove_trace(traces[row].path)

    def _sync_spectrum_list(self) -> None:
        self._spectrum_list.set_traces(
            self._canvas.traces, self._canvas.selected_index()
        )

    def _on_load_failed(self, path: str, message: str) -> None:
        """A file that will not load must say so, not vanish silently."""
        from pathlib import Path as _P
        self.statusBar().showMessage(f"{_P(path).name}: {message}", 8000)

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
        """Colour, line style and line width for each of eight spectrum slots.

        Cancel changes nothing: the dialog has no side effects, values are
        only read back and applied here on accept.
        """
        dialog = PreferencesDialog(
            styles=self._canvas.slot_styles(),
            grid_spacing=self._canvas.grid_spacing_ppm(),
            x_decimals=self._canvas.x_decimals(),
            label_scale=self._canvas.label_scale(),
            parent=self,
        )
        if dialog.exec() != PreferencesDialog.Accepted:
            return
        styles = dialog.styles()
        self._canvas.apply_styles(styles)
        save_slot_styles(styles)   # default for next run
        self._sync_spectrum_list()

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
