"""Non-linear polish for CEST fits.

scipy lives here rather than in domain/ because the architecture gate forbids
it there. The division is not merely bureaucratic: domain/cest.py finds the
GLOBAL optimum by exact linear algebra over a scanned grid, and this module
only refines it locally and extracts a covariance matrix. If scipy were
absent or a polish failed to converge, the grid answer still stands, so every
function here degrades to the domain result rather than raising.
"""

from __future__ import annotations

import math

import numpy as np

from ..domain.cest import (
    CestError,
    NutationFit,
    ZSpectrum,
    lorentzian_dip,
    nutation_model,
    search_nutation,
    two_lorentzian,
)


def _curve_fit():
    """Import scipy on first use.

    nmrglue already costs ~0.8 s at import and the reader defers it for that
    reason; scipy.optimize is the same bargain. Nothing on the browsing path
    needs a fit.
    """
    from scipy.optimize import curve_fit

    return curve_fit


def polish_nutation(t, y, seed: NutationFit) -> NutationFit:
    """Refine a grid fit and attach a real error bar on B1.

    The polish is seeded from the global grid optimum, so it starts inside
    the correct basin -- which is the whole point. Left to its own devices
    from a naive guess, curve_fit on this model reaches 100.0, 105.3 or
    112.9 Hz on the same data depending only on the initial T2. Bounds keep
    it in that basin: the field may move by at most 20%, which is far wider
    than any real refinement and far narrower than the gap to the next
    alias.

    Any failure returns the seed untouched. A converged-but-worse result is
    also rejected, because a polish that increases the residual has left the
    basin rather than improved on it.
    """
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)

    if seed.damped and seed.t2_s:
        def model(x, offset, amplitude, field_hz, phase, t2_s):
            return nutation_model(x, offset, amplitude, field_hz, phase, t2_s)

        p0 = [seed.offset, seed.amplitude, seed.field_hz, seed.phase_rad, seed.t2_s]
        span = float(t[-1] - t[0]) or 1.0
        lower = [-np.inf, -np.inf, seed.field_hz * 0.8, -np.inf, 1e-6]
        upper = [np.inf, np.inf, seed.field_hz * 1.2, np.inf, 1000.0 * span]
    else:
        def model(x, offset, amplitude, field_hz, phase):
            return nutation_model(x, offset, amplitude, field_hz, phase, None)

        p0 = [seed.offset, seed.amplitude, seed.field_hz, seed.phase_rad]
        lower = [-np.inf, -np.inf, seed.field_hz * 0.8, -np.inf]
        upper = [np.inf, np.inf, seed.field_hz * 1.2, np.inf]

    try:
        curve_fit = _curve_fit()
        popt, pcov = curve_fit(
            model, t, y, p0=p0, bounds=(lower, upper), maxfev=200_000
        )
    except Exception:
        return seed

    residual = y - model(t, *popt)
    rss = float(residual @ residual)
    if not math.isfinite(rss) or rss > (seed.residual_rms ** 2) * t.size:
        return seed

    offset, amplitude, field_hz = float(popt[0]), float(popt[1]), float(popt[2])
    phase = float(popt[3])
    t2_s = float(popt[4]) if len(popt) > 4 else None

    # sin(x) and -sin(x+pi) are the same curve; fix the sign so a reported
    # amplitude is always positive and the phase is comparable between fits.
    # Phase is wrapped into the half-open interval [-pi, pi).
    if amplitude < 0:
        amplitude, phase = -amplitude, phase + math.pi
    field_hz = abs(field_hz)
    phase = (phase + math.pi) % (2.0 * math.pi) - math.pi

    try:
        error = float(np.sqrt(np.diag(pcov))[2])
        if not math.isfinite(error):
            error = float("nan")
    except (ValueError, IndexError):                     # pragma: no cover
        error = float("nan")

    rms = math.sqrt(rss / t.size)
    return NutationFit(
        field_hz=field_hz,
        field_error_hz=error,
        amplitude=amplitude,
        offset=offset,
        phase_rad=phase,
        t2_s=t2_s,
        residual_rms=rms,
        residual_fraction=rms / abs(amplitude) if amplitude else float("inf"),
        damped=t2_s is not None,
        aicc=seed.aicc,
        n_points=int(t.size),
        polished=True,
        warnings=seed.warnings,
    )


