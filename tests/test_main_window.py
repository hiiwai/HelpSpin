"""The application entry point: a real, headless launch of MainWindow.

This is what proves the package installs into something runnable, not just
that its pieces import cleanly in isolation.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

import helspin.core.settings as settings_module
from helspin.__main__ import MainWindow
from helspin.domain.ports import DataRoot

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        settings_module, "_settings", lambda: QSettings(ini, QSettings.IniFormat)
    )
    yield


def test_main_window_constructs_and_shows(qtbot):
    """A real construct-and-show, not just an import check."""
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()
    assert window.windowTitle().startswith("HelSpin")


def test_main_calls_raise_and_activate_to_avoid_the_macos_hidden_window_trap(
    monkeypatch,
):
    """On macOS a Python-launched Qt window can open behind everything else
    with no visible signal, which is what a 'the app hangs' report usually
    turns out to be. main() must force it to the front."""
    import helspin.__main__ as main_module

    calls = []

    class TrackedWindow(main_module.MainWindow):
        def show(self):
            calls.append("show")
            super().show()

        def raise_(self):
            calls.append("raise_")
            super().raise_()

        def activateWindow(self):
            calls.append("activateWindow")
            super().activateWindow()

    monkeypatch.setattr(main_module, "MainWindow", TrackedWindow)
    monkeypatch.setattr(main_module.sys, "argv", ["helspin"])

    app = main_module.QApplication.instance()
    monkeypatch.setattr(app, "exec", lambda: 0)  # never actually block

    main_module.main()
    assert calls == ["show", "raise_", "activateWindow"]


def test_first_run_shows_a_status_hint(qtbot):
    """First run: no data root configured. Must guide the user, not sit blank."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert "Add Data Root" in window.statusBar().currentMessage()


def test_no_status_hint_once_a_root_is_configured(qtbot, tmp_path):
    settings_module.save_data_roots([DataRoot(name="600", path=tmp_path / "600")])
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.statusBar().currentMessage() == ""


def test_persisted_root_loads_into_the_browser(qtbot, tmp_path):
    root_dir = tmp_path / "600"
    root_dir.mkdir()
    settings_module.save_data_roots([DataRoot(name="600 MHz", path=root_dir)])

    window = MainWindow()
    qtbot.addWidget(window)
    assert window._browser.model.rowCount() == 1
    assert window._browser.model.data(window._browser.model.index(0, 0)) == "600 MHz"


def test_menu_bar_has_file_and_help(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    titles = [a.text() for a in window.menuBar().actions()]
    assert any("File" in t for t in titles)
    assert any("Help" in t for t in titles)


def test_add_data_root_persists_and_populates(qtbot, tmp_path, monkeypatch):
    """Simulates the File > Add Data Root... action without a real file
    dialog, which cannot run headlessly: patches QFileDialog/QInputDialog to
    return fixed values, then calls the same handler the menu action calls."""
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    new_root = tmp_path / "400"
    new_root.mkdir()

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(new_root))
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("400 MHz", True))
    )

    window = MainWindow()
    qtbot.addWidget(window)
    window._add_data_root()

    assert window._browser.model.rowCount() == 1
    saved = settings_module.load_data_roots()
    assert len(saved) == 1 and saved[0].name == "400 MHz"


def test_cancelling_the_folder_dialog_adds_nothing(qtbot, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: "")
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._add_data_root()
    assert window._browser.model.rowCount() == 0


def test_cancelling_the_name_dialog_adds_nothing(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog

    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path))
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False))
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._add_data_root()
    assert window._browser.model.rowCount() == 0


def test_about_dialog_does_not_raise(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "about", staticmethod(lambda *a, **k: None))
    window = MainWindow()
    qtbot.addWidget(window)
    window._about()  # must not raise


# --- shell layout: toolbar, splitters, panel identities ---------------------


def test_toolbar_has_the_expected_actions(qtbot):
    from PySide6.QtWidgets import QToolBar

    window = MainWindow()
    qtbot.addWidget(window)
    toolbars = window.findChildren(QToolBar)
    assert len(toolbars) == 1
    labels = [a.text() for a in toolbars[0].actions() if a.text()]
    assert any("Add Data Root" in t for t in labels)
    assert any("New Figure" in t for t in labels)
    assert any("Preferences" in t for t in labels)
    assert any("About" in t for t in labels)


def test_toolbar_is_not_movable(qtbot):
    """A fixed toolbar matches the reference app's static button row and
    avoids users accidentally detaching it into a floating window."""
    from PySide6.QtWidgets import QToolBar

    window = MainWindow()
    qtbot.addWidget(window)
    toolbar = window.findChild(QToolBar)
    assert not toolbar.isMovable()


def test_central_widget_is_the_vertical_split(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.centralWidget() is window._vertical_split


def test_browser_is_the_left_panel(qtbot):
    """Left panel = explorer, per the requested layout."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._horizontal_split.widget(0) is window._browser


def test_canvas_placeholder_is_the_centre_panel(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._horizontal_split.widget(1) is window._canvas


def test_adjustment_bar_is_below_the_split(qtbot):
    """Bottom = adjustment, per the requested layout."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._vertical_split.widget(0) is window._horizontal_split
    assert window._vertical_split.widget(1) is window._adjustment_bar


def test_canvas_gets_more_space_than_the_browser(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    sizes = window._horizontal_split.sizes()
    assert sizes[1] > sizes[0]


def test_new_figure_stub_does_not_raise(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    captured = {}
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda parent, title, text: captured.update(title=title, text=text)),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._new_figure()
    assert "not" in captured["title"].lower()


def test_preferences_stub_does_not_raise(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    window = MainWindow()
    qtbot.addWidget(window)
    window._preferences()  # must not raise


def test_adjustment_bar_starts_disabled(qtbot):
    """No figure exists yet in this build, so there is nothing to adjust."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert not window._adjustment_bar._full_button.isEnabled()
