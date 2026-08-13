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


def test_recent_ranges_populate_after_a_valid_edit(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(8.5)
    bar._right_spin.setValue(6.0)
    bar._apply_button.click()
    assert bar._recent_combo.count() == 1
    assert "8.50" in bar._recent_combo.itemText(0)


def test_recent_ranges_deduplicate_and_move_to_front(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)

    def apply(left, right):
        bar._left_spin.setValue(left)
        bar._right_spin.setValue(right)
        bar._apply_button.click()

    apply(8.5, 6.0)
    apply(4.0, 2.0)
    apply(8.5, 6.0)   # repeat of the first -- should move to front, not duplicate
    assert bar._recent_combo.count() == 2
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
    bar._on_recent_selected(5)   # nothing recorded yet
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


def test_recent_ranges_follow_the_axis_order(qtbot):
    """Everything shows HIGH to LOW -- the boxes, the recent list and the
    plot. Any component using a different order is what kept reading as
    "reversed"."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(1.0)
    bar._right_spin.setValue(12.0)
    bar._apply_button.click()
    assert bar._recent_combo.itemText(0).startswith("12.00 \u2192 1.00")


def test_recent_range_display_is_independent_of_typed_order(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(12.0)
    bar._right_spin.setValue(1.0)
    bar._apply_button.click()
    assert bar._recent_combo.itemText(0).startswith("12.00 \u2192 1.00")


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
