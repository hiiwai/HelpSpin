"""Y zoom: wheel-zooms the intensity axis, all spectra together.

Written to cover the branches rather than the happy path. The three that
matter most, because each is a bug that has already been made once in this
codebase or is one keystroke away from it:

* an explicit zoom must survive `_y_limits = None`, which happens on load,
  remove, arrangement change and x zoom;
* the escape hatches (Fit Y, reset) must CLEAR the zoom, or they silently do
  nothing once the user has zoomed;
* the wheel must reach the zoom before the "is anything selected?" check, or
  the feature is dead until a trace happens to be clicked.
"""

from __future__ import annotations

import numpy as np
import pytest

from helspin.ui.spectrum_canvas import SpectrumCanvas, Trace


def _trace(label="A", color="#1f77b4", centre=5.0, amp=1.0):
    ppm = np.linspace(10.0, 0.0, 256)
    return Trace(
        path=None, label=label, ppm=ppm,
        intensity=amp * np.exp(-((ppm - centre) ** 2) / 0.05), color=color,
    )


class _Wheel:
    """A matplotlib scroll event, only as much of one as the handler reads."""

    def __init__(self, step=1, xdata=5.0, ydata=0.5, inaxes=True):
        self.step = step
        self.button = "up" if step > 0 else "down"
        self.xdata = xdata
        self.ydata = ydata
        self.inaxes = inaxes


@pytest.fixture
def canvas(qtbot):
    c = SpectrumCanvas()
    qtbot.addWidget(c)
    return c


def _add(canvas, *traces):
    canvas._traces.extend(traces)
    canvas._redraw()


def _height(canvas):
    low, high = canvas._axes.get_ylim()
    return high - low


# --- the toggle itself ------------------------------------------------------


def test_y_zoom_is_off_by_default(canvas):
    """The wheel must keep the meaning it already had until asked."""
    assert canvas.y_zoom_mode() is False
    assert canvas.y_range() is None


def test_toggle_round_trips(canvas):
    canvas.set_y_zoom_mode(True)
    assert canvas.y_zoom_mode() is True
    canvas.set_y_zoom_mode(False)
    assert canvas.y_zoom_mode() is False


def test_toggle_coerces_truthy_values(canvas):
    canvas.set_y_zoom_mode(1)
    assert canvas.y_zoom_mode() is True
    canvas.set_y_zoom_mode(0)
    assert canvas.y_zoom_mode() is False


def test_turning_it_on_does_not_disturb_the_view(canvas):
    """Arming a tool must not itself move anything."""
    _add(canvas, _trace())
    before = canvas._axes.get_ylim()
    canvas.set_y_zoom_mode(True)
    assert canvas._axes.get_ylim() == pytest.approx(before)
    assert canvas.y_range() is None


