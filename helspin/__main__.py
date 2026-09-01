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
import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import CONTACT_EMAIL, __version__
from .core.settings import (
    load_data_roots,
    load_display_prefs,
    load_slot_styles,
    save_data_roots,
    save_display_prefs,
    save_slot_styles,
)
from .domain.ports import DataRoot
from .ui.adjustment_bar import AdjustmentBar
from .ui.browser import DatasetBrowser
from .ui.preferences_dialog import PreferencesDialog
from .ui.spectrum_canvas import SpectrumCanvas
from .ui.spectrum_list_panel import SpectrumListPanel

APP_TITLE = "HelSpin"


def app_icon() -> QIcon:
    """The window / taskbar icon, or an empty QIcon if it is missing.

    Empty rather than raising: a missing resource should cost the icon, not
    the application. Qt scales the single 512px source to whatever the
    platform asks for.
    """
    path = Path(__file__).resolve().parent / "resources" / "icon.png"
    return QIcon(str(path)) if path.is_file() else QIcon()


class _ExplorerWindow(QWidget):
    """Top-level window that hosts the explorer while it is detached.

    QDockWidget was tried first and proved unreliable here: a floated dock kept
    a stale geometry, re-docking left the central area not re-laid-out, and
    closing the floating window could leave the tree drawn over the main
    window. Re-parenting the widget explicitly between the splitter and a
    plain window is predictable, and closing simply hands it back.
    """

    def __init__(self, owner):
        super().__init__(None)
        self._owner = owner
        self.setWindowTitle("Data explorer")
        self.setWindowIcon(app_icon())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._layout = layout

    def closeEvent(self, event):
        # Hand the explorer back rather than letting it disappear with the
        # window -- otherwise there is no way to get it again.
        self._owner._attach_browser()
        event.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} {__version__}")
        self.setWindowIcon(app_icon())
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
        self._explorer_window = None
        self._horizontal_split = QSplitter(Qt.Horizontal)
        self._horizontal_split.addWidget(self._browser)
        self._horizontal_split.addWidget(self._canvas)
        self._horizontal_split.addWidget(self._spectrum_list)
        self._horizontal_split.setStretchFactor(0, 0)
        self._horizontal_split.setStretchFactor(1, 1)
        self._horizontal_split.setStretchFactor(2, 0)
        self._horizontal_split.setSizes([280, 720, 220])
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
        self._horizontal_split.setCollapsible(1, False)

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
        self._adjustment_bar.rangeChanged.connect(self._on_main_range)
        self._adjustment_bar.fullRequested.connect(self._on_full_range)
        self._adjustment_bar.f1RangeChanged.connect(self._canvas.set_f1_range)
        self._adjustment_bar.zoomModeChanged.connect(self._canvas.set_zoom_mode)
        self._adjustment_bar.yZoomModeChanged.connect(
            self._canvas.set_y_zoom_mode
        )
        # A zoom performed ON THE PLOT has to be reflected in the range boxes,
        # or they show a range that is no longer displayed and the next Apply
        # jumps the view back.
        self._canvas.viewChanged.connect(self._sync_ranges_from_canvas)
        saved_styles = load_slot_styles()
        if saved_styles:
            self._canvas.apply_styles(saved_styles)
        self._browser.dataRootRemoved.connect(self._on_data_root_removed)
        # Indexing progress goes to the status bar. A browser that is quietly
        # walking a share must say so: silence for two minutes is
        # indistinguishable from a hang, which is exactly how the old
        # all-at-once index build was experienced.
        self._browser.statusChanged.connect(self._on_browser_status)
        self._canvas.loadFailed.connect(self._on_load_failed)
        self._canvas.modeChanged.connect(self._on_mode_changed)
        self._canvas.dimensionalityRefused.connect(
            self._on_dimensionality_refused
        )
        self._canvas.imageSaved.connect(
            lambda p: self.statusBar().showMessage(f"Saved {p}", 6000)
        )
        self._canvas.spectrumAdded.connect(self._on_spectrum_added)
        self._canvas.tracesChanged.connect(self._sync_spectrum_list)
        self._spectrum_list.selectionChanged.connect(self._on_list_selection)
        self._spectrum_list.yScaleChanged.connect(self._canvas.set_y_scale)
        self._spectrum_list.yOffsetChanged.connect(self._canvas.set_y_offset)
        self._spectrum_list.xOffsetChanged.connect(self._canvas.set_x_offset)
        self._spectrum_list.visibilityToggled.connect(
            self._canvas.set_trace_visible
        )
        self._spectrum_list.colorChangeRequested.connect(
            self._canvas.set_trace_color
        )
        self._spectrum_list.removeRequested.connect(self._on_remove_requested)
        self._spectrum_list.subtractRequested.connect(self._on_subtract)
        self._spectrum_list.addRequested.connect(self._on_add)
        self._spectrum_list.contourLevelsChanged.connect(
            self._canvas.set_contour_levels
        )
        self._spectrum_list.contourFactorChanged.connect(
            self._canvas.set_contour_factor
        )
        self._spectrum_list.contourBaseChanged.connect(
            self._canvas.set_contour_base_sigma
        )
        self._spectrum_list.combineRefused.connect(
            lambda msg: self.statusBar().showMessage(msg, 8000)
        )
        self._spectrum_list.labelOffsetChanged.connect(
            self._canvas.set_label_offset
        )
        self._spectrum_list.moveToBottomRequested.connect(
            self._canvas.move_to_bottom
        )
        # The cursor readout gets its OWN widget on the right of the status
        # bar. It used to call showMessage(), which is the same slot every
        # warning uses -- so "Canvas is in 1D mode, clear it first", "cannot
        # load ...", and every other explanation was wiped by the next mouse
        # movement over the plot, usually within a few milliseconds. That is
        # why failures read as "nothing happens" with a clean terminal.
        self._cursor_label = QLabel("")
        self.statusBar().addPermanentWidget(self._cursor_label)
        self._canvas.cursorMoved.connect(self._on_cursor_moved)
        self._palette_name = ""
        self._restore_display_prefs()
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
        self._labels_action = toolbar.addAction("Labels", self._toggle_labels)
        self._labels_action.setCheckable(True)
        self._labels_action.setChecked(True)
        self._labels_action.setToolTip(
            "Show each spectrum's name and pulse programme on the plot.\n"
            "Turn off for a figure whose caption names them, or when the "
            "names sit over a crowded 2D map."
        )
        clear_action = toolbar.addAction("Clear", self._clear_canvas)
        # Clear throws away every loaded spectrum, so it is marked out from
        # the adjustments beside it rather than looking like one of them.
        clear_font = clear_action.font()
        clear_font.setBold(True)
        clear_action.setFont(clear_font)
        clear_action.setToolTip("Remove all spectra and reset the canvas")
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
        file_menu.addSeparator()
        file_menu.addAction("&Save Session…", self._save_session).setShortcut("Ctrl+S")
        file_menu.addAction("&Open Session…", self._open_session).setShortcut("Ctrl+O")
        file_menu.addAction("&Refresh All", self._browser.refresh_all).setShortcut("F5")
        file_menu.addAction("&Clear Canvas", self._clear_canvas)
        file_menu.addSeparator()
        file_menu.addAction("&Preferences…", self._preferences)
        file_menu.addSeparator()
        file_menu.addAction("&Quit", self.close)

        # Edit comes AFTER File: File-then-Edit is the order every desktop
        # application uses, and menus are found by position as much as by
        # name. Menus appear in creation order, so this block must stay
        # below the File one.
        # Shortcuts are the standard ones every application already uses.
        # Deliberately Ctrl+Z / Ctrl+Shift+Z rather than a bespoke scheme:
        # muscle memory for undo is universal, and a program that answers it
        # with nothing feels broken however good its own scheme is.
        edit_menu = self.menuBar().addMenu("&Edit")
        self._undo_action = edit_menu.addAction("&Undo", self._undo)
        self._undo_action.setShortcut(QKeySequence.Undo)
        self._redo_action = edit_menu.addAction("&Redo", self._redo)
        self._redo_action.setShortcuts(
            [QKeySequence.Redo, QKeySequence("Ctrl+Y")]
        )
        self._canvas.historyChanged.connect(self._sync_history_actions)
        self._sync_history_actions()

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&Licence…", self._show_licence)
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

    def _toggle_labels(self) -> None:
        self._canvas.set_labels_visible(self._labels_action.isChecked())

    def _on_cursor_moved(self, x: float, y: float) -> None:
        """Position readout. Writes to its own permanent widget, never to the
        message area, so it cannot overwrite a warning the user needs.

        In 2D BOTH numbers are chemical shifts, and the second was being
        printed bare, formatted as if it were an intensity -- so the F1
        position was on screen the whole time without saying what it was or
        carrying a unit. Named per dimension, and both marked ppm.
        """
        digits = self._canvas.cursor_decimals()
        if self._canvas.mode() == "2D":
            # No unit: "F2" and "F1" already say these are chemical shifts,
            # and the pair was the longest thing in the status bar.
            self._cursor_label.setText(f"F2 {x:.{digits}f}   F1 {y:.{digits}f}")
        else:
            # "ppm" stays in 1D: the second number is an INTENSITY, and
            # without a unit on the first the pair would be ambiguous.
            self._cursor_label.setText(f"{x:.{digits}f} ppm   {y:.4g}")

    def _save_session(self) -> None:
        """Write the current view to a .helspin file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save session", "", "HelSpin session (*.helspin)"
        )
        if not path:
            return
        if not path.lower().endswith(".helspin"):
            path += ".helspin"
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self._canvas.session_state(), handle, indent=2)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
            return
        self.statusBar().showMessage(f"Session saved to {path}", 6000)

    def _open_session(self) -> None:
        """Restore a saved view, re-reading the spectra from disk."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open session", "", "HelSpin session (*.helspin)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
            failed = self._canvas.restore_session(state)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open session", str(exc))
            return
        self._sync_spectrum_list()
        self._adjustment_bar.set_mode(self._canvas.mode())
        bounds = self._canvas.ppm_bounds()
        if bounds:
            self._adjustment_bar.set_range(*bounds)
        if failed:
            # Say which spectra are missing rather than quietly restoring a
            # partial view that looks complete.
            QMessageBox.warning(
                self, "Some spectra could not be reloaded",
                "\n".join(failed[:10]),
            )
        self.statusBar().showMessage(
            f"Session restored ({len(self._canvas.traces)} spectra)", 6000
        )

    def _undo(self) -> None:
        if not self._canvas.undo():
            self.statusBar().showMessage("Nothing to undo.", 4000)

    def _redo(self) -> None:
        if not self._canvas.redo():
            self.statusBar().showMessage("Nothing to redo.", 4000)

    def _sync_history_actions(self) -> None:
        """Grey the menu entries out when there is nothing to go back to, so
        the menu says what is possible rather than offering a no-op."""
        self._undo_action.setEnabled(self._canvas.can_undo())
        self._redo_action.setEnabled(self._canvas.can_redo())
        self._sync_ranges_from_canvas()

    def _sync_ranges_from_canvas(self) -> None:
        """Push the canvas's current ranges into the range boxes."""
        if self._canvas.mode() == "2D":
            f2 = self._canvas.f2_range() or self._canvas.f2_bounds()
            f1 = self._canvas.f1_range() or self._canvas.f1_bounds()
            if f2:
                self._adjustment_bar.set_range(*f2)
            if f1:
                self._adjustment_bar.set_f1_range(*f1)
            return
        current = self._canvas.ppm_range() or self._canvas.ppm_bounds()
        if current:
            self._adjustment_bar.set_range(*current)

    def _on_browser_status(self, message: str) -> None:
        if message:
            self.statusBar().showMessage(message)
        else:
            self.statusBar().clearMessage()

    def closeEvent(self, event):
        """Stop background indexing, save the index, and close every window.

        Without the shutdown, the worker threads keep reading the share while
        Python tears the object graph down around them, and what the session
        learned about the root is discarded instead of making the next launch
        instant.

        Without closing the detached explorer, Quit left it on screen and the
        application alive behind it: Qt exits when the LAST window closes, so
        a second top-level window kept the process running with no main window
        to quit from. The explorer is re-attached first rather than merely
        hidden, so its close handler runs the same path it always does and
        cannot leave the widget parented to a window that is going away.
        """
        try:
            if self._explorer_window is not None:
                self._attach_browser()
            self._browser.shutdown()
        finally:
            super().closeEvent(event)

    def _on_data_root_removed(self) -> None:
        """Persist the removal, so it does not come back on the next run."""
        save_data_roots(self._browser.data_roots())
        self.statusBar().showMessage("Data root removed", 4000)

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

    def _on_main_range(self, high: float, low: float) -> None:
        """The main pair means F2 in 2D and the ppm axis in 1D."""
        if self._canvas.mode() == "2D":
            self._canvas.set_f2_range(high, low)
        else:
            self._canvas.set_ppm_range(high, low)

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
        """Detach the explorer into its own window, or bring it back."""
        if self._explorer_window is None:
            self._detach_browser()
        else:
            self._attach_browser()

    def _detach_browser(self) -> None:
        window = _ExplorerWindow(self)
        # Re-parent: takeWidget-style move out of the splitter.
        self._browser.setParent(window)
        window._layout.addWidget(self._browser)
        self._browser.show()
        window.resize(640, 720)
        window.show()
        window.raise_()
        self._explorer_window = window
        self._sync_dock_button()

    def _attach_browser(self) -> None:
        """Put the explorer back as the left panel and restore proportions."""
        window = self._explorer_window
        self._explorer_window = None
        self._browser.setParent(None)
        self._horizontal_split.insertWidget(0, self._browser)
        self._browser.show()
        self._horizontal_split.setSizes([280, 720, 220])
        if window is not None:
            window.hide()
            window.deleteLater()
        self._sync_dock_button()

    def _sync_dock_button(self) -> None:
        """Label always reflects what a click will DO, from the real state."""
        detached = self._explorer_window is not None
        self._detach_action.setChecked(detached)
        self._detach_action.setText(
            "Dock Explorer" if detached else "Detach Explorer"
        )

    def _on_mode_changed(self, mode: str) -> None:
        """Switch the whole UI between 1D and 2D.

        In 2D the bottom bar gains an independent F1 range and the main pair
        becomes F2, because the two dimensions need separate ranges.
        """
        self.setWindowTitle(f"{APP_TITLE} {__version__}  \u2014  {mode} mode")
        self._adjustment_bar.set_mode(mode)
        self._spectrum_list.set_mode(mode)
        if mode == "2D":
            f2 = self._canvas.f2_bounds()
            f1 = self._canvas.f1_bounds()
            if f2:
                self._adjustment_bar.set_range(*f2)
            if f1:
                self._adjustment_bar.set_f1_range(*f1)
        self.statusBar().showMessage(f"Canvas is now in {mode} mode", 4000)

    def _on_dimensionality_refused(self, message: str) -> None:
        """Refusing a drop is a decision the user must see explained."""
        self.statusBar().showMessage(message, 15000)

    def _on_add(self, index_a: int, index_b: int) -> None:
        if self._canvas.add_spectra(index_a, index_b):
            self.statusBar().showMessage("Added sum spectrum", 5000)
        else:
            self.statusBar().showMessage(
                "Could not add: pick two different 1D spectra that overlap "
                "in ppm",
                6000,
            )

    def _on_subtract(self, index_a: int, index_b: int) -> None:
        if self._canvas.subtract(index_a, index_b):
            self.statusBar().showMessage("Added difference spectrum", 5000)
        else:
            self.statusBar().showMessage(
                "Could not subtract: pick two different 1D spectra that "
                "overlap in ppm",
                6000,
            )

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
        """A drop that could not be loaded must leave a visible trace.

        Two of them: a status message that now survives (the cursor readout
        moved out of the way), and the browser row itself, which goes grey and
        carries the reason in its tooltip. Before this the only signal was a
        message wiped within milliseconds, so a failed drop was
        indistinguishable from a drag that never registered.
        """
        name = Path(path).name
        sample = Path(path).parent.name
        self.statusBar().showMessage(
            f"Could not load {sample}/{name}: {message}", 15000
        )
        self._browser.mark_dataset_failed(path, message)

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

    def _restore_display_prefs(self) -> None:
        """Apply saved display preferences at start-up.

        Each is applied only if it was saved, so an absent value keeps the
        canvas default rather than being reset to zero by a missing key.
        """
        saved = load_display_prefs()
        self._palette_name = saved.get("palette", "")
        setters = {
            "grid_spacing_ppm": self._canvas.set_grid_spacing_ppm,
            "grid_spacing_y": self._canvas.set_grid_spacing_y,
            "x_decimals": self._canvas.set_x_decimals,
            "label_scale": self._canvas.set_label_scale,
            "opacity": self._canvas.set_trace_opacity,
            "cursor_decimals": self._canvas.set_cursor_decimals,
        }
        for key, setter in setters.items():
            if key in saved:
                setter(saved[key])

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
            opacity=self._canvas.trace_opacity(),
            cursor_decimals=self._canvas.cursor_decimals(),
            grid_spacing_y=self._canvas.grid_spacing_y(),
            palette=self._palette_name,
            parent=self,
        )
        if dialog.exec() != PreferencesDialog.Accepted:
            return
        styles = dialog.styles()
        self._canvas.apply_styles(styles)
        # These three were read from the dialog but never applied -- the
        # settings existed and did nothing. Applied here, with the canvas
        # redrawing for each.
        self._canvas.set_grid_spacing_ppm(dialog.grid_spacing())
        self._canvas.set_x_decimals(dialog.x_decimals())
        self._canvas.set_label_scale(dialog.label_scale())
        self._canvas.set_trace_opacity(dialog.opacity())
        self._canvas.set_cursor_decimals(dialog.cursor_decimals())
        self._canvas.set_grid_spacing_y(dialog.grid_spacing_y())
        self._palette_name = dialog.palette()
        save_slot_styles(styles)   # default for next run
        # Everything else used to be applied and then forgotten: the dialog
        # appeared to work and silently reverted on the next launch.
        save_display_prefs({
            "grid_spacing_ppm": self._canvas.grid_spacing_ppm(),
            "grid_spacing_y": self._canvas.grid_spacing_y(),
            "x_decimals": self._canvas.x_decimals(),
            "label_scale": self._canvas.label_scale(),
            "opacity": self._canvas.trace_opacity(),
            "cursor_decimals": self._canvas.cursor_decimals(),
            "palette": self._palette_name,
        })
        self._sync_spectrum_list()

    def _show_licence(self) -> None:
        """The licence, reachable from inside the application.

        HelSpin is free for research and teaching and not free commercially;
        a user cannot honour a condition they have never seen. It also carries
        the third-party notices, which is how a packaged build satisfies Qt's
        LGPL attribution requirement when there is no visible LICENSE file.
        """
        from .ui.licence_dialog import LicenceDialog

        dialog = LicenceDialog(self)
        dialog.exec()

    def _about(self) -> None:
        # QMessageBox.about() renders rich text when it detects markup, which
        # is what makes the address a clickable mailto link. Plain text would
        # leave the user to retype it by hand from a dialog they cannot copy
        # out of easily.
        QMessageBox.about(
            self,
            f"About {APP_TITLE}",
            f"<p><b>{APP_TITLE} {__version__}</b></p>"
            "<p>Written and developed by H. Iw-ai<br>"
            "Copyright \u00a9 2026 H. Iw-ai. All rights reserved.</p>"
            "<p>Compare Bruker NMR spectra and build publication figures.</p>"
            "<p>Free for academic research, teaching and personal use; "
            "commercial use requires a licence. See Help \u2192 Licence.</p>"
            f'<p>Contact: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>',
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="helspin")
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the installed version and exit, without opening a window",
    )
    parser.add_argument(
        "--check",
        metavar="PATH",
        help="report what processed data a sample or experiment holds, and "
             "exit. Answers 'why is this row dimmed?' from the shell, "
             "without a window and without guessing.",
    )
    return parser.parse_args(argv)


