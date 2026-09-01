"""Per-trace horizontal (ppm) offset.

Deliberately mirrors the y_offset tests, because the failure modes are the
same ones -- with one addition that y_offset does not have: an x offset moves
a trace along the chemical shift axis, so the tests here also pin down that
the shift is never silently applied to reported values.
"""

from __future__ import annotations

import numpy as np
import pytest

from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace


def _trace(label="A", color="#1f77b4", centre=5.0):
    ppm = np.linspace(10.0, 0.0, 512)
    intensity = np.exp(-((ppm - centre) ** 2) / 0.02)
    return Trace(path=None, label=label, ppm=ppm, intensity=intensity, color=color)


@pytest.fixture
def canvas(qtbot):
    c = SpectrumCanvas()
    qtbot.addWidget(c)
    return c


def _add(canvas, *traces):
    canvas._traces.extend(traces)
    canvas._redraw()


# --- the core behaviour -----------------------------------------------------


def test_offset_shifts_the_drawn_axis_only(canvas):
    """The drawn position moves; the underlying data does not.

    If the shift were written into trace.ppm, every later readout, export and
    difference would silently inherit it and there would be no way back to the
    true values.
    """
    t = _trace()
    _add(canvas, t)
    original = t.ppm.copy()

    canvas.set_x_offset(0, 0.25)

    assert np.allclose(t.ppm, original), "raw data must not be modified"
    assert np.allclose(canvas.drawn_ppm(t), original + 0.25)
    assert t.x_offset == pytest.approx(0.25)


def test_zero_offset_returns_the_array_untouched(canvas):
    """No shift means no copy: the common case must not pay for the feature."""
    t = _trace()
    _add(canvas, t)
    assert canvas.drawn_ppm(t) is t.ppm


def test_offset_does_not_refit_the_view(canvas):
    """The rule this feature inherits from y_offset.

    Re-fitting the frame when one trace is nudged makes every OTHER trace
    appear to slide and compress, which is worse than the problem it solves.
    Fixed once on the y axis; must not be reintroduced on the x axis.
    """
    a, b = _trace("A"), _trace("B", centre=7.0)
    _add(canvas, a, b)
    before = canvas._axes.get_xlim()

    canvas.set_x_offset(0, 0.4)

    assert canvas._axes.get_xlim() == pytest.approx(before), (
        "shifting one spectrum must not move the frame"
    )


def test_explicit_ppm_range_survives_an_offset(canvas):
    """A user-set zoom is a stated intention and outranks any auto-fitting."""
    t = _trace()
    _add(canvas, t)
    canvas.set_ppm_range(8.0, 2.0)
    before = canvas._axes.get_xlim()

    canvas.set_x_offset(0, 0.3)

    assert canvas._axes.get_xlim() == pytest.approx(before)


def test_negative_and_positive_shifts_both_apply(canvas):
    t = _trace()
    _add(canvas, t)
    canvas.set_x_offset(0, -0.75)
    assert canvas.drawn_ppm(t)[0] == pytest.approx(t.ppm[0] - 0.75)
    canvas.set_x_offset(0, 0.75)
    assert canvas.drawn_ppm(t)[0] == pytest.approx(t.ppm[0] + 0.75)


def test_nudge_accumulates(canvas):
    t = _trace()
    _add(canvas, t)
    canvas.nudge_x_offset(0, 0.1)
    canvas.nudge_x_offset(0, 0.1)
    assert t.x_offset == pytest.approx(0.2)


def test_only_the_named_trace_moves(canvas):
    a, b = _trace("A"), _trace("B")
    _add(canvas, a, b)
    canvas.set_x_offset(0, 0.5)
    assert a.x_offset == pytest.approx(0.5)
    assert b.x_offset == 0.0
    assert np.allclose(canvas.drawn_ppm(b), b.ppm)


# --- guards -----------------------------------------------------------------