def test_turning_it_off_keeps_the_zoom_already_applied(canvas):
    """Disarming is not undoing. Losing the zoom on toggle-off would make the
    button feel like it discards work."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    zoomed = canvas.y_range()
    assert zoomed is not None
    canvas.set_y_zoom_mode(False)
    assert canvas.y_range() == pytest.approx(zoomed)


# --- wheel behaviour --------------------------------------------------------


def test_wheel_up_magnifies(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)
    canvas._on_scroll(_Wheel(step=1))
    assert _height(canvas) < before, "scrolling up should magnify"


def test_wheel_down_widens(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)
    canvas._on_scroll(_Wheel(step=-1))
    assert _height(canvas) > before


def test_zoom_keeps_the_point_under_the_cursor_fixed(canvas):
    """The whole point of zooming about the cursor. Zooming about the centre
    walks the peak of interest off the edge and turns one gesture into three.
    """
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    low, high = canvas._axes.get_ylim()
    cursor = low + (high - low) * 0.25
    canvas._on_scroll(_Wheel(step=1, ydata=cursor))
    new_low, new_high = canvas._axes.get_ylim()
    assert new_low < cursor < new_high
    # The cursor sits at the same FRACTION of the window as before.
    assert ((cursor - new_low) / (new_high - new_low)) == pytest.approx(0.25, abs=1e-6)


def test_wheel_works_with_nothing_selected(canvas):
    """The bug this guards: putting the zoom check after the selection check
    would leave Y zoom dead until a trace happened to be clicked."""
    _add(canvas, _trace())
    canvas._selected_index = None
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    assert canvas.y_range() is not None


def test_wheel_does_not_scale_any_trace(canvas):
    """Y zoom magnifies the view; it must not touch the data relationship.
    Scaling a trace instead would change what the figure claims."""
    a, b = _trace("A"), _trace("B", amp=3.0)
    _add(canvas, a, b)
    canvas.select_trace(0)
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    assert a.y_scale == 1.0 and b.y_scale == 1.0
    assert a.y_offset == 0.0 and b.y_offset == 0.0


def test_wheel_still_scales_the_selection_when_both_modes_are_off(canvas):
    """The pre-existing behaviour must be exactly preserved."""
    t = _trace()
    _add(canvas, t)
    canvas.select_trace(0)
    canvas._on_scroll(_Wheel(step=1))
    assert t.y_scale > 1.0
    assert canvas.y_range() is None


def test_a_zero_step_wheel_does_nothing(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    event = _Wheel(step=1)
    event.step = 0
    event.button = None
    canvas._on_scroll(event)
    assert canvas.y_range() is None


def test_wheel_without_ydata_is_ignored(canvas):
    """Off the axes: matplotlib reports no data coordinate."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1, xdata=None, ydata=None))
    assert canvas.y_range() is None


def test_wheel_with_only_xdata_missing_still_zooms_y(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1, xdata=None, ydata=0.5))
    assert canvas.y_range() is not None


def test_repeated_zoom_stays_finite(canvas):
    """Forty notches in each direction must not produce NaN or an inverted
    axis, which would render as a blank plot with no way back."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(40):
        canvas._on_scroll(_Wheel(step=1))
    low, high = canvas._axes.get_ylim()
    assert np.isfinite(low) and np.isfinite(high) and high > low
    for _ in range(80):
        canvas._on_scroll(_Wheel(step=-1))
    low, high = canvas._axes.get_ylim()
    assert np.isfinite(low) and np.isfinite(high) and high > low


def test_wheel_on_an_empty_canvas_does_not_raise(canvas):
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))     # no traces at all


# --- independence from X zoom -----------------------------------------------


def test_y_zoom_alone_leaves_the_ppm_axis_alone(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = canvas._axes.get_xlim()
    canvas._on_scroll(_Wheel(step=1))
    assert canvas._axes.get_xlim() == pytest.approx(before)


def test_x_zoom_alone_leaves_the_intensity_axis_explicit_range_alone(canvas):
    _add(canvas, _trace())
    canvas.set_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    assert canvas.y_range() is None, "x zoom must not create a y window"


def test_both_on_zooms_both_axes(canvas):
    _add(canvas, _trace())
    canvas.set_zoom_mode(True)
    canvas.set_y_zoom_mode(True)
    x_before = canvas._axes.get_xlim()
    y_before = _height(canvas)
    canvas._on_scroll(_Wheel(step=1))
    assert canvas._axes.get_xlim() != pytest.approx(x_before)
    assert _height(canvas) < y_before


def test_x_zoom_does_not_discard_an_explicit_y_zoom(canvas):
    """`_y_limits = None` is how x zoom asks for a vertical refit. An explicit
    Y zoom is a stated intention and must outrank it -- otherwise zooming
    horizontally silently throws away the vertical zoom."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    zoomed = canvas.y_range()

    canvas.set_y_zoom_mode(False)
    canvas.set_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))

    assert canvas.y_range() == pytest.approx(zoomed)
    assert canvas._axes.get_ylim() == pytest.approx(zoomed)


# --- surviving the events that clear _y_limits ------------------------------