def check_datasets(target) -> int:
    """Print, per experiment, whether a plottable spectrum is there.

    The browser dims a row it believes has no processed data, but a dimmed row
    still only says what HelSpin concluded. This says what is actually on
    disk, experiment by experiment, so a wrong conclusion can be seen for what
    it is rather than argued about.
    """
    from .core.dataset_index import inspect_processed, scan_expnos

    target = Path(target).expanduser()
    if not target.is_dir():
        print(f"{target}: not a directory")
        return 2

    expnos = scan_expnos(str(target))
    if not expnos:
        # Perhaps this IS an experiment rather than a sample.
        if (target / "acqus").is_file():
            state, note = inspect_processed(target)
            mark = {True: "OK  ", False: "NO  ", None: "??  "}[state]
            print(f"{mark}{target.name}: {note or 'has processed data'}")
            return 0
        print(f"{target}: no experiments found here")
        return 2

    print(f"{target}\n")
    for entry in expnos:
        state, note = inspect_processed(target / entry.name)
        mark = {True: "OK  ", False: "NO  ", None: "??  "}[state]
        raw = []
        if entry.has_fid:
            raw.append("fid")
        if entry.has_ser:
            raw.append("ser")
        detail = note or "has processed data"
        print(f"{mark}{entry.name:>5}  [{'+'.join(raw) or 'no raw'}]  {detail}")
    print("\nOK = can be plotted   NO = nothing to plot   ?? = could not tell")
    return 0