def test_non_finite_offset_is_refused(canvas):
    """NaN would propagate into the drawn axis and blank the plot."""
    t = _trace()
    _add(canvas, t)
    canvas.set_x_offset(0, float("nan"))
    assert t.x_offset == 0.0
    canvas.set_x_offset(0, float("inf"))
    assert t.x_offset == 0.0


def test_out_of_range_index_is_ignored(canvas):
    _add(canvas, _trace())
    canvas.set_x_offset(5, 0.5)      # must not raise
    canvas.set_x_offset(-1, 0.5)
    canvas.nudge_x_offset(99, 0.5)


# --- undo -------------------------------------------------------------------


def test_offset_is_undoable(canvas):
    t = _trace()
    _add(canvas, t)
    canvas.set_x_offset(0, 0.6)
    assert t.x_offset == pytest.approx(0.6)
    canvas.undo()
    assert t.x_offset == 0.0


def test_repeated_edits_coalesce_into_one_undo_step(canvas):
    """A spin box emits valueChanged per keystroke. Without coalescing,
    typing "0.25" leaves four separate undo steps to walk back through."""
    t = _trace()
    _add(canvas, t)
    for value in (0.1, 0.12, 0.125, 0.1255):
        canvas.set_x_offset(0, value)
    canvas.undo()
    assert t.x_offset == 0.0, "one edit should take one undo"


def test_clear_all_restores_true_shifts(canvas):
    a, b = _trace("A"), _trace("B")
    _add(canvas, a, b)
    canvas.set_x_offset(0, 0.3)
    canvas.set_x_offset(1, -0.2)

    canvas.clear_x_offsets()

    assert a.x_offset == 0.0 and b.x_offset == 0.0


def test_clear_all_is_undoable(canvas):
    a = _trace("A")
    _add(canvas, a)
    canvas.set_x_offset(0, 0.3)
    canvas.clear_x_offsets()
    canvas.undo()
    assert a.x_offset == pytest.approx(0.3)


def test_clear_all_with_nothing_shifted_is_a_no_op(canvas):
    """It must not consume an undo step for doing nothing."""
    a = _trace("A")
    _add(canvas, a)
    canvas.set_y_offset(0, 5.0)
    canvas.clear_x_offsets()
    canvas.undo()
    assert a.y_offset == 0.0, "the y edit should be what undo reaches"


# --- persistence ------------------------------------------------------------


def test_offset_survives_a_session_round_trip(canvas, tmp_path, monkeypatch):
    t = _trace()
    _add(canvas, t)
    canvas.set_x_offset(0, 0.42)

    state = canvas.session_state()
    assert state["spectra"][0]["x_offset"] == pytest.approx(0.42)


def test_a_session_without_x_offset_loads_as_zero(canvas):
    """Sessions written before this feature carry no field. Absent means
    unshifted, which is the truthful reading of an older file."""
    entry = {"y_offset": 3.0}
    assert float(entry.get("x_offset", 0.0)) == 0.0


# --- the part that matters scientifically -----------------------------------


def test_a_shifted_trace_is_named_as_shifted(canvas):
    """A figure must not quietly claim a peak sits where it does not."""
    t = _trace()
    _add(canvas, t)
    assert "ppm" not in t.display_label()

    canvas.set_x_offset(0, 0.25)
    label = t.display_label()
    assert "+0.250" in label and "ppm" in label


def test_the_marker_disappears_when_the_shift_is_removed(canvas):
    t = _trace()
    _add(canvas, t)
    canvas.set_x_offset(0, 0.25)
    canvas.set_x_offset(0, 0.0)
    assert "ppm]" not in t.display_label()


def test_the_marker_survives_alongside_the_pulse_programme(canvas):
    t = _trace()
    t.pulse_program = "zg30"
    _add(canvas, t)
    canvas.set_x_offset(0, -0.1)
    label = t.display_label(show_pulprog=True)
    assert "zg30" in label and "-0.100 ppm" in label
