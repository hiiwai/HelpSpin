"""CanvasPlaceholder: an honest stand-in, not a mockup."""

import pytest

from helspin.ui.canvas_placeholder import DEFAULT_MESSAGE, CanvasPlaceholder

pytestmark = pytest.mark.usefixtures("qapp")


def test_shows_the_default_message_on_construction():
    canvas = CanvasPlaceholder()
    assert canvas._label.text() == DEFAULT_MESSAGE


def test_show_message_replaces_the_text():
    canvas = CanvasPlaceholder()
    canvas.show_message("Loading spectrum…")
    assert canvas._label.text() == "Loading spectrum…"


def test_clear_message_restores_the_default():
    canvas = CanvasPlaceholder()
    canvas.show_message("Something else")
    canvas.clear_message()
    assert canvas._label.text() == DEFAULT_MESSAGE
