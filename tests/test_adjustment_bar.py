"""AdjustmentBar: Full/typed range, validation, recent ranges, disabled state."""

import pytest

from helspin.ui.adjustment_bar import AdjustmentBar

pytestmark = pytest.mark.usefixtures("qapp")


def test_disabled_by_default():
    bar = AdjustmentBar()
    assert not bar._full_button.isEnabled()
    assert not bar._left_spin.isEnabled()
    assert not bar._right_spin.isEnabled()
    assert not bar._recent_combo.isEnabled()


def test_enabling_for_a_figure_enables_controls():
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    assert bar._full_button.isEnabled()
    assert bar._left_spin.isEnabled()
    assert bar._right_spin.isEnabled()


def test_full_button_emits_full_requested(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    with qtbot.waitSignal(bar.fullRequested, timeout=500):
        bar._full_button.click()


def test_set_range_does_not_emit_range_changed(qtbot):
    """Programmatic updates (e.g. reflecting a canvas zoom) must not create
    a feedback loop by re-emitting rangeChanged."""
    bar = AdjustmentBar()
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    bar.set_range(8.5, 6.0)
    assert received == []
    assert bar.current_range() == (8.5, 6.0)


def test_editing_a_valid_range_emits_range_changed(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(8.5)
    bar._right_spin.setValue(6.0)
    with qtbot.waitSignal(bar.rangeChanged, timeout=500) as blocker:
        bar._apply_button.click()
    assert blocker.args == [8.5, 6.0]


def test_ascending_range_is_normalised_not_rejected(qtbot):
    """A user types a range as "0 to 12" as readily as "12 to 0". An earlier
    version rejected the ascending form outright, which just looked broken.
    It is swapped into the descending order the ppm axis uses instead, and the
    boxes are updated so what is shown matches what is applied."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(0.0)
    bar._right_spin.setValue(8.5)   # ascending as typed

    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    bar._apply_button.click()

    assert received == [(8.5, 0.0)]            # plot gets high->low
    # Boxes normalise to the same order as the axis: left = higher ppm.
    assert bar.current_range() == (8.5, 0.0)

def test_equal_left_and_right_is_rejected():
    bar = AdjustmentBar()
    bar._left_spin.setValue(5.0)
    bar._right_spin.setValue(5.0)
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    bar._apply_button.click()
    assert received == []


def test_recent_ranges_start_with_the_seeded_defaults(qtbot):
    """The list is seeded rather than empty: a "recent ranges" control that
    does nothing until you have already typed a range by hand is a control
    that helps only after you no longer need it."""
    from helspin.core.settings import DEFAULT_RECENT_RANGES

    bar = AdjustmentBar()
    assert bar._recent_combo.count() == len(DEFAULT_RECENT_RANGES)
    shown = [bar._recent_combo.itemText(i)
             for i in range(bar._recent_combo.count())]
    assert any("0.00" in t and "12.00" in t for t in shown)
    assert any("-1.00" in t and "13.00" in t for t in shown)
    assert any("5.00" in t and "13.00" in t for t in shown)
    # Most-used first: the menu is read top-down.
    assert "0.00" in shown[0] and "12.00" in shown[0]


def test_seeded_ranges_are_usable_windows(qtbot):
    """Guards the mistake this replaces: the first attempt seeded
    -1 to -12 ppm, a window entirely below zero, where a 1H spectrum has
    nothing. A default that has to be deleted before the control is useful is
    worse than no default."""
    from helspin.core.settings import DEFAULT_RECENT_RANGES

    for left, right in DEFAULT_RECENT_RANGES:
        low, high = min(left, right), max(left, right)
        assert high > low, "a range must have width"
        # Every default must cover some part of the region where 1H signals
        # actually appear.
        assert high > 0.0, f"{low}..{high} lies entirely below 0 ppm"
        assert high - low >= 5.0, f"{low}..{high} is too narrow to be a default"
        assert left > right, "stored high-to-low, matching the descending axis"


def test_recent_ranges_populate_after_a_valid_edit(qtbot):
    from helspin.core.settings import DEFAULT_RECENT_RANGES

    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    before = bar._recent_combo.count()
    assert before == len(DEFAULT_RECENT_RANGES)
    bar._left_spin.setValue(8.5)
    bar._right_spin.setValue(6.0)
    bar._apply_button.click()
    # A new range is added to the seeded ones, at the front.
    assert bar._recent_combo.count() == before + 1
    assert "8.50" in bar._recent_combo.itemText(0)


def test_recent_ranges_deduplicate_and_move_to_front(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)

    def apply(left, right):
        bar._left_spin.setValue(left)
        bar._right_spin.setValue(right)
        bar._apply_button.click()

    before = bar._recent_combo.count()      # the seeded defaults
    apply(8.5, 6.0)
    apply(4.0, 2.0)
    apply(8.5, 6.0)   # repeat of the first -- should move to front, not duplicate
    assert bar._recent_combo.count() == before + 2
    assert "8.50" in bar._recent_combo.itemText(0)


def test_recent_ranges_capped(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    for i in range(12):
        bar._left_spin.setValue(10.0 + i)
        bar._right_spin.setValue(1.0)
        bar._apply_button.click()
    assert bar._recent_combo.count() <= 8


def test_selecting_a_recent_range_re_emits_it(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(8.5)
    bar._right_spin.setValue(6.0)
    bar._apply_button.click()

    with qtbot.waitSignal(bar.rangeChanged, timeout=500) as blocker:
        bar._on_recent_selected(0)
    assert blocker.args == [8.5, 6.0]
    assert bar.current_range() == (8.5, 6.0)


def test_selecting_an_out_of_range_recent_index_is_a_no_op(qtbot):
    bar = AdjustmentBar()
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    # Past the end of the seeded list, so there is nothing to select.
    bar._on_recent_selected(len(bar._recent) + 3)
    assert received == []


# --- explicit Apply -------------------------------------------------------


def test_typing_only_one_box_does_not_apply_a_half_finished_range(qtbot):
    """Reported bug: editing one box applied a range built from that value and
    whatever stale value was in the other, so 'from 0 to 10' never worked.
    Nothing is applied until Apply is pressed and both values are read."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))

    bar._left_spin.setValue(0.0)
    assert received == []


def test_apply_accepts_the_range_in_ascending_order(qtbot):
    """0 to 10 is as natural to type as 10 to 0; both must work."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))

    bar._left_spin.setValue(0.0)
    bar._right_spin.setValue(10.0)
    bar._apply_button.click()

    assert received == [(10.0, 0.0)]           # plot gets high->low
    assert bar.current_range() == (10.0, 0.0)  # normalised to match


def test_apply_with_a_zero_width_range_does_nothing(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    bar._left_spin.setValue(4.0)
    bar._right_spin.setValue(4.0)
    bar._apply_button.click()
    assert received == []


def test_recent_ranges_use_range_notation_low_to_high(qtbot):
    """The remembered list is a DESCRIPTION of a range, so it reads from the
    smaller number to the larger ("0 -> 10 ppm"). The two boxes keep the axis
    order (left = higher ppm) because they map onto the plot's edges."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(1.0)
    bar._right_spin.setValue(12.0)
    bar._apply_button.click()
    assert bar._recent_combo.itemText(0).startswith("1.00 \u2192 12.00")


def test_recent_range_display_is_independent_of_typed_order(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(12.0)
    bar._right_spin.setValue(1.0)
    bar._apply_button.click()
    assert bar._recent_combo.itemText(0).startswith("1.00 \u2192 12.00")


def test_boxes_normalise_so_left_is_the_higher_ppm(qtbot):
    """NMR convention, applied to the CONTROLS as well as the plot: the left
    box is the left edge of the plot, so it holds the higher value."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(1.0)
    bar._right_spin.setValue(12.0)
    bar._apply_button.click()
    left, right = bar.current_range()
    assert left > right


def test_set_range_normalises_too(qtbot):
    bar = AdjustmentBar()
    bar.set_range(-3.31, 12.7)
    assert bar.current_range() == (12.7, -3.31)


def test_f1_controls_are_hidden_in_1d(qtbot):
    bar = AdjustmentBar()
    bar.set_mode("1D")
    assert not bar._f1_apply.isVisible()
    assert bar._ppm_label.text().startswith("ppm")


def test_f1_controls_appear_in_2d(qtbot):
    """F1 is the indirect dimension; it needs its own range, which is why one
    pair of boxes is not enough in 2D."""
    bar = AdjustmentBar()
    bar.show()
    bar.set_mode("2D")
    assert bar._f1_apply.isVisible()
    assert bar._ppm_label.text().startswith("F2")


def test_f1_range_normalises_and_emits(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._f1_left.setValue(40.0)
    bar._f1_right.setValue(120.0)
    with qtbot.waitSignal(bar.f1RangeChanged, timeout=1000) as blocker:
        bar._f1_apply.click()
    assert blocker.args == [120.0, 40.0]
    assert bar.f1_range() == (120.0, 40.0)


def test_recent_ranges_persist_across_instances(qtbot):
    """They were in-memory only, so a "recent" list never survived long enough
    to be recent. Written on every change, not at shutdown, so a crash is not
    what loses them."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(9.25)
    bar._right_spin.setValue(3.5)
    bar._apply_button.click()

    fresh = AdjustmentBar()
    assert "9.25" in fresh._recent_combo.itemText(0)


def test_corrupt_stored_ranges_fall_back_to_defaults(qtbot, monkeypatch):
    """A hand-edited or truncated settings value should cost a stale list, not
    an empty control or a crash on startup."""
    from helspin.core import settings as st

    class _Bad:
        def value(self, *_a, **_k):
            return "{not json at all"

        def setValue(self, *_a, **_k):
            pass

    monkeypatch.setattr(st, "_settings", lambda: _Bad())
    assert st.load_recent_ranges() == st.DEFAULT_RECENT_RANGES


def test_unusable_rows_are_skipped_not_fatal(qtbot, monkeypatch):
    """One bad row must not discard the good ones."""
    import json

    from helspin.core import settings as st

    class _Mixed:
        def value(self, *_a, **_k):
            return json.dumps([[8.0, 2.0], ["x", 1.0], [3.0, 3.0], [5.0, 1.0]])

        def setValue(self, *_a, **_k):
            pass

    monkeypatch.setattr(st, "_settings", lambda: _Mixed())
    # The non-numeric row and the zero-width one are dropped.
    assert st.load_recent_ranges() == [(8.0, 2.0), (5.0, 1.0)]
