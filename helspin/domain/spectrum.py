"""Spectra and ppm axis calibration.

Pure numpy. No Qt, no nmrglue, no matplotlib -- see the layering rule in the
handoff (section 3). Everything here is exhaustively unit-testable without a GUI.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import EmptySpectrum, InvalidAxis


@dataclass(frozen=True)
class AxisCalibration:
    """Index <-> ppm mapping for one dimension.

    ppm axes in NMR are conventionally DESCENDING: index 0 is the leftmost
    point and has the HIGHEST ppm value. Every method here preserves that.

    Attributes:
        size: number of points.
        sw_hz: spectral width in Hz.
        obs_mhz: observe frequency (SFO1) in MHz.
        car_hz: carrier offset from the centre of the spectrum, in Hz.
        nucleus: e.g. "1H", "13C".
    """

    size: int
    sw_hz: float
    obs_mhz: float
    car_hz: float = 0.0
    nucleus: str = ""

    def __post_init__(self) -> None:
        if self.size < 1:
            raise InvalidAxis(f"size must be >= 1, got {self.size}")
        if self.sw_hz <= 0:
            raise InvalidAxis(f"sw_hz must be > 0, got {self.sw_hz}")
        if self.obs_mhz <= 0:
            raise InvalidAxis(f"obs_mhz must be > 0, got {self.obs_mhz}")

    @property
    def sw_ppm(self) -> float:
        """Spectral width in ppm."""
        return self.sw_hz / self.obs_mhz

    @property
    def centre_ppm(self) -> float:
        """ppm value at the centre of the spectrum."""
        return self.car_hz / self.obs_mhz

    def ppm_scale(self) -> np.ndarray:
        """Descending ppm array of length ``size``.

        A size-1 axis degenerates to a single point at the centre; np.linspace
        handles that without a divide-by-zero.
        """
        left, right = self.ppm_limits()
        return np.linspace(left, right, self.size)

    def ppm_limits(self) -> tuple[float, float]:
        """(left, right) in conventional NMR order, so left > right."""
        half = self.sw_ppm / 2.0
        centre = self.centre_ppm
        return (centre + half, centre - half)

    def index_to_ppm(self, index: float) -> float:
        """ppm at a (possibly fractional) index. Not clamped."""
        left, right = self.ppm_limits()
        if self.size == 1:
            return (left + right) / 2.0
        step = (right - left) / (self.size - 1)
        return left + step * index

    def ppm_to_index(self, ppm: float) -> int:
        """Nearest index to a ppm value, clamped into range.

        Clamping is deliberate: callers use this to slice display ranges, and a
        user-typed range legitimately extends past the data.
        """
        if self.size == 1:
            return 0
        left, right = self.ppm_limits()
        step = (right - left) / (self.size - 1)
        raw = (ppm - left) / step
        return int(np.clip(round(raw), 0, self.size - 1))

    def slice_for(self, left_ppm: float, right_ppm: float) -> slice:
        """Index slice covering a ppm window, inclusive of both endpoints.

        Accepts the bounds in either order, so a rubber-band drag right-to-left
        still works (checklist: "Rubber-band drag right-to-left").
        """
        hi, lo = max(left_ppm, right_ppm), min(left_ppm, right_ppm)
        start = self.ppm_to_index(hi)
        stop = self.ppm_to_index(lo)
        return slice(start, stop + 1)


@dataclass(frozen=True)
class Spectrum1D:
    """A 1D spectrum.

    Attributes:
        real: float64 intensities, shape (n,).
        axis: calibration whose ``size`` must equal ``len(real)``.
        complex_spectrum: retained for re-phasing when available. None when
            loaded from pdata without the imaginary part, in which case phase
            controls must be disabled rather than silently no-op.
        ns: number of scans (acqus NS).
        rg: receiver gain (acqus RG).
    """

    real: np.ndarray
    axis: AxisCalibration
    complex_spectrum: np.ndarray | None = None
    ns: int = 1
    rg: float = 1.0

    def __post_init__(self) -> None:
        if self.real.ndim != 1:
            raise InvalidAxis(f"real must be 1-D, got {self.real.ndim}-D")
        if self.real.size == 0:
            raise EmptySpectrum("spectrum has no points")
        if self.real.size != self.axis.size:
            raise InvalidAxis(
                f"axis size {self.axis.size} != data size {self.real.size}"
            )

    @property
    def can_rephase(self) -> bool:
        return self.complex_spectrum is not None

    def ppm_scale(self) -> np.ndarray:
        return self.axis.ppm_scale()


@dataclass(frozen=True)
class Spectrum2D:
    """A 2D spectrum. axis_f2 is the direct (x) dimension."""

    real: np.ndarray
    axis_f1: AxisCalibration
    axis_f2: AxisCalibration
    ns: int = 1
    rg: float = 1.0

    def __post_init__(self) -> None:
        if self.real.ndim != 2:
            raise InvalidAxis(f"real must be 2-D, got {self.real.ndim}-D")
        if self.real.size == 0:
            raise EmptySpectrum("spectrum has no points")
        n_f1, n_f2 = self.real.shape
        if n_f1 != self.axis_f1.size or n_f2 != self.axis_f2.size:
            raise InvalidAxis(
                f"axes ({self.axis_f1.size}, {self.axis_f2.size}) "
                f"!= data {self.real.shape}"
            )


def union_ppm_range(
    limits: list[tuple[float, float]],
) -> tuple[float, float]:
    """Union of ppm windows, in conventional (left > right) order.

    The handoff specifies UNION, not intersection: seeing everything first and
    then zooming in matters more than avoiding dead space.
    """
    if not limits:
        raise ValueError("no limits to combine")
    left = max(max(a, b) for a, b in limits)
    right = min(min(a, b) for a, b in limits)
    return (left, right)


def ranges_overlap(limits: list[tuple[float, float]]) -> bool:
    """True when every window shares at least one point with every other.

    Used to warn on a probable mis-drop; the union is still shown either way.
    """
    if len(limits) < 2:
        return True
    lowest_top = min(max(a, b) for a, b in limits)
    highest_bottom = max(min(a, b) for a, b in limits)
    return lowest_top >= highest_bottom
