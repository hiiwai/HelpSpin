"""Quit: red, always visible, pinned to the right end of the toolbar row.

Reported: on a narrower window Quit vanished behind the toolbar's "»"
overflow (a second line), because QToolBar hides its LAST actions first and
Quit was last. These tests drive the real MainWindow at real widths and
check what is on screen, not just what was configured.
"""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QStyle,
    QToolBar,
    QToolButton,
)

from helspin.__main__ import MainWindow
from helspin.ui import toolbar_layout as tl

pytestmark = pytest.mark.usefixtures("qapp")


def _settle():
    for _ in range(4):
        QApplication.processEvents()


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)
    _settle()
    return w


def _quit_button(window) -> QToolButton:
    return window.findChild(QToolButton, tl.QUIT_BUTTON_NAME)


def _at(window, width):
    window.resize(width, 600)
    _settle()


def _quit_span(window):
    button = _quit_button(window)
    left = button.mapTo(window, button.rect().topLeft()).x()
    return left, left + button.width()


def _ext_visible(toolbar) -> bool:
    ext = tl.extension_button(toolbar)
    return ext is not None and ext.isVisible()


def _hidden_main_actions(window):
    tb = window._toolbar
    return [
        a.text()
        for a in tb.actions()
        if a.text() and not tb.widgetForAction(a).isVisible()
    ]


def _assert_quit_pinned(window):
    left, right = _quit_span(window)
    assert _quit_button(window).isVisible()
    # Only the toolbar's own content margin separates it from the edge.
    assert 0 <= window.width() - right <= 8
    # Never overlapping the main toolbar.
    assert left >= window._toolbar.geometry().right()


def _light(widget):
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#ececec"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#ececec"))
    widget.setPalette(pal)


