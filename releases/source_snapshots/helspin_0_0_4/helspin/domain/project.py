"""The document model.

Two-store design: this module holds only the light, serialisable, undoable
document. Heavy numpy arrays live in the DatasetStore and are referenced by id.

The two rules that matter most here:

1.  Slot count is DYNAMIC. The number typed in the New Figure dialog is a
    starting point, not a constraint.
2.  Colour binds to the slot INSTANCE, not to its index. Deleting slot 2 must
    leave slots 1, 3 and 4 exactly as they were.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import NewType

from .errors import DimensionalityMismatch, NucleusMismatch, SlotNotFound

DatasetId = NewType("DatasetId", str)
SlotId = NewType("SlotId", str)
BlockId = NewType("BlockId", str)
BoxId = NewType("BoxId", str)
LinkGroupId = NewType("LinkGroupId", str)

_counter = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}{next(_counter)}"


# Okabe-Ito: distinguishable under the common forms of colour blindness.
DEFAULT_PALETTE: tuple[str, ...] = (
    "#000000",
    "#E69F00",
    "#56B4E9",
    "#009E73",
    "#F0E442",
    "#0072B2",
    "#D55E00",
    "#CC79A7",
)


class Arrangement(Enum):
    OVERLAY = "overlay"
    STACKED = "stacked"
    TILED = "tiled"
    SUBTRACTED = "subtracted"


class YScaleMode(Enum):
    ABSOLUTE = "absolute"        # raw / (NS * RG)
    MAX = "max"
    IDENTICAL_SNR = "snr"
    REFERENCE = "reference"


class LegendPosition(Enum):
    """Fixed anchors only.

    matplotlib's automatic placement is banned: it moves the legend when the
    data changes, so a series of figures for one paper comes out inconsistent.
    See tests/test_architecture.py for the gate that enforces this.
    """

    NONE = "none"
    UPPER_RIGHT = "upper right"
    UPPER_LEFT = "upper left"
    LOWER_RIGHT = "lower right"
    LOWER_LEFT = "lower left"
    OUTSIDE_RIGHT = "outside right"


class Dimensionality(Enum):
    ONE_D = 1
    TWO_D = 2


# --- settings ---------------------------------------------------------------


@dataclass
class LegendSettings:
    position: LegendPosition = LegendPosition.UPPER_RIGHT
    font_size_pt: float = 7.0
    frame: bool = False
    max_label_chars: int = 28


@dataclass
class LabelTemplate:
    """Default label is {sample}/{expno}, e.g. ABC-124/11.

    Missing tokens render empty and whitespace is then collapsed, so a template
    referencing {title} on a dataset without one yields 'ABC-124/11', not
    'ABC-124/11 ' with a trailing gap.
    """

    pattern: str = "{sample}/{expno}"

    BUILTIN_TOKENS = frozenset(
        {
            "sample",
            "expno",
            "procno",
            "title",
            "nucleus",
            "solvent",
            "holder",
            "barcode",
            "index",
        }
    )

    def render(self, fields: dict[str, str]) -> str:
        import re

        def sub(match: "re.Match[str]") -> str:
            return str(fields.get(match.group(1), "") or "")

        text = re.sub(r"\{(\w+)\}", sub, self.pattern)
        # Collapse runs of whitespace, and tidy separators left dangling by an
        # empty token ("ABC/11 - " -> "ABC/11").
        text = re.sub(r"\s+", " ", text).strip()
        return text.strip(" -/,;")


@dataclass
class ContourSettings:
    base_level: float
    factor: float = 1.3
    count: int = 12
    show_negative: bool = True


# --- slots ------------------------------------------------------------------


@dataclass
class Slot1D:
    id: SlotId
    color: str
    dataset_id: DatasetId | None = None
    label_override: str | None = None
    alpha: float = 1.0
    line_width: float = 1.0
    shift_ppm: float = 0.0
    scale: float = 1.0
    visible: bool = True

    @property
    def is_filled(self) -> bool:
        return self.dataset_id is not None


@dataclass
class Slot2D:
    id: SlotId
    color_positive: str
    color_negative: str
    dataset_id: DatasetId | None = None
    label_override: str | None = None
    alpha: float = 1.0
    line_width: float = 0.6
    shift_f1_ppm: float = 0.0
    shift_f2_ppm: float = 0.0
    contours: ContourSettings | None = None
    visible: bool = True

    @property
    def is_filled(self) -> bool:
        return self.dataset_id is not None


@dataclass
class DifferenceSlot:
    """A derived slot: A - k*B, referencing two sibling slots by id."""

    id: SlotId
    color: str
    minuend: SlotId
    subtrahend: SlotId
    k: float = 1.0
    label_override: str | None = None
    visible: bool = True


Slot = Slot1D | Slot2D


# --- blocks -----------------------------------------------------------------


@dataclass
class Block1D:
    id: BlockId
    title: str = ""
    arrangement: Arrangement = Arrangement.OVERLAY
    slots: list[Slot1D] = field(default_factory=list)
    difference: DifferenceSlot | None = None
    link_group: LinkGroupId | None = None
    stack_spacing: float = 0.0
    y_scale: YScaleMode = YScaleMode.MAX
    noise_window_ppm: tuple[float, float] | None = None
    legend: LegendSettings = field(default_factory=LegendSettings)
    label_template: LabelTemplate = field(default_factory=LabelTemplate)

    dimensionality = Dimensionality.ONE_D

    @property
    def filled_slots(self) -> list[Slot1D]:
        return [s for s in self.slots if s.is_filled]

    @property
    def visible_slots(self) -> list[Slot1D]:
        return [s for s in self.slots if s.is_filled and s.visible]

    def slot(self, slot_id: SlotId) -> Slot1D:
        for s in self.slots:
            if s.id == slot_id:
                return s
        raise SlotNotFound(slot_id)

    def index_of(self, slot_id: SlotId) -> int:
        for i, s in enumerate(self.slots):
            if s.id == slot_id:
                return i
        raise SlotNotFound(slot_id)


@dataclass
class Block2D:
    id: BlockId
    title: str = ""
    arrangement: Arrangement = Arrangement.TILED
    slots: list[Slot2D] = field(default_factory=list)
    link_group: LinkGroupId | None = None
    legend: LegendSettings = field(default_factory=LegendSettings)
    label_template: LabelTemplate = field(default_factory=LabelTemplate)
    panel_titles: bool = True

    dimensionality = Dimensionality.TWO_D

    @property
    def filled_slots(self) -> list[Slot2D]:
        return [s for s in self.slots if s.is_filled]

    @property
    def visible_slots(self) -> list[Slot2D]:
        return [s for s in self.slots if s.is_filled and s.visible]

    def slot(self, slot_id: SlotId) -> Slot2D:
        for s in self.slots:
            if s.id == slot_id:
                return s
        raise SlotNotFound(slot_id)


Block = Block1D | Block2D


# --- colour assignment ------------------------------------------------------


def next_colour(used: list[str], palette: tuple[str, ...] | list[str]) -> str:
    """Lowest palette entry not currently in use; cycles when exhausted.

    Colour binds to the slot instance for life, so this is called once at
    creation and never recomputed from an index.
    """
    if not palette:
        raise ValueError("palette is empty")
    for colour in palette:
        if colour not in used:
            return colour
    return palette[len(used) % len(palette)]


def add_slot_1d(
    block: Block1D, palette: tuple[str, ...] | list[str] = DEFAULT_PALETTE
) -> Slot1D:
    used = [s.color for s in block.slots]
    slot = Slot1D(id=SlotId(new_id("s")), color=next_colour(used, palette))
    block.slots.append(slot)
    return slot


def add_slot_2d(
    block: Block2D, palette: tuple[str, ...] | list[str] = DEFAULT_PALETTE
) -> Slot2D:
    used = [s.color_positive for s in block.slots]
    positive = next_colour(used, palette)
    slot = Slot2D(
        id=SlotId(new_id("s")),
        color_positive=positive,
        color_negative=positive,
    )
    block.slots.append(slot)
    return slot


# --- link groups ------------------------------------------------------------


@dataclass
class LinkGroup:
    """Authoritative axis limits, shared by every panel that references it.

    Default scope is one group per nucleus for the whole project, so setting the
    range once moves everything -- "the same region, for all of them".
    """

    id: LinkGroupId
    nucleus_x: str
    nucleus_y: str | None = None
    x_limits: tuple[float, float] | None = None
    y_limits: tuple[float, float] | None = None
    link_intensity: bool = True

    def set_x(self, left: float, right: float) -> None:
        if left <= right:
            raise ValueError(
                f"ppm range must be left > right (descending), got {left} to {right}"
            )
        self.x_limits = (left, right)


# --- boxes ------------------------------------------------------------------


@dataclass
class Box:
    """Positioned in figure-fraction coordinates, mapping straight onto
    matplotlib's fig.add_axes([left, bottom, width, height])."""

    id: BoxId
    rect: tuple[float, float, float, float]
    z: int = 0

    def __post_init__(self) -> None:
        _, _, w, h = self.rect
        if w <= 0 or h <= 0:
            raise ValueError(f"box must have positive extent, got {self.rect}")


