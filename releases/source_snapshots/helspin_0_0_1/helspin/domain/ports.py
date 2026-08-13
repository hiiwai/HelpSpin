"""Interfaces the domain defines and infrastructure implements.

These are what isolate nmrglue and matplotlib. A future ParaVision reader, or a
vendored copy of nmrglue's Bruker module, plugs in here and nothing else changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .project import Dimensionality
from .spectrum import Spectrum1D, Spectrum2D


@dataclass(frozen=True)
class DatasetInfo:
    """Enough to populate a browser row without loading array data.

    Cheap to produce: reading acqus is a text-file parse, not an array load.
    """

    path: Path
    expno: int
    procno: int
    dimensionality: Dimensionality
    nucleus: str = ""
    pulse_program: str = ""
    solvent: str = ""
    date: str = ""
    ns: int = 1
    rg: float = 1.0
    title: str | None = None
    holder: str | None = None
    barcode: str | None = None
    acqus: dict = field(default_factory=dict)


@dataclass
class SampleNamePattern:
    """User-configurable regex over the sample directory name.

    Named capture groups become label tokens ({project}, {fraction}, ...)
    alongside the built-in ones. A non-matching name is normal, not an error:
    all groups render empty and the sample still appears under its raw name.
    """

    regex: str = ""
    enabled: bool = False

    def group_names(self) -> list[str]:
        if not self.regex:
            return []
        try:
            return list(re.compile(self.regex).groupindex.keys())
        except re.error:
            return []

    def parse(self, sample_dir_name: str) -> dict[str, str]:
        if not self.enabled or not self.regex:
            return {}
        try:
            pattern = re.compile(self.regex)
        except re.error:
            return {}
        match = pattern.match(sample_dir_name)
        return match.groupdict() if match else {}

    def validate(self) -> str | None:
        """Return an error message, or None if the regex compiles."""
        if not self.regex:
            return None
        try:
            re.compile(self.regex)
        except re.error as exc:
            return str(exc)
        return None


@dataclass
class DataRoot:
    """One data root in preferences: the 600, the 400, an archive volume.

    A list of these, not a single path -- different instruments often serve
    different groups with different naming conventions.
    """

    name: str
    path: Path
    enabled: bool = True
    name_pattern: SampleNamePattern = field(default_factory=SampleNamePattern)
    barcode_key: str | None = None   # acqus key; None => auto-detect
    default_procno: int = 1
    show_2d: bool = True


class DatasetInfoProtocol(Protocol):
    """Structural shape a DatasetInfo satisfies; kept for typing call sites
    that should not depend on the concrete dataclass."""

    path: Path
    expno: int
    procno: int
    dimensionality: Dimensionality
    nucleus: str
    pulse_program: str
    solvent: str


@runtime_checkable
class SpectrumReader(Protocol):
    def can_read(self, path: Path) -> bool: ...

    def probe(self, path: Path, procno: int = 1) -> DatasetInfo:
        """Cheap metadata read for browser rows. Must not load arrays."""
        ...

    def read_1d(self, path: Path, procno: int = 1) -> Spectrum1D: ...

    def read_2d(self, path: Path, procno: int = 1) -> Spectrum2D: ...


@runtime_checkable
class FigureExporter(Protocol):
    def export(self, figure, spec, destination: Path) -> Path: ...