def _dark(widget):
    pal = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Button):
        pal.setColor(role, QColor("#2b2b2b"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(role, QColor("#e0e0e0"))
    widget.setPalette(pal)


def _red_pixels(button, dark=False) -> int:
    image = button.grab().toImage()
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if dark:
                if c.red() >= 180 and c.green() <= 140 and c.red() - c.green() >= 80:
                    count += 1
            elif c.red() >= 140 and c.green() <= 110 and c.blue() <= 110:
                count += 1
    return count


# -- structure -----------------------------------------------------------------


def test_quit_lives_alone_in_its_own_toolbar(window):
    bar = window._quit_toolbar
    assert bar.objectName() == tl.QUIT_TOOLBAR_NAME
    assert bar.actions() == [window._quit_action]
    assert window._quit_action.text() == "Quit"
    assert window._quit_action.menuRole() == window._quit_action.MenuRole.NoRole
    assert not bar.isMovable() and not bar.isFloatable()
    assert window.toolBarArea(bar) == Qt.ToolBarArea.TopToolBarArea
    assert window.toolBarArea(window._toolbar) == Qt.ToolBarArea.TopToolBarArea
    assert "Quit" not in [a.text() for a in window._toolbar.actions()]
    # One row: same top edge, Quit's toolbar after the main one.
    assert bar.geometry().top() == window._toolbar.geometry().top()
    assert bar.geometry().left() >= window._toolbar.geometry().right()
    # A single-item toolbar never gets an overflow button.
    assert not _ext_visible(bar)


def test_quit_cannot_be_hidden_from_the_toolbar_context_menu(window):
    menu = window.createPopupMenu()
    visible = [a.text() for a in menu.actions() if a.isVisible() and a.text()]
    assert "Quit" not in visible
    assert not window._quit_toolbar.toggleViewAction().isVisible()


def test_quit_closes_the_window(window):
    window._quit_action.trigger()
    assert not window.isVisible()


def test_file_menu_quit_is_unchanged(window):
    file_menu = window.menuBar().actions()[0].menu()
    assert "&Quit" in [a.text() for a in file_menu.actions()]


# -- red -------------------------------------------------------------------------


def test_quit_is_bold_red_in_light_mode(window):
    _light(window)
    _settle()
    button = _quit_button(window)
    assert f"color: {tl.QUIT_RED_LIGHT}" in button.styleSheet()
    assert "font-weight: bold" in button.styleSheet()
    assert button.font().bold()
    # What is actually drawn, not just what was configured.
    assert _red_pixels(button) >= 20


def test_quit_turns_light_red_in_dark_mode_and_back(window):
    button = _quit_button(window)
    _dark(window)
    _settle()
    assert f"color: {tl.QUIT_RED_DARK}" in button.styleSheet()
    assert _red_pixels(button, dark=True) >= 20
    _light(window)
    _settle()
    assert f"color: {tl.QUIT_RED_LIGHT}" in button.styleSheet()


def test_red_follows_an_application_wide_palette_change(window):
    app = QApplication.instance()
    saved = app.palette()
    try:
        _dark(app)
        _settle()
        assert f"color: {tl.QUIT_RED_DARK}" in _quit_button(window).styleSheet()
    finally:
        app.setPalette(saved)
        _settle()
    assert tl.quit_red(window.palette()) in _quit_button(window).styleSheet()


def test_restyle_is_a_no_op_when_nothing_changed(window):
    button = _quit_button(window)
    sheet = button.styleSheet()
    window._toolbar_fitter.restyle_quit()
    assert button.styleSheet() == sheet


def test_red_helpers():
    light, dark = QPalette(), QPalette()
    light.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    dark.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    assert not tl.is_dark(light) and tl.is_dark(dark)
    assert tl.quit_red(light) == tl.QUIT_RED_LIGHT
    assert tl.quit_red(dark) == tl.QUIT_RED_DARK
    sheet = tl.quit_style_sheet("#123456")
    assert "#123456" in sheet and tl.QUIT_BUTTON_NAME in sheet
    # A box is declared so the text colour is honoured on every platform.
    assert "padding" in sheet and "border" in sheet


# -- always visible, always at the right end ---------------------------------


def test_quit_stays_pinned_at_every_width(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    candidates = [
        widths[0] + quit_w + 400,
        widths[0] + quit_w,
        widths[0] + quit_w - 1,
        widths[-1] + quit_w,
        widths[-1] + quit_w - 1,
        window.minimumSizeHint().width(),
    ]
    for width in candidates:
        _at(window, max(width, window.minimumSizeHint().width()))
        _assert_quit_pinned(window)


def test_main_toolbar_shrinks_before_it_overflows(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    assert list(widths) == sorted(widths, reverse=True)
    assert widths[-1] < widths[0]

    # Exactly enough room: native look, nothing hidden.
    _at(window, widths[0] + quit_w)
    assert fitter.level() == 0
    assert not _ext_visible(window._toolbar)
    assert _hidden_main_actions(window) == []

    # One pixel short: shrinks instead of hiding anything.
    _at(window, widths[0] + quit_w - 1)
    assert fitter.level() >= 1
    assert not _ext_visible(window._toolbar)
    assert _hidden_main_actions(window) == []
    _assert_quit_pinned(window)

    # Most compact level, still everything on one line.
    if widths[-1] + quit_w >= window.minimumSizeHint().width():
        _at(window, widths[-1] + quit_w)
        assert fitter.level() == len(tl.COMPACT_LEVELS) - 1
        assert not _ext_visible(window._toolbar)
        assert _hidden_main_actions(window) == []
        _assert_quit_pinned(window)


def test_overflow_is_the_last_resort_and_never_takes_quit(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    narrow = widths[-1] + quit_w - 40
    if narrow < window.minimumSizeHint().width():
        pytest.skip("window minimum is wider than the compact toolbar")
    _at(window, narrow)
    assert fitter.level() == len(tl.COMPACT_LEVELS) - 1
    assert _ext_visible(window._toolbar)
    assert _hidden_main_actions(window)  # something went to the overflow
    _assert_quit_pinned(window)


def test_each_level_is_chosen_exactly_when_it_first_fits(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    for level, natural in enumerate(widths):
        if level and widths[level - 1] == natural:
            continue  # clamped to the same size as the previous level
        width = natural + quit_w
        if width < window.minimumSizeHint().width():
            continue
        _at(window, width)
        assert fitter.level() == level
        assert window._toolbar.width() == natural
        assert not _ext_visible(window._toolbar)


def test_level_depends_only_on_width_not_history(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    target = (widths[1] + widths[2]) // 2 + quit_w
    _at(window, widths[0] + quit_w + 300)
    _at(window, target)
    from_wide = (fitter.level(), window._toolbar.font().pointSizeF())
    _at(window, window.minimumSizeHint().width())
    _at(window, target)
    from_narrow = (fitter.level(), window._toolbar.font().pointSizeF())
    assert from_wide == from_narrow
    # Repeating the same resize changes nothing (no oscillation).
    _at(window, target)
    assert (fitter.level(), window._toolbar.font().pointSizeF()) == from_wide


def test_growing_back_restores_the_native_look(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    default_spacing = window._toolbar.style().pixelMetric(
        QStyle.PixelMetric.PM_ToolBarItemSpacing, None, window._toolbar
    )
    _at(window, max(widths[-1] + quit_w, window.minimumSizeHint().width()))
    assert window._toolbar.testAttribute(Qt.WidgetAttribute.WA_SetFont)
    _at(window, widths[0] + quit_w + 200)
    assert fitter.level() == 0
    # The override is CLEARED, not set to an equal size, so the toolbar
    # follows a later system font change again.
    assert not window._toolbar.testAttribute(Qt.WidgetAttribute.WA_SetFont)
    assert window._toolbar.layout().spacing() == default_spacing
    assert window._toolbar.width() == widths[0]


def test_compact_levels_only_shrink_the_text(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    tb = window._toolbar
    first = tb.widgetForAction(tb.actions()[0])
    clear_action = next(a for a in tb.actions() if a.text() == "Clear")
    clear = tb.widgetForAction(clear_action)
    natural_font = QFont(first.font())
    _at(window, max(widths[-1] + quit_w, window.minimumSizeHint().width()))
    assert fitter.level() > 0
    small = first.font()
    assert small.pointSizeF() < natural_font.pointSizeF()
    assert small.pointSizeF() >= min(tl.MIN_POINT_SIZE, natural_font.pointSizeF())
    assert small.family() == natural_font.family()
    assert clear.font().bold()  # Clear keeps standing out
    assert clear.font().pointSizeF() == small.pointSizeF()
    # Quit is not part of the compaction.
    assert _quit_button(window).font().pointSizeF() >= natural_font.pointSizeF()


# -- re-measuring when the content changes ----------------------------------------


def test_relabelling_an_action_remeasures(window):
    fitter = window._toolbar_fitter
    before = fitter.natural_widths()
    window._toggle_browser_window()  # "Detach Explorer" -> "Dock Explorer"
    _settle()
    try:
        assert window._detach_action.text() == "Dock Explorer"
        after = fitter.natural_widths()
        assert after[0] < before[0]
        assert fitter._cache_key == fitter._key()
    finally:
        window._toggle_browser_window()
        _settle()
    assert fitter.natural_widths() == before


def test_application_font_change_remeasures(window):
    app = QApplication.instance()
    saved = QFont(app.font())
    fitter = window._toolbar_fitter
    before = fitter.natural_widths()
    try:
        bigger = QFont(saved)
        bigger.setPointSizeF(saved.pointSizeF() + 3)
        app.setFont(bigger)
        _settle()
        assert fitter.natural_widths()[0] > before[0]
        _assert_quit_pinned(window)
    finally:
        app.setFont(saved)
        _settle()
    assert fitter.natural_widths() == before


def test_hidden_main_toolbar_leaves_quit_right_aligned(window):
    fitter = window._toolbar_fitter
    widths = fitter.natural_widths()
    quit_w = window._quit_toolbar.sizeHint().width()
    window._toolbar.hide()
    _settle()
    _at(window, widths[0] + quit_w + 100)
    _assert_quit_pinned(window)
    level_while_hidden = fitter.level()
    # Narrowing while hidden does nothing (refit skips a hidden toolbar)...
    _at(window, max(widths[-1] + quit_w, window.minimumSizeHint().width()))
    assert fitter.level() == level_while_hidden
    # ...and showing it again fits it to the width it now has.
    window._toolbar.show()
    _settle()
    assert fitter.level() == fitter.choose_level(fitter.available_width())
    assert not _ext_visible(window._toolbar)


def test_pending_refit_survives_window_deletion(qtbot, monkeypatch):
    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    w = MainWindow()
    w.show()
    _settle()
    w._toolbar_fitter.schedule()
    w.close()
    w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _settle()  # the timer died with the fitter; nothing may fire


# -- the fitter on its own: edges ---------------------------------------------------


def _bare(qtbot, actions=("One", "Two", "Three"), pinned=True):
    win = QMainWindow()
    win.setCentralWidget(QLabel("x"))
    qtbot.addWidget(win)
    main = QToolBar("Main", win)
    for text in actions:
        main.addAction(text)
    win.addToolBar(main)
    bar = button = None
    if pinned:
        bar, _action, button = tl.build_quit_toolbar(win, win.close)
        win.addToolBar(bar)
    fitter = tl.ToolbarFitter(win, main, bar, button)
    return win, main, bar, fitter


def test_fitter_without_pinned_toolbar_uses_the_whole_line(qtbot):
    win, _main, _bar, fitter = _bare(qtbot, pinned=False)
    win.resize(500, 200)
    assert fitter.available_width() == 500
    assert fitter.available_width(321) == 321
    fitter.restyle_quit()  # no Quit button: must simply do nothing


def test_fitter_with_an_empty_toolbar(qtbot):
    win, main, _bar, fitter = _bare(qtbot, actions=())
    widths = fitter.natural_widths()
    assert len(widths) == len(tl.COMPACT_LEVELS)
    assert fitter._natural is None
    assert fitter.scaled_font(3) is None
    fitter.refit(50)
    assert not main.testAttribute(Qt.WidgetAttribute.WA_SetFont)


def test_choose_level_before_anything_is_measured(qtbot):
    _win, _main, _bar, fitter = _bare(qtbot)
    assert fitter.choose_level(10) == 0
    fitter.natural_widths()
    assert fitter.choose_level(10**6) == 0
    assert fitter.choose_level(-1) == len(tl.COMPACT_LEVELS) - 1


def test_natural_widths_does_not_leave_the_toolbar_shrunk(qtbot):
    _win, main, _bar, fitter = _bare(qtbot)
    fitter.natural_widths()
    assert fitter._applied == fitter.level() == 0
    assert not main.testAttribute(Qt.WidgetAttribute.WA_SetFont)


def test_reentrant_calls_are_ignored(qtbot):
    _win, _main, _bar, fitter = _bare(qtbot)
    fitter.natural_widths()
    fitter._busy = True
    try:
        fitter.refit(10)  # would shrink if it ran
        assert fitter.level() == 0
        assert fitter.natural_widths() == fitter._widths
    finally:
        fitter._busy = False


@pytest.mark.parametrize(
    "natural, level, expected",
    [
        ((13.0, -1), 1, ("pt", 13.0 * 0.92)),
        ((13.0, -1), 3, ("pt", 13.0 * 0.78)),
        ((8.0, -1), 3, ("pt", tl.MIN_POINT_SIZE)),  # clamped up to the floor
        ((6.0, -1), 3, ("pt", 6.0)),  # never ENLARGED past the natural size
        ((-1.0, 16), 3, ("px", 12)),
        ((-1.0, 10), 3, ("px", tl.MIN_PIXEL_SIZE)),
        ((-1.0, 8), 2, ("px", 8)),
        ((-1.0, -1), 2, None),
        ((13.0, -1), 0, None),
    ],
)
def test_scaled_font_sizes(qtbot, natural, level, expected):
    _win, _main, _bar, fitter = _bare(qtbot)
    fitter._natural = natural
    font = fitter.scaled_font(level)
    if expected is None:
        assert font is None
        return
    kind, value = expected
    if kind == "pt":
        assert font.pointSizeF() == pytest.approx(value)
    else:
        assert font.pixelSize() == value
    # Only the size is resolved: family and weight still come from the
    # button the font is applied to.
    base = QFont("Courier New")
    base.setBold(True)
    merged = font.resolve(base)
    assert merged.bold()
    assert merged.family() == base.family()


def test_scaled_font_without_measurement(qtbot):
    _win, _main, _bar, fitter = _bare(qtbot)
    assert fitter._natural is None
    assert fitter.scaled_font(2) is None


def test_build_quit_toolbar_calls_back(qtbot):
    win = QMainWindow()
    qtbot.addWidget(win)
    hits = []
    bar, action, button = tl.build_quit_toolbar(win, lambda: hits.append(1))
    assert button is not None and button.objectName() == tl.QUIT_BUTTON_NAME
    assert bar.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert action.toolTip() == "Quit HelSpin"
    action.trigger()
    assert hits == [1]


def test_natural_size_skips_separators_and_plain_widgets(qtbot):
    win = QMainWindow()
    qtbot.addWidget(win)
    main = QToolBar("Main", win)
    main.addSeparator()
    main.addWidget(QLabel("not a button"))
    action = main.addAction("Real")
    win.addToolBar(main)
    fitter = tl.ToolbarFitter(win, main)
    fitter.natural_widths()
    button_font = main.widgetForAction(action).font()
    assert fitter._natural == (button_font.pointSizeF(), button_font.pixelSize())


def test_apply_tolerates_a_toolbar_without_layout(qtbot):
    _win, main, _bar, fitter = _bare(qtbot)
    fitter.natural_widths()
    main.layout = lambda: None  # instance attribute shadows the Qt method
    fitter._apply(2, force=True)
    assert fitter._applied == 2
    assert main.testAttribute(Qt.WidgetAttribute.WA_SetFont)
