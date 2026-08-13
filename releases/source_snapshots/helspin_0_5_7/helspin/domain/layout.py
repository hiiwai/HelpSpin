"""Turning the New Figure dialog into a laid-out project.

The simple path. Two numbers and a radio button produce a complete figure:
boxes generated with consistent margins, slots coloured and numbered, ready for
drops. A user need never touch the box canvas.

Box rects are figure fractions, mapping straight onto matplotlib's
fig.add_axes([left, bottom, width, height]).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .project import (
    Arrangement,
    Block1D,
    Block2D,
    BlockId,
    BoxId,
    DifferenceBox,
    DifferenceSlot,
    FigureSize,
    LegendBox,
    Project,
    SlotId,
    SpectrumBox,
    add_slot_1d,
    add_slot_2d,
    new_id,
    next_colour,
)

# Figure-fraction margins. Left is widest to leave room for the y axis when
# it is shown; bottom leaves room for the ppm axis label.
MARGIN_LEFT = 0.12
MARGIN_RIGHT = 0.03
MARGIN_TOP = 0.06
MARGIN_BOTTOM = 0.12
GUTTER = 0.06

# A difference panel takes the lower third of its source box's height.
DIFFERENCE_FRACTION = 1.0 / 3.0


@dataclass(frozen=True)
class BlockSpec:
    """One comparison group as requested in the dialog."""

    count: int
    arrangement: Arrangement
    is_2d: bool = False
    title: str = ""


@dataclass(frozen=True)
class NewFigureRequest:
    """Exactly what the New Figure dialog collects."""

    count_1d: int = 0
    arrangement_1d: Arrangement = Arrangement.OVERLAY
    count_2d: int = 0
    arrangement_2d: Arrangement = Arrangement.TILED
    figure: FigureSize = field(default_factory=FigureSize)
    legend_box: bool = False

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.count_1d < 0 or self.count_2d < 0:
            problems.append("Spectrum counts cannot be negative.")
        if self.count_1d == 0 and self.count_2d == 0:
            problems.append("Add at least one 1D or 2D spectrum.")
        if self.arrangement_1d is Arrangement.SUBTRACTED and self.count_1d < 2:
            problems.append("A difference needs at least two 1D spectra.")
        if self.arrangement_2d is Arrangement.SUBTRACTED:
            problems.append("2D differences are not supported yet.")
        if self.arrangement_2d is Arrangement.STACKED:
            problems.append("2D spectra cannot be stacked; use overlay or tiled.")
        return problems


def stack_rects(n: int, with_difference: list[bool] | None = None):
    """Split the drawing area into n vertically stacked rows.

    Rows that carry a difference panel are subdivided in place, so adding a
    difference never displaces the other boxes.
    """
    if n < 1:
        return []
    if with_difference is None:
        with_difference = [False] * n

    area_left = MARGIN_LEFT
    area_width = 1.0 - MARGIN_LEFT - MARGIN_RIGHT
    area_bottom = MARGIN_BOTTOM
    area_height = 1.0 - MARGIN_TOP - MARGIN_BOTTOM

    total_gutter = GUTTER * (n - 1)
    row_height = (area_height - total_gutter) / n

    rows = []
    for i in range(n):
        # Row 0 at the top, matching reading order.
        bottom = area_bottom + (n - 1 - i) * (row_height + GUTTER)
        if with_difference[i]:
            diff_height = row_height * DIFFERENCE_FRACTION
            source_height = row_height - diff_height
            rows.append(
                (
                    (area_left, bottom + diff_height, area_width, source_height),
                    (area_left, bottom, area_width, diff_height),
                )
            )
        else:
            rows.append(((area_left, bottom, area_width, row_height), None))
    return rows


def _panel_count(spec: BlockSpec) -> int:
    """How many rows a block occupies."""
    if spec.arrangement is Arrangement.TILED:
        return max(1, spec.count)
    return 1


def build_project(request: NewFigureRequest) -> Project:
    """Materialise a request into a complete, empty, laid-out project."""
    problems = request.validate()
    if problems:
        raise ValueError("; ".join(problems))

    specs: list[BlockSpec] = []
    if request.count_1d > 0:
        specs.append(BlockSpec(request.count_1d, request.arrangement_1d, is_2d=False))
    if request.count_2d > 0:
        specs.append(BlockSpec(request.count_2d, request.arrangement_2d, is_2d=True))

    # One row per panel, plus a difference row where requested.
    rows_needed: list[bool] = []
    for spec in specs:
        panels = _panel_count(spec)
        wants_difference = spec.arrangement is Arrangement.SUBTRACTED
        for panel_index in range(panels):
            rows_needed.append(wants_difference and panel_index == 0)

    rects = stack_rects(len(rows_needed), rows_needed)
    project = Project(figure=request.figure)
    palette = tuple(project.palette)

    row = 0
    for spec in specs:
        if spec.is_2d:
            block = Block2D(id=BlockId(new_id("b")), arrangement=spec.arrangement)
            for _ in range(spec.count):
                add_slot_2d(block, palette)
        else:
            block = Block1D(id=BlockId(new_id("b")), arrangement=spec.arrangement)
            for _ in range(spec.count):
                add_slot_1d(block, palette)
            if spec.arrangement is Arrangement.SUBTRACTED:
                block.difference = _wire_difference(block, palette)

        panels = _panel_count(spec)
        for panel_index in range(panels):
            source_rect, diff_rect = rects[row]
            box = SpectrumBox(
                id=BoxId(new_id("box")), rect=source_rect, block=block
            )
            project.boxes.append(box)
            if diff_rect is not None:
                project.boxes.append(
                    DifferenceBox(
                        id=BoxId(new_id("box")), rect=diff_rect, source_box=box.id
                    )
                )
            row += 1

    if request.legend_box:
        project.boxes.append(
            LegendBox(
                id=BoxId(new_id("box")),
                rect=(0.70, 0.78, 0.22, 0.14),
                z=1,
                sources=[b.id for b in project.spectrum_boxes()],
            )
        )
    return project


def _wire_difference(block: Block1D, palette) -> DifferenceSlot:
    """A - B on the first two slots.

    Auto-wired because with exactly two slots there is only one sensible
    interpretation. Prompting there would be a dialog with one answer.
    """
    used = [s.color for s in block.slots]
    return DifferenceSlot(
        id=SlotId(new_id("s")),
        color=next_colour(used, palette),
        minuend=block.slots[0].id,
        subtrahend=block.slots[1].id,
    )


def can_add_difference(block) -> tuple[bool, str]:
    """Whether 'Add difference panel' should be enabled, and why not.

    Returning the reason lets the UI put it in a tooltip rather than leaving a
    greyed control unexplained.
    """
    if isinstance(block, Block2D):
        return False, "2D differences are not supported yet."
    if block is None:
        return False, "Select a panel first."
    if block.difference is not None:
        return False, "This panel already has a difference."
    filled = len(block.filled_slots)
    if filled < 2:
        return False, f"Needs two filled spectra; this panel has {filled}."
    return True, ""


def add_difference_to(project: Project, box_id: BoxId) -> DifferenceBox:
    """Split a source box in place and attach a difference panel.

    Splitting rather than reflowing means the rest of the figure does not move.
    Undo restores the original rect exactly, which is why the old rect is
    returned to the caller through the command layer.
    """
    box = project.box(box_id)
    if not isinstance(box, SpectrumBox) or box.block is None:
        raise ValueError("difference panels attach to spectrum boxes only")
    ok, reason = can_add_difference(box.block)
    if not ok:
        raise ValueError(reason)

    left, bottom, width, height = box.rect
    diff_height = height * DIFFERENCE_FRACTION
    box.rect = (left, bottom + diff_height, width, height - diff_height)
    box.block.difference = _wire_difference(box.block, tuple(project.palette))

    diff_box = DifferenceBox(
        id=BoxId(new_id("box")),
        rect=(left, bottom, width, diff_height),
        source_box=box.id,
    )
    project.boxes.append(diff_box)
    return diff_box