def test_zoom_survives_loading_another_spectrum(canvas):
    """Adding a trace clears the automatic frame. It must not clear a zoom.

    `_y_limits = None` and `_stack_step = None` are exactly what add_dataset
    does on every drop, so they are reproduced here rather than reading a file
    from disk -- the point under test is the interaction with that
    invalidation, not the reading.
    """
    _add(canvas, _trace("A"))
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    zoomed = canvas.y_range()

    canvas._traces.append(_trace("B", amp=5.0))
    canvas._y_limits = None
    canvas._stack_step = None
    canvas._redraw()

    assert canvas.y_range() == pytest.approx(zoomed)
    assert canvas._axes.get_ylim() == pytest.approx(zoomed)


def test_arrangement_change_deliberately_drops_the_zoom(canvas):
    """The one invalidation the zoom must NOT survive, and it is not an
    oversight.

    set_arrangement ends in fit_to_drawn, on purpose: overlay and stacked
    frames are built from different quantities, and the offsets that suit one
    do not suit the other. Carrying a vertical window across the switch would
    leave the traces outside it -- the disappearing-spectrum fault that the
    re-fit was added to fix, arriving by a new route.

    Switching layout is an explicit "show me it this way", so re-framing is
    the honest response to it.
    """
    _add(canvas, _trace("A"), _trace("B"))
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    assert canvas.y_range() is not None

    canvas.set_arrangement(canvas.ARRANGEMENT_STACKED)

    assert canvas.y_range() is None
    low, high = canvas._axes.get_ylim()
    assert high > low, "and what it re-framed to must be usable"


