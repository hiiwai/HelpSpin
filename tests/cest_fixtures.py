"""Synthetic Bruker pseudo-2D datasets for CEST tests.

Written rather than shipped: the real 19F datasets that drove this feature
are 46 MB, which does not belong in a source distribution. Generating them
also lets a test ask for a particular SI(F1)/TD(F1) relationship directly
instead of hoping one turns up in sample data.

The 2rr is zero-filled beyond min(n_rows, si_f1), exactly as xf2 leaves it.
That detail is the whole point of the fixture: it is what distinguishes a
harmless zero-filled pdata from one that has genuinely lost rows.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

DEFAULT_OFFSETS = [-8000] + list(range(-1000, 1001, 100)) + [8000, 9000]

_SW_HZ = 10000.0
_SFO1 = 564.62
_BF1 = 564.68


def _jcamp_array(name: str, values: dict[int, float], size: int = 64) -> str:
    """A Bruker indexed parameter array, five entries to a line."""
    entries = ["0"] * size
    for index, value in values.items():
        entries[index] = str(value)
    lines = [f"##${name}= (0..{size - 1})"]
    for start in range(0, size, 5):
        lines.append(" ".join(entries[start:start + 5]))
    return "\n".join(lines)


def _acqus(td_f2: int, pulprog: str) -> str:
    return "\n".join([
        "##TITLE= synthetic",
        "##JCAMPDX= 5.0",
        "##DATATYPE= Parameter Values",
        f"##$BF1= {_BF1:.6f}",
        f"##$SFO1= {_SFO1:.6f}",
        f"##$SW_h= {_SW_HZ:.4f}",
        f"##$TD= {td_f2}",
        "##$NS= 8",
        "##$RG= 1",
        "##$NUC1= <19F>",
        f"##$PULPROG= <{pulprog}>",
        "##$AQ_mod= 3",
        "##$BYTORDA= 0",
        "##$DTYPA= 0",
        "##$DECIM= 16",
        "##$DSPFVS= 20",
        "##$GRPDLY= 0",
        "##$FQ1LIST= <synth.list>",
        "##$PARMODE= 1",
        # CNST8 is the nominal nutation field, CNST25 the saturation field.
        _jcamp_array("CNST", {8: 100, 25: 60}),
        # P8 is the 90 degree pulse at 100 Hz; P9 and INP9 are the 45 degree
        # increment the nutation sequence steps.
        _jcamp_array("P", {1: 12, 8: 2500, 9: 1250, 25: 4166.66}),
        _jcamp_array("PLW", {1: 9.5121, 8: 0.000219158784, 25: 7.88973e-05}),
        _jcamp_array("INP", {9: 1250}),
        _jcamp_array("D", {1: 3, 18: 0.8}),
        "##END=",
    ])


def _acqu2s(n_rows: int) -> str:
    # NUC1 = 1H and a proton SFO1 are deliberately wrong-looking: that is
    # exactly what TopSpin writes for a pseudo-2D, and the reader must not
    # believe it.
    return "\n".join([
        "##TITLE= synthetic",
        "##JCAMPDX= 5.0",
        "##DATATYPE= Parameter Values",
        f"##$TD= {n_rows}",
        "##$SFO1= 600.13",
        "##$NUC1= <1H>",
        "##$SW_h= 5882.35",
        "##$FnMODE= 1",
        "##END=",
    ])


def _procs(si_f2: int) -> str:
    return "\n".join([
        "##TITLE= synthetic",
        "##JCAMPDX= 5.0",
        "##DATATYPE= Parameter Values",
        f"##$SI= {si_f2}",
        f"##$XDIM= {si_f2}",
        f"##$SF= {_BF1:.6f}",
        "##$OFFSET= -100.0",
        "##$NC_proc= 0",
        "##$BYTORDP= 0",
        "##$DTYPP= 0",
        "##$PHC0= 0.0",
        "##$PHC1= 0.0",
        "##$LB= 1.0",
        "##$WDW= 1",
        "##$REVERSE= no",
        "##END=",
    ])


def _proc2s(si_f1: int) -> str:
    return "\n".join([
        "##TITLE= synthetic",
        "##JCAMPDX= 5.0",
        "##DATATYPE= Parameter Values",
        f"##$SI= {si_f1}",
        f"##$XDIM= {si_f1}",
        "##$SF= 600.13",
        "##$OFFSET= 4.7",
        "##$NC_proc= 0",
        "##$MC2= 0",
        "##END=",
    ])


def make_dataset(
    root,
    n_rows: int,
    si_f1: int,
    *,
    td_f2: int = 2048,
    si_f2: int = 4096,
    pulprog: str = "19f_cest.iw",
    offsets=None,
) -> Path:
    """Write a synthetic Bruker expno and return its path.

    n_rows is TD(F1), the number of rows actually acquired and present in
    `ser`. si_f1 is SI(F1), the number of rows `xf2` stored in 2rr -- set it
    below n_rows to reproduce the truncation, above to reproduce zero-fill.
    """
    root = Path(root)
    shutil.rmtree(root, ignore_errors=True)
    (root / "pdata" / "1").mkdir(parents=True)
    (root / "lists" / "f1").mkdir(parents=True)

    (root / "acqus").write_text(_acqus(td_f2, pulprog) + "\n")
    (root / "acqu2s").write_text(_acqu2s(n_rows) + "\n")
    (root / "pdata" / "1" / "procs").write_text(_procs(si_f2) + "\n")
    (root / "pdata" / "1" / "proc2s").write_text(_proc2s(si_f1) + "\n")

    # 2rr: a peak that shrinks down the rows, with everything past the
    # acquired count left as zeros -- what xf2 writes when SI exceeds TD.
    x = np.arange(si_f2)
    peak = np.exp(-((x - si_f2 * 0.5) ** 2) / (2 * 20.0 ** 2))
    real_rows = min(n_rows, si_f1)
    processed = np.zeros((si_f1, si_f2), dtype="<i4")
    processed[:real_rows] = (
        np.linspace(1.0, 0.3, real_rows)[:, None] * peak[None, :] * 1e6
    ).astype("<i4")
    processed.tofile(root / "pdata" / "1" / "2rr")

    # ser: interleaved real/imaginary int32, every acquired row present.
    fid = np.zeros((n_rows, td_f2), dtype="<i4")
    tick = np.arange(td_f2 // 2)
    envelope = np.exp(-tick / 300.0) * 1e5
    for row in range(n_rows):
        scale = 1.0 - 0.5 * row / max(1, n_rows)
        wave = envelope * scale
        fid[row, 0::2] = (wave * np.cos(2 * np.pi * 0.05 * tick)).astype("<i4")
        fid[row, 1::2] = (wave * np.sin(2 * np.pi * 0.05 * tick)).astype("<i4")
    fid.tofile(root / "ser")

    if offsets is not None:
        # CRLF on purpose: lists authored on a Windows TopSpin and read from
        # a POSIX share are the normal case, not an edge case.
        text = "\r\n".join(str(int(value)) for value in offsets) + "\r\n"
        (root / "lists" / "f1" / "synth.list").write_text(text)

    return root
