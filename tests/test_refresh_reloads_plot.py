"""0.9.28: Refresh All brings reprocessed data onto the plot.

Reported: a spectrum on the plot was reprocessed in TopSpin (new 2rr), and
neither Refresh All nor dropping it again changed what was shown. Refresh
only updated the explorer; a plotted spectrum was read once, at its drop,
and dropping it again was silently ignored.

Now every plotted spectrum is checked -- a few stats -- and only one whose
processed files changed is read again, its data replaced in place with its
colour, scale, offsets and label kept.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

from helspin.ui.spectrum_canvas import SpectrumCanvas, processed_stamp

pytestmark = pytest.mark.usefixtures("qapp")


class _Axis:
    def __init__(self, n):
        self.size = n

    def ppm_scale(self):
        return np.linspace(10.0, 0.0, self.size)


class _Spec1D:
    def __init__(self, values):
        self.real = values
        self.axis = _Axis(values.size)
        self.ns, self.rg = 1, 1.0


class _Spec2D:
    def __init__(self, matrix):
        self.real = matrix
        self.axis_f1 = _Axis(matrix.shape[0])
        self.axis_f2 = _Axis(matrix.shape[1])


class DiskReader:
    """Reads 'processed data' really from the experiment folder, so a file
    rewritten on disk is what the next read returns; counts reads."""

    def __init__(self, fail=False):
        self.reads = 0
        self.fail = fail

    def _values(self, path, name):
        self.reads += 1
        if self.fail:
            raise OSError("2rr is being written")
        return np.frombuffer((Path(path) / "pdata" / "1" / name).read_bytes(),
                             "<f8").copy()

    def read_1d(self, path, procno=1):
        return _Spec1D(self._values(path, "1r"))

    def read_2d(self, path, procno=1):
        return _Spec2D(self._values(path, "2rr").reshape(4, 8))


def write_processed(expno: Path, name: str, values, bump=0):
    pdata = expno / "pdata" / "1"
    pdata.mkdir(parents=True, exist_ok=True)
    (pdata / name).write_bytes(np.asarray(values, "<f8").tobytes())
    (pdata / "procs").write_text("##$PHC0= 0\n##END=\n")
    if bump:                     # a later write: newer mtime, like TopSpin
        later = time.time() + bump
        for f in pdata.iterdir():
            os.utime(f, (later, later))


def plotted(qtbot, canvas, expno, dim=1):
    canvas.add_dataset(expno, expno.name, dim)
    qtbot.waitUntil(lambda: len(canvas.traces) == 1 and not canvas._inflight,
                    timeout=5000)
    return canvas.traces[0]


@pytest.fixture
def canvas(qtbot):
    c = SpectrumCanvas(reader=DiskReader())
    qtbot.addWidget(c)
    return c


def finished(qtbot, canvas):
    results = []
    canvas.reloadFinished.connect(lambda *r: results.append(r))
    return results


def test_a_reprocessed_1d_is_replaced_keeping_its_style(qtbot, canvas, tmp_path):
    expno = tmp_path / "S" / "1"
    write_processed(expno, "1r", np.arange(16.0))
    trace = plotted(qtbot, canvas, expno)
    trace.color, trace.y_scale, trace.y_offset = "#123456", 2.5, 7.0
    write_processed(expno, "1r", np.arange(16.0) * 10, bump=5)   # TopSpin again
    results = finished(qtbot, canvas)
    assert canvas.reload_plotted() == 1
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    assert results == [(1, 0, 0)]
    assert canvas.traces[0] is trace                       # same trace, new data
    assert np.array_equal(trace.intensity, np.arange(16.0) * 10)
    assert (trace.color, trace.y_scale, trace.y_offset) == ("#123456", 2.5, 7.0)
    assert trace.source_stamp == processed_stamp(expno)


def test_an_unchanged_spectrum_is_not_read_again(qtbot, canvas, tmp_path):
    expno = tmp_path / "S" / "1"
    write_processed(expno, "1r", np.arange(16.0))
    plotted(qtbot, canvas, expno)
    reads = canvas._reader.reads
    results = finished(qtbot, canvas)
    canvas.reload_plotted()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    assert results == [(0, 1, 0)] and canvas._reader.reads == reads


def test_a_reprocessed_2d_is_replaced(qtbot, canvas, tmp_path):
    expno = tmp_path / "S" / "10"
    write_processed(expno, "2rr", np.ones(32))
    trace = plotted(qtbot, canvas, expno, dim=2)
    write_processed(expno, "2rr", np.full(32, 3.0), bump=5)
    results = finished(qtbot, canvas)
    canvas.reload_plotted()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    assert results == [(1, 0, 0)] and float(trace.matrix.max()) == 3.0


def test_dropping_a_plotted_spectrum_again_brings_in_new_data(qtbot, canvas, tmp_path):
    expno = tmp_path / "S" / "1"
    write_processed(expno, "1r", np.arange(16.0))
    trace = plotted(qtbot, canvas, expno)
    write_processed(expno, "1r", np.full(16, 5.0), bump=5)
    canvas.add_dataset(expno, expno.name, 1)                 # dropped again
    qtbot.waitUntil(lambda: not canvas._reloading, timeout=5000)
    assert len(canvas.traces) == 1 and float(trace.intensity[0]) == 5.0


def test_a_failed_reload_keeps_the_data_and_does_not_grey(qtbot, canvas, tmp_path):
    expno = tmp_path / "S" / "1"
    write_processed(expno, "1r", np.arange(16.0))
    trace = plotted(qtbot, canvas, expno)
    write_processed(expno, "1r", np.full(16, 5.0), bump=5)
    canvas._reader.fail = True
    failed, load_failed = [], []
    canvas.reloadFailed.connect(lambda p, m: failed.append(m))
    canvas.loadFailed.connect(lambda p, m: load_failed.append(m))
    results = finished(qtbot, canvas)
    canvas.reload_plotted()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    assert results == [(0, 0, 1)] and failed and not load_failed
    assert np.array_equal(trace.intensity, np.arange(16.0))  # old data kept
    canvas._reader.fail = False                              # TopSpin finished
    results.clear()
    canvas.reload_plotted()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    assert results == [(1, 0, 0)] and float(trace.intensity[0]) == 5.0


def test_several_spectra_only_changed_ones_are_read(qtbot, canvas, tmp_path):
    paths = []
    for n in (1, 2, 3):
        expno = tmp_path / "S" / str(n)
        write_processed(expno, "1r", np.arange(16.0) + n)
        canvas.add_dataset(expno, expno.name, 1)
        paths.append(expno)
    qtbot.waitUntil(lambda: len(canvas.traces) == 3 and not canvas._inflight,
                    timeout=5000)
    write_processed(paths[1], "1r", np.zeros(16), bump=5)
    results = finished(qtbot, canvas)
    canvas.reload_plotted()
    qtbot.waitUntil(lambda: bool(results), timeout=5000)
    assert results == [(1, 2, 0)]


def test_refresh_all_in_the_window_also_reloads_the_plot(qtbot, monkeypatch):
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window._canvas, "reload_plotted",
                        lambda *a: calls.append(a) or 0)
    refresh = next(a for a in window.findChildren(type(window._detach_action))
                   if a.text() == "Refresh All")
    refresh.trigger()
    assert calls == [()]