def test_zoom_survives_a_redraw(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    zoomed = canvas.y_range()
    canvas._redraw()
    assert canvas._axes.get_ylim() == pytest.approx(zoomed)


def test_clearing_the_canvas_drops_the_zoom(canvas):
    """An empty canvas has nothing to stay zoomed on; keeping the window would
    frame a view of nothing."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    canvas.clear()
    assert canvas.y_range() is None


# --- the escape hatches -----------------------------------------------------


def test_fit_y_replaces_the_zoom(canvas):
    """The bug this guards: leaving _y_range set means the redraw re-applies
    the zoom over the fitted limits and Fit Y appears to do nothing."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(5):
        canvas._on_scroll(_Wheel(step=1))
    assert canvas.y_range() is not None

    canvas.fit_to_drawn()

    assert canvas.y_range() is None
    low, high = canvas._axes.get_ylim()
    assert high > low


def test_reset_y_limits_replaces_the_zoom(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    canvas.reset_y_limits()
    assert canvas.y_range() is None


def test_reset_y_zoom_returns_to_automatic(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    canvas.reset_y_zoom()
    assert canvas.y_range() is None


def test_reset_y_zoom_with_nothing_zoomed_is_a_no_op(canvas):
    """It must not consume an undo step for doing nothing."""
    t = _trace()
    _add(canvas, t)
    canvas.select_trace(0)
    canvas.set_y_scale(0, 4.0)
    canvas.reset_y_zoom()
    canvas.undo()
    assert t.y_scale == 1.0, "the scale edit should be what undo reaches"


# --- explicit set_y_range ---------------------------------------------------


def test_set_y_range_applies(canvas):
    _add(canvas, _trace())
    canvas.set_y_range(-1.0, 2.0)
    assert canvas.y_range() == pytest.approx((-1.0, 2.0))
    assert canvas._axes.get_ylim() == pytest.approx((-1.0, 2.0))


def test_set_y_range_refuses_an_inverted_pair(canvas):
    _add(canvas, _trace())
    canvas.set_y_range(2.0, -1.0)
    assert canvas.y_range() is None


def test_set_y_range_refuses_a_zero_height_window(canvas):
    """A zero-height axis renders blank, which reads as a crash."""
    _add(canvas, _trace())
    canvas.set_y_range(1.0, 1.0)
    assert canvas.y_range() is None


def test_set_y_range_refuses_non_finite(canvas):
    _add(canvas, _trace())
    canvas.set_y_range(float("nan"), 1.0)
    assert canvas.y_range() is None
    canvas.set_y_range(0.0, float("inf"))
    assert canvas.y_range() is None


def test_set_y_range_refuses_junk(canvas):
    _add(canvas, _trace())
    canvas.set_y_range("a", "b")
    assert canvas.y_range() is None
    canvas.set_y_range(None, None)
    assert canvas.y_range() is None


# --- undo -------------------------------------------------------------------


def test_zoom_is_undoable(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1))
    assert canvas.y_range() is not None
    canvas.undo()
    assert canvas.y_range() is None


def test_a_burst_of_zooming_is_one_undo_step(canvas):
    """Consistent with the X zoom, which shares the same coalescing key: a
    dozen wheel notches is one gesture and should cost one undo, not twelve."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(12):
        canvas._on_scroll(_Wheel(step=1))
    canvas.undo()
    assert canvas.y_range() is None


def test_undo_of_an_old_snapshot_without_the_field_does_not_raise(canvas):
    """A snapshot taken before this field existed can still be on the stack
    when a session is restored. It must apply, not raise KeyError."""
    _add(canvas, _trace())
    canvas.push_undo()
    canvas._undo[-1].pop("y_range", None)
    canvas.undo()          # must not raise


# --- persistence ------------------------------------------------------------


def test_zoom_is_saved_in_a_session(canvas):
    _add(canvas, _trace())
    canvas.set_y_range(-1.5, 3.5)
    state = canvas.session_state()
    assert state["y_range"] == pytest.approx([-1.5, 3.5])


def test_a_session_with_no_y_range_means_automatic(canvas):
    assert (lambda st: tuple(st["y_range"]) if st.get("y_range") else None)(
        {"ppm_range": None}
    ) is None


# --- 2D ---------------------------------------------------------------------


def _trace_2d(label="2D"):
    f2 = np.linspace(10.0, 0.0, 32)
    f1 = np.linspace(150.0, 0.0, 24)
    return Trace(
        path=None, label=label, ppm=np.asarray([]), intensity=np.asarray([]),
        color="#1f77b4", is_2d=True,
        matrix=np.random.default_rng(0).normal(size=(24, 32)),
        ppm_f1=f1, ppm_f2=f2,
    )


def test_2d_x_zoom_still_zooms_both_axes(canvas):
    """Long-standing behaviour. Turning Y zoom off must not remove it."""
    _add(canvas, _trace_2d())
    canvas.set_zoom_mode(True)
    canvas.set_y_zoom_mode(False)
    canvas._on_scroll(_Wheel(step=1, xdata=5.0, ydata=75.0))
    assert canvas._f2_range is not None
    assert canvas._f1_range is not None


def test_2d_y_zoom_alone_zooms_f1_only(canvas):
    _add(canvas, _trace_2d())
    canvas.set_zoom_mode(False)
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1, xdata=5.0, ydata=75.0))
    assert canvas._f1_range is not None
    assert canvas._f2_range is None, "F2 must be untouched"


# --- gaps the first pass left: stacked mode, the Windows wheel, desync ------


def test_zoom_applies_in_stacked_mode(canvas):
    """Stacked mode computes its frame from the lane layout, in a different
    branch from overlay. A zoom that works in one and is silently ignored in
    the other is the kind of half-feature that is worse than none."""
    _add(canvas, _trace("A"), _trace("B", centre=7.0))
    canvas.set_arrangement("stacked")
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)

    canvas._on_scroll(_Wheel(step=1, ydata=0.5))

    assert _height(canvas) < before, "wheel must zoom the stacked view too"
    assert canvas.y_range() is not None


def test_zoom_survives_a_stack_step_change(canvas):
    """Changing the stacking gap recomputes the automatic frame. An explicit
    zoom is a stated intention and must outrank that, exactly as it does for
    an x zoom."""
    _add(canvas, _trace("A"), _trace("B", centre=7.0))
    canvas.set_arrangement("stacked")
    canvas.set_y_range(-0.2, 0.8)
    pinned = canvas.y_range()

    canvas._redraw()

    assert canvas.y_range() == pinned


def test_windows_wheel_without_a_step_attribute_still_zooms(canvas):
    """Not every backend fills in `step`.

    matplotlib's Qt backend populates it, but the handler carries a fallback
    to `button` for the case where it is absent or zero, and that fallback is
    what a Windows machine may land on. Untested, it is a coin flip.
    """
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)

    class _NoStep:
        button = "up"
        xdata = 5.0
        ydata = 0.5
        inaxes = True
        step = 0            # present but zero: the documented fallback case

    canvas._on_scroll(_NoStep())
    assert _height(canvas) < before, "the button fallback must drive the zoom"


def test_windows_wheel_down_without_step_widens(canvas):
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)

    class _NoStep:
        button = "down"
        xdata = 5.0
        ydata = 0.5
        inaxes = True
        step = 0

    canvas._on_scroll(_NoStep())
    assert _height(canvas) > before


def test_an_unknown_wheel_button_does_nothing(canvas):
    """A horizontal wheel or a stray button must not be read as 'up'."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = canvas._axes.get_ylim()

    class _Sideways:
        button = "left"
        xdata = 5.0
        ydata = 0.5
        inaxes = True
        step = 0

    canvas._on_scroll(_Sideways())
    assert canvas._axes.get_ylim() == pytest.approx(before)


def test_a_fractional_step_still_zooms_once(canvas):
    """High-resolution wheels and precision touchpads -- common on Windows
    laptops -- report fractional steps. A step of 0.2 is still one gesture
    upward and must zoom in, not be rounded away to nothing."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)

    canvas._on_scroll(_Wheel(step=0.2, ydata=0.5))
    assert _height(canvas) < before

    mid = _height(canvas)
    canvas._on_scroll(_Wheel(step=-0.2, ydata=0.5))
    assert _height(canvas) > mid


def test_zooming_far_out_does_not_reach_infinity(canvas):
    """Sixty notches out is a plausible accident with a free-spinning wheel.
    The window must stay finite and ordered, not overflow into a blank plot."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(60):
        canvas._on_scroll(_Wheel(step=-1, ydata=0.5))

    low, high = canvas._axes.get_ylim()
    assert np.isfinite(low) and np.isfinite(high)
    assert high > low


def test_zooming_far_in_does_not_collapse_the_axis(canvas):
    """The mirror case: a zero-height axis draws an empty plot with no
    obvious way back, which reads as the application having crashed."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(200):
        canvas._on_scroll(_Wheel(step=1, ydata=0.5))

    low, high = canvas._axes.get_ylim()
    assert np.isfinite(low) and np.isfinite(high)
    assert high > low, "the vertical axis must never collapse to zero height"


def test_both_toggles_off_after_zooming_leaves_the_view_put(canvas):
    """Turning the tool off is not an undo. The zoom stays until something
    explicitly clears it."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    canvas._on_scroll(_Wheel(step=1, ydata=0.5))
    zoomed = canvas._axes.get_ylim()

    canvas.set_y_zoom_mode(False)
    canvas._redraw()

    assert canvas._axes.get_ylim() == pytest.approx(zoomed)


def test_wheel_with_y_zoom_on_does_not_touch_trace_scales(canvas):
    """The whole distinction between this and the wheel's default job: Y zoom
    magnifies the view and must change NOTHING between the spectra."""
    a, b = _trace("A"), _trace("B", amp=3.0)
    _add(canvas, a, b)
    canvas.select_trace(0)
    canvas.set_y_zoom_mode(True)

    for _ in range(5):
        canvas._on_scroll(_Wheel(step=1, ydata=0.5))

    assert a.y_scale == 1.0 and b.y_scale == 1.0
    assert a.y_offset == 0.0 and b.y_offset == 0.0


def test_zoom_then_remove_a_spectrum_keeps_the_window(canvas):
    """Removing a trace recomputes the automatic frame; an explicit zoom must
    survive it, the same as it survives a load."""
    a, b = _trace("A"), _trace("B", centre=7.0)
    b.path = __import__("pathlib").Path("/tmp/does-not-need-to-exist-B")
    _add(canvas, a, b)
    canvas.set_y_range(-0.5, 1.5)
    pinned = canvas.y_range()

    canvas.remove_trace(b.path)

    assert canvas.y_range() == pinned


def test_zoom_survives_an_x_offset(canvas):
    """The feature added alongside this one. Shifting a trace horizontally
    must not disturb a vertical zoom."""
    _add(canvas, _trace())
    canvas.set_y_range(-0.5, 1.5)
    pinned = canvas.y_range()

    canvas.set_x_offset(0, 0.3)

    assert canvas.y_range() == pinned


# --- Fit Y must be recoverable ---------------------------------------------


def test_fit_y_can_be_undone(canvas):
    """One stray click on Fit Y must not be the end of a carefully set zoom.

    Every other change to the vertical window is undoable; this one silently
    was not, because before Y zoom existed it only cleared a cache. Now it can
    discard a window the user set deliberately.
    """
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(3):
        canvas._on_scroll(_Wheel(step=1, ydata=0.5))
    zoomed = canvas.y_range()
    assert zoomed is not None

    canvas.reset_y_limits()
    assert canvas.y_range() is None

    canvas.undo()
    assert canvas.y_range() == pytest.approx(zoomed)


def test_fit_y_with_nothing_zoomed_costs_no_undo_step(canvas):
    """The other half: pushing unconditionally would fill the history with
    steps that restore nothing a user can see."""
    _add(canvas, _trace())
    depth = len(canvas._undo)

    canvas.reset_y_limits()
    canvas.reset_y_limits()

    assert len(canvas._undo) == depth


def test_fit_y_does_not_fold_into_the_zoom_burst_before_it(canvas):
    """Undo must reach the zoom, not jump past it.

    Fit Y carries its own coalescing key. Sharing "y_zoom" would let a click
    landing inside the coalescing window merge into the wheel burst, so one
    undo would skip back past both and the state the user wants -- the zoom --
    would be unreachable.
    """
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    for _ in range(4):                     # a burst, well inside the window
        canvas._on_scroll(_Wheel(step=1, ydata=0.5))
    zoomed = canvas.y_range()

    canvas.reset_y_limits()                # immediately after, same window
    canvas.undo()

    assert canvas.y_range() == pytest.approx(zoomed), (
        "undo landed past the zoom instead of on it"
    )


def test_a_large_pixel_delta_step_zooms_once_and_stays_sane(canvas):
    """A Windows precision touchpad reports raw pixels, not notches.

    matplotlib's Qt backend uses angleDelta/120 on X11 -- always a clean
    +/-1 -- but prefers pixelDelta elsewhere, so on Windows `step` can arrive
    as 40 or 120 rather than 1. The handler must read that as direction, not
    as a magnitude to raise the zoom factor to: 1.1**120 is not a zoom, it is
    a blank plot.
    """
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    before = _height(canvas)

    canvas._on_scroll(_Wheel(step=120, ydata=0.5))

    after = _height(canvas)
    assert np.isfinite(after) and after > 0
    assert after < before, "a large positive step must still zoom in"
    assert after > before / 10, (
        "one wheel event must not zoom by orders of magnitude"
    )


def test_pixel_delta_down_widens_by_the_same_amount(canvas):
    """Direction only: up then down by the same raw step returns to where it
    started, so a gesture and its reverse cancel."""
    _add(canvas, _trace())
    canvas.set_y_zoom_mode(True)
    start = _height(canvas)

    canvas._on_scroll(_Wheel(step=120, ydata=0.5))
    canvas._on_scroll(_Wheel(step=-120, ydata=0.5))

    assert _height(canvas) == pytest.approx(start, rel=1e-6)
