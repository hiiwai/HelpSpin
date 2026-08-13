"""Version tracking: __version__, the --version flag, window title, About."""

import subprocess
import sys
from pathlib import Path

import pytest

import helspin
from helspin.__main__ import MainWindow, _parse_args, APP_TITLE

pytestmark = pytest.mark.usefixtures("qapp")


def test_package_has_a_version_string():
    assert isinstance(helspin.__version__, str)
    assert helspin.__version__ != ""


def test_window_title_includes_the_version(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert helspin.__version__ in window.windowTitle()
    assert window.windowTitle().startswith(APP_TITLE)


def test_about_dialog_text_includes_the_version(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    captured = {}

    def fake_about(parent, title, text):
        captured["title"] = title
        captured["text"] = text

    monkeypatch.setattr(QMessageBox, "about", staticmethod(fake_about))
    window = MainWindow()
    qtbot.addWidget(window)
    window._about()

    assert helspin.__version__ in captured["text"]


def test_version_flag_parses():
    args = _parse_args(["--version"])
    assert args.version is True


def test_no_flags_parses_to_false():
    args = _parse_args([])
    assert args.version is False


def test_version_flag_via_subprocess_prints_and_exits_zero_without_a_display():
    """The real proof: --version must work even with no display and no
    QApplication ever constructed -- this is what makes it useful for
    troubleshooting a machine where the GUI itself won't come up.

    Deliberately strips QT_QPA_PLATFORM so this cannot be passing only
    because the offscreen platform happens to be set in the test
    environment -- --version must not need Qt to initialise at all.
    """
    import os

    env = os.environ.copy()
    env.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run(
        [sys.executable, "-m", "helspin", "--version"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "HelSpin" in result.stdout
    assert helspin.__version__ in result.stdout
