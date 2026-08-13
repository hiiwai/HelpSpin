"""The licence, readable from inside the application.

Two reasons this exists rather than a line in the About box:

* HelSpin is free for research and teaching and NOT free for commercial use.
  A user cannot honour a condition they have never seen, so the terms have to
  be reachable without hunting for a file on disk.
* Qt ships under the LGPL, which requires that its licence and the offer of
  source travel with the binary. A menu entry satisfies that for a packaged
  build, where there may be no visible LICENSE file at all.

The texts are read from the installed package, not embedded in this module,
so the file a user reads in the dialog is byte-for-byte the one distributed
with the software -- they cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
)

MISSING = (
    "The licence text could not be found in this installation.\n\n"
    "HelSpin is free for academic research, teaching and personal use.\n"
    "Commercial use requires a separate licence from the copyright holder.\n\n"
    "The full text ships as LICENSE with the source distribution."
)


def _resource(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / name


def licence_text() -> str:
    """The licence as distributed, or a short summary if it is missing.

    A missing file must not raise: failing to open a dialog is a worse outcome
    than showing a summary, and the summary still states the one condition
    that matters.
    """
    try:
        return _resource("LICENSE.txt").read_text(encoding="utf-8")
    except OSError:
        return MISSING


def notice_text() -> str:
    """Third-party licences and the LGPL relinking offer."""
    try:
        return _resource("NOTICE.txt").read_text(encoding="utf-8")
    except OSError:
        return (
            "HelSpin depends on PySide6/Qt (LGPL v3), nmrglue, numpy, scipy\n"
            "and matplotlib, each under its own licence."
        )


def _page(text: str) -> QPlainTextEdit:
    view = QPlainTextEdit()
    view.setPlainText(text)
    view.setReadOnly(True)
    # Fixed pitch because both documents are hand-wrapped plain text; a
    # proportional font ruins the alignment in the NOTICE table.
    font = view.font()
    font.setFamily("monospace")
    font.setStyleHint(font.StyleHint.TypeWriter)
    view.setFont(font)
    view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    return view


class LicenceDialog(QDialog):
    """Read-only view of the licence and the third-party notices."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HelSpin — Licence")
        self.resize(720, 560)

        self._tabs = QTabWidget()
        self._tabs.addTab(_page(licence_text()), "Licence")
        self._tabs.addTab(_page(notice_text()), "Third-party notices")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(buttons)
