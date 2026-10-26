"""The top toolbar row: a pinned red Quit at the right, everything else beside it.

Quit used to be the last action of the one main toolbar, pushed right by a
stretch spacer. When the window was narrower than the toolbar, QToolBar hid
the overflowing actions behind its extension button -- and it always hides
from the END, so Quit was the first thing to disappear into the "»" second
line. The two fixes here:

* Quit lives in its OWN toolbar holding exactly one action. QToolBarLayout
  adds an extension button only when a toolbar has more than one item, so a
  single-action toolbar has minimum width == preferred width and cannot
  overflow. It is added after the main toolbar on the same line; the main
  window's toolbar layout stretches the LAST toolbar of a line to the line's
  end, and a right-to-left layout direction puts its one button against the
  right edge. The main toolbar is reserved only its minimum, so Quit always
  keeps its full width.

* The main toolbar shrinks before it overflows. ToolbarFitter picks the
  least compact of a few levels (smaller font, then tighter spacing) whose
  natural width fits beside Quit. The choice depends only on the available
  width and on widths measured per level -- never on the current level -- so
  it cannot oscillate during a resize. Only when even the most compact level
  does not fit does the main toolbar's own extension take the remainder;
  Quit is never part of that.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStyle,
    QToolBar,
    QToolButton,
    QWidget,
)

QUIT_TOOLBAR_NAME = "QuitToolBar"
QUIT_BUTTON_NAME = "helspinQuitButton"

# Red that keeps >= 4.5:1-ish contrast on each theme's toolbar background:
# a deep red on light grey, a light red on dark grey. One red for both would
# be either murky on dark or washed out on light.
QUIT_RED_LIGHT = "#c62828"
QUIT_RED_DARK = "#ff6b6b"

# (font scale, tight spacing). Level 0 is the untouched native look.
COMPACT_LEVELS: tuple[tuple[float, bool], ...] = (
    (1.0, False),
    (0.92, False),
    (0.85, True),
    (0.78, True),
)
MIN_POINT_SIZE = 7.0
MIN_PIXEL_SIZE = 9


def is_dark(palette: QPalette) -> bool:
    """True when the window background is dark (macOS/Windows dark mode)."""
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def quit_red(palette: QPalette) -> str:
    return QUIT_RED_DARK if is_dark(palette) else QUIT_RED_LIGHT


def quit_style_sheet(color: str) -> str:
    """Bold red text with a box of its own.

    A border/padding is declared on purpose: with a box, the style-sheet
    engine draws the button itself and honours the text colour on every
    platform. With colour alone it delegates to the native style, and the
    macOS style draws tool-button text in a toolbar its own way.
    """
    return (
        f"QToolButton#{QUIT_BUTTON_NAME} {{"
        f" color: {color}; font-weight: bold;"
        " border: 1px solid transparent; border-radius: 4px;"
        " padding: 2px 10px; background: transparent; }"
        f"QToolButton#{QUIT_BUTTON_NAME}:hover {{ border-color: {color}; }}"
        f"QToolButton#{QUIT_BUTTON_NAME}:pressed {{"
        " background-color: rgba(198, 40, 40, 60); }"
    )


def build_quit_toolbar(
    window: QMainWindow, on_quit: Callable[[], object]
) -> tuple[QToolBar, QAction, QToolButton | None]:
    """A one-action toolbar holding Quit. Add it AFTER the main toolbar."""
    bar = QToolBar("Quit", window)
    bar.setObjectName(QUIT_TOOLBAR_NAME)
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
    # The last toolbar on a line is stretched to the line's end; right-to-left
    # puts its single button at the right edge of that stretched space.
    bar.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    # Not offered in the toolbar context menu: Quit must not be hideable.
    bar.toggleViewAction().setVisible(False)
    action = bar.addAction("Quit", on_quit)
    # Explicit role rather than Qt's text heuristic: on macOS an action named
    # "Quit" is otherwise moved into the application menu, which would
    # silently empty this button.
    action.setMenuRole(QAction.MenuRole.NoRole)
    action.setToolTip("Quit HelSpin")
    button = bar.widgetForAction(action)
    if isinstance(button, QToolButton):
        button.setObjectName(QUIT_BUTTON_NAME)
    else:  # pragma: no cover - QToolBar always makes a QToolButton
        button = None
    return bar, action, button


class ToolbarFitter(QObject):
    """Keeps the main toolbar on one line beside the pinned Quit toolbar.

    Also keeps the Quit button's red matched to the current light/dark theme.
    """

    def __init__(
        self,
        window: QMainWindow,
        main: QToolBar,
        pinned: QToolBar | None = None,
        quit_button: QToolButton | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._main = main
        self._pinned = pinned
        self._quit_button = quit_button
        self._level = 0
        self._applied = 0
        self._natural: tuple[float, int] | None = None  # (point, pixel)
        self._cache_key: tuple | None = None
        self._widths: tuple[int, ...] = ()
        self._busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.refit)
        window.installEventFilter(self)
        main.installEventFilter(self)

    # -- public ---------------------------------------------------------------

    def level(self) -> int:
        return self._level

    def natural_widths(self) -> tuple[int, ...]:
        """Main-toolbar width at each compact level (recomputed on change)."""
        if self._busy:
            return self._widths
        self._busy = True
        try:
            self._ensure_widths()
        finally:
            self._busy = False
        return self._widths

    def available_width(self, line_width: int | None = None) -> int:
        """Room on the toolbar line for the main toolbar, beside Quit."""
        line = self._window.width() if line_width is None else line_width
        if self._pinned is not None and not self._pinned.isHidden():
            line -= self._pinned.sizeHint().width()
        return line

    def choose_level(self, available: int) -> int:
        """Least compact level that fits; the most compact when none does."""
        if not self._widths:
            return 0
        for index, width in enumerate(self._widths):
            if width <= available:
                return index
        return len(COMPACT_LEVELS) - 1

    def refit(self, line_width: int | None = None) -> None:
        if self._busy or self._main.isHidden():
            return
        self._busy = True
        try:
            self._ensure_widths()
            level = self.choose_level(self.available_width(line_width))
            self._apply(level)
            self._level = level
        finally:
            self._busy = False

    def invalidate(self) -> None:
        """Forget the measured widths; the next refit measures again."""
        self._cache_key = None

    def schedule(self) -> None:
        """Refit once control returns to the event loop (coalesced)."""
        self._timer.start()

    def restyle_quit(self) -> None:
        button = self._quit_button
        if button is None:
            return
        sheet = quit_style_sheet(quit_red(self._window.palette()))
        if button.styleSheet() != sheet:
            button.setStyleSheet(sheet)
            # Bold text and padding change Quit's width, hence the room left.
            self.schedule()

    # -- events ---------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt API)
        kind = event.type()
        if obj is self._window:
            if kind == QEvent.Type.Resize:
                self.refit(event.size().width())
            elif kind in (
                QEvent.Type.Show,
                QEvent.Type.FontChange,
                QEvent.Type.ApplicationFontChange,
                QEvent.Type.StyleChange,
            ):
                # Re-measure rather than trust the cache: a style change also
                # resets the toolbar's own spacing behind our back.
                self.invalidate()
                self.schedule()
            if kind in (
                QEvent.Type.PaletteChange,
                QEvent.Type.ApplicationPaletteChange,
                QEvent.Type.StyleChange,
            ):
                self.restyle_quit()
        elif obj is self._main:
            if kind in (
                QEvent.Type.ActionAdded,
                QEvent.Type.ActionRemoved,
                QEvent.Type.ActionChanged,
                QEvent.Type.Show,
            ):
                # Text changes ("Detach Explorer" -> "Dock Explorer") change
                # the widths; Show covers re-showing it from the context menu,
                # since refit() skips a hidden toolbar.
                self.schedule()
        return False

    # -- internals --------------------------------------------------------------

    def _key(self) -> tuple:
        main = self._main
        actions = tuple(
            (a.text(), a.isVisible(), a.isSeparator(), a.font().key())
            for a in main.actions()
        )
        return (
            actions,
            QApplication.font().key(),
            self._window.font().key(),
            main.style().name(),
            main.logicalDpiX(),
        )

    def _ensure_widths(self) -> None:
        key = self._key()
        if key == self._cache_key and len(self._widths) == len(COMPACT_LEVELS):
            return
        main = self._main
        self._apply(0, force=True)
        self._natural = self._natural_font_size()
        widths = [main.sizeHint().width()]
        for index in range(1, len(COMPACT_LEVELS)):
            self._apply(index, force=True)
            widths.append(main.sizeHint().width())
        self._widths = tuple(widths)
        self._cache_key = key
        # Measuring left the last level applied; refit() re-applies the right
        # one, and a bare natural_widths() call must not leave it shrunk.
        self._apply(self._level, force=True)

    def _natural_font_size(self) -> tuple[float, int] | None:
        """Size of the main toolbar's button text at level 0."""
        for action in self._main.actions():
            if action.isSeparator():
                continue
            widget = self._main.widgetForAction(action)
            if isinstance(widget, QToolButton):
                font = widget.font()
                return (font.pointSizeF(), font.pixelSize())
        return None

    def scaled_font(self, level: int) -> QFont | None:
        """Font override for a level, or None for the inherited font.

        Only the SIZE is set: a default-constructed QFont resolves nothing
        else, so buttons keep their own family and weight (Clear stays bold)
        and only shrink.
        """
        scale, _tight = COMPACT_LEVELS[level]
        if level == 0 or self._natural is None:
            return None
        point, pixel = self._natural
        font = QFont()
        if point > 0:
            size = min(point, max(MIN_POINT_SIZE, point * scale))
            font.setPointSizeF(size)
        elif pixel > 0:
            size_px = min(pixel, max(MIN_PIXEL_SIZE, round(pixel * scale)))
            font.setPixelSize(size_px)
        else:
            return None
        return font

    def _apply(self, level: int, force: bool = False) -> None:
        if not force and level == self._applied:
            return
        main = self._main
        font = self.scaled_font(level)
        # setFont(QFont()) clears the override, so the toolbar follows the
        # application font again (e.g. a later system font change).
        main.setFont(font if font is not None else QFont())
        layout = main.layout()
        if layout is not None:
            _scale, tight = COMPACT_LEVELS[level]
            spacing = main.style().pixelMetric(
                QStyle.PixelMetric.PM_ToolBarItemSpacing, None, main
            )
            layout.setSpacing(0 if tight else spacing)
        self._applied = level


def extension_button(toolbar: QToolBar) -> QWidget | None:
    """The toolbar's "»" overflow button (Qt's own object name)."""
    return toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
