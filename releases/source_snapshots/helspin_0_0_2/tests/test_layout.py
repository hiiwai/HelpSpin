"""New Figure: two numbers to a laid-out project."""

import pytest

from helspin.domain.layout import (
    DIFFERENCE_FRACTION,
    GUTTER,
    MARGIN_BOTTOM,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    MARGIN_TOP,
    NewFigureRequest,
    add_difference_to,
    build_project,
    can_add_difference,
    stack_rects,
)
from helspin.domain.project import (
    Arrangement,
    Block1D,
    Block2D,
    BlockId,
    DatasetId,
    DifferenceBox,
    LegendBox,
    SpectrumBox,
    add_slot_1d,
    new_id,
)


def request(**kw):
    base = dict(count_1d=3, arrangement_1d=Arrangement.OVERLAY, count_2d=0)
    base.update(kw)
    return NewFigureRequest(**base)


# --- validation -------------------------------------------------------------


def test_valid_request_has_no_problems():
    assert request().validate() == []


def test_zero_of_both_is_rejected():
    problems = request(count_1d=0, count_2d=0).validate()
    assert problems and "at least one" in problems[0]


def test_negative_counts_rejected():
    assert request(count_1d=-1).validate()


def test_subtracted_needs_two_spectra():
    problems = request(count_1d=1, arrangement_1d=Arrangement.SUBTRACTED).validate()
    assert any("two" in p for p in problems)
    assert request(count_1d=2, arrangement_1d=Arrangement.SUBTRACTED).validate() == []


def test_2d_subtraction_not_yet_supported():
    problems = request(
        count_1d=0, count_2d=2, arrangement_2d=Arrangement.SUBTRACTED
    ).validate()
    assert any("2D difference" in p for p in problems)


def test_2d_cannot_be_stacked():
    problems = request(
        count_1d=0, count_2d=2, arrangement_2d=Arrangement.STACKED
    ).validate()
    assert any("stacked" in p for p in problems)


def test_build_raises_on_invalid_request():
    with pytest.raises(ValueError):
        build_project(request(count_1d=0, count_2d=0))


# --- geometry ---------------------------------------------------------------


def test_rects_are_inside_the_figure():
    for source, diff in stack_rects(4):
        for rect in (source, diff):
            if rect is None:
                continue
            left, bottom, width, height = rect
            assert left >= 0 and bottom >= 0
            assert left + width <= 1.0 + 1e-9
            assert bottom + height <= 1.0 + 1e-9
            assert width > 0 and height > 0


def test_rects_respect_margins():
    (left, bottom, width, height), _ = stack_rects(1)[0]
    assert left == pytest.approx(MARGIN_LEFT)
    assert bottom == pytest.approx(MARGIN_BOTTOM)
    assert width == pytest.approx(1.0 - MARGIN_LEFT - MARGIN_RIGHT)
    assert height == pytest.approx(1.0 - MARGIN_TOP - MARGIN_BOTTOM)


def test_rows_do_not_overlap():
    rects = [source for source, _ in stack_rects(4)]
    tops = sorted((b, b + h) for _, b, _, h in rects)
    for (_, top), (next_bottom, _) in zip(tops, tops[1:]):
        assert next_bottom >= top - 1e-9


def test_rows_are_evenly_spaced():
    rects = [source for source, _ in stack_rects(3)]
    heights = {round(h, 9) for _, _, _, h in rects}
    assert len(heights) == 1


def test_first_row_is_at_the_top():
    rects = [source for source, _ in stack_rects(3)]
    assert rects[0][1] > rects[-1][1]


def test_gutter_separates_rows():
    rects = [source for source, _ in stack_rects(2)]
    (_, b0, _, _), (_, b1, _, h1) = rects
    assert b0 - (b1 + h1) == pytest.approx(GUTTER)


def test_zero_rows():
    assert stack_rects(0) == []


def test_difference_row_subdivides_in_place():
    plain = stack_rects(1)[0][0]
    source, diff = stack_rects(1, [True])[0]
    assert source[3] + diff[3] == pytest.approx(plain[3])
    assert diff[3] == pytest.approx(plain[3] * DIFFERENCE_FRACTION)
    assert diff[1] == pytest.approx(plain[1])


def test_many_rows_still_positive_height():
    for source, _ in stack_rects(12):
        assert source[3] > 0


# --- project construction ---------------------------------------------------


def test_three_overlaid_1d_gives_one_box():
    p = build_project(request(count_1d=3, arrangement_1d=Arrangement.OVERLAY))
    assert len(p.spectrum_boxes()) == 1
    assert len(p.blocks()[0].slots) == 3


def test_tiled_gives_one_box_per_spectrum():
    p = build_project(request(count_1d=4, arrangement_1d=Arrangement.TILED))
    assert len(p.spectrum_boxes()) == 4


