"""Acquisition times from Bruker parameters: AQ and t1max.

    AQ    = TD / (2 * SW_h)                    direct dimension (acqus)
    t1max = TD1 / (2 * SW_h1)                  indirect dimension (acqu2s),
            for complex F1 acquisition (QSEQ, TPPI, States, States-TPPI,
            echo-antiecho: FnMODE 2-6), and
          = TD1 / SW_h1                        for QF (FnMODE 1)

TD counts REAL points (TopSpin's convention), SW_h is in Hz, results are in
seconds. FnMODE 0 ("undefined") is what pseudo-2D series (relaxation, CEST,
titration planes) carry: F1 there is a list, not a time, so there is no
t1max and 0 is returned. Data from TopSpin versions without FnMODE are taken
as complex acquisition, by far the common case.
"""

from __future__ import annotations

from pathlib import Path

_KEYS = ("TD", "SW_h", "FnMODE")


def _number(value) -> float:
    try:
        return float(str(value).strip().strip("<>"))
    except (TypeError, ValueError):
        return 0.0


def aq_seconds(acqus: dict) -> float:
    """AQ from acqus (TD, SW_h); 0.0 when either is missing or not positive."""
    td, sw = _number(acqus.get("TD")), _number(acqus.get("SW_h"))
    return td / (2.0 * sw) if td > 0 and sw > 0 else 0.0


def t1max_seconds(acqu2s: dict) -> float:
    """t1max from acqu2s (TD, SW_h, FnMODE); 0.0 when it does not apply."""
    td, sw = _number(acqu2s.get("TD")), _number(acqu2s.get("SW_h"))
    if td <= 0 or sw <= 0:
        return 0.0
    if "FnMODE" in acqu2s:
        mode = int(_number(acqu2s.get("FnMODE")))
        if mode == 0:
            return 0.0          # pseudo-2D: F1 is a list, not a time
        if mode == 1:
            return td / sw      # QF: every increment is one complex point
    return td / (2.0 * sw)


def read_timing_keys(path) -> dict:
    """TD, SW_h and FnMODE from one acqus/acqu2s file -- a line scan, not a
    full JCAMP parse (this runs for every 2D row in the explorer)."""
    out: dict = {}
    raw = Path(path).read_bytes()
    text = raw.decode("latin-1")
    for line in text.splitlines():
        if not line.startswith("##$"):
            continue
        key, separator, value = line[3:].partition("=")
        if separator and key in _KEYS:
            out[key] = value.strip()
    return out


def t1max_of(expno_path) -> float:
    """t1max of an experiment folder, reading its acqu2s; 0.0 if absent."""
    try:
        return t1max_seconds(read_timing_keys(Path(expno_path) / "acqu2s"))
    except OSError:
        return 0.0


def as_ms_text(seconds: float) -> str:
    """'1363' for long times, '64.0' below 100 ms, '' for unknown."""
    if not seconds or seconds <= 0:
        return ""
    ms = seconds * 1000.0
    return f"{ms:.0f}" if ms >= 100 else f"{ms:.1f}"
