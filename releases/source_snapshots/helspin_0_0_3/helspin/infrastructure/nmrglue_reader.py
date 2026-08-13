"""nmrglue-backed dataset reader.

Only nmrglue lives in this module (plus numpy for read_1d/read_2d later) --
everything else, including the browser and the drag/drop payload, depends on
domain.ports.SpectrumReader, never on this file directly. Swapping or
vendoring nmrglue means editing here and nowhere else.

Scope for this slice: `probe()` only, which is all the browser needs. It reads
acqus as a text parse -- cheap, no array load -- and is what populates browser
rows and drag payloads. `read_1d`/`read_2d` (the actual FID/pdata array load)
belong to the rendering milestone and are stubbed here with a clear marker
rather than guessed at.
"""

from __future__ import annotations

from pathlib import Path

import nmrglue as ng

from ..domain.errors import DatasetNotFound, UnsupportedDimension
from ..domain.metadata import read_title, resolve_barcode, strip_jcamp
from ..domain.paths import is_expno, procnos_in
from ..domain.project import Dimensionality
from ..domain.ports import DataRoot, DatasetInfo

_JCAMP_ENCODINGS = ("utf-8", "latin-1")


def read_acqus(path: Path) -> dict:
    """acqus/acqu2s/acqu3s as a dict, tolerant of encoding.

    nmrglue's read_jcamp defaults to the system locale encoding and raises
    UnicodeDecodeError on a non-ASCII byte outside that encoding -- the same
    trap as the title file (domain.metadata.read_title), just one level
    lower in the stack. Retried under latin-1, which accepts any byte
    sequence, so this never raises on encoding grounds.
    """
    last_error: Exception | None = None
    for encoding in _JCAMP_ENCODINGS:
        try:
            return ng.bruker.read_jcamp(str(path), encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise DatasetNotFound(f"could not decode {path}: {last_error}")


def _dimensionality(expno: Path) -> Dimensionality:
    """1D vs 2D from the files actually present, not from a guess.

    A ser file plus acqu2s is 2D; a bare fid is 1D. Higher dimensionality is
    rejected explicitly rather than silently mis-plotted.
    """
    if (expno / "acqu3s").is_file():
        raise UnsupportedDimension(f"{expno} is 3D or higher, not supported")
    if (expno / "ser").is_file() and (expno / "acqu2s").is_file():
        return Dimensionality.TWO_D
    if (expno / "fid").is_file():
        return Dimensionality.ONE_D
    raise DatasetNotFound(f"{expno} has neither fid nor ser")


def _epoch_to_date(value) -> str:
    """acqus DATE is a Unix epoch integer. Best-effort only -- used for
    browser sorting, not for anything safety-critical, so any failure just
    yields an empty string rather than propagating."""
    try:
        import datetime

        return datetime.datetime.fromtimestamp(int(value)).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


class NmrglueReader:
    """The only SpectrumReader implementation in scope so far.

    Satisfies domain.ports.SpectrumReader structurally (a Protocol, so no
    explicit inheritance is required).
    """

    def can_read(self, path: Path) -> bool:
        return is_expno(path)

    def probe(
        self,
        path: Path,
        procno: int = 1,
        data_root: DataRoot | None = None,
    ) -> DatasetInfo:
        """Cheap metadata read for a browser row. Never loads array data."""
        if not is_expno(path):
            raise DatasetNotFound(f"{path} is not a Bruker expno")

        acqus = read_acqus(path / "acqus")
        dimensionality = _dimensionality(path)

        configured_key = data_root.barcode_key if data_root else None
        barcode = resolve_barcode(acqus, configured_key)

        title_path = path / "pdata" / str(procno) / "title"
        title = read_title(title_path)

        return DatasetInfo(
            path=path,
            expno=int(path.name),
            procno=procno,
            dimensionality=dimensionality,
            nucleus=strip_jcamp(acqus.get("NUC1")) or "",
            pulse_program=strip_jcamp(acqus.get("PULPROG")) or "",
            solvent=strip_jcamp(acqus.get("SOLVENT")) or "",
            date=_epoch_to_date(acqus.get("DATE")),
            ns=int(acqus.get("NS", 1) or 1),
            rg=float(acqus.get("RG", 1.0) or 1.0),
            title=title,
            holder=strip_jcamp(acqus.get("HOLDER")),
            barcode=barcode,
            acqus=acqus,
        )

    def probe_procnos(self, expno: Path) -> list[int]:
        """Procnos available for an expno, for the (rare) case of >1."""
        return [int(p.name) for p in procnos_in(expno)]

    def read_1d(self, path: Path, procno: int = 1):
        raise NotImplementedError(
            "1D array loading is part of the rendering milestone, not the "
            "browser slice. probe() is sufficient for browsing and drag/drop."
        )

    def read_2d(self, path: Path, procno: int = 1):
        raise NotImplementedError(
            "2D array loading is part of the rendering milestone, not the "
            "browser slice."
        )
