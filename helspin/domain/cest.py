"""CEST and nutation-calibration maths.

Pure numpy and stdlib: no scipy, no nmrglue, no Qt. The architecture gate
(tests/test_architecture.py) enforces that, and it is the reason the
non-linear polish lives in services/cest_fit.py rather than here. Everything
in this module is exact linear algebra or closed form, so it has no starting
guess to get wrong and cannot land in a local minimum.

Two experiments are supported, both pseudo-2D and both from the Waudby lab
pulse programs:

  19f_calib_nut  F1 steps a low-power pulse DURATION; the signed peak
                 integral oscillates as sin(2*pi*B1*t) and the fitted
                 frequency IS the RF field in Hz.

  19f_cest       F1 steps a saturation OFFSET read from FQ1LIST; the peak
                 intensity normalised to a remote reference gives the
                 Z-spectrum.

Why a grid search rather than curve_fit alone
---------------------------------------------
A sine with a free frequency is multimodal. Measured on real 19F nutation
data, seeding scipy's curve_fit with T2 = 0.01/0.03 s converged to 100.0 and
105.3 Hz at 30-35% residuals, while 0.02/0.05/0.10/0.30 s all reached the
true optimum at 112.9 Hz and 4.3%. The reported field was an artefact of the
starting guess.

The fix is separable least squares. Holding B1 (and T2) fixed makes the model

    y = C + exp(-t/T2) * (Ac*cos(2*pi*B1*t) + As*sin(2*pi*B1*t))

LINEAR in C, Ac and As, so those three are solved exactly by lstsq at every
grid point and only the one or two genuinely non-linear parameters are
scanned. The global optimum over the scanned range is found by construction.
services/cest_fit.py then polishes from there, which moves the answer by
well under its own error bar and buys a covariance matrix.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Frequency-list unit keywords Bruker writes as the first line of an fq list.
# A list with no keyword is Hz relative to O1, which is the common case and
# what TopSpin writes when you type plain numbers.
_FQ_UNITS = {
    "O1": "hz", "O2": "hz", "O3": "hz", "HZ": "hz",
    "P": "ppm", "PPM": "ppm",
    "SFO": "mhz", "SFO1": "mhz", "MHZ": "mhz",
    "BF": "bf", "BF1": "bf",
}


class CestError(ValueError):
    """A CEST dataset or list could not be interpreted."""


# --------------------------------------------------------------------------
# Frequency lists
# --------------------------------------------------------------------------

def parse_fq_list(
    text: str, *, sfo1_mhz: float, bf1_mhz: float | None = None
) -> np.ndarray:
    """Bruker frequency list -> saturation offsets in Hz relative to the carrier.

    Handles every form seen in practice: a bare column of numbers (Hz), a
    leading unit keyword (O1/P/SFO/BF), comment lines introduced by ';' or
    '#', blank lines, and CRLF endings from a Windows-side TopSpin.

    ppm is converted with SFO1 rather than BF1 because FQ1LIST offsets are
    applied to the transmitter, so the carrier is the reference point. An
    absolute-MHz list (SFO) is converted to an offset by subtracting SFO1;
    a BF list subtracts BF1, which must then be supplied.
    """
    if sfo1_mhz <= 0:
        raise CestError(f"SFO1 must be positive to read a frequency list, got {sfo1_mhz}")

    unit = "hz"
    values: list[float] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line[0] in ";#":
            continue
        # A unit keyword may only appear before any number.
        token = line.split()[0].upper().rstrip(":")
        if not values and token in _FQ_UNITS:
            unit = _FQ_UNITS[token]
            # "O1 1234" on one line is legal; keep any trailing number.
            rest = line.split()[1:]
            if not rest:
                continue
            line = " ".join(rest)
        try:
            values.append(float(line.split()[0]))
        except (ValueError, IndexError) as exc:
            raise CestError(f"cannot read frequency-list entry {raw_line!r}") from exc

    if not values:
        raise CestError("frequency list contains no entries")

    array = np.asarray(values, dtype=np.float64)
    if unit == "hz":
        return array
    if unit == "ppm":
        return array * sfo1_mhz
    if unit == "mhz":
        return (array - sfo1_mhz) * 1e6
    if unit == "bf":
        if not bf1_mhz or bf1_mhz <= 0:
            raise CestError("a BF-referenced frequency list needs BF1")
        return (array - bf1_mhz) * 1e6
    raise CestError(f"unsupported frequency-list unit {unit!r}")   # pragma: no cover


# --------------------------------------------------------------------------
# Nutation time axis
# --------------------------------------------------------------------------

def nutation_times(p9_us: float, inp9_us: float, rows: int) -> np.ndarray:
    """Pulse duration in SECONDS for each F1 row of 19f_calib_nut.

    The pulse program sets p9 = p8*0.5 and inp9 = p8*0.5, then increments
    with `ipu9` AFTER each row's `go`. Row 1 therefore uses p9 as written and
    row i uses p9 + (i-1)*inp9 -- the first increment is 45 degrees and the
    second row is the nominal 90 degrees, matching the manual's table.

    Written as p9 + (i-1)*inp9 rather than i*inp9 on purpose. They are equal
    only because the pulse program happens to set p9 == inp9; under -DMANUAL
    the operator sets both by hand and they need not match. Reading both from
    acqus keeps a manual acquisition correct instead of silently mis-scaling
    the whole axis.
    """
    if rows <= 0:
        raise CestError(f"need at least one row, got {rows}")
    if inp9_us <= 0:
        raise CestError(
            f"INP9 must be positive to build a nutation axis, got {inp9_us} us"
        )
    if p9_us <= 0:
        raise CestError(f"P9 must be positive, got {p9_us} us")
    return (p9_us + np.arange(rows, dtype=np.float64) * inp9_us) * 1e-6


def nominal_field_hz(p90_us: float) -> float:
    """nu1 = 1/(4*p90). The manual's Part 2 relation, in Hz from microseconds."""
    if p90_us <= 0:
        raise CestError(f"90-degree pulse length must be positive, got {p90_us}")
    return 1e6 / (4.0 * p90_us)


