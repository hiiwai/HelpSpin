"""Pseudo-2D loading for CEST and nutation calibration.

Like nmrglue_reader, nmrglue is imported lazily so that browsing costs
nothing. The Bruker-specific knowledge lives here and nowhere else; the panel
and the maths both work in plain arrays.

Why raw `ser` is the default source
-----------------------------------
`xf2` stores SI(F1) rows, and TopSpin defaults SI to a power of two. A CEST
series with TD(F1) = 42 offsets processed at SI(F1) = 32 therefore SILENTLY
loses the last ten rows -- observed on a real 19F dataset, where the missing
rows were the whole +1.95 to +3.19 ppm wing plus BOTH high-frequency I0
references. Nothing in the processed data says so; the 2rr is a valid
32-row file and pdata carries no record of the offsets it dropped.

So the reader treats the FQ1LIST as the authority on how many rows should
exist, reads `ser` by default because it always holds all of them, and only
uses `2rr` when its row count actually matches. Row counts that disagree
produce an explicit note rather than a truncated Z-spectrum.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..domain.cest import CestError, nutation_times, parse_fq_list
from ..domain.errors import DatasetNotFound

NUTATION_PROGRAMS = ("calib_nut", "nutation")
CEST_PROGRAMS = ("cest",)


@dataclass
class PseudoTwoD:
    """One pseudo-2D experiment, processed to real spectra with a ppm axis."""

    rows: np.ndarray                 # (n_rows, n_points) real
    ppm: np.ndarray                  # (n_points,) descending, Bruker order
    source: str                      # "ser" or "2rr"
    expected_rows: int               # from TD(F1) or the frequency list
    acqus: dict
    procs: dict
    sfo1_mhz: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def n_rows(self) -> int:
        return int(self.rows.shape[0])


def _ng():
    import nmrglue as ng

    return ng


def _first_float(values, index: int, default: float = 0.0) -> float:
    """acqus array entry as a float, tolerant of shortness and junk."""
    try:
        return float(values[index])
    except (TypeError, ValueError, IndexError, KeyError):
        return default


def classify(pulprog: str) -> str:
    """'nutation', 'cest' or 'unknown' from PULPROG.

    Matched on substrings because sites rename pulse programs freely -- the
    test data ships `19f_calib_nut.iw` and `19f_cest.iw`, renamed from the
    `.cw` originals. Matching the exact published names would have failed on
    the very datasets this was written for.
    """
    name = (pulprog or "").strip().strip("<>").lower()
    if any(token in name for token in NUTATION_PROGRAMS):
        return "nutation"
    if any(token in name for token in CEST_PROGRAMS):
        return "cest"
    return "unknown"


def find_fq_list(expno: Path) -> Path | None:
    """The FQ1LIST copied into the dataset, if TopSpin left one there.

    TopSpin 4 copies the lists an experiment used into <expno>/lists/f1/.
    That copy is authoritative -- the central list under the TopSpin
    installation may have been edited since acquisition, which would
    silently mislabel every offset.
    """
    expno = Path(expno)
    directory = expno / "lists" / "f1"
    if not directory.is_dir():
        return None
    named = None
    try:
        acqus = _read_acqus(expno / "acqus")
        wanted = str(acqus.get("FQ1LIST", "")).strip().strip("<>")
        if wanted:
            candidate = directory / wanted
            if candidate.is_file():
                return candidate
            named = wanted
    except (CestError, DatasetNotFound, OSError):
        pass
    files = sorted(p for p in directory.iterdir() if p.is_file())
    if named and files:
        # Case-insensitive retry: a list authored on Windows and read from a
        # case-sensitive share is a real and confusing failure.
        for candidate in files:
            if candidate.name.lower() == named.lower():
                return candidate
    return files[0] if files else None


def _read_acqus(path: Path) -> dict:
    from .nmrglue_reader import read_acqus

    return read_acqus(path)


def read_offsets(expno: Path, sfo1_mhz: float, bf1_mhz: float | None = None):
    """Saturation offsets in Hz, or None when no list is present."""
    path = find_fq_list(Path(expno))
    if path is None:
        return None, None
    raw = path.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:                                                 # pragma: no cover
        raise CestError(f"could not decode frequency list {path}")
    return parse_fq_list(text, sfo1_mhz=sfo1_mhz, bf1_mhz=bf1_mhz), path


def _ppm_axis(procs: dict, acqus: dict, size: int) -> np.ndarray:
    """Descending ppm axis from OFFSET and the sweep width.

    Built from procs rather than nmrglue's guess_udic because for a pseudo-2D
    the F1 udic is meaningless -- acqu2s reports NUC1 = 1H and OFFSET = 4.7
    even for a 19F nutation series, since nothing was ever transformed in F1.
    Only the direct dimension carries a real axis, and this constructs it
    explicitly rather than inheriting a wrong one.
    """
    sf = float(procs.get("SF", 0.0) or 0.0)
    offset = float(procs.get("OFFSET", 0.0) or 0.0)
    sw_hz = float(acqus.get("SW_h", 0.0) or 0.0)
    if sf <= 0 or sw_hz <= 0 or size <= 0:
        raise CestError(f"cannot build a ppm axis: SF={sf}, SW_h={sw_hz}, size={size}")
    return offset - np.arange(size, dtype=np.float64) * (sw_hz / sf) / size


def _process_ser(expno: Path, acqus: dict, procs: dict):
    """FT the raw serial file the way TopSpin's xf2 would.

    The phase convention is the fiddly part and was determined by measurement
    rather than assumed: reproducing TopSpin's own 2rr from the same ser
    requires reversing BEFORE phasing, negating PHC1, and adding 180 degrees
    to PHC0. Correlation against the shipped 2rr is +0.9957; the three wrong
    combinations give +0.24, +0.66 and -0.32, and one of them (-0.9957) is
    the right curve inverted, which would flip a Z-spectrum upside down
    while looking entirely plausible.
    """
    ng = _ng()
    dic, data = ng.bruker.read(str(expno))
    data = np.atleast_2d(np.asarray(data))
    if data.ndim != 2:                                    # pragma: no cover
        raise CestError(f"expected a pseudo-2D ser at {expno}, got {data.ndim}D")

    size = int(float(procs.get("SI", 0) or 0))
    if not size:
        size = int(2 ** np.ceil(np.log2(data.shape[1])))
    sw_hz = float(acqus.get("SW_h", 0.0) or 0.0)
    lb = float(procs.get("LB", 0.0) or 0.0)

    work = ng.bruker.remove_digital_filter(dic, data)
    if lb and sw_hz > 0:
        work = ng.proc_base.em(work, lb=lb / sw_hz)
    if size >= work.shape[1]:
        work = ng.proc_base.zf_size(work, size)
    work = ng.proc_base.fft(work)
    work = ng.proc_base.rev(work)
    work = ng.proc_base.ps(
        work,
        p0=float(procs.get("PHC0", 0.0) or 0.0) + 180.0,
        p1=-float(procs.get("PHC1", 0.0) or 0.0),
    )
    return np.asarray(work.real, dtype=np.float64)


def _read_2rr(expno: Path, procno: int):
    ng = _ng()
    pdata = Path(expno) / "pdata" / str(procno)
    if not pdata.is_dir():
        return None
    dic, data = ng.bruker.read_pdata(str(pdata))
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 2 or data.size == 0:
        return None
    return dic, data


def load_pseudo_2d(
    expno: str | Path, *, procno: int = 1, prefer: str = "auto"
) -> PseudoTwoD:
    """Load a pseudo-2D experiment, choosing the source that has every row.

    prefer is "auto" (ser unless 2rr is complete and agrees), "ser", or
    "2rr". "auto" is the default because a truncated 2rr is silent: it is a
    perfectly well-formed file that simply has fewer offsets than were
    acquired.
    """
    expno = Path(expno)
    if not expno.is_dir():
        raise DatasetNotFound(f"no such experiment directory: {expno}")
    if prefer not in {"auto", "ser", "2rr"}:
        raise CestError(f"unknown source preference {prefer!r}")

    acqus = _read_acqus(expno / "acqus")
    sfo1 = float(acqus.get("SFO1", 0.0) or 0.0)
    if sfo1 <= 0:
        raise CestError(f"SFO1 missing or non-positive in {expno / 'acqus'}")

    pdata = _read_2rr(expno, procno)
    if pdata is None:
        procs: dict = {}
        rows_2rr = None
    else:
        procs = pdata[0].get("procs", {}) or {}
        rows_2rr = pdata[1]

    # How many rows SHOULD there be? The frequency list wins when present,
    # because it is what the pulse program actually stepped through; TD(F1)
    # is the fallback.
    expected = 0
    try:
        acqu2s = _read_acqus(expno / "acqu2s")
        expected = int(float(acqu2s.get("TD", 0) or 0))
    except (DatasetNotFound, OSError, ValueError):
        pass
    offsets = None
    try:
        bf1 = float(acqus.get("BF1", 0.0) or 0.0) or None
        offsets, _ = read_offsets(expno, sfo1, bf1)
    except CestError:
        offsets = None
    if offsets is not None and offsets.size:
        expected = int(offsets.size)

    notes: list[str] = []
    if not procs and rows_2rr is None:
        # No pdata at all: processing parameters have to be guessed, but a
        # ser can still be transformed.
        procs = {"SI": 0, "PHC0": 0.0, "PHC1": 0.0, "LB": 0.0,
                 "SF": float(acqus.get("BF1", 0.0) or 0.0), "OFFSET": 0.0}
        notes.append(
            "no pdata found; processed with default phase and no reference offset"
        )

    # SI(F1) > TD(F1) is the HEALTHY case, not a fault: xf2 stores SI rows and
    # pads the surplus with zeros, so setting SI = 64 for 42 offsets keeps
    # every real row. Those trailing zeros must be trimmed rather than
    # normalised as if they were measurements -- an all-zero row divided by I0
    # would plot as a spurious total-saturation point at whatever offset the
    # frequency list happened to run out at. Only SI < TD loses data.
    padded = False
    if rows_2rr is not None and expected and rows_2rr.shape[0] > expected:
        surplus = rows_2rr[expected:]
        if not np.any(surplus):
            rows_2rr = rows_2rr[:expected]
            padded = True
        else:
            notes.append(
                f"pdata has {rows_2rr.shape[0]} rows against {expected} "
                f"expected, and the surplus is not blank -- using raw ser"
            )

    truncated = (
        rows_2rr is not None and expected and rows_2rr.shape[0] < expected
    )
    complete = (
        rows_2rr is not None and expected and rows_2rr.shape[0] == expected
    )

    use_ser = prefer == "ser"
    if prefer == "auto":
        use_ser = not complete
    elif prefer == "2rr":
        if rows_2rr is None:
            raise CestError(f"no processed 2rr under {expno / 'pdata' / str(procno)}")
        use_ser = False

    if padded:
        notes.append(
            f"pdata was zero-filled to {expected + surplus.shape[0]} rows; "
            f"trimmed the {surplus.shape[0]} blank trailing row(s)"
        )
    if truncated:
        notes.append(
            f"pdata has only {rows_2rr.shape[0]} of {expected} rows "
            f"(SI in F1 is smaller than TD, so xf2 discarded the rest) -- "
            + (
                "using raw ser instead"
                if use_ser
                else "PROCESSED DATA IS TRUNCATED; later offsets are missing"
            )
        )

    if use_ser:
        if not (expno / "ser").is_file():
            if rows_2rr is None:
                raise DatasetNotFound(f"neither ser nor 2rr under {expno}")
            rows, source = rows_2rr, "2rr"
            notes.append("no ser file; fell back to processed data")
        else:
            rows, source = _process_ser(expno, acqus, procs), "ser"
    else:
        rows, source = rows_2rr, "2rr"

    if rows is None or rows.size == 0:                    # pragma: no cover
        raise CestError(f"no usable data in {expno}")

    ppm = _ppm_axis(procs, acqus, rows.shape[1])
    return PseudoTwoD(
        rows=rows, ppm=ppm, source=source,
        expected_rows=expected or int(rows.shape[0]),
        acqus=acqus, procs=procs, sfo1_mhz=sfo1, notes=tuple(notes),
    )


def nutation_axis(data: PseudoTwoD) -> np.ndarray:
    """Pulse durations in seconds for a loaded nutation experiment."""
    p_array = data.acqus.get("P", [])
    inp_array = data.acqus.get("INP", [])
    p9 = _first_float(p_array, 9)
    inp9 = _first_float(inp_array, 9)
    if inp9 <= 0:
        # Fall back to the pulse program's own relation, p9 = inp9 = p8/2,
        # for a dataset whose INP array did not survive a conversion.
        p8 = _first_float(p_array, 8)
        if p8 > 0:
            p9 = p9 or p8 * 0.5
            inp9 = p8 * 0.5
    return nutation_times(p9, inp9, data.n_rows)


def nominal_nutation_field(data: PseudoTwoD) -> float:
    """CNST8 if it is set, else derived from P8."""
    cnst8 = _first_float(data.acqus.get("CNST", []), 8)
    if cnst8 > 0:
        return cnst8
    p8 = _first_float(data.acqus.get("P", []), 8)
    return 1e6 / (4.0 * p8) if p8 > 0 else 0.0
