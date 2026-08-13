"""Project model: dynamic slots, instance-bound colour, link groups, labels."""

import pytest

from helspin.domain.errors import (
    DimensionalityMismatch,
    NucleusMismatch,
    SlotNotFound,
)
from helspin.domain.project import (
    DEFAULT_PALETTE,
    Arrangement,
    Block1D,
    Block2D,
    BlockId,
    BoxId,
    DatasetId,
    Dimensionality,
    LabelTemplate,
    LegendBox,
    LinkGroup,
    LinkGroupId,
    Project,
    SlotId,
    SpectrumBox,
    add_slot_1d,
    add_slot_2d,
    assert_same_dimensionality,
    new_id,
    next_colour,
)


def block1d(n=0):
    b = Block1D(id=BlockId(new_id("b")))
    for _ in range(n):
        add_slot_1d(b)
    return b


def fill(block, count=None):
    for slot in block.slots[: count if count is not None else len(block.slots)]:
        slot.dataset_id = DatasetId(new_id("d"))
    return block


# --- colour assignment ------------------------------------------------------


def test_slots_take_distinct_palette_colours():
    b = block1d(4)
    colours = [s.color for s in b.slots]
    assert colours == list(DEFAULT_PALETTE[:4])
    assert len(set(colours)) == 4


def test_deleting_a_slot_does_not_recolour_survivors():
    """The critical rule: an unrelated deletion must not change other traces."""
    b = block1d(4)
    before = {s.id: s.color for s in b.slots}
    doomed = b.slots[1]
    b.slots.remove(doomed)
    for s in b.slots:
        assert s.color == before[s.id]


def test_new_slot_after_deletion_reuses_the_freed_colour():
    b = block1d(4)
    freed = b.slots[1].color
    b.slots.remove(b.slots[1])
    added = add_slot_1d(b)
    assert added.color == freed
    assert len({s.color for s in b.slots}) == 4


def test_palette_cycles_without_duplicating_a_visible_colour():
    b = block1d(len(DEFAULT_PALETTE))
    assert len({s.color for s in b.slots}) == len(DEFAULT_PALETTE)
    extra = add_slot_1d(b)
    assert extra.color in DEFAULT_PALETTE


def test_next_colour_prefers_unused():
    assert next_colour([DEFAULT_PALETTE[0]], DEFAULT_PALETTE) == DEFAULT_PALETTE[1]
    assert next_colour([], DEFAULT_PALETTE) == DEFAULT_PALETTE[0]


def test_next_colour_with_empty_palette_raises():
    with pytest.raises(ValueError):
        next_colour([], [])


def test_reordering_carries_colour_with_the_slot():
    b = block1d(3)
    first = b.slots[0]
    b.slots.reverse()
    assert b.slots[-1] is first
    assert b.slots[-1].color == DEFAULT_PALETTE[0]


def test_2d_slot_colours_are_independent_per_block():
    b = Block2D(id=BlockId(new_id("b")))
    s1, s2 = add_slot_2d(b), add_slot_2d(b)
    assert s1.color_positive != s2.color_positive


# --- dynamic slot count -----------------------------------------------------


def test_block_grows_on_demand():
    b = block1d(3)
    add_slot_1d(b)
    assert len(b.slots) == 4


def test_empty_block_is_legal():
    b = block1d(0)
    assert b.filled_slots == []
    assert b.visible_slots == []


def test_filled_and_visible_are_distinct():
    b = fill(block1d(3))
    b.slots[0].visible = False
    b.slots[2].dataset_id = None
    assert len(b.filled_slots) == 2
    assert len(b.visible_slots) == 1


def test_clearing_keeps_the_slot_and_its_colour():
    b = fill(block1d(2))
    colour = b.slots[0].color
    b.slots[0].dataset_id = None
    assert len(b.slots) == 2
    assert b.slots[0].color == colour
    assert not b.slots[0].is_filled


def test_slot_lookup_by_id():
    b = block1d(2)
    assert b.slot(b.slots[1].id) is b.slots[1]
    assert b.index_of(b.slots[1].id) == 1


def test_missing_slot_raises():
    b = block1d(1)
    with pytest.raises(SlotNotFound):
        b.slot(SlotId("nope"))
    with pytest.raises(SlotNotFound):
        b.index_of(SlotId("nope"))


# --- dimensionality ---------------------------------------------------------


def test_dimensionality_guard():
    assert_same_dimensionality(block1d(), Dimensionality.ONE_D)
    with pytest.raises(DimensionalityMismatch):
        assert_same_dimensionality(block1d(), Dimensionality.TWO_D)
    with pytest.raises(DimensionalityMismatch):
        assert_same_dimensionality(
            Block2D(id=BlockId("b")), Dimensionality.ONE_D
        )