def corrected_power(plw_old: float, target_hz: float, actual_hz: float) -> float:
    """PLW(new) = PLW(old) * (B1_target/B1_actual)^2.

    Power goes as the square of the field, so the ratio is dimensionless and
    the SAME factor rescales any nominal power on the same probe and tuning.
    That is what lets a calibration acquired at CNST8 = 100 Hz correct a CEST
    experiment run at CNST25 = 60 Hz without re-running the nutation.
    """
    if actual_hz <= 0:
        raise CestError("fitted field must be positive to correct power")
    if plw_old <= 0:
        raise CestError(f"reference power must be positive, got {plw_old} W")
    if target_hz <= 0:
        raise CestError(f"target field must be positive, got {target_hz} Hz")
    return plw_old * (target_hz / actual_hz) ** 2


def power_ratio_db(plw_new: float, plw_old: float) -> float:
    """Change in Bruker dB terms. Larger PLdB means LOWER power, hence the sign."""
    if plw_new <= 0 or plw_old <= 0:
        raise CestError("powers must be positive to express a dB change")
    return -10.0 * math.log10(plw_new / plw_old)


# --------------------------------------------------------------------------
# Nutation model and global search
# --------------------------------------------------------------------------

def nutation_model(
    t: np.ndarray, offset: float, amplitude: float, field_hz: float,
    phase: float, t2_s: float | None,
) -> np.ndarray:
    """C + A*exp(-t/T2)*sin(2*pi*B1*t + phi); T2 None gives the plain sine."""
    envelope = 1.0 if t2_s is None else np.exp(-np.asarray(t, dtype=float) / t2_s)
    return offset + amplitude * envelope * np.sin(2.0 * np.pi * field_hz * t + phase)


@dataclass(frozen=True)
class NutationFit:
    """Outcome of a nutation fit, in the units a record sheet wants."""

    field_hz: float
    field_error_hz: float
    amplitude: float
    offset: float
    phase_rad: float
    t2_s: float | None
    residual_rms: float
    residual_fraction: float      # RMS residual / |amplitude|
    damped: bool
    aicc: float
    n_points: int
    polished: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def flip_per_row_deg(self) -> float:
        """Actual flip angle delivered by one increment, at the fitted field."""
        return 360.0 * self.field_hz


def _aicc(residual_sum_squares: float, n: int, k: int) -> float:
    """Corrected Akaike criterion, for choosing plain vs damped sine.

    AICc rather than AIC because a nutation series is short -- 16 or 32
    points against 4 or 5 parameters -- which is exactly the regime where
    plain AIC under-penalises the extra parameter and would pick the damped
    model every time.
    """
    if n <= k + 1:
        return math.inf
    if residual_sum_squares <= 0:
        residual_sum_squares = np.finfo(float).tiny
    aic = n * math.log(residual_sum_squares / n) + 2 * k
    return aic + (2 * k * (k + 1)) / (n - k - 1)


