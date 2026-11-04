"""0.9.12: copy the plot to the clipboard, to paste into another application."""

from __future__ import annotations

import pytest
from matplotlib.figure import Figure
from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence
from PySide6.QtTest import QTest

import helspin.ui.figure_export as fe
from helspin.domain.cest import CestError
from helspin.ui.cest_panel import CestPanel
from helspin.ui.spectrum_canvas import SpectrumCanvas

from .cest_fixtures import DEFAULT_OFFSETS, make_dataset
from .test_canvas_controls import FakeReader, item, mime_for

pytestmark = pytest.mark.usefixtures("qapp")


class RecordingClipboard:
    def __init__(self):
        self.images = []

    def setImage(self, image):            # noqa: N802 (Qt API)
        self.images.append(image)


class BrokenClipboard:
    def setImage(self, image):            # noqa: N802 (Qt API)
        raise RuntimeError("clipboard is locked")


def _figure(width=4.0, height=3.0):
    figure = Figure(figsize=(width, height))
    figure.add_subplot().plot([0, 1], [0, 1], color="black")
    return figure


def _actions(menu):
    return {a.text(): a for a in menu.actions() if a.text()}


# --------------------------------------------------------- shared helper


def test_png_bytes_are_a_png_at_the_figure_size():
    data = fe.figure_png_bytes(_figure(4, 3), dpi=50)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    from PySide6.QtGui import QImage

    image = QImage.fromData(data, "PNG")
    assert (image.width(), image.height()) == (200, 150)


def test_copy_puts_an_opaque_white_image_on_the_clipboard():
    image = fe.copy_figure_to_clipboard(_figure(4, 3), dpi=100)
    pasted = QGuiApplication.clipboard().image()
    assert not pasted.isNull()
    assert (pasted.width(), pasted.height()) == (image.width(), image.height())
    assert (image.width(), image.height()) == (400, 300)
    corner = pasted.pixelColor(0, 0)
    assert corner == QColor("white") and corner.alpha() == 255


def test_copy_records_the_resolution():
    image = fe.copy_figure_to_clipboard(_figure(), dpi=300,
                                        clipboard=RecordingClipboard())
    assert image.dotsPerMeterX() == image.dotsPerMeterY() == round(300 / 0.0254)


def test_copy_uses_the_clipboard_given():
    board = RecordingClipboard()
    fe.copy_figure_to_clipboard(_figure(), dpi=40, clipboard=board)
    assert len(board.images) == 1


def test_an_unrenderable_figure_raises(monkeypatch):
    monkeypatch.setattr(fe, "figure_png_bytes", lambda figure, dpi: b"not a png")
    with pytest.raises(RuntimeError, match="could not be rendered"):
        fe.copy_figure_to_clipboard(_figure(), clipboard=RecordingClipboard())


def test_no_clipboard_raises(monkeypatch):
    monkeypatch.setattr(QGuiApplication, "clipboard", staticmethod(lambda: None))
    with pytest.raises(RuntimeError, match="no clipboard"):
        fe.copy_figure_to_clipboard(_figure(), dpi=40)


# ---------------------------------------------------------- main canvas


@pytest.fixture
def canvas(qtbot):
    widget = SpectrumCanvas(reader=FakeReader())
    qtbot.addWidget(widget)
    widget.resize(700, 450)
    widget.show()
    qtbot.waitExposed(widget)
    widget.handle_mime_data(mime_for(item("/d/0", "s/0")))
    qtbot.waitUntil(lambda: len(widget.traces) == 1, timeout=3000)
    return widget


def test_an_empty_canvas_copies_nothing(qtbot):
    widget = SpectrumCanvas(reader=FakeReader())
    qtbot.addWidget(widget)
    QGuiApplication.clipboard().setText("keep me")
    copied = []
    widget.imageCopied.connect(lambda: copied.append(True))
    assert widget.copy_image() is False
    assert QGuiApplication.clipboard().text() == "keep me"
    assert copied == []


def test_copy_image_copies_without_the_crosshair(qtbot, canvas):
    mpl = canvas._canvas
    mpl.draw()
    bbox = canvas._axes.bbox
    QTest.mouseMove(mpl, QPoint(int((bbox.x0 + bbox.x1) / 2),
                                int(mpl.figure.bbox.height - (bbox.y0 + bbox.y1) / 2)))
    assert canvas._crosshair is not None
    with qtbot.waitSignal(canvas.imageCopied, timeout=2000):
        assert canvas.copy_image(dpi=50) is True
    assert canvas._crosshair is None
    pasted = QGuiApplication.clipboard().image()
    width, height = canvas._figure.get_size_inches()
    assert pasted.width() == pytest.approx(width * 50, abs=1)
    assert pasted.height() == pytest.approx(height * 50, abs=1)


def test_a_failed_copy_is_reported_not_raised(qtbot, canvas):
    copied = []
    canvas.imageCopied.connect(lambda: copied.append(True))
    with qtbot.waitSignal(canvas.exportFailed, timeout=2000) as blocker:
        assert canvas.copy_image(dpi=40, clipboard=BrokenClipboard()) is False
    assert "Could not copy" in blocker.args[0] and "locked" in blocker.args[0]
    assert copied == []