def _claim_windows_taskbar_identity() -> None:
    """Tell Windows this process is HelSpin, not the Python that launched it.

    Without an explicit AppUserModelID, Windows attributes the taskbar button
    to the host interpreter: the window's own title-bar icon is correct, while
    the taskbar and Alt-Tab show a generic Python icon and group HelSpin with
    any other Python window. Setting an ID -- stable across versions, so
    pinned shortcuts survive an upgrade -- makes the taskbar use the window
    icon instead.

    No-op everywhere else, and failure is ignored: a wrong icon is not worth
    refusing to start over.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "HelSpin.NMR.Viewer"
        )
    except Exception:      # noqa: BLE001 - cosmetic only, never fatal
        pass


def _claim_linux_desktop_identity(app) -> None:
    """Tell a Linux desktop which .desktop file describes this process.

    The Linux counterpart of the Windows AppUserModelID above, and needed for
    the same reason. Under Wayland a compositor does NOT take the dock or
    Alt-Tab icon from setWindowIcon() at all -- it looks up the application ID
    in the installed .desktop files and takes the icon from there. Without
    this the window title bar is correct while the dock shows a generic
    placeholder, which reads as "the icon is broken" rather than "the desktop
    entry is missing".

    Guarded to Linux rather than set everywhere. Qt ignores it on macOS and
    Windows, so calling it there would very probably be harmless -- but
    "probably harmless" is not a reason to alter application identity on the
    platform this is developed and mainly used on. On X11 it sets WM_CLASS,
    which is what icon themes and window rules match against; on Wayland it is
    the only route to a correct dock icon. Neither concept exists on macOS.

    The .desktop file need not be installed for this to be safe: an
    uninstalled run simply falls back to the window icon, as before.
    """
    if not sys.platform.startswith("linux"):
        return
    app.setDesktopFileName("helspin")


def main() -> int:
    args = _parse_args(sys.argv[1:])
    if getattr(args, "check", None):
        return check_datasets(args.check)
    if args.version:
        # Deliberately no QApplication here: this must work even on a
        # machine with no display at all, e.g. over SSH, purely to answer
        # "which version is installed" for troubleshooting.
        print(f"{APP_TITLE} {__version__}")
        return 0

    _claim_windows_taskbar_identity()
    app = QApplication.instance() or QApplication(sys.argv)
    _claim_linux_desktop_identity(app)
    # Set on the APPLICATION as well as the window: Windows uses this for the
    # taskbar entry and Alt-Tab, and a window-only icon leaves those blank.
    app.setWindowIcon(app_icon())
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