def _linear_solve(t, y, field_hz, t2_s):
    """Exact least squares for (C, Ac, As) at fixed B1 and T2."""
    envelope = np.ones_like(t) if t2_s is None else np.exp(-t / t2_s)
    design = np.column_stack([
        np.ones_like(t),
        envelope * np.cos(2.0 * np.pi * field_hz * t),
        envelope * np.sin(2.0 * np.pi * field_hz * t),
    ])
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residual = y - design @ coefficients
    return coefficients, float(residual @ residual)


def search_nutation(
    t: np.ndarray,
    y: np.ndarray,
    *,
    nominal_hz: float,
    damped: bool,
    field_points: int = 4000,
    t2_points: int = 40,
) -> NutationFit:
    """Global separable-least-squares fit. No starting guess is required.

    The field grid runs from a twentieth of nominal up to the Nyquist limit
    of the sampling, 1/(2*dt). Beyond that a faster nutation is
    indistinguishable from a slower one -- it aliases -- so scanning past it
    would invent optima that the data cannot distinguish. The range is
    clipped rather than trusted, and a field landing near either end raises a
    warning instead of being reported as a clean number.
    """
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if t.shape != y.shape:
        raise CestError(f"axis and data disagree: {t.shape} vs {y.shape}")

    n = t.size
    n_params = 5 if damped else 4
    # n_params + 2 keeps AICc finite; below that a fit is meaningless anyway.
    if n < n_params + 2:
        raise CestError(
            f"need at least {n_params + 2} points for a "
            f"{'damped ' if damped else ''}sine fit, got {n}"
        )
    if not np.all(np.isfinite(t)) or not np.all(np.isfinite(y)):
        raise CestError("nutation series contains non-finite values")
    if np.ptp(y) == 0:
        raise CestError("nutation series is constant; nothing to fit")

    spacing = float(np.median(np.diff(t)))
    if spacing <= 0:
        raise CestError("nutation time axis must increase")
    nyquist = 0.5 / spacing
    low = max(nominal_hz / 20.0, 0.5 / (t[-1] - t[0] + spacing))
    high = min(nyquist, max(nominal_hz * 4.0, nominal_hz + 10.0))
    if not high > low:                                    # pragma: no cover
        raise CestError("no usable field range for this sampling")
    fields = np.linspace(low, high, int(field_points))

    span = float(t[-1] - t[0]) + spacing
    if damped:
        # Bounded below by two dwells (faster decay is unresolvable) and
        # above by a long multiple of the series, plus infinity so the
        # damped search can degenerate gracefully to no decay at all.
        t2_grid: list[float | None] = list(
            np.geomspace(2.0 * spacing, 40.0 * span, int(t2_points))
        ) + [None]
    else:
        t2_grid = [None]

    best = None
    for t2 in t2_grid:
        for field_hz in fields:
            coefficients, rss = _linear_solve(t, y, field_hz, t2)
            if best is None or rss < best[0]:
                best = (rss, field_hz, t2, coefficients)
    rss, field_hz, t2, coefficients = best        # type: ignore[misc]

    offset, cos_amp, sin_amp = coefficients
    amplitude = float(math.hypot(cos_amp, sin_amp))
    phase = float(math.atan2(cos_amp, sin_amp))
    if amplitude == 0:                                    # pragma: no cover
        raise CestError("fit collapsed to zero amplitude")

    residual_rms = math.sqrt(rss / n)
    notes: list[str] = []
    if field_hz > 0.95 * high:
        notes.append(
            f"fitted field {field_hz:.1f} Hz is at the top of the searchable "
            f"range ({high:.1f} Hz); the series may be undersampled"
        )
    if field_hz < 1.05 * low:
        notes.append(
            f"fitted field {field_hz:.1f} Hz is at the bottom of the "
            f"searchable range; less than one full cycle may be present"
        )
    if nominal_hz > 0 and not 0.5 <= field_hz / nominal_hz <= 2.0:
        notes.append(
            f"fitted field is {field_hz / nominal_hz:.2f}x the nominal "
            f"{nominal_hz:.1f} Hz -- check CNST and the power level"
        )
    if t2 is not None and t2 < 2.0 * spacing * 1.01:
        notes.append("fitted decay is faster than the sampling can resolve")

    return NutationFit(
        field_hz=float(field_hz),
        field_error_hz=float("nan"),      # only a polish yields a covariance
        amplitude=amplitude,
        offset=float(offset),
        phase_rad=phase,
        t2_s=None if t2 is None else float(t2),
        residual_rms=residual_rms,
        residual_fraction=residual_rms / amplitude,
        damped=t2 is not None,
        aicc=_aicc(rss, n, n_params if t2 is not None else 4),
        n_points=n,
        warnings=tuple(notes),
    )