@dataclass
class SpectrumBox(Box):
    block: Block | None = None


@dataclass
class DifferenceBox(Box):
    """Renders the difference slot of its source block."""

    source_box: BoxId | None = None


@dataclass
class LegendBox(Box):
    sources: list[BoxId] = field(default_factory=list)


@dataclass
class FigureSize:
    """Background setting. Defaults to single column; users should be able to
    ignore it entirely."""

    width_cm: float = 8.5
    height_cm: float = 6.0
    dpi: int = 300

    @property
    def inches(self) -> tuple[float, float]:
        return (self.width_cm / 2.54, self.height_cm / 2.54)


@dataclass
class Project:
    schema_version: int = 1
    boxes: list[Box] = field(default_factory=list)
    link_groups: dict[LinkGroupId, LinkGroup] = field(default_factory=dict)
    figure: FigureSize = field(default_factory=FigureSize)
    palette: list[str] = field(default_factory=lambda: list(DEFAULT_PALETTE))

    def box(self, box_id: BoxId) -> Box:
        for b in self.boxes:
            if b.id == box_id:
                return b
        raise KeyError(box_id)

    def spectrum_boxes(self) -> list[SpectrumBox]:
        return [b for b in self.boxes if isinstance(b, SpectrumBox)]

    def blocks(self) -> list[Block]:
        return [b.block for b in self.spectrum_boxes() if b.block is not None]

    # -- link groups --------------------------------------------------------

    def link_group_for(self, nucleus: str) -> LinkGroup:
        """One group per nucleus, created lazily on first fill."""
        for group in self.link_groups.values():
            if group.nucleus_x == nucleus:
                return group
        group = LinkGroup(id=LinkGroupId(new_id("lg")), nucleus_x=nucleus)
        self.link_groups[group.id] = group
        return group

    def join(self, block: Block, nucleus: str) -> LinkGroup:
        group = self.link_group_for(nucleus)
        if block.link_group is not None and block.link_group != group.id:
            existing = self.link_groups.get(block.link_group)
            if existing is not None and existing.nucleus_x != nucleus:
                raise NucleusMismatch(
                    f"cannot link {existing.nucleus_x} panel to {nucleus}: "
                    "a shared ppm window across different nuclei is meaningless"
                )
        block.link_group = group.id
        return group


def assert_same_dimensionality(block: Block, dim: Dimensionality) -> None:
    """1D and 2D never share a block. Checked at dragEnter so the user gets a
    'no drop' cursor rather than a runtime error."""
    if block.dimensionality is not dim:
        raise DimensionalityMismatch(
            f"cannot place {dim.name} data in a {block.dimensionality.name} block"
        )
