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
    assert any("Refresh All" in t for t in labels)
    assert any("Overlay" in t for t in labels)
    assert any("Stacked" in t for t in labels)
    assert any("Clear" in t for t in labels)
    assert any("Preferences" in t for t in labels)
    assert any("About" in t for t in labels)
    assert any(t == "Quit" for t in labels)


def test_toolbar_quit_is_the_last_action(qtbot):
    """Matches the reference app: Quit sits at the far right, separated
    from everything else by the stretch spacer."""
    from PySide6.QtWidgets import QToolBar

    window = MainWindow()
    qtbot.addWidget(window)
    toolbar = window.findChild(QToolBar)
    actions_with_text = [a for a in toolbar.actions() if a.text()]
    assert actions_with_text[-1].text() == "Quit"


def test_toolbar_quit_action_closes_the_window(qtbot):
    from PySide6.QtWidgets import QToolBar

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.isVisible()

    toolbar = window.findChild(QToolBar)
    quit_action = next(a for a in toolbar.actions() if a.text() == "Quit")
    quit_action.trigger()

    assert not window.isVisible()


def test_refresh_all_toolbar_action_actually_refreshes(qtbot, tmp_path):
    """End-to-end through the real toolbar action, not just the browser
    method directly: the reported bug, fixed, verified from the top of the
    stack a user actually clicks."""
    from PySide6.QtWidgets import QToolBar

    root_dir = tmp_path / "600"
    root_dir.mkdir(parents=True)
    (root_dir / "sample_a" / "11").mkdir(parents=True)
    (root_dir / "sample_a" / "11" / "acqus").write_text("##$NUC1= <1H>\n##END=\n")
    (root_dir / "sample_a" / "11" / "fid").write_bytes(b"\x00")
    (root_dir / "sample_a" / "11" / "pdata" / "1").mkdir(parents=True)

    settings_module.save_data_roots([DataRoot(name="600", path=root_dir)])
    window = MainWindow()
    qtbot.addWidget(window)
    root_index = window._browser.model.index(0, 0)
    qtbot.waitUntil(lambda: window._browser.model.rowCount(root_index) == 1, timeout=2000)

    (root_dir / "sample_b" / "12").mkdir(parents=True)
    (root_dir / "sample_b" / "12" / "acqus").write_text("##$NUC1= <1H>\n##END=\n")
    (root_dir / "sample_b" / "12" / "fid").write_bytes(b"\x00")
    (root_dir / "sample_b" / "12" / "pdata" / "1").mkdir(parents=True)

    toolbar = window.findChild(QToolBar)
    refresh_action = next(a for a in toolbar.actions() if a.text() == "Refresh All")
    refresh_action.trigger()

    qtbot.waitUntil(lambda: window._browser.model.rowCount(root_index) == 2, timeout=2000)




# --- drag straight onto the canvas (no layout step) -------------------------


def test_canvas_is_a_live_spectrum_canvas_from_the_start(qtbot):
    """No 'New Figure' step: the centre widget is a real, drop-ready spectrum
    canvas as soon as the window opens."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window._canvas, SpectrumCanvas)


def test_adjustment_bar_is_enabled_from_the_start(qtbot):
    """The canvas is always live, so the ppm controls are usable immediately
    rather than gated behind creating a figure."""
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._adjustment_bar._full_button.isEnabled()


def test_overlay_and_stacked_toggle_the_canvas_arrangement(qtbot):
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    window = MainWindow()
    qtbot.addWidget(window)

    window._use_stacked()
    assert window._canvas.arrangement() == SpectrumCanvas.ARRANGEMENT_STACKED
    assert window._stacked_action.isChecked()
    assert not window._overlay_action.isChecked()

    window._use_overlay()
    assert window._canvas.arrangement() == SpectrumCanvas.ARRANGEMENT_OVERLAY
    assert window._overlay_action.isChecked()
    assert not window._stacked_action.isChecked()


def test_clear_canvas_removes_all_traces(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window._clear_canvas()
    assert window._canvas.traces == []


def test_load_failure_is_reported_not_silent(qtbot):
    """A file that will not load must surface a message rather than vanishing."""
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_load_failed("/data/sample/99", "no pdata/1")
    assert "99" in window.statusBar().currentMessage()