def choose_nutation_model(
    t: np.ndarray, y: np.ndarray, *, nominal_hz: float, delta_aicc: float = 4.0
) -> tuple[NutationFit, NutationFit, bool]:
    """Fit both models and say whether damping is justified.

    Returns (plain, damped, damped_is_better). The manual asks for the plain
    sine unless residuals clearly favour the damped form, so the threshold is
    a genuine AICc margin rather than any improvement at all: on 19F data at
    112.5 Hz the damped model cut residuals from 27% to 3.9%, which clears
    this comfortably, while noise-driven improvements do not.
    """
    plain = search_nutation(t, y, nominal_hz=nominal_hz, damped=False)
    try:
        damped = search_nutation(t, y, nominal_hz=nominal_hz, damped=True)
    except CestError:                                     # pragma: no cover
        return plain, plain, False
    return plain, damped, bool(damped.aicc < plain.aicc - delta_aicc)


# --------------------------------------------------------------------------
# Z-spectrum
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ZSpectrum:
    """Normalised CEST profile, sorted by offset and carrying its own noise."""

    offsets_hz: np.ndarray
    intensity: np.ndarray           # I/I0
    reference_mask: np.ndarray      # True where a row served as an I0 reference
    i0: float
    i0_scatter: float               # sd of the references, in I/I0 units
    sfo1_mhz: float
    error: np.ndarray | None = None  # 1 sigma on each I/I0 point

    @property
    def noise(self) -> float:
        """One representative sigma, for thresholding candidate dips.

        Prefers the spread of the repeated reference offsets: they are the
        same measurement made more than once, so their scatter includes every
        source of run-to-run variation, not just thermal noise in one row.
        Falls back to the median propagated error, then to a visible-scatter
        estimate from the off-resonance points.
        """
        if math.isfinite(self.i0_scatter) and self.i0_scatter > 0:
            return float(self.i0_scatter)
        if self.error is not None and np.any(np.isfinite(self.error)):
            return float(np.median(self.error[np.isfinite(self.error)]))
        interior = self.intensity[~self.reference_mask]
        if interior.size > 3:
            # Median absolute deviation of successive differences: robust to
            # the dip itself, which is signal rather than scatter.
            diffs = np.diff(interior)
            mad = float(np.median(np.abs(diffs - np.median(diffs))))
            return 1.4826 * mad / math.sqrt(2.0)
        return float("nan")

    @property
    def offsets_ppm(self) -> np.ndarray:
        return self.offsets_hz / self.sfo1_mhz