def fit_nutation(t, y, *, nominal_hz: float, damped: bool) -> NutationFit:
    """Grid search then polish -- the whole calibration in one call."""
    seed = search_nutation(t, y, nominal_hz=nominal_hz, damped=damped)
    return polish_nutation(t, y, seed)


class DipFit:
    """A fitted Z-spectrum dip, in Hz and ppm."""

    __slots__ = (
        "centre_hz", "centre_error_hz", "depth", "width_hz",
        "baseline", "sfo1_mhz", "residual_rms",
    )

    def __init__(self, centre_hz, centre_error_hz, depth, width_hz,
                 baseline, sfo1_mhz, residual_rms):
        self.centre_hz = centre_hz
        self.centre_error_hz = centre_error_hz
        self.depth = depth
        self.width_hz = width_hz
        self.baseline = baseline
        self.sfo1_mhz = sfo1_mhz
        self.residual_rms = residual_rms

    @property
    def centre_ppm(self) -> float:
        return self.centre_hz / self.sfo1_mhz

    @property
    def width_ppm(self) -> float:
        return self.width_hz / self.sfo1_mhz

    def __repr__(self) -> str:                            # pragma: no cover
        return (
            f"DipFit(centre={self.centre_hz:.1f} Hz / {self.centre_ppm:.3f} ppm, "
            f"depth={self.depth:.3f}, fwhm={self.width_hz:.1f} Hz)"
        )


def fit_dip(
    z: ZSpectrum, centre_guess_hz: float, *, window_hz: float | None = None
) -> DipFit:
    """Least-squares Lorentzian around one dip.

    Fitted on the points near the dip only. Including the whole Z-spectrum
    would let a distant baseline wobble pull the centre, and the Lorentzian
    is a local description of one feature rather than a model of the profile.

    This locates a dip; it does not measure exchange. Turning a centre and a
    depth into kex and a populated fraction needs Bloch-McConnell against
    several saturation fields, which is deliberately not attempted here.
    """
    x = np.asarray(z.offsets_hz, dtype=np.float64)
    y = np.asarray(z.intensity, dtype=np.float64)
    keep = ~z.reference_mask
    x, y = x[keep], y[keep]
    if x.size < 4:
        raise CestError("too few non-reference offsets to fit a dip")

    if window_hz is None:
        spacing = float(np.median(np.abs(np.diff(x)))) if x.size > 1 else 1.0
        window_hz = max(8.0 * spacing, 1.0)
    near = np.abs(x - centre_guess_hz) <= window_hz
    if near.sum() < 4:
        # Widen once rather than failing: a coarse list can leave a genuine
        # dip with only two or three points inside a spacing-derived window.
        near = np.argsort(np.abs(x - centre_guess_hz))[:max(5, min(9, x.size))]
    xf, yf = x[near], y[near]
    if xf.size < 4:                                       # pragma: no cover
        raise CestError("too few points near the dip to fit")

    baseline0 = float(np.max(yf))
    depth0 = max(baseline0 - float(np.min(yf)), 1e-6)
    width0 = max(float(np.ptp(xf)) / 3.0, 1e-6)

    try:
        curve_fit = _curve_fit()
        popt, pcov = curve_fit(
            lorentzian_dip, xf, yf,
            p0=[baseline0, depth0, centre_guess_hz, width0],
            bounds=(
                [-np.inf, 0.0, float(xf.min()) - width0, 1e-6],
                [np.inf, np.inf, float(xf.max()) + width0, np.inf],
            ),
            maxfev=200_000,
        )
    except Exception as exc:
        raise CestError(f"dip fit did not converge: {exc}") from exc

    residual = yf - lorentzian_dip(xf, *popt)
    try:
        centre_error = float(np.sqrt(np.diag(pcov))[2])
    except (ValueError, IndexError):                      # pragma: no cover
        centre_error = float("nan")

    return DipFit(
        centre_hz=float(popt[2]),
        centre_error_hz=centre_error,
        depth=float(popt[1]),
        width_hz=float(popt[3]),
        baseline=float(popt[0]),
        sfo1_mhz=z.sfo1_mhz,
        residual_rms=float(np.sqrt(np.mean(residual ** 2))),
    )


