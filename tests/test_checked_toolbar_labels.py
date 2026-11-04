"""0.9.12: ON toolbar buttons (Dock Explorer, Stacked, Labels) readable on macOS.

The macOS style paints a down/ON text-only toolbar label white (the menu
palette's HighlightedText) on a translucent black bezel -- pale grey in light
mode, so the text is unreadable. The fix is a proxy style that draws the
LABEL as if the button were up. The real macOS style cannot run here, so these
tests pin the mechanism on the available style: what the base style is asked
to draw, which widgets get the proxy, and that nothing else changes.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QProxyStyle,
    QStyle,
    QToolBar,
)

from helspin.ui import toolbar_layout as tl

pytestmark = pytest.mark.usefixtures("qapp")

ON = QStyle.StateFlag.State_On
SUNKEN = QStyle.StateFlag.State_Sunken
LABEL = QStyle.ControlElement.CE_ToolButtonLabel


class Recorder(QProxyStyle):
    """Sits BELOW CheckedLabelStyle and records what it passes down."""

    def __init__(self, key):
        super().__init__(key)
        self.labels = []

    def drawControl(self, element, option, painter, widget=None):  # noqa: N802
        if element == LABEL:
            self.labels.append(option.state)
        super().drawControl(element, option, painter, widget)


class Probe(tl.CheckedLabelStyle, Recorder):
    """CheckedLabelStyle.drawControl's super() lands in Recorder."""

    def __init__(self, key):
        Recorder.__init__(self, key)
        self.callers = []

    def drawControl(self, element, option, painter, widget=None):  # noqa: N802
        before = option.state
        super().drawControl(element, option, painter, widget)
        if element == LABEL:
            self.callers.append((before, option.state))   # caller's copy


class FakeStyle:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


@pytest.fixture
def bar(qtbot):
    window = QMainWindow()
    toolbar = QToolBar("Main", window)
    window.addToolBar(toolbar)
    on = toolbar.addAction("Stacked")
    on.setCheckable(True)
    on.setChecked(True)
    off = toolbar.addAction("Overlay")
    off.setCheckable(True)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window, toolbar, on, off


def test_only_the_macos_style_is_recognised():
    assert tl.is_macos_style(FakeStyle("macos"))
    assert tl.is_macos_style(FakeStyle("macOS"))
    assert not tl.is_macos_style(FakeStyle("fusion"))
    assert not tl.is_macos_style(FakeStyle("windowsvista"))
    assert not tl.is_macos_style(FakeStyle("windows11"))
    assert not tl.is_macos_style(None)


def test_an_on_label_is_drawn_as_up_without_touching_the_caller(bar):
    _window, toolbar, on, _off = bar
    probe = Probe(QApplication.style().name())
    button = toolbar.widgetForAction(on)
    button.setStyle(probe)
    button.grab()
    assert probe.labels, "the label was never drawn through the proxy"
    assert all(not (state & (ON | SUNKEN)) for state in probe.labels)
    assert all(before & ON and after & ON for before, after in probe.callers)
    assert all(state & QStyle.StateFlag.State_Enabled for state in probe.labels)


def test_an_off_label_passes_through_unchanged(bar):
    _window, toolbar, _on, off = bar
    probe = Probe(QApplication.style().name())
    button = toolbar.widgetForAction(off)
    button.setStyle(probe)
    button.grab()
    assert probe.labels
    assert [before for before, _after in probe.callers] == probe.labels


def test_off_by_default_except_on_macos(bar):
    _window, toolbar, on, _off = bar
    assert not tl.is_macos_style(QApplication.style())     # this sandbox
    fix = tl.CheckedLabelFix(toolbar)
    assert not fix.enabled() and fix.proxy_style() is None
    assert fix.apply() == 0
    assert toolbar.widgetForAction(on).style() is QApplication.style()


def test_enabled_styles_every_text_button_but_the_overflow(bar):
    window, toolbar, on, off = bar
    widths = {a.text(): toolbar.widgetForAction(a).sizeHint() for a in (on, off)}
    fix = tl.CheckedLabelFix(toolbar, parent=window, enabled=True)
    proxy = fix.proxy_style()
    for action in (on, off):
        assert toolbar.widgetForAction(action).style() is proxy
        # Same metrics as before: the base does the sizing.
        assert toolbar.widgetForAction(action).sizeHint() == widths[action.text()]
    overflow = tl.extension_button(toolbar)
    if overflow is not None:
        assert overflow.style() is not proxy
    assert fix.apply() == 0          # idempotent


def test_buttons_added_later_are_styled_too(qtbot, bar):
    window, toolbar, _on, _off = bar
    fix = tl.CheckedLabelFix(toolbar, parent=window, enabled=True)
    late = toolbar.addAction("Labels")
    qtbot.waitUntil(lambda: toolbar.widgetForAction(late).style() is fix.proxy_style())


def test_the_base_is_a_new_instance_not_the_application_style(bar):
    """Handing QApplication.style() to a QProxyStyle transfers ownership and
    makes the proxy the proxy of every widget's style."""
    window, toolbar, _on, _off = bar
    fix = tl.CheckedLabelFix(toolbar, parent=window, enabled=True)
    app_style = QApplication.style()
    assert fix.proxy_style().baseStyle() is not app_style
    assert app_style.proxy() is app_style


def test_closing_the_window_with_the_fix_is_clean(qtbot):
    window = QMainWindow()
    toolbar = QToolBar(window)
    window.addToolBar(toolbar)
    toolbar.addAction("Stacked").setCheckable(True)
    tl.CheckedLabelFix(toolbar, parent=window, enabled=True)
    window.show()
    qtbot.waitExposed(window)
    window.close()
    window.deleteLater()
    qtbot.wait(10)


def test_main_window_carries_the_fix_and_leaves_quit_alone(qtbot, monkeypatch):
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)
    fix = window._checked_label_fix
    assert fix.enabled() == tl.is_macos_style(QApplication.style())
    # Quit keeps its own style-sheet styling in its own toolbar.
    quit_button = window._quit_toolbar.widgetForAction(window._quit_action)
    assert quit_button.styleSheet()
    assert window._toolbar is not window._quit_toolbar
