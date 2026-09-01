"""Regressions for the five issues reported against 0.4.0.

Reported as: "could load 3 spectra but not more", "contour controls show for
1D", "cannot drop after removing", "unclear which spectra can be dropped, no
errors in the terminal", and "starting is slow".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, Qt

from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui.dataset_model import MIME_DATASET, DatasetTreeModel
from helspin.ui.spectrum_canvas import SpectrumCanvas
from helspin.ui.spectrum_list_panel import SpectrumListPanel

pytestmark = pytest.mark.usefixtures("qapp")


ACQUS = """##$NUC1= <1H>
##$PULPROG= <zgesgp>
##$PARMODE= 0
##$NS= 16
##$RG= 101
##END=
"""


def make_expno(sample: Path, expno: int, processed=True) -> Path:
    d = sample / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    (d / "acqus").write_text(ACQUS)
    (d / "fid").write_bytes(b"\x00" * 8)
    procno = d / "pdata" / "1"
    procno.mkdir(parents=True, exist_ok=True)
    # An acquired-but-never-processed experiment still has pdata/1 full of
    # parameter files -- just no spectrum in it.
    (procno / "procs").write_text("##$SI= 32768\n")
    if processed:
        (procno / "1r").write_bytes(b"\x00" * 8)
    return d


class FakeSpectrum:
    def __init__(self, n=128):
        self.real = np.linspace(0.0, 100.0, n)

        class Axis:
            size = n

            def ppm_scale(self_inner):
                return np.linspace(10.0, 0.0, n)

        self.axis = Axis()
        self.ns = 1
        self.rg = 1.0


class FakeReader:
    def read_1d(self, path, procno=1):
        return FakeSpectrum()

    def read_auto(self, path, procno=1):
        return 1, FakeSpectrum()


def mime_for(*items):
    mime = QMimeData()
    mime.setData(MIME_DATASET, json.dumps(list(items)).encode("utf-8"))
    return mime


def item(path, dim=1):
    return {"path": path, "dimensionality": dim, "label": Path(path).name}


# --- 1 & 3: "could load 3 but not more", "cannot drop after removing" -------


def test_there_is_no_limit_on_how_many_spectra_can_be_loaded(qtbot):
    """Ten separate drops, one at a time, exactly as a user does them.

    Reported as "I could load 3 spectra but I could not load more". There is
    no cap anywhere in the canvas -- what stopped at three were LOAD FAILURES
    whose message the status bar threw away (see the status-bar test below).
    This pins down that the canvas itself has no such limit, so any future
    "it stops after N" is a failure to report, not a ceiling.
    """
    canvas = SpectrumCanvas(reader=FakeReader())
    qtbot.addWidget(canvas)
    for i in range(10):
        canvas.handle_mime_data(mime_for(item(f"/d/{i}")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 10, timeout=5000)


def test_dropping_still_works_after_removing_traces(qtbot):
    """Remove some, drop more, re-drop a removed one. Reported as "I could
    not drop new spectra after some and delete"."""
    canvas = SpectrumCanvas(reader=FakeReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(mime_for(*[item(f"/d/{i}") for i in range(5)]))
    qtbot.waitUntil(lambda: len(canvas.traces) == 5, timeout=5000)

    canvas.remove_trace("/d/1")
    canvas.remove_trace("/d/3")
    assert len(canvas.traces) == 3

    canvas.handle_mime_data(mime_for(item("/d/20"), item("/d/21")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 5, timeout=5000)

    # A path that was removed must be droppable again -- the de-duplication
    # is against what is CURRENTLY shown, not against everything ever seen.
    canvas.handle_mime_data(mime_for(item("/d/1")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 6, timeout=5000)


def test_dropping_works_after_clearing_the_canvas(qtbot):
    canvas = SpectrumCanvas(reader=FakeReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(mime_for(item("/d/1"), item("/d/2")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=5000)
    canvas.clear()
    assert canvas.traces == []
    canvas.handle_mime_data(mime_for(item("/d/1")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=5000)


def test_a_failed_load_leaves_no_state_blocking_the_next_drop(qtbot):
    """One dataset failing must not poison the canvas for the next one."""

    class HalfBrokenReader(FakeReader):
        def read_1d(self, path, procno=1):
            if str(path).endswith("bad"):
                raise OSError("no processed data")
            return FakeSpectrum()

    canvas = SpectrumCanvas(reader=HalfBrokenReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(mime_for(item("/d/bad")))
    qtbot.wait(200)
    assert canvas.traces == []

    canvas.handle_mime_data(mime_for(item("/d/good")))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=5000)
    assert canvas._pending_meta == {}, "a failed load must not leak pending state"


# --- 2: contour controls belong to 2D only ----------------------------------


def test_contour_controls_are_hidden_until_a_2d_spectrum_is_shown(qtbot):
    """Contours / Factor / Base sigma mean nothing for a 1D trace.

    set_mode() had the logic but was only reached through the canvas's
    modeChanged signal, which fires on a CHANGE -- so at start-up, in 1D,
    nothing had ever called it and all three controls were on screen.
    """
    panel = SpectrumListPanel()
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._levels_spin.isVisible()
    assert not panel._factor_spin.isVisible()
    assert not panel._base_spin.isVisible()
    assert all(not label.isVisible() for label in panel._contour_labels)

    panel.set_mode("2D")
    assert panel._levels_spin.isVisible()
    panel.set_mode("1D")
    assert not panel._levels_spin.isVisible()


# --- 4: "unclear which spectra can be dropped, no errors in the terminal" ---


def test_an_unprocessed_experiment_is_dimmed_and_explains_itself(tmp_path):
    """pdata existing is NOT the same as a spectrum existing.

    An experiment acquired but never processed has a pdata/1 full of parameter
    files and no 1r. It was listed as perfectly droppable and then failed on
    drop, with the explanation wiped from the status bar -- so it read as
    "some spectra just do not open".

    It is DIMMED, not blocked: the check can be wrong (a share that blinked,
    an unusual procno layout, data processed since it last ran), and turning a
    wrong guess into "this file cannot be opened at all" would be a worse
    failure than a warning. The tooltip names what was actually found.
    """
    root = tmp_path / "nmr"
    make_expno(root / "sample", 1, processed=True)
    make_expno(root / "sample", 2, processed=False)

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)
    for child in sample_idx.internalPointer().children:
        model.probe_node(child)

    rows = {
        model.data(model.index(r, 0, sample_idx)): model.index(r, 0, sample_idx)
        for r in range(model.rowCount(sample_idx))
    }
    good, bad = rows["1"], rows["2"]

    assert good.internalPointer().loadable
    assert not bad.internalPointer().loadable
    assert model.data(bad, Qt.ForegroundRole) is not None, "must be dimmed"
    assert model.data(good, Qt.ForegroundRole) is None

    note = model.data(bad, Qt.ToolTipRole)
    assert "1r" in note and "procs" in note, (
        f"the reason must name what was actually found, got {note!r}"
    )

    # Still draggable: a wrong guess must never make real data unopenable.
    assert model.flags(bad) & Qt.ItemIsDragEnabled
    payload = json.loads(
        bytes(model.mimeData([good, bad]).data(MIME_DATASET)).decode("utf-8")
    )
    assert [p["expno"] for p in payload] == ["1", "2"]


def test_an_unreadable_pdata_is_undecided_not_declared_empty(tmp_path, monkeypatch):
    """"Could not look" and "nothing there" are different answers.

    Returning False for an OSError meant one momentary share hiccup marked a
    perfectly good dataset as having no data -- and cached it, so the row
    stayed grey for that session and every session after.
    """
    expno = tmp_path / "7"
    (expno / "pdata" / "1").mkdir(parents=True)
    (expno / "pdata" / "1" / "1r").write_bytes(b"\x00" * 8)

    real_scandir = os.scandir

    def flaky(path, *a, **k):
        if "pdata" in str(path):
            raise OSError(5, "Input/output error")
        return real_scandir(path, *a, **k)

    monkeypatch.setattr(os, "scandir", flaky)
    state, note = di.inspect_processed(expno)
    assert state is None, "a failed read must not be recorded as 'no data'"
    assert "could not read" in note.lower()


def test_processing_a_dataset_then_refreshing_clears_the_verdict(tmp_path):
    """Processing writes inside <expno>/pdata, which does not change the
    SAMPLE directory's mtime -- so if a refresh kept the old answer, a dataset
    just processed in TopSpin would stay marked "no data" for ever and
    Refresh, the one thing the user would try, would appear to do nothing."""
    root = tmp_path / "nmr"
    sample = root / "sample"
    expno = make_expno(sample, 1, processed=False)

    entry = di.SampleEntry(path=str(sample))
    di.refresh_sample(entry)
    entry.expnos[0].has_processed = False
    entry.expnos[0].processed_note = "no 1r"

    (expno / "pdata" / "1" / "1r").write_bytes(b"\x00" * 8)   # process it
    di.refresh_sample(entry)
    assert entry.expnos[0].has_processed is None, (
        "refresh must re-check, not carry the stale verdict over"
    )


def test_processed_present_finds_a_spectrum_under_any_procno(tmp_path):
    expno = tmp_path / "5"
    (expno / "pdata" / "3").mkdir(parents=True)
    (expno / "pdata" / "3" / "2rr").write_bytes(b"\x00" * 8)
    assert di.processed_present(expno) is True

    empty = tmp_path / "6"
    (empty / "pdata" / "1").mkdir(parents=True)
    assert di.processed_present(empty) is False


def test_a_failed_drop_greys_out_the_row_it_came_from(tmp_path):
    """The loop the user never got closed: a drop that fails must mark the
    row, not just emit a message nothing displays for long."""
    root = tmp_path / "nmr"
    expno = make_expno(root / "sample", 1)

    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    root_idx = model.index(0, 0)
    model.fetchMore(root_idx)
    sample_idx = model.index(0, 0, root_idx)
    model.fetchMore(sample_idx)
    row = model.index(0, 0, sample_idx)
    assert model.data(row, Qt.ForegroundRole) is None

    assert model.mark_expno_failed(str(expno), "no pdata/1 under this experiment")
    assert "no pdata/1" in model.data(row, Qt.ToolTipRole)
    assert model.data(row, Qt.ForegroundRole) is not None, "must be dimmed"
    assert row.internalPointer().load_note() == "no pdata/1 under this experiment"


def test_marking_an_unknown_path_is_a_harmless_no_op(tmp_path):
    root = tmp_path / "nmr"
    make_expno(root / "sample", 1)
    model = DatasetTreeModel([DataRoot(name="600", path=root)])
    assert model.mark_expno_failed("/nowhere/at/all/9", "boom") is False


def test_the_cursor_readout_cannot_wipe_a_warning(qtbot, tmp_path, monkeypatch):
    """The reason every failure looked like silence.

    The position readout called statusBar().showMessage() -- the same slot
    every warning uses -- on every mouse move over the plot. A "cannot load"
    or "clear the canvas first" message survived milliseconds. The readout now
    owns a separate permanent widget.
    """
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)

    window._on_load_failed(str(tmp_path / "sample" / "1"), "no processed data")
    assert "no processed data" in window.statusBar().currentMessage()

    window._on_cursor_moved(7.26, 1.2e9)
    assert "no processed data" in window.statusBar().currentMessage(), (
        "moving the cursor must not erase the explanation"
    )
    assert "7.26" in window._cursor_label.text()


# --- 5: "why is starting so slow?" ------------------------------------------


def test_nmrglue_is_not_imported_at_start_up():
    """nmrglue costs ~0.8 s to import (it pulls in scipy) and nothing on the
    start-up path needs it: browser rows are filled by read_acqus_fast, which
    is pure stdlib. Importing it eagerly was ~60% of the time between
    launching HelSpin and seeing a window."""
    code = (
        "import sys;"
        "from helspin.__main__ import MainWindow;"
        "print('nmrglue' in sys.modules, 'scipy' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin",
             "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False False", out.stdout


def test_a_spectrum_in_a_non_default_procno_is_found(tmp_path):
    """pdata/1 holding no spectrum does NOT mean the experiment has none.

    Bruker routinely puts the result elsewhere -- an STD difference writes
    on-resonance, off-resonance and the difference into separate procnos, and
    pdata/1 can hold parameters and nothing plottable. Stopping at an empty
    pdata/1 marked exactly those datasets unusable. Found by `helspin --check`
    reporting NO for an experiment whose spectrum was sitting in pdata/3.
    """
    expno = tmp_path / "10"
    (expno / "pdata" / "1").mkdir(parents=True)
    (expno / "pdata" / "1" / "procs").write_text("##$SI= 1024\n")
    (expno / "pdata" / "3").mkdir()
    (expno / "pdata" / "3" / "2rr").write_bytes(b"\x00" * 8)

    state, note = di.inspect_processed(expno)
    assert state is True, note


def test_an_experiment_with_no_pdata_at_all_says_so(tmp_path):
    expno = tmp_path / "20"
    expno.mkdir()
    state, note = di.inspect_processed(expno)
    assert state is False
    assert "never processed" in note


def test_check_reports_every_experiment_in_a_sample(tmp_path, capsys):
    """`helspin --check <sample>` is the answer to "why is this row dimmed?"
    that does not require trusting the browser's own conclusion."""
    from helspin.__main__ import check_datasets

    sample = tmp_path / "sample"
    make_expno(sample, 1, processed=True)
    make_expno(sample, 2, processed=False)

    assert check_datasets(sample) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "NO" in out
    assert "1r" in out, "the report must say what was missing"


def test_check_on_a_directory_that_is_not_a_dataset_is_reported(tmp_path, capsys):
    from helspin.__main__ import check_datasets

    empty = tmp_path / "nothing"
    empty.mkdir()
    assert check_datasets(empty) == 2
    assert "no experiments" in capsys.readouterr().out


# --- 0.4.9: axis naming and 2D arrangement ----------------------------------


def test_the_f1_range_boxes_are_labelled_for_a_vertical_axis(qtbot):
    """F1 is the INDIRECT dimension and is drawn vertically, so "left" and
    "right" named the wrong axis entirely."""
    from helspin.ui.adjustment_bar import AdjustmentBar

    bar = AdjustmentBar()
    qtbot.addWidget(bar)
    bar.set_mode("2D")

    assert "top" in bar._f1_label.text().lower()
    assert "bottom" in bar._f1_right_label.text().lower()
    assert "left" not in bar._f1_label.text().lower()
    assert "right" not in bar._f1_right_label.text().lower()


# --- 0.4.10: F1 orientation, zoom mode, opacity -----------------------------


def test_the_f1_top_box_holds_what_is_at_the_top_of_the_plot(qtbot):
    """F1 is drawn with the LOW ppm value at the top (set_ylim(high, low)), so
    putting the high value in the box labelled "top" stated the opposite of
    what the plot showed: 35 ppm under "top" while 35 sat on the bottom axis.
    """
    from helspin.ui.adjustment_bar import AdjustmentBar

    bar = AdjustmentBar()
    qtbot.addWidget(bar)
    bar.set_mode("2D")
    bar.set_f1_range(35.0, 5.0)          # (high, low), as the canvas uses

    assert bar._f1_left.value() == pytest.approx(5.0), "top box = top of plot"
    assert bar._f1_right.value() == pytest.approx(35.0)
    # and the accessor still hands the canvas the order it needs
    assert bar.f1_range() == pytest.approx((35.0, 5.0))


def test_the_zoom_toggle_reports_its_state(qtbot):
    from helspin.ui.adjustment_bar import AdjustmentBar

    bar = AdjustmentBar()
    qtbot.addWidget(bar)
    seen = []
    bar.zoomModeChanged.connect(seen.append)
    bar._zoom_toggle.setChecked(True)
    bar._zoom_toggle.setChecked(False)
    assert seen == [True, False]


class _Wheel:
    def __init__(self, x, y=None, step=1, axes=None):
        self.xdata, self.ydata, self.step = x, y, step
        self.inaxes = axes
        self.button = "up" if step > 0 else "down"


def test_the_wheel_zooms_about_the_cursor_when_zoom_is_on(qtbot):
    """Zooming about the plot centre walks the peak of interest off the edge
    and turns one gesture into three; the point under the cursor stays put."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_ppm_range(10.0, 0.0)
    canvas.set_zoom_mode(True)

    canvas._on_scroll(_Wheel(x=2.0, step=1, axes=canvas._axes))
    left, right = canvas.ppm_range()

    assert right < 2.0 < left, "the cursor position must stay inside the view"
    assert (left - right) < 10.0, "scrolling up must magnify"
    # the cursor keeps its relative place in the window
    assert (left - 2.0) / (left - right) == pytest.approx(0.8, abs=0.01)


def test_the_wheel_still_scales_a_trace_when_zoom_is_off(qtbot):
    """The toggle must not take the old behaviour away."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_ppm_range(10.0, 0.0)
    canvas.set_zoom_mode(False)
    before = canvas.ppm_range()
    canvas._on_scroll(_Wheel(x=2.0, step=1, axes=canvas._axes))
    assert canvas.ppm_range() == before, "zoom off: the view must not move"


def test_a_zoom_announces_itself_so_the_range_boxes_can_follow(qtbot):
    """A zoom done on the plot has to reach the range boxes, or they show a
    range that is no longer displayed and the next Apply jumps the view back.
    """
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_ppm_range(10.0, 0.0)
    canvas.set_zoom_mode(True)
    with qtbot.waitSignal(canvas.viewChanged, timeout=1000):
        canvas._on_scroll(_Wheel(x=5.0, step=1, axes=canvas._axes))


def test_opacity_is_clamped_to_something_visible(qtbot):
    """Zero opacity is an invisible spectrum with no clue why."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_trace_opacity(0.0)
    assert canvas.trace_opacity() >= 0.05
    canvas.set_trace_opacity(5.0)
    assert canvas.trace_opacity() == pytest.approx(1.0)
    canvas.set_trace_opacity(0.4)
    assert canvas.trace_opacity() == pytest.approx(0.4)


def test_preferences_round_trips_opacity(qtbot):
    from helspin.ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog(opacity=0.35)
    qtbot.addWidget(dialog)
    assert dialog.opacity() == pytest.approx(0.35)


# --- 0.4.11 ------------------------------------------------------------------


def test_full_range_clears_the_2d_axes_too(qtbot):
    """Full did nothing in 2D. It reset only _ppm_range, which nothing reads
    in 2D mode -- the view there is driven by _f1_range and _f2_range."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_f1_range(35.0, 5.0)
    canvas.set_f2_range(4.0, -1.0)
    canvas.set_ppm_range(10.0, 0.0)

    canvas.full_range()

    assert canvas.f1_range() is None
    assert canvas.f2_range() is None
    assert canvas.ppm_range() is None


def test_quitting_takes_the_detached_explorer_with_it(qtbot, monkeypatch):
    """Qt exits when the LAST window closes, so a detached explorer kept the
    process alive with no main window left to quit from."""
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)
    window._detach_browser()
    assert window._explorer_window is not None
    explorer = window._explorer_window

    window.close()
    assert window._explorer_window is None
    assert not explorer.isVisible()


def test_the_app_icon_ships_and_loads(qtbot):
    """A missing resource must cost the icon, not the application -- but it
    should not be missing, so this checks the file is really packaged."""
    from pathlib import Path

    import helspin
    from helspin.__main__ import app_icon

    packaged = Path(helspin.__file__).parent / "resources" / "icon.png"
    assert packaged.is_file(), "icon.png must ship inside the package"
    assert not app_icon().isNull()


# --- 0.4.12: 2D cursor readout and undo/redo --------------------------------


def test_the_2d_readout_names_both_dimensions_in_ppm(qtbot, monkeypatch):
    """In 2D both numbers are chemical shifts. The second was printed bare and
    formatted as an intensity, so the F1 position was on screen the whole time
    without saying what it was or carrying a unit."""
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)

    monkeypatch.setattr(window._canvas, "mode", lambda: "2D")
    window._on_cursor_moved(2.9813, 18.75)
    text = window._cursor_label.text()
    # "F2" and "F1" already say these are chemical shifts, and the pair was
    # the longest thing in the status bar, so the unit is dropped in 2D.
    assert "F1" in text and "F2" in text
    assert "2.98" in text and "18.75" in text
    assert "2.9813" not in text, "two decimals by default, not four"

    monkeypatch.setattr(window._canvas, "mode", lambda: "1D")
    window._on_cursor_moved(7.26, 1.2e9)
    # 1D keeps the unit: the second number is an intensity, so without it on
    # the first the pair would be ambiguous.
    assert window._cursor_label.text().count("ppm") == 1


def test_undo_restores_a_removed_spectrum_without_re_reading_it(qtbot):
    """The removed Trace object stays alive in the undo stack, so coming back
    costs nothing -- no re-read, which on a share is the difference between
    instant and a second per spectrum."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas(reader=_CountingReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(_mime_1d("/d/1", "/d/2"))
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    reads_before = canvas._reader.reads

    canvas.remove_trace("/d/1")
    assert len(canvas.traces) == 1
    assert canvas.can_undo()

    assert canvas.undo()
    assert [str(t.path) for t in canvas.traces] == ["/d/1", "/d/2"]
    assert canvas._reader.reads == reads_before, "undo must not re-read files"


def test_undo_and_redo_walk_the_view_back_and_forward(qtbot):
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_ppm_range(10.0, 0.0)
    canvas._last_undo_key = None      # as a pause between two deliberate edits
    canvas.set_ppm_range(8.0, 2.0)

    assert canvas.undo()
    assert canvas.ppm_range() == (10.0, 0.0)
    assert canvas.redo()
    assert canvas.ppm_range() == (8.0, 2.0)


def test_a_new_action_discards_the_redo_path(qtbot):
    """Once history branches, the old forward path describes nothing
    reachable; offering Redo for it would restore a state never returned to."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas.set_ppm_range(10.0, 0.0)
    canvas.undo()
    assert canvas.can_redo()
    canvas.set_ppm_range(5.0, 1.0)
    assert not canvas.can_redo()


def test_undo_is_bounded(qtbot):
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    for i in range(canvas.UNDO_DEPTH + 25):
        canvas.set_ppm_range(10.0, float(i % 5))
    assert len(canvas._undo) <= canvas.UNDO_DEPTH


def test_undo_with_empty_history_is_a_no_op(qtbot):
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    assert canvas.undo() is False
    assert canvas.redo() is False


class _CountingReader:
    def __init__(self):
        self.reads = 0

    def read_1d(self, path, procno=1):
        self.reads += 1
        return _FakeSpec()

    def read_auto(self, path, procno=1):
        self.reads += 1
        return 1, _FakeSpec()


class _FakeSpec:
    def __init__(self, n=64):
        import numpy as _np

        self.real = _np.linspace(0.0, 10.0, n)
        self.ns, self.rg = 1, 1.0

        class _Axis:
            size = n

            def ppm_scale(self_inner):
                return _np.linspace(10.0, 0.0, n)

        self.axis = _Axis()


def _mime_1d(*paths):
    from PySide6.QtCore import QMimeData

    from helspin.ui.dataset_model import MIME_DATASET

    mime = QMimeData()
    mime.setData(MIME_DATASET, json.dumps(
        [{"path": p, "dimensionality": 1, "label": p} for p in paths]
    ).encode("utf-8"))
    return mime


def _crosshair_texts(canvas):
    return [a.get_text() for a in (canvas._crosshair or []) if hasattr(a, "get_text")]


class _Move:
    def __init__(self, x, y, axes):
        self.xdata, self.ydata, self.inaxes = x, y, axes


def _one_trace_canvas():
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    n = 256
    canvas._traces.append(
        Trace(path=Path("/d/1"), label="t", ppm=np.linspace(10, 0, n),
              intensity=np.abs(np.sin(np.linspace(0, 6, n))) * 1e6,
              color="#000000")
    )
    canvas._y_limits = None
    canvas._redraw()
    canvas._canvas.draw()
    return canvas


def test_the_crosshair_labels_both_axes(qtbot):
    """The vertical line had carried its value since the crosshair existed and
    the horizontal one had not, so half the crosshair was decoration."""
    canvas = _one_trace_canvas()
    qtbot.addWidget(canvas)
    canvas._on_mouse_move(_Move(5.0, 5e5, canvas._axes))
    texts = _crosshair_texts(canvas)
    assert len(texts) == 2, texts
    # No unit on the plot: the axis directly beneath already says ppm, and the
    # crosshair readouts are where space is tightest.
    assert "5.00" in texts[0]


def test_in_2d_both_crosshair_labels_are_chemical_shifts(qtbot, monkeypatch):
    canvas = _one_trace_canvas()
    qtbot.addWidget(canvas)
    monkeypatch.setattr(canvas, "mode", lambda: "2D")
    canvas._on_mouse_move(_Move(0.5963, 17.9706, canvas._axes))
    texts = _crosshair_texts(canvas)
    assert texts == ["0.60", "17.97"], texts


def test_the_y_crosshair_label_is_not_clipped_off_the_figure(qtbot):
    """It is drawn OUTSIDE the axes, and tight_layout cannot know about an
    artist added after it runs -- so without a reserved right margin the label
    was positioned correctly and then cut off at the figure edge."""
    canvas = _one_trace_canvas()
    qtbot.addWidget(canvas)
    canvas._on_mouse_move(_Move(5.0, 5e5, canvas._axes))
    canvas._canvas.draw()

    renderer = canvas._canvas.get_renderer()
    width = canvas._figure.bbox.width
    for artist in canvas._crosshair:
        if hasattr(artist, "get_text"):
            assert artist.get_window_extent(renderer).x1 <= width


def test_leaving_the_axes_removes_both_labels(qtbot):
    canvas = _one_trace_canvas()
    qtbot.addWidget(canvas)
    canvas._on_mouse_move(_Move(5.0, 5e5, canvas._axes))
    assert canvas._crosshair is not None
    canvas._on_mouse_move(_Move(None, None, None))
    assert canvas._crosshair is None


def test_the_taskbar_identity_call_is_harmless_off_windows(monkeypatch):
    """Windows attributes the taskbar button to the host interpreter unless an
    AppUserModelID is set, so the title-bar icon is right while the taskbar
    shows a generic Python one. The call must be a no-op elsewhere and must
    never stop the app starting."""
    from helspin.__main__ import _claim_windows_taskbar_identity

    monkeypatch.setattr("helspin.__main__.sys.platform", "linux")
    _claim_windows_taskbar_identity()          # must not raise

    monkeypatch.setattr("helspin.__main__.sys.platform", "win32")
    _claim_windows_taskbar_identity()          # no ctypes.windll here either


def test_typing_a_value_is_one_undo_step_not_five(qtbot):
    """The reported "undo does not work".

    A spin box emits valueChanged on every keystroke, so typing "21.114" fired
    five changes and pushed five snapshots. One undo then moved the scale from
    21.114 to 21.11 -- indistinguishable from undo doing nothing.
    """
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas._traces.append(
        Trace(path=Path("/d/1"), label="t", ppm=np.linspace(10, 0, 64),
              intensity=np.linspace(0, 10, 64), color="#000000")
    )
    canvas._redraw()

    for value in (2.0, 21.0, 21.1, 21.11, 21.114):     # keystroke by keystroke
        canvas.set_y_scale(0, value)
    assert len(canvas._undo) == 1, "a burst of edits is one step"

    canvas.undo()
    assert canvas.traces[0].y_scale == pytest.approx(1.0), (
        "one undo must revert the whole edit, not one keystroke of it"
    )


def test_opening_a_session_starts_a_fresh_history(qtbot):
    """A second Ctrl+Z after opening a file swapped the whole restored session
    out for whatever had been on the canvas beforehand."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas(reader=_CountingReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(_mime_1d("/d/1", "/d/2"))
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)
    state = json.loads(json.dumps(canvas.session_state()))

    reopened = SpectrumCanvas(reader=_CountingReader())
    qtbot.addWidget(reopened)
    reopened.handle_mime_data(_mime_1d("/d/9"))
    qtbot.waitUntil(lambda: len(reopened.traces) == 1, timeout=3000)
    reopened.restore_session(state)

    assert not reopened.can_undo(), "an opened session has no history behind it"
    reopened.undo()
    assert len(reopened.traces) == 2, "the restored session must survive"


# --- licence and the legend toggle -------------------------------------------


def test_the_licence_text_ships_with_the_package():
    """A packaged build may have no visible LICENSE file, so the text has to
    travel inside the package -- both for the user's sake and because Qt's
    LGPL requires its notices to accompany the binary."""
    from helspin.ui.licence_dialog import licence_text, notice_text

    licence = licence_text()
    assert "commercial" in licence.lower()
    assert "research" in licence.lower()
    assert licence != "" and "could not be found" not in licence

    notice = notice_text()
    assert "LGPL" in notice, "Qt's licence must be named"
    assert "nmrglue" in notice


def test_the_licence_dialog_opens_without_a_licence_file(qtbot, monkeypatch):
    """A missing file must not stop the dialog opening: showing a summary is a
    better outcome than an exception, and the summary still states the one
    condition that matters."""
    from helspin.ui import licence_dialog

    monkeypatch.setattr(
        licence_dialog, "_resource", lambda name: Path("/nowhere/at/all")
    )
    assert "Commercial use requires" in licence_dialog.licence_text()

    dialog = licence_dialog.LicenceDialog()
    qtbot.addWidget(dialog)
    assert dialog._tabs.count() == 2


def test_labels_can_be_switched_off(qtbot):
    """An overlay of unlabelled traces is unreadable, so labels are on by
    default -- but a figure whose caption names the spectra does not want them,
    and on a crowded 2D map they sit over the data."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas(reader=_CountingReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(_mime_1d("/d/1", "/d/2"))
    qtbot.waitUntil(lambda: len(canvas.traces) == 2, timeout=3000)

    assert canvas.labels_visible()
    labelled = [t.get_text() for t in canvas._axes.texts]
    assert any("/d/1" in t for t in labelled)

    canvas.set_labels_visible(False)
    assert [t.get_text() for t in canvas._axes.texts] == []

    canvas.set_labels_visible(True)
    assert [t.get_text() for t in canvas._axes.texts] == labelled


def test_the_labels_toolbar_action_drives_the_canvas(qtbot, monkeypatch):
    from helspin.__main__ import MainWindow

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._labels_action.isChecked()
    window._labels_action.setChecked(False)
    window._toggle_labels()
    assert not window._canvas.labels_visible()


def test_the_grid_covers_both_axes_in_1d_overlay(qtbot):
    """Only the vertical rules were drawn; the horizontal ones were switched
    off outright, so "Grid" gave half a grid."""
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas._traces.append(
        Trace(path=Path("/d/1"), label="t", ppm=np.linspace(10, 0, 64),
              intensity=np.linspace(0, 10, 64), color="#000000")
    )
    canvas.set_grid_visible(True)

    assert any(line.get_visible() for line in canvas._axes.get_xgridlines())
    assert any(line.get_visible() for line in canvas._axes.get_ygridlines())


def test_no_horizontal_grid_when_stacked(qtbot):
    """Stacked gives every spectrum its own offset and scale, so a y value
    means something different for each one. Ruling lines across them would
    imply a shared scale that does not exist."""
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas._traces.append(
        Trace(path=Path("/d/1"), label="t", ppm=np.linspace(10, 0, 64),
              intensity=np.linspace(0, 10, 64), color="#000000")
    )
    canvas.set_grid_visible(True)
    canvas.set_arrangement(canvas.ARRANGEMENT_STACKED)

    assert any(line.get_visible() for line in canvas._axes.get_xgridlines())
    assert not any(line.get_visible() for line in canvas._axes.get_ygridlines())


def test_the_grid_reaches_2d_at_all(qtbot):
    """The 2D drawing had no grid code, so the toolbar toggle silently did
    nothing there -- the button was on and no grid appeared."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas(reader=_Reader2D())
    qtbot.addWidget(canvas)
    canvas.set_grid_visible(True)
    canvas.set_arrangement(canvas.ARRANGEMENT_OVERLAY)
    canvas.handle_mime_data(_mime_2d("/d/a"))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)

    axes = canvas._figure.axes[0]
    assert any(line.get_visible() for line in axes.get_xgridlines())
    assert any(line.get_visible() for line in axes.get_ygridlines()), (
        "in 2D both axes are chemical shifts; both deserve a grid"
    )


def test_grid_y_spacing_is_configurable(qtbot):
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    canvas._traces.append(
        Trace(path=Path("/d/1"), label="t", ppm=np.linspace(10, 0, 64),
              intensity=np.linspace(0, 10, 64), color="#000000")
    )
    canvas.set_grid_visible(True)
    canvas.set_grid_spacing_y(2.0)
    spacing = np.diff(canvas._axes.get_yticks())
    assert spacing.size and spacing[0] == pytest.approx(2.0)


class _Spec2D:
    def __init__(self, n=16):
        import numpy as np

        self.real = np.outer(np.linspace(0, 1, n), np.linspace(0, 1, n)) * 1e6

        class _Axis:
            def __init__(self, hi, lo):
                self._hi, self._lo, self.size = hi, lo, n

            def ppm_scale(self):
                import numpy as np

                return np.linspace(self._hi, self._lo, n)

        self.axis_f1 = _Axis(35.0, 5.0)
        self.axis_f2 = _Axis(4.0, -1.0)


class _Reader2D:
    def read_2d(self, path, procno=1):
        return _Spec2D()

    def read_auto(self, path, procno=1):
        return 2, _Spec2D()


def _mime_2d(*paths):
    from PySide6.QtCore import QMimeData

    from helspin.ui.dataset_model import MIME_DATASET

    mime = QMimeData()
    mime.setData(MIME_DATASET, json.dumps(
        [{"path": p, "dimensionality": 2, "label": p} for p in paths]
    ).encode("utf-8"))
    return mime


def test_grid_is_off_and_labels_are_on_by_default(qtbot, monkeypatch):
    """A grid is a reading aid the user asks for; spectrum names are needed
    the moment there is more than one trace. Pinned so neither default drifts."""
    from helspin.__main__ import MainWindow
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    assert canvas.labels_visible() is True
    assert canvas._show_grid is False

    monkeypatch.setattr("helspin.__main__.load_data_roots", lambda: [])
    window = MainWindow()
    qtbot.addWidget(window)
    assert window._labels_action.isChecked() is True
    assert window._grid_action.isChecked() is False


def test_opacity_applies_to_the_spectra_not_the_labels(qtbot):
    """Opacity exists so overlapping traces and contours can be read through
    each other. Fading the names would only make them hard to read."""
    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas(reader=_CountingReader())
    qtbot.addWidget(canvas)
    canvas.handle_mime_data(_mime_1d("/d/1"))
    qtbot.waitUntil(lambda: len(canvas.traces) == 1, timeout=3000)

    canvas.set_trace_opacity(0.4)
    line = canvas._axes.lines[0]
    assert line.get_alpha() == pytest.approx(0.4)

    labels = [t for t in canvas._axes.texts if "/d/1" in t.get_text()]
    assert labels, "the spectrum should still be named"
    assert labels[0].get_alpha() in (None, 1.0), "names stay fully opaque"


def test_ten_slots_and_ten_default_colours(qtbot):
    """Expanded from eight to ten so up to ten overlaid spectra each get their
    own colour and row in Preferences."""
    from helspin.ui.preferences_dialog import (
        DEFAULT_COLORS,
        SLOT_COUNT,
        PreferencesDialog,
    )

    assert SLOT_COUNT == 10
    assert len(DEFAULT_COLORS) == 10

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    assert len(dialog.styles()) == 10


def test_the_default_line_style_is_solid_for_every_slot(qtbot):
    """A fresh configuration is all solid; only the greyscale palette, chosen
    deliberately, introduces dashes."""
    from helspin.ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    assert all(s["style"] == "-" for s in dialog.styles())


def test_reset_restores_solid_across_all_ten_slots(qtbot):
    """After the greyscale palette has set dashes, Reset must put every slot
    back to solid, not just the first eight."""
    from helspin.ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    dialog._palette_box.setCurrentText("Print (greyscale)")
    dialog._apply_palette()
    assert len({s["style"] for s in dialog.styles()}) > 1  # dashes now present

    dialog._reset()
    assert all(s["style"] == "-" for s in dialog.styles())


def test_rainbow_and_hot_cold_palettes_exist(qtbot):
    """Two ordered ramps for series where colour should track order: Rainbow
    for a plain progression, Hot-cold for one with a meaningful centre."""
    from helspin.domain.project import palette_colours, palette_names

    assert "Rainbow" in palette_names()
    assert "Hot-cold" in palette_names()
    # Hot-cold runs blue -> pale -> red; its ends must be far apart.
    hot_cold = palette_colours("Hot-cold")
    assert hot_cold[0].upper().startswith("#2") or hot_cold[0].upper().startswith("#1")
    assert hot_cold[-1].upper().startswith("#6") or hot_cold[-1].upper().startswith("#B")


def test_nudging_a_stacked_offset_keeps_the_trace_on_screen(qtbot):
    """The reported vanishing spectrum: nudging Y offset in stacked mode moved
    the trace but not the frame, so it slid off the canvas. The frame now
    follows the offset."""
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    for i in range(3):
        canvas._traces.append(Trace(
            path=Path(f"/d/{i}"), label=f"t{i}",
            ppm=np.linspace(1.66, 1.04, 512),
            intensity=np.abs(np.sin(np.linspace(0, 6, 512))) * 2.1e10,
            color="#000000",
        ))
    canvas._y_limits = None
    canvas.set_arrangement(canvas.ARRANGEMENT_STACKED)

    def fraction_on_screen(index):
        low, high = canvas._axes.get_ylim()
        data = np.asarray(canvas._axes.lines[index].get_ydata())
        if data.max() < low or data.min() > high:
            return 0.0
        return (min(data.max(), high) - max(data.min(), low)) / (high - low)

    span = 2.1e10
    canvas.set_y_offset(0, span * 0.06)
    assert fraction_on_screen(0) > 0.1, "a nudged-up trace must stay visible"
    canvas.set_y_offset(0, -span * 0.06)
    assert fraction_on_screen(0) > 0.1, "a nudged-down trace must stay visible"


def test_hot_cold_palette_has_no_invisible_near_white(qtbot):
    """The pale centre of the original ramp vanished on the white plot; the
    middle spectra of a series simply disappeared."""
    from helspin.domain.project import palette_colours

    for hex_colour in palette_colours("Hot-cold"):
        r = int(hex_colour[1:3], 16)
        g = int(hex_colour[3:5], 16)
        b = int(hex_colour[5:7], 16)
        assert not (r > 225 and g > 225 and b > 225), (
            f"{hex_colour} is near-white and invisible on a white background"
        )


def test_greyscale_palette_distinguishes_lines_by_dash(qtbot):
    """In black and white, hue conveys nothing; the dash pattern is the only
    thing telling two traces apart."""
    from helspin.domain.project import palette_styles

    styles = palette_styles("Print (greyscale)")
    assert styles is not None
    assert len(styles) == 10
    assert len(set(styles)) >= 3, "several distinct dash patterns are needed"
    assert styles[0] != styles[1], "adjacent traces must differ"


def test_offset_is_pure_translation_in_stacked_mode(qtbot):
    """Offset moves a spectrum up or down and changes NOTHING else. An earlier
    fix cleared the frame on every offset change, which recomputed the view
    scale so every spectrum appeared to resize when only one was moved. Offset
    is a translation, not a zoom."""
    from pathlib import Path

    import numpy as np

    from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace

    canvas = SpectrumCanvas()
    qtbot.addWidget(canvas)
    for i in range(3):
        canvas._traces.append(Trace(
            path=Path(f"/d/{i}"), label=f"t{i}",
            ppm=np.linspace(12, -2, 512),
            intensity=np.abs(np.sin(np.linspace(0, 6, 512))) * 2.1e10,
            color="#000000",
        ))
    canvas._y_limits = None
    canvas.set_arrangement(canvas.ARRANGEMENT_STACKED)

    def peak_height_pct(index):
        low, high = canvas._axes.get_ylim()
        data = np.asarray(canvas._axes.lines[index].get_ydata())
        return (data.max() - data.min()) / (high - low) * 100

    def baseline_pct(index):
        low, high = canvas._axes.get_ylim()
        data = np.asarray(canvas._axes.lines[index].get_ydata())
        return (data.min() - low) / (high - low) * 100

    heights_before = [peak_height_pct(i) for i in range(3)]
    frame_before = canvas._axes.get_ylim()
    others_before = baseline_pct(1), baseline_pct(2)

    canvas.set_y_offset(0, canvas._traces[0].y_offset + 2.1e10 * 0.1)

    # every spectrum keeps its apparent height -- no rescaling
    for i in range(3):
        assert peak_height_pct(i) == pytest.approx(heights_before[i], abs=0.5), (
            "moving one offset must not resize any spectrum"
        )
    # the frame does not move
    assert canvas._axes.get_ylim() == pytest.approx(frame_before, rel=1e-6)
    # the other two spectra do not move
    assert baseline_pct(1) == pytest.approx(others_before[0], abs=0.5)
    assert baseline_pct(2) == pytest.approx(others_before[1], abs=0.5)