def test_menu_offers_copy_right_after_save(canvas):
    texts = [a.text() for a in canvas.build_context_menu().actions() if a.text()]
    assert texts[:2] == ["Save image\u2026", "Copy image"]
    QGuiApplication.clipboard().clear()
    _actions(canvas.build_context_menu())["Copy image"].trigger()
    assert not QGuiApplication.clipboard().image().isNull()


def test_a_failed_save_says_save_not_load(qtbot, canvas, monkeypatch, tmp_path):
    """It used to emit loadFailed: "Could not load <image>" in the status
    bar, and an attempt to grey out a browser row for the image file."""
    import helspin.ui.spectrum_canvas as sc

    monkeypatch.setattr(sc.QFileDialog, "getSaveFileName", staticmethod(
        lambda *a, **k: (str(tmp_path / "x.png"), "PNG image (*.png)")))

    def boom(path, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(canvas, "save_image", boom)
    loads = []
    canvas.loadFailed.connect(lambda *a: loads.append(a))
    with qtbot.waitSignal(canvas.exportFailed, timeout=2000) as blocker:
        canvas.request_save_image()
    assert blocker.args[0].startswith("Could not save") and "disk full" in blocker.args[0]
    assert loads == []


# ----------------------------------------------------------- main window


@pytest.fixture
def window(qtbot, monkeypatch):
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    win = MainWindow()
    qtbot.addWidget(win)
    win._canvas._reader = FakeReader()
    return win


def test_edit_menu_has_copy_image_with_its_own_shortcut(window):
    action = window._copy_image_action
    assert action.text() == "&Copy Image"
    # Not plain Copy: that belongs to the focused text field.
    assert action.shortcut() == QKeySequence("Ctrl+Shift+C")
    assert action.shortcut() != QKeySequence(QKeySequence.StandardKey.Copy)
    edit = [m for m in window.menuBar().actions() if m.text() == "&Edit"][0].menu()
    assert action in edit.actions()


def test_copy_from_the_menu_with_nothing_loaded_says_so(window):
    QGuiApplication.clipboard().setText("keep me")
    window._copy_image_action.trigger()
    assert "Nothing to copy" in window.statusBar().currentMessage()
    assert QGuiApplication.clipboard().text() == "keep me"


def test_copy_from_the_menu_reports_success(qtbot, window):
    window._canvas.handle_mime_data(mime_for(item("/d/0", "s/0")))
    qtbot.waitUntil(lambda: len(window._canvas.traces) == 1, timeout=3000)
    window._copy_image_action.trigger()
    assert "copied" in window.statusBar().currentMessage().lower()
    assert not QGuiApplication.clipboard().image().isNull()


def test_export_failures_reach_the_status_bar(window):
    window._canvas.exportFailed.emit("Could not copy the image: x")
    assert window.statusBar().currentMessage() == "Could not copy the image: x"


# ------------------------------------------------------------------ CEST


@pytest.fixture
def panel(qtbot):
    widget = CestPanel()
    qtbot.addWidget(widget)
    return widget


def _built(panel, tmp_path):
    n = len(DEFAULT_OFFSETS)
    root = make_dataset(tmp_path / "set" / "c", n_rows=n, si_f1=n,
                        offsets=DEFAULT_OFFSETS)
    panel.load(root)
    panel._build_z()


def test_cest_copy_of_an_empty_tab_is_refused(panel):
    with pytest.raises(CestError, match="nothing is plotted"):
        panel.copy_image(panel._z_plot, clipboard=RecordingClipboard())


def test_cest_menu_copy_is_disabled_until_there_is_a_plot(panel, tmp_path):
    assert not _actions(panel.build_plot_menu(panel._z_plot))["Copy image"].isEnabled()
    _built(panel, tmp_path)
    copy = _actions(panel.build_plot_menu(panel._z_plot))["Copy image"]
    assert copy.isEnabled()
    QGuiApplication.clipboard().clear()
    copy.trigger()
    pasted = QGuiApplication.clipboard().image()
    width, height = panel._z_plot.figure.get_size_inches()
    assert pasted.width() == pytest.approx(width * 300, abs=1)
    assert pasted.height() == pytest.approx(height * 300, abs=1)


def test_cest_copy_failure_raises_cest_error(panel, tmp_path):
    _built(panel, tmp_path)
    with pytest.raises(CestError, match="could not copy"):
        panel.copy_image(panel._z_plot, dpi=40, clipboard=BrokenClipboard())


def test_cest_menu_copy_failure_is_shown_not_raised(panel, tmp_path, monkeypatch):
    import helspin.ui.cest_panel as cp

    _built(panel, tmp_path)
    shown = []
    monkeypatch.setattr(cp.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(cp, "copy_figure_to_clipboard",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("busy")))
    _actions(panel.build_plot_menu(panel._z_plot))["Copy image"].trigger()
    assert shown and "busy" in shown[0]