# --- link groups ------------------------------------------------------------


def test_one_group_per_nucleus_project_wide():
    p = Project()
    a, b = p.link_group_for("1H"), p.link_group_for("1H")
    assert a is b
    assert p.link_group_for("13C") is not a
    assert len(p.link_groups) == 2


def test_joining_two_blocks_on_the_same_nucleus_shares_the_group():
    p = Project()
    b1, b2 = block1d(1), block1d(1)
    assert p.join(b1, "1H").id == p.join(b2, "1H").id


def test_cannot_link_across_nuclei():
    p = Project()
    b = block1d(1)
    p.join(b, "1H")
    with pytest.raises(NucleusMismatch):
        p.join(b, "13C")


def test_rejoining_same_nucleus_is_idempotent():
    p = Project()
    b = block1d(1)
    first = p.join(b, "1H")
    assert p.join(b, "1H") is first


def test_link_group_rejects_ascending_range():
    g = LinkGroup(id=LinkGroupId("lg"), nucleus_x="1H")
    with pytest.raises(ValueError):
        g.set_x(6.0, 8.0)
    with pytest.raises(ValueError):
        g.set_x(5.0, 5.0)
    g.set_x(8.0, 6.0)
    assert g.x_limits == (8.0, 6.0)


# --- boxes ------------------------------------------------------------------


def test_box_rejects_zero_extent():
    with pytest.raises(ValueError):
        SpectrumBox(id=BoxId("x"), rect=(0.1, 0.1, 0.0, 0.5))
    with pytest.raises(ValueError):
        SpectrumBox(id=BoxId("x"), rect=(0.1, 0.1, 0.5, -0.2))


def test_project_box_lookup():
    p = Project()
    box = SpectrumBox(id=BoxId("b1"), rect=(0, 0, 1, 1), block=block1d(1))
    p.boxes.append(box)
    assert p.box(BoxId("b1")) is box
    with pytest.raises(KeyError):
        p.box(BoxId("missing"))


def test_blocks_skips_empty_and_legend_boxes():
    p = Project()
    p.boxes.append(SpectrumBox(id=BoxId("b1"), rect=(0, 0, 1, 0.5), block=block1d(1)))
    p.boxes.append(SpectrumBox(id=BoxId("b2"), rect=(0, 0.5, 1, 0.5)))
    p.boxes.append(LegendBox(id=BoxId("l1"), rect=(0.7, 0.8, 0.2, 0.1)))
    assert len(p.spectrum_boxes()) == 2
    assert len(p.blocks()) == 1


def test_figure_size_converts_to_inches():
    p = Project()
    w, h = p.figure.inches
    assert w == pytest.approx(8.5 / 2.54)
    assert h == pytest.approx(6.0 / 2.54)


# --- labels -----------------------------------------------------------------


def test_default_label_is_sample_slash_expno():
    t = LabelTemplate()
    assert t.render({"sample": "ABC-124", "expno": "11"}) == "ABC-124/11"


def test_missing_token_leaves_no_trailing_gap():
    t = LabelTemplate("{sample}/{expno} {title}")
    assert t.render({"sample": "ABC-124", "expno": "11"}) == "ABC-124/11"


def test_missing_token_leaves_no_dangling_separator():
    t = LabelTemplate("{sample} - {title}")
    assert t.render({"sample": "ABC-124"}) == "ABC-124"


def test_none_valued_token_renders_empty():
    t = LabelTemplate("{sample} {barcode}")
    assert t.render({"sample": "ABC", "barcode": None}) == "ABC"


def test_unknown_token_renders_empty_rather_than_raising():
    t = LabelTemplate("{sample} {nonsense}")
    assert t.render({"sample": "ABC"}) == "ABC"


def test_all_tokens_missing_yields_empty_string():
    assert LabelTemplate("{a} {b}").render({}) == ""


def test_metadata_tokens_render():
    t = LabelTemplate("{project} {fraction} {solvent}")
    assert (
        t.render({"project": "SampleB", "fraction": "FT2", "solvent": "CDCl3"})
        == "SampleB FT2 CDCl3"
    )


def test_non_ascii_survives():
    assert LabelTemplate("{sample}").render({"sample": "Müller-1 ±2°"}) == "Müller-1 ±2°"


def test_arrangement_includes_subtracted():
    assert Arrangement.SUBTRACTED.value == "subtracted"
    assert len(Arrangement) == 4