def normalise_z(
    intensities: np.ndarray,
    offsets_hz: np.ndarray,
    *,
    sfo1_mhz: float,
    reference_min_hz: float | None = None,
    intensity_error: np.ndarray | None = None,
) -> ZSpectrum:
    """Divide by I0 taken from the most remote saturation offsets.

    Saturation far from every resonance does nothing, so those rows measure
    the unsaturated intensity under identical conditions -- which is what
    makes them a better I0 than a separate reference experiment. When more
    than one remote offset exists their scatter is reported: it is a direct,
    assumption-free measure of the noise on every other point, and on real
    19F data it separated peak height (0.007) from integration (0.090)
    decisively.
    """
    intensities = np.asarray(intensities, dtype=np.float64)
    offsets_hz = np.asarray(offsets_hz, dtype=np.float64)
    if intensities.shape != offsets_hz.shape:
        raise CestError(
            f"{intensities.size} intensities but {offsets_hz.size} offsets"
        )
    if intensities.size == 0:
        raise CestError("empty Z-spectrum")
    if not np.all(np.isfinite(intensities)):
        raise CestError("Z-spectrum contains non-finite intensities")

    absolute = np.abs(offsets_hz)
    if reference_min_hz is None:
        # Default: anything at least four times further out than the bulk of
        # the scan. Falls back to the single most remote point when the list
        # has no deliberate reference offsets at all.
        interior = np.median(absolute) if absolute.size else 0.0
        threshold = max(4.0 * interior, 0.0)
        mask = absolute >= threshold
        if not mask.any() or mask.all():
            mask = absolute >= absolute.max()
    else:
        mask = absolute >= reference_min_hz
        if not mask.any():
            raise CestError(
                f"no offset reaches the reference threshold "
                f"{reference_min_hz:g} Hz (furthest is {absolute.max():g} Hz)"
            )

    references = intensities[mask]
    i0 = float(np.mean(references))
    if not math.isfinite(i0) or i0 == 0.0:
        raise CestError("reference intensity is zero; cannot normalise")

    scatter = (
        float(np.std(references / i0, ddof=1)) if references.size > 1 else float("nan")
    )
    # I0 is an average of n references, so its own error shrinks as
    # 1/sqrt(n). Both terms matter: dividing by an uncertain I0 tilts the
    # whole spectrum, which a per-row error alone would not show.
    error = None
    if intensity_error is not None:
        intensity_error = np.asarray(intensity_error, dtype=np.float64)
        if intensity_error.shape != intensities.shape:
            raise CestError(
                f"{intensity_error.size} errors for {intensities.size} points"
            )
        n_ref = max(int(mask.sum()), 1)
        i0_error = float(np.sqrt(np.sum(intensity_error[mask] ** 2))) / n_ref
        ratio = intensities / i0
        error = np.sqrt(
            (intensity_error / i0) ** 2 + (ratio * i0_error / i0) ** 2
        )
        # Anchor the ABSOLUTE scale to the repeated references when there are
        # enough of them, keeping the RELATIVE differences between rows.
        #
        # A per-row estimate read off the spectrum is a good relative weight
        # -- it correctly says which rows are noisier -- but its absolute
        # level is unreliable, because a 19F spectrum holds resonances other
        # than the one being measured and because the error on a peak MAXIMUM
        # is not the error on a single point. Measured on real data it came
        # out 2.8x the true run-to-run scatter. The references are the same
        # measurement repeated under identical conditions, so their spread is
        # the one figure here that needs no modelling; rescaling to it is the
        # usual practice of matching quoted errors to observed variance.
        finite = np.isfinite(error) & (error > 0)
        if (
            int(mask.sum()) > 2
            and math.isfinite(scatter)
            and scatter > 0
            and finite.any()
        ):
            error = error * (scatter / float(np.median(error[finite])))

    order = np.argsort(offsets_hz)
    return ZSpectrum(
        offsets_hz=offsets_hz[order],
        intensity=intensities[order] / i0,
        reference_mask=mask[order],
        i0=i0,
        i0_scatter=scatter,
        sfo1_mhz=sfo1_mhz,
        error=None if error is None else error[order],
    )


def lorentzian_dip(x, baseline: float, depth: float, centre: float, width: float):
    """baseline - depth / (1 + (2*(x-centre)/width)^2). Width is FWHM."""
    if width <= 0:
        raise CestError("dip width must be positive")
    reduced = 2.0 * (np.asarray(x, float) - centre) / width
    return baseline - depth / (1.0 + reduced ** 2)


def dip_candidates(
    z: ZSpectrum, *, min_sigma: float = 3.0, exclude_reference: bool = True
) -> list[tuple[float, float, float]]:
    """Local minima as (offset_hz, depth, significance), deepest first.

    Significance is depth divided by the noise on a point, so the threshold
    is stated in the units that matter -- "three times the scatter" rather
    than an absolute depth that means different things on different samples.
    On 19F data with 64 offsets the reference scatter was 0.0021, so a dip of
    0.007 is already a 3-sigma feature; with 42 offsets it was 0.0072 and the
    same dip would not have been believable.
    """
    sigma = z.noise
    if not math.isfinite(sigma) or sigma <= 0:
        sigma = 0.01
    mask = ~z.reference_mask if exclude_reference else np.ones_like(
        z.reference_mask, dtype=bool
    )
    x = z.offsets_hz[mask]
    y = z.intensity[mask]
    if y.size < 3:
        return []
    baseline = float(np.median(y))
    found: list[tuple[float, float, float]] = []
    for i in range(1, y.size - 1):
        if y[i] < y[i - 1] and y[i] < y[i + 1]:
            depth = baseline - float(y[i])
            if depth <= 0:
                continue
            significance = depth / sigma
            if significance >= min_sigma:
                found.append((float(x[i]), depth, significance))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def remove_dip(z: ZSpectrum, baseline: float, depth: float,
               centre_hz: float, width_hz: float) -> ZSpectrum:
    """The Z-spectrum with one fitted Lorentzian divided out of the way.

    Direct saturation of the observed resonance is usually an order of
    magnitude deeper than any exchange feature, and its wings extend far
    enough to hide a small dip a few hundred Hz away. Subtracting the fitted
    profile flattens those wings so a secondary dip stands clear of them.

    This is a display aid for LOCATING a second dip, not a quantitative
    correction: the subtraction assumes the two features simply add, which a
    proper Bloch-McConnell treatment would not.
    """
    model = lorentzian_dip(z.offsets_hz, baseline, depth, centre_hz, width_hz)
    residual = z.intensity - model + baseline
    return ZSpectrum(
        offsets_hz=z.offsets_hz, intensity=residual,
        reference_mask=z.reference_mask, i0=z.i0, i0_scatter=z.i0_scatter,
        sfo1_mhz=z.sfo1_mhz, error=z.error,
    )


