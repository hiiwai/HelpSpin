"""Y scaling and difference spectra.

Two places where a wrong answer looks entirely plausible on screen:

1.  Absolute intensity comparison WITHOUT dividing by NS and RG. A 256-scan
    spectrum simply looks stronger than a 16-scan one, and the figure silently
    lies about concentration.
2.  Subtracting two spectra by ARRAY INDEX when they do not share a ppm grid.
    Different SI, SW or spectrometer frequency means point i of A and point i
    of B are different chemical shifts.

Both are guarded here and covered by tests.
"""

from __future__ import annotations

import numpy as np

from .errors import EmptySpectrum, MissingParameter
from .project import YScaleMode
from .spectrum import Spectrum1D

# Fraction of the spectrum used for each candidate noise window.
NOISE_WINDOW_FRACTION = 0.05
_TINY = 1e-30


def acquisition_scale(spectrum: Spectrum1D) -> float:
    """Divisor making intensities comparable between acquisitions.

    Bruker intensities scale with the number of scans and the receiver gain, so
    raw comparison between two spectra is meaningless without this.
    """
    if spectrum.ns is None or spectrum.rg is None:
        raise MissingParameter("NS and RG are required for absolute scaling")
    if spectrum.ns <= 0 or spectrum.rg <= 0:
        raise MissingParameter(
            f"NS and RG must be positive, got NS={spectrum.ns}, RG={spectrum.rg}"
        )
    return float(spectrum.ns) * float(spectrum.rg)


def noise_rms(data: np.ndarray, window: slice | None = None) -> float:
    """RMS of a signal-free region.

    With no window, the quietest candidate region is chosen automatically: the
    spectrum is divided into blocks and the one with the lowest standard
    deviation wins. Crude, but it lands on baseline for almost every real
    spectrum, and the user can override when it does not.
    """
    if data.size == 0:
        raise EmptySpectrum("cannot estimate noise from an empty spectrum")
    if window is not None:
        segment = data[window]
        if segment.size == 0:
            raise EmptySpectrum("noise window contains no points")
        return float(np.sqrt(np.mean(np.square(segment))))

    block = max(1, int(data.size * NOISE_WINDOW_FRACTION))
    n_blocks = max(1, data.size // block)
    best = None
    for i in range(n_blocks):
        segment = data[i * block : (i + 1) * block]
        if segment.size == 0:
            continue
        sd = float(np.std(segment))
        if best is None or sd < best[0]:
            best = (sd, segment)
    if best is None:
        raise EmptySpectrum("no usable noise window")
    return float(np.sqrt(np.mean(np.square(best[1]))))


def scale_factors(
    spectra: list[Spectrum1D],
    mode: YScaleMode,
    noise_window: slice | None = None,
    reference_window: slice | None = None,
) -> list[float]:
    """Per-trace multipliers for a y-scaling mode.

    An all-zero or noise-free trace yields a factor of 1.0 rather than raising:
    a blank trace should render flat, not abort the whole panel.
    """
    if not spectra:
        return []

    if mode is YScaleMode.ABSOLUTE:
        return [1.0 / acquisition_scale(s) for s in spectra]

    if mode is YScaleMode.MAX:
        out = []
        for s in spectra:
            peak = float(np.max(np.abs(s.real)))
            out.append(1.0 / peak if peak > _TINY else 1.0)
        return out

    if mode is YScaleMode.IDENTICAL_SNR:
        levels = []
        for s in spectra:
            try:
                levels.append(noise_rms(s.real, noise_window))
            except EmptySpectrum:
                levels.append(0.0)
        target = next((v for v in levels if v > _TINY), None)
        if target is None:
            return [1.0] * len(spectra)
        return [target / v if v > _TINY else 1.0 for v in levels]

    if mode is YScaleMode.REFERENCE:
        if reference_window is None:
            raise ValueError("reference mode requires a reference window")
        areas = [float(np.trapezoid(np.abs(s.real[reference_window]))) for s in spectra]
        target = next((a for a in areas if a > _TINY), None)
        if target is None:
            return [1.0] * len(spectra)
        return [target / a if a > _TINY else 1.0 for a in areas]

    raise ValueError(f"unhandled scale mode: {mode}")


def stack_offsets(count: int, spacing: float, reference: float) -> list[float]:
    """Vertical offsets for a stacked arrangement.

    Computed from the VISIBLE traces only, so hiding a middle trace does not
    leave a gap where it used to be.
    """
    return [i * spacing * reference for i in range(count)]


# --- difference -------------------------------------------------------------


def common_range(a: Spectrum1D, b: Spectrum1D, shift_a=0.0, shift_b=0.0):
    """Overlapping ppm window of two spectra, after their display shifts."""
    a_left, a_right = a.axis.ppm_limits()
    b_left, b_right = b.axis.ppm_limits()
    left = min(a_left + shift_a, b_left + shift_b)
    right = max(a_right + shift_a, b_right + shift_b)
    return (left, right)


def difference(
    a: Spectrum1D,
    b: Spectrum1D,
    k: float = 1.0,
    shift_a: float = 0.0,
    shift_b: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """A - k*B on a common ppm axis. Returns (ppm, values).

    Order of operations is fixed and it matters:

        1. apply each slot's display shift      (alignment)
        2. interpolate B onto A's ppm axis      (common grid)
        3. scale  B' = k * B
        4. subtract  D = A - B'

    Shifting BEFORE subtraction is not optional: a misalignment of even a
    fraction of a ppm produces derivative-shaped artifacts that look like real
    peaks.

    Outside the overlap the difference is undefined, so nothing is returned
    there -- it is not zero-filled, which would invent a flat baseline.
    """
    left, right = common_range(a, b, shift_a, shift_b)
    if left <= right:
        raise EmptySpectrum(
            "spectra do not overlap in ppm; a difference is undefined"
        )

    ppm_a = a.axis.ppm_scale() + shift_a
    ppm_b = b.axis.ppm_scale() + shift_b

    mask = (ppm_a <= left) & (ppm_a >= right)
    ppm = ppm_a[mask]
    if ppm.size == 0:
        raise EmptySpectrum("no points of the minuend fall inside the overlap")

    # np.interp needs ascending x, and ppm axes descend.
    b_interp = np.interp(ppm[::-1], ppm_b[::-1], b.real[::-1])[::-1]
    return ppm, a.real[mask] - k * b_interp


def fit_k(
    a: Spectrum1D,
    b: Spectrum1D,
    window: tuple[float, float] | None = None,
    shift_a: float = 0.0,
    shift_b: float = 0.0,
) -> float:
    """Least-squares k minimising |A - k*B| over a ppm window.

    k = <A,B> / <B,B>. Choosing the window is the user's judgement; solving for
    k is not. A window containing only noise gives <B,B> ~ 0, so guard it.
    """
    ppm, _ = difference(a, b, k=0.0, shift_a=shift_a, shift_b=shift_b)
    a_vals = np.interp(ppm[::-1], (a.axis.ppm_scale() + shift_a)[::-1], a.real[::-1])[::-1]
    b_vals = np.interp(ppm[::-1], (b.axis.ppm_scale() + shift_b)[::-1], b.real[::-1])[::-1]

    if window is not None:
        hi, lo = max(window), min(window)
        mask = (ppm <= hi) & (ppm >= lo)
        if not mask.any():
            raise EmptySpectrum("fit window contains no points")
        a_vals, b_vals = a_vals[mask], b_vals[mask]

    denominator = float(np.dot(b_vals, b_vals))
    if denominator <= _TINY:
        return 1.0
    return float(np.dot(a_vals, b_vals) / denominator)