def test_tiled_boxes_share_one_block():
    p = build_project(request(count_1d=4, arrangement_1d=Arrangement.TILED))
    blocks = {id(b.block) for b in p.spectrum_boxes()}
    assert len(blocks) == 1


def test_slots_start_empty_with_distinct_colours():
    p = build_project(request(count_1d=4))
    slots = p.blocks()[0].slots
    assert all(not s.is_filled for s in slots)
    assert len({s.color for s in slots}) == 4


def test_1d_and_2d_are_separate_blocks():
    p = build_project(request(count_1d=2, count_2d=2))
    dims = {b.dimensionality for b in p.blocks()}
    assert len(dims) == 2


def test_subtracted_creates_a_difference_box():
    p = build_project(request(count_1d=2, arrangement_1d=Arrangement.SUBTRACTED))
    assert any(isinstance(b, DifferenceBox) for b in p.boxes)
    block = p.blocks()[0]
    assert block.difference is not None
    assert block.difference.minuend == block.slots[0].id
    assert block.difference.subtrahend == block.slots[1].id


def test_difference_colour_does_not_collide():
    p = build_project(request(count_1d=2, arrangement_1d=Arrangement.SUBTRACTED))
    block = p.blocks()[0]
    assert block.difference.color not in {s.color for s in block.slots}


def test_legend_box_references_every_spectrum_box():
    p = build_project(request(count_1d=2, arrangement_1d=Arrangement.TILED, legend_box=True))
    legends = [b for b in p.boxes if isinstance(b, LegendBox)]
    assert len(legends) == 1
    assert set(legends[0].sources) == {b.id for b in p.spectrum_boxes()}


def test_no_legend_box_by_default():
    p = build_project(request())
    assert not any(isinstance(b, LegendBox) for b in p.boxes)


def test_figure_size_carried_through():
    p = build_project(request())
    assert p.figure.width_cm == pytest.approx(8.5)


def test_single_spectrum_figure():
    p = build_project(request(count_1d=1))
    assert len(p.spectrum_boxes()) == 1
    assert len(p.blocks()[0].slots) == 1


def test_many_spectra_tiled():
    p = build_project(request(count_1d=8, arrangement_1d=Arrangement.TILED))
    assert len(p.spectrum_boxes()) == 8
    for box in p.spectrum_boxes():
        assert box.rect[3] > 0


# --- adding a difference after the fact --------------------------------------


def filled_block(n=2):
    b = Block1D(id=BlockId(new_id("b")))
    for _ in range(n):
        slot = add_slot_1d(b)
        slot.dataset_id = DatasetId(new_id("d"))
    return b


def test_enabled_only_with_two_filled_slots():
    assert can_add_difference(filled_block(2))[0]
    ok, reason = can_add_difference(filled_block(1))
    assert not ok and "two" in reason


def test_unfilled_slots_do_not_count():
    b = filled_block(1)
    add_slot_1d(b)
    ok, reason = can_add_difference(b)
    assert not ok and "1" in reason


def test_disabled_for_2d_with_a_reason():
    ok, reason = can_add_difference(Block2D(id=BlockId("b")))
    assert not ok and "2D" in reason


def test_disabled_when_one_already_exists():
    p = build_project(request(count_1d=2, arrangement_1d=Arrangement.SUBTRACTED))
    ok, reason = can_add_difference(p.blocks()[0])
    assert not ok and "already" in reason


def test_disabled_for_no_selection():
    assert not can_add_difference(None)[0]


def test_adding_splits_only_the_source_box():
    p = build_project(request(count_1d=2, arrangement_1d=Arrangement.TILED))
    for box in p.spectrum_boxes():
        for slot in box.block.slots:
            slot.dataset_id = DatasetId(new_id("d"))
    target, other = p.spectrum_boxes()
    original, other_before = target.rect, other.rect

    diff = add_difference_to(p, target.id)

    assert other.rect == other_before          # nothing else moves
    assert target.rect[3] < original[3]
    assert target.rect[3] + diff.rect[3] == pytest.approx(original[3])
    assert diff.source_box == target.id


def test_adding_twice_is_refused():
    p = build_project(request(count_1d=2))
    box = p.spectrum_boxes()[0]
    for slot in box.block.slots:
        slot.dataset_id = DatasetId(new_id("d"))
    add_difference_to(p, box.id)
    with pytest.raises(ValueError):
        add_difference_to(p, box.id)


def test_adding_to_an_underfilled_block_is_refused():
    p = build_project(request(count_1d=2))
    with pytest.raises(ValueError):
        add_difference_to(p, p.spectrum_boxes()[0].id)


def test_adding_to_a_legend_box_is_refused():
    p = build_project(request(count_1d=2, legend_box=True))
    legend = next(b for b in p.boxes if isinstance(b, LegendBox))
    with pytest.raises(ValueError):
        add_difference_to(p, legend.id)