def two_lorentzian(x, baseline: float, depth_a: float, centre_a: float,
                   width_a: float, depth_b: float, centre_b: float,
                   width_b: float):
    """Baseline minus two Lorentzian dips.

    Two independent dips that simply subtract. That is an approximation: a
    real two-state CEST profile comes from the Bloch-McConnell equations, in
    which the major and minor dips are coupled through the exchange rate and
    are not separable like this. It is good enough to LOCATE a second dip and
    measure its position and width, and not good enough to derive rate
    constants from.
    """
    x = np.asarray(x, dtype=np.float64)
    first = depth_a / (1.0 + (2.0 * (x - centre_a) / width_a) ** 2)
    second = depth_b / (1.0 + (2.0 * (x - centre_b) / width_b) ** 2)
    return baseline - first - second


def seed_dips(
    z: ZSpectrum, *, min_depth: float = 0.05, exclude_reference: bool = True
) -> list[tuple[float, float]]:
    """Local minima worth fitting, as (offset_hz, depth) deepest first.

    A point qualifies only if it is below both neighbours and deeper than
    min_depth below the local baseline. min_depth should be set from the
    reference scatter, not chosen arbitrarily: on the 19F test data the
    off-resonance noise was +/-0.03 in I/I0, so anything shallower than about
    0.1 is not distinguishable from baseline with 42 offsets.
    """
    mask = ~z.reference_mask if exclude_reference else np.ones_like(
        z.reference_mask, dtype=bool
    )
    x = z.offsets_hz[mask]
    y = z.intensity[mask]
    if y.size < 3:
        return []
    found: list[tuple[float, float]] = []
    for i in range(1, y.size - 1):
        if y[i] < y[i - 1] and y[i] < y[i + 1]:
            depth = float(min(y[i - 1], y[i + 1]) - y[i])
            baseline_depth = float(np.median(y) - y[i])
            if depth > 0 and baseline_depth >= min_depth:
                found.append((float(x[i]), baseline_depth))
    found.sort(key=lambda pair: pair[1], reverse=True)
    return found


# --------------------------------------------------------------------------
# Row measurement
# --------------------------------------------------------------------------

def window_indices(ppm: np.ndarray, centre_ppm: float, half_width_ppm: float):
    """Index bounds of a ppm window on a DESCENDING Bruker axis.

    searchsorted needs an ascending sequence, so the axis and both bounds are
    negated together rather than the axis being reversed -- reversing would
    invert the returned indices relative to the data.
    """
    if half_width_ppm <= 0:
        raise CestError(f"window half-width must be positive, got {half_width_ppm}")
    ppm = np.asarray(ppm, dtype=np.float64)
    if ppm.size < 2:
        raise CestError("ppm axis too short to define a window")
    lo = int(np.searchsorted(-ppm, -(centre_ppm + half_width_ppm)))
    hi = int(np.searchsorted(-ppm, -(centre_ppm - half_width_ppm)))
    lo, hi = max(0, min(lo, ppm.size - 1)), max(1, min(hi, ppm.size))
    if hi <= lo:
        hi = min(lo + 1, ppm.size)
        lo = hi - 1
    return lo, hi