class TwoDipFit:
    """Two dips fitted together, with the minor one's apparent weight.

    `minor_fraction` is the minor dip's depth as a fraction of the total dip
    depth. It is NOT a populated fraction, and the distinction matters enough
    to be worth stating wherever the number is shown.

    In a CEST experiment the depth of a minor-state dip depends on the
    populated fraction AND on the exchange rate, the saturation field, the
    saturation time and both states' relaxation rates. Saturation transferred
    to the observed state during d18 is what makes the dip, so a small
    population in fast exchange can dig deeper than a larger one in slow
    exchange. Reading depth as population is only defensible in the limit of
    full saturation and complete transfer, which a real experiment does not
    reach.

    Getting an actual population means fitting Bloch-McConnell against
    several saturation fields, which is deliberately not attempted here. What
    this fit does give honestly: both dip positions, their widths, and a
    depth ratio that is reproducible and comparable BETWEEN experiments run
    under identical conditions -- which is what a titration or a control
    series needs.
    """

    __slots__ = ("baseline", "major", "minor", "sfo1_mhz", "residual_rms",
                 "minor_fraction", "separation_hz")

    def __init__(self, baseline, major, minor, sfo1_mhz, residual_rms):
        self.baseline = baseline
        self.major = major            # (depth, centre_hz, width_hz)
        self.minor = minor
        self.sfo1_mhz = sfo1_mhz
        self.residual_rms = residual_rms
        total = major[0] + minor[0]
        self.minor_fraction = (minor[0] / total) if total > 0 else float("nan")
        self.separation_hz = minor[1] - major[1]

    @property
    def separation_ppm(self) -> float:
        return self.separation_hz / self.sfo1_mhz


def fit_two_dips(z: ZSpectrum, centre_a_hz: float, centre_b_hz: float) -> TwoDipFit:
    """Fit two Lorentzian dips simultaneously across the whole profile.

    Fitted together rather than one after the other: the major dip's wings
    reach under the minor one, so fitting it alone and subtracting biases the
    minor depth by whatever the wing contributes there. Both are constrained
    to stay near their seeds so the pair cannot collapse onto the same
    feature, which is the usual failure of a two-component fit.
    """
    x = np.asarray(z.offsets_hz, dtype=np.float64)
    y = np.asarray(z.intensity, dtype=np.float64)
    keep = ~z.reference_mask
    x, y = x[keep], y[keep]
    if x.size < 8:
        raise CestError("too few offsets to fit two dips")
    if centre_a_hz == centre_b_hz:
        raise CestError("the two dips need different starting positions")

    span = float(np.ptp(x)) or 1.0
    spacing = float(np.median(np.abs(np.diff(np.sort(x))))) or 1.0
    baseline0 = float(np.percentile(y, 90))
    depth_a0 = max(baseline0 - float(np.min(y)), 1e-6)
    depth_b0 = max(depth_a0 * 0.1, 1e-6)
    width0 = max(4.0 * spacing, 1.0)
    # Each centre may move by a quarter of the gap between them, so they
    # cannot swap or merge.
    slack = max(abs(centre_b_hz - centre_a_hz) / 4.0, 2.0 * spacing)

    try:
        curve_fit = _curve_fit()
        popt, _ = curve_fit(
            two_lorentzian, x, y,
            p0=[baseline0, depth_a0, centre_a_hz, width0,
                depth_b0, centre_b_hz, width0],
            bounds=(
                [-np.inf, 0.0, centre_a_hz - slack, spacing,
                 0.0, centre_b_hz - slack, spacing],
                [np.inf, np.inf, centre_a_hz + slack, span,
                 np.inf, centre_b_hz + slack, span],
            ),
            maxfev=400_000,
        )
    except Exception as exc:
        raise CestError(f"two-dip fit did not converge: {exc}") from exc

    residual = y - two_lorentzian(x, *popt)
    baseline, da, ca, wa, db, cb, wb = (float(v) for v in popt)
    major, minor = (da, ca, wa), (db, cb, wb)
    if db > da:                       # report the deeper one as major
        major, minor = minor, major
    return TwoDipFit(
        baseline=baseline, major=major, minor=minor, sfo1_mhz=z.sfo1_mhz,
        residual_rms=float(np.sqrt(np.mean(residual ** 2))),
    )
