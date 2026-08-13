"""SpectrumCanvas: drop datasets, load them, draw them.

Drop handling is tested via handle_mime_data with a real QMimeData rather
than constructing QDropEvent objects, whose signature varies across Qt
versions -- the same approach used elsewhere in this suite.
"""

import json

import numpy as np
import pytest
from PySide6.QtCore import QMimeData

from helspin.ui.dataset_model import MIME_DATASET
from helspin.ui.spectrum_canvas import SpectrumCanvas

pytestmark = pytest.mark.usefixtures("qapp")


class FakeSpectrum:
    def __init__(self, n=256):
        self.real = np.linspace(0.0, 100.0, n)

        class Axis:
            size = n

            def ppm_scale(self_inner):
                # descending, as ppm axes always are
                return np.linspace(10.0, 0.0, n)

        self.axis = Axis()
        self.ns = 1
        self.rg = 1.0


class FakeReader:
    """Returns a spectrum for any path; records what was asked for."""

    def __init__(self, fail_for=None):
        self.requested = []
        self._fail_for = set(fail_for or ())

    def read_1d(self, path, procno=1):
        self.requested.append(str(path))
        if str(path) in self._fail_for:
            raise OSError("unreadable")
        return FakeSpectrum()


def mime_for(*items) -> QMimeData:
    mime = QMimeData()
    mime.setData(MIME_DATASET, json.dumps(list(items)).encode("utf-8"))
    return mime


def item(path="/data/s/1", dim=1, label="s/1"):
    return {"path": path, "dimensionality": dim, "label": label}


def wait_for_traces(qtbot, canvas, n, timeout=3000):
    qtbot.waitUntil(lambda: len(canvas.traces) == n, timeout=timeout)


# --- empty state ---------------------------------------------------------


def test_starts_empty():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.traces == []


def test_default_arrangement_is_overlay():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.arrangement() == SpectrumCanvas.ARRANGEMENT_OVERLAY


def test_accepts_drops():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.acceptDrops()


# --- dropping ------------------------------------------------------------


def test_dropping_one_dataset_loads_and_draws_it(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    assert canvas.handle_mime_data(mime_for(item()))
    wait_for_traces(qtbot, canvas, 1)
    assert canvas.traces[0].label == "s/1"
    assert canvas.traces[0].ppm.size == 256


def test_dropping_several_loads_all_of_them(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(
        mime_for(item(path="/d/1"), item(path="/d/2"), item(path="/d/3"))
    )
    wait_for_traces(qtbot, canvas, 3)


def test_each_trace_gets_a_distinct_palette_colour(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item(path="/d/1"), item(path="/d/2")))
    wait_for_traces(qtbot, canvas, 2)
    colours = [t.color for t in canvas.traces]
    assert colours[0] != colours[1]


def test_dropping_the_same_dataset_twice_does_not_duplicate(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item(path="/d/1")))
    wait_for_traces(qtbot, canvas, 1)
    canvas.handle_mime_data(mime_for(item(path="/d/1")))
    qtbot.wait(150)
    assert len(canvas.traces) == 1


def test_drop_with_wrong_mime_type_is_ignored():
    canvas = SpectrumCanvas(reader=FakeReader())
    other = QMimeData()
    other.setText("not a dataset")
    assert not canvas.handle_mime_data(other)


def test_drop_with_malformed_json_is_ignored():
    canvas = SpectrumCanvas(reader=FakeReader())
    bad = QMimeData()
    bad.setData(MIME_DATASET, b"{not json")
    assert not canvas.handle_mime_data(bad)


def test_drop_with_empty_payload_is_ignored():
    canvas = SpectrumCanvas(reader=FakeReader())
    assert not canvas.handle_mime_data(mime_for())


# --- failures must be visible, not silent --------------------------------


def test_unreadable_dataset_emits_load_failed(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader(fail_for=["/d/bad"]))
    with qtbot.waitSignal(canvas.loadFailed, timeout=3000) as blocker:
        canvas.handle_mime_data(mime_for(item(path="/d/bad")))
    assert "/d/bad" in blocker.args[0]
    assert canvas.traces == []


def test_a_2d_dataset_reports_that_2d_is_not_implemented(qtbot):
    """2D contour display is genuinely not built yet; dropping one must say
    so rather than silently doing nothing."""
    canvas = SpectrumCanvas(reader=FakeReader())
    with qtbot.waitSignal(canvas.loadFailed, timeout=3000) as blocker:
        canvas.handle_mime_data(mime_for(item(path="/d/2d", dim=2)))
    assert "2d" in blocker.args[1].lower()


def test_one_bad_file_does_not_prevent_the_others_loading(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader(fail_for=["/d/bad"]))
    canvas.handle_mime_data(
        mime_for(item(path="/d/1"), item(path="/d/bad"), item(path="/d/2"))
    )
    wait_for_traces(qtbot, canvas, 2)
    paths = {str(t.path) for t in canvas.traces}
    assert paths == {"/d/1", "/d/2"}


# --- arrangement and range ------------------------------------------------


def test_set_arrangement_switches_to_stacked(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.set_arrangement(SpectrumCanvas.ARRANGEMENT_STACKED)
    assert canvas.arrangement() == SpectrumCanvas.ARRANGEMENT_STACKED


def test_unknown_arrangement_is_ignored():
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.set_arrangement("spiral")
    assert canvas.arrangement() == SpectrumCanvas.ARRANGEMENT_OVERLAY


def test_ascending_ppm_range_is_rejected(qtbot):
    """ppm axes descend: left must be the higher value."""
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item()))
    wait_for_traces(qtbot, canvas, 1)
    canvas.set_ppm_range(2.0, 8.0)      # invalid
    assert canvas._ppm_range is None


def test_valid_ppm_range_is_applied(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item()))
    wait_for_traces(qtbot, canvas, 1)
    canvas.set_ppm_range(8.0, 2.0)
    assert canvas._ppm_range == (8.0, 2.0)


def test_full_range_clears_the_manual_range(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item()))
    wait_for_traces(qtbot, canvas, 1)
    canvas.set_ppm_range(8.0, 2.0)
    canvas.full_range()
    assert canvas._ppm_range is None


# --- removing -------------------------------------------------------------


def test_clear_removes_everything(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item(path="/d/1"), item(path="/d/2")))
    wait_for_traces(qtbot, canvas, 2)
    canvas.clear()
    assert canvas.traces == []


def test_remove_trace_removes_only_that_one(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item(path="/d/1"), item(path="/d/2")))
    wait_for_traces(qtbot, canvas, 2)
    canvas.remove_trace("/d/1")
    assert [str(t.path) for t in canvas.traces] == ["/d/2"]


def test_removing_an_unknown_path_is_harmless(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.handle_mime_data(mime_for(item(path="/d/1")))
    wait_for_traces(qtbot, canvas, 1)
    canvas.remove_trace("/d/nope")
    assert len(canvas.traces) == 1


def test_redrawing_with_no_traces_does_not_raise():
    canvas = SpectrumCanvas(reader=FakeReader())
    canvas.clear()          # already empty
    canvas._redraw()        # must not raise