def measure_rows(
    rows: np.ndarray,
    ppm: np.ndarray,
    centre_ppm: float,
    half_width_ppm: float,
    *,
    mode: str = "integral",
    baseline: bool = False,
) -> np.ndarray:
    """One number per row from a window applied identically to every row.

    mode is 'integral', 'height' (largest value in the window) or 'fixed'
    (the value at the window's centre index).

    The window must be the same for every row and the result must stay
    SIGNED. For a nutation the sign inversion across rows IS the measurement;
    taking a magnitude, or letting each row find its own phase or window,
    destroys the physics and yields a rectified curve that fits at twice the
    true field.

    Which mode is right depends on the experiment, and measurement on real
    19F data settled it rather than taste. For a NUTATION, integration wins:
    it averages noise over a peak whose sign carries the information, and
    narrowing the window from +/-0.20 to +/-0.03 ppm cut fit residuals from
    7.0% to 3.9%. For a Z-SPECTRUM, height wins by an order of magnitude:
    repeated reference offsets scattered by 0.007 using height against 0.090
    using the integral, because a saturated peak leaves a drifting baseline
    that an integral accumulates without bound -- badly enough to drive I/I0
    negative, which is unphysical.
    """
    rows = np.atleast_2d(np.asarray(rows, dtype=np.float64))
    lo, hi = window_indices(ppm, centre_ppm, half_width_ppm)
    segment = rows[:, lo:hi]

    if baseline:
        width = hi - lo
        left = rows[:, max(0, lo - 4 * width):max(1, lo - width)]
        end = rows.shape[1]
        right = rows[:, min(end - 1, hi + width):min(end, hi + 4 * width)]
        parts = [p.mean(axis=1) for p in (left, right) if p.size]
        if parts:
            segment = segment - (sum(parts) / len(parts))[:, None]

    if mode == "integral":
        return segment.sum(axis=1)
    if mode == "height":
        return segment.max(axis=1)
    if mode == "fixed":
        return rows[:, (lo + hi) // 2]
    raise CestError(f"unknown measurement mode {mode!r}")


def row_noise(
    rows: np.ndarray, ppm: np.ndarray, centre_ppm: float, exclude_ppm: float
) -> np.ndarray:
    """One-sigma noise per row, from the spectrum away from the peak.

    This is the honest per-point error for a Z-spectrum: each row is an
    independent acquisition, so its own baseline noise sets how well its
    intensity is known. The reference scatter is a good aggregate figure but
    gives one number for the whole series, which cannot show that a row with
    fewer effective scans, or one sitting on a spoiled baseline, is less
    certain than its neighbours.

    Uses a median absolute deviation rather than a standard deviation so a
    residual peak, a spike or a baseline roll inside the sampled region
    inflates the estimate far less than it would otherwise.

    MAD alone is not enough, though. Taking it over the whole non-peak
    spectrum measured 0.0227 on real 19F data against a true run-to-run
    scatter of 0.0072 -- three times too large, because a 19F spectrum
    routinely holds other resonances and MAD only tolerates a small minority
    of outliers. So the region is cut into chunks and the SMALLEST chunk MAD
    is taken: the quietest stretch of baseline is the one with no signal in
    it, which is what a noise figure is supposed to describe.
    """
    rows = np.atleast_2d(np.asarray(rows, dtype=np.float64))
    ppm = np.asarray(ppm, dtype=np.float64)
    if rows.shape[1] != ppm.size:
        raise CestError("rows and ppm axis must be the same length")
    if exclude_ppm <= 0:
        raise CestError("exclusion width must be positive")
    keep = np.abs(ppm - centre_ppm) > exclude_ppm
    if keep.sum() < 16:
        raise CestError("too little signal-free spectrum to estimate noise")
    segment = rows[:, keep]

    n_chunks = max(1, min(16, segment.shape[1] // 64))
    if n_chunks == 1:
        median = np.median(segment, axis=1, keepdims=True)
        return 1.4826 * np.median(np.abs(segment - median), axis=1)

    width = segment.shape[1] // n_chunks
    per_chunk = np.empty((rows.shape[0], n_chunks), dtype=np.float64)
    for index in range(n_chunks):
        chunk = segment[:, index * width:(index + 1) * width]
        median = np.median(chunk, axis=1, keepdims=True)
        per_chunk[:, index] = 1.4826 * np.median(np.abs(chunk - median), axis=1)
    return per_chunk.min(axis=1)


def carrier_ppm(sfo1_mhz: float, sf_mhz: float) -> float:
    """Where the transmitter sits, in ppm on the processed axis.

    FQ1LIST offsets are applied to the CARRIER, so this is the true zero of
    the saturation axis -- and it is not the same as the observed peak.
    On real 19F data the peak sat 0.138 ppm from the carrier, which is why
    the direct-saturation dip appeared at +78 Hz rather than at zero.
    """
    if sf_mhz <= 0:
        raise CestError(f"SF must be positive, got {sf_mhz}")
    return (sfo1_mhz - sf_mhz) / sf_mhz * 1e6


def find_peaks_ppm(
    row: np.ndarray,
    ppm: np.ndarray,
    *,
    min_fraction: float = 0.02,
    min_separation_ppm: float = 0.05,
    min_snr: float = 20.0,
    noise: float | None = None,
    limit: int = 20,
) -> list[tuple[float, float]]:
    """Resonances in one row as (ppm, signed height), tallest first.

    Local maxima of the MAGNITUDE, so a negative peak is still found -- a
    row part-way through a nutation, or a badly phased spectrum, can invert
    one. The height returned keeps its sign, because that is what a
    measurement has to preserve.

    min_separation_ppm stops a single noisy peak being reported as several:
    without it the shoulders of one resonance each register as their own
    maximum, and the peak list fills with duplicates of the same signal.

    The threshold is driven by NOISE, not only by a fraction of the tallest
    peak. A fraction alone fails badly when one resonance dominates: on real
    19F data, 5% of the main peak still sat above the baseline ripple and
    returned 94 "peaks", and picking the one nearest the carrier then chose
    noise at -116.804 ppm instead of the true resonance at -116.663. Both
    tests must pass -- a candidate has to clear min_snr times the noise AND
    min_fraction of the maximum.
    """
    row = np.asarray(row, dtype=np.float64)
    ppm = np.asarray(ppm, dtype=np.float64)
    if row.size == 0 or row.size != ppm.size:
        raise CestError("row and ppm axis must be the same non-zero length")
    if not 0.0 < min_fraction < 1.0:
        raise CestError(f"min_fraction must be between 0 and 1, got {min_fraction}")

    magnitude = np.abs(row)
    ceiling = float(magnitude.max())
    if ceiling <= 0:
        return []
    if noise is None:
        # Same estimator as row_noise: the quietest chunk, so a second
        # resonance does not inflate the figure and hide itself.
        chunks = max(1, min(16, magnitude.size // 64))
        width = magnitude.size // chunks
        estimates = []
        for index in range(chunks):
            block = row[index * width:(index + 1) * width]
            if block.size:
                estimates.append(
                    1.4826 * float(np.median(np.abs(block - np.median(block))))
                )
        noise = min(estimates) if estimates else 0.0
    threshold = max(ceiling * min_fraction, min_snr * float(noise))

    interior = magnitude[1:-1]
    is_peak = (interior > magnitude[:-2]) & (interior >= magnitude[2:])
    candidates = np.flatnonzero(is_peak & (interior >= threshold)) + 1
    if candidates.size == 0:
        return []

    # Strongest first, then drop anything too close to one already kept.
    order = candidates[np.argsort(magnitude[candidates])[::-1]]
    kept: list[int] = []
    for index in order:
        if all(abs(ppm[index] - ppm[other]) >= min_separation_ppm for other in kept):
            kept.append(int(index))
    # Capped, strongest first. Lowering the threshold far enough always
    # starts returning baseline ripple -- on real 19F data, 3x noise gave 62
    # "peaks" where 8x gave the one real resonance -- and an unbounded list
    # is both unreadable and slow to build a widget from.
    if limit > 0:
        kept = kept[:limit]
    return [(float(ppm[i]), float(row[i])) for i in kept]


def nearest_peak_ppm(peaks: list[tuple[float, float]], target_ppm: float) -> float | None:
    """The peak closest to a given position, or None if there are none."""
    if not peaks:
        return None
    return min((p for p, _ in peaks), key=lambda p: abs(p - target_ppm))


def find_peak_ppm(row: np.ndarray, ppm: np.ndarray) -> float:
    """ppm of the largest-magnitude point in a row."""
    row = np.asarray(row, dtype=np.float64)
    ppm = np.asarray(ppm, dtype=np.float64)
    if row.size == 0 or row.size != ppm.size:
        raise CestError("row and ppm axis must be the same non-zero length")
    return float(ppm[int(np.argmax(np.abs(row)))])
