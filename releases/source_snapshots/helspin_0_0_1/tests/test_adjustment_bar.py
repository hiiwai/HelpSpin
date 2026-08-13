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
        bar._right_spin.editingFinished.emit()
    assert blocker.args == [8.5, 6.0]


def test_ascending_range_is_rejected_not_propagated(qtbot):
    """left <= right must never reach rangeChanged -- same rule as
    LinkGroup.set_x in the domain layer, enforced here too."""
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(6.0)
    bar._right_spin.setValue(8.5)   # ascending: invalid

    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    bar._right_spin.editingFinished.emit()

    assert received == []
    assert bar._left_spin.value() > bar._right_spin.value()


def test_equal_left_and_right_is_rejected():
    bar = AdjustmentBar()
    bar._left_spin.setValue(5.0)
    bar._right_spin.setValue(5.0)
    received = []
    bar.rangeChanged.connect(lambda l, r: received.append((l, r)))
    bar._right_spin.editingFinished.emit()
    assert received == []


def test_recent_ranges_populate_after_a_valid_edit(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(8.5)
    bar._right_spin.setValue(6.0)
    bar._right_spin.editingFinished.emit()
    assert bar._recent_combo.count() == 1
    assert "8.50" in bar._recent_combo.itemText(0)


def test_recent_ranges_deduplicate_and_move_to_front(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)

    def apply(left, right):
        bar._left_spin.setValue(left)
        bar._right_spin.setValue(right)
        bar._right_spin.editingFinished.emit()

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
        bar._right_spin.editingFinished.emit()
    assert bar._recent_combo.count() <= 8


def test_selecting_a_recent_range_re_emits_it(qtbot):
    bar = AdjustmentBar()
    bar.set_enabled_for_figure(True)
    bar._left_spin.setValue(8.5)
    bar._right_spin.setValue(6.0)
    bar._right_spin.editingFinished.emit()

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
