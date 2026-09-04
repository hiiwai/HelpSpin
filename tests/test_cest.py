"""CEST calibration and Z-spectrum tests.

Several of these encode faults found on real 19F data during development and
would have shipped silently otherwise:

  * curve_fit alone reaches 100.0, 105.3 or 112.9 Hz on the SAME nutation
    series depending only on the initial T2 guess. test_grid_search_is_
    independent_of_seed pins the global search against that.
  * A CEST series with TD(F1)=42 processed at SI(F1)=32 loses ten offsets,
    including both high-frequency I0 references, with nothing in the
    processed data to say so.
  * Reproducing TopSpin's own 2rr from ser needs PHC1 negated and 180
    degrees added to PHC0; the sign-flipped variant correlates at -0.9957,
    which is the right curve upside down and would invert a Z-spectrum.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from helspin.domain.cest import (
    CestError,
    ZSpectrum,
    choose_nutation_model,
    corrected_power,
    find_peak_ppm,
    lorentzian_dip,
    measure_rows,
    nominal_field_hz,
    normalise_z,
    nutation_model,
    nutation_times,
    parse_fq_list,
    power_ratio_db,
    search_nutation,
    seed_dips,
    window_indices,
)
from helspin.infrastructure.cest_reader import classify
from helspin.services.cest_fit import fit_dip, fit_nutation, polish_nutation

# --------------------------------------------------------------- time axis

def test_row_one_is_45_degrees_and_row_two_is_90():
    """The pulse program increments AFTER the first row's go.

    p9 = inp9 = p8/2, so row 1 is a 45-degree pulse and row 2 the nominal
    90 degrees. An off-by-one here puts the first intensity maximum on row 1
    and mis-scales the fitted field by a whole increment.
    """
    p8 = 2500.0                      # 90 degrees at 100 Hz
    t = nutation_times(p8 * 0.5, p8 * 0.5, 4)
    assert t[0] == pytest.approx(1250e-6)
    assert t[1] == pytest.approx(2500e-6)
    flip = t * 100.0 * 360.0
    assert flip[0] == pytest.approx(45.0)
    assert flip[1] == pytest.approx(90.0)


def test_manual_mode_where_p9_differs_from_inp9():
    """-DMANUAL lets p9 and inp9 be set independently; both must be honoured."""
    t = nutation_times(1000.0, 250.0, 3)
    assert list(t * 1e6) == pytest.approx([1000.0, 1250.0, 1500.0])


@pytest.mark.parametrize(
    "p9,inp9,rows",
    [(0, 100, 4), (100, 0, 4), (100, -5, 4), (100, 100, 0)],
)
def test_time_axis_rejects_impossible_parameters(p9, inp9, rows):
    with pytest.raises(CestError):
        nutation_times(p9, inp9, rows)


def test_nominal_field_matches_the_manual():
    """nu1 = 1/(4*p90): 12 us gives 20833 Hz, as in Part 2."""
    assert nominal_field_hz(12.0) == pytest.approx(20833.33, rel=1e-4)
    with pytest.raises(CestError):
        nominal_field_hz(0.0)


# ------------------------------------------------------------------- power

def test_power_correction_and_transfer_between_fields():
    """PLW scales as B1^2, so the correction factor is field-independent.

    That is what allows a nutation acquired at CNST8 = 100 Hz to correct a
    CEST experiment run at CNST25 = 60 Hz without re-calibrating.
    """
    factor = corrected_power(1.0, 100.0, 112.51) / 1.0
    assert factor == pytest.approx((100.0 / 112.51) ** 2)
    # Same factor applied to a different nominal power.
    transferred = corrected_power(7.88973e-05, 100.0, 112.51)
    assert transferred == pytest.approx(7.88973e-05 * factor)
    # And the manual's own worked example, Part 4.3.
    assert corrected_power(73.0e-6, 60.0, 54.9) == pytest.approx(87.2e-6, rel=1e-2)


def test_power_correction_rejects_nonsense():
    for args in [(1.0, 60.0, 0.0), (0.0, 60.0, 50.0), (1.0, 0.0, 50.0)]:
        with pytest.raises(CestError):
            corrected_power(*args)
    with pytest.raises(CestError):
        power_ratio_db(0.0, 1.0)


def test_lower_power_is_a_positive_db_change():
    """Bruker dB grows as power falls; the sign must not be inverted."""
    assert power_ratio_db(0.5, 1.0) > 0


# ------------------------------------------------------------ frequency list

def test_bare_hz_list():
    values = parse_fq_list("-8000\n-100\n0\n100\n", sfo1_mhz=564.62)
    assert list(values) == [-8000.0, -100.0, 0.0, 100.0]


def test_crlf_comments_and_blank_lines():
    text = "; a comment\r\n\r\n-100\r\n# another\r\n0\r\n100\r\n"
    assert list(parse_fq_list(text, sfo1_mhz=564.62)) == [-100.0, 0.0, 100.0]


def test_ppm_list_uses_sfo1():
    values = parse_fq_list("P\n1.0\n-2.0\n", sfo1_mhz=564.62)
    assert values == pytest.approx([564.62, -1129.24])


def test_absolute_mhz_list_becomes_an_offset():
    values = parse_fq_list("SFO\n564.62\n564.621\n", sfo1_mhz=564.62)
    assert values == pytest.approx([0.0, 1000.0], abs=1e-6)


def test_bf_list_needs_bf1():
    with pytest.raises(CestError):
        parse_fq_list("BF\n564.62\n", sfo1_mhz=564.62)
    values = parse_fq_list("BF\n564.62\n", sfo1_mhz=564.62, bf1_mhz=564.62)
    assert values == pytest.approx([0.0])


def test_empty_and_malformed_lists_raise():
    with pytest.raises(CestError):
        parse_fq_list("\n; nothing\n", sfo1_mhz=564.62)
    with pytest.raises(CestError):
        parse_fq_list("100\nnot-a-number\n", sfo1_mhz=564.62)
    with pytest.raises(CestError):
        parse_fq_list("100\n", sfo1_mhz=0.0)


# ------------------------------------------------------------ nutation fit

def _synthetic(field_hz=112.5, t2=0.030, n=32, noise=0.0, seed=0):
    t = nutation_times(1250.0, 1250.0, n)
    y = nutation_model(t, 0.2, 1.0, field_hz, 0.0, t2)
    if noise:
        y = y + np.random.default_rng(seed).normal(0.0, noise, size=y.shape)
    return t, y


def test_grid_search_recovers_a_known_field():
    t, y = _synthetic()
    fit = search_nutation(t, y, nominal_hz=100.0, damped=True)
    assert fit.field_hz == pytest.approx(112.5, abs=0.5)


def test_grid_search_is_independent_of_seed():
    """The whole reason the grid exists.

    curve_fit from a naive guess landed on 100.0, 105.3 and 112.9 Hz for the
    same real series depending only on the initial T2. A global search has no
    seed to be sensitive to, so repeated calls must agree exactly.
    """
    t, y = _synthetic(noise=0.02)
    first = search_nutation(t, y, nominal_hz=100.0, damped=True)
    second = search_nutation(t, y, nominal_hz=100.0, damped=True)
    assert first.field_hz == second.field_hz
    # And it must not depend on the nominal value it is told, either.
    from_far = search_nutation(t, y, nominal_hz=60.0, damped=True)
    assert from_far.field_hz == pytest.approx(first.field_hz, rel=0.02)


def test_polish_never_makes_the_fit_worse():
    t, y = _synthetic(noise=0.05, seed=3)
    seed = search_nutation(t, y, nominal_hz=100.0, damped=True)
    polished = polish_nutation(t, y, seed)
    assert polished.residual_rms <= seed.residual_rms * 1.0000001


def test_aicc_prefers_damped_only_when_damping_is_real():
    t, damped_y = _synthetic(t2=0.020, noise=0.01)
    _, _, use_damped = choose_nutation_model(t, damped_y, nominal_hz=100.0)
    assert use_damped
    t, plain_y = _synthetic(t2=1e6, noise=0.01)
    _, _, use_damped = choose_nutation_model(t, plain_y, nominal_hz=100.0)
    assert not use_damped


def test_amplitude_is_reported_positive():
    """sin(x) and -sin(x+pi) are the same curve; the sign must be canonical."""
    t, y = _synthetic()
    fit = fit_nutation(t, -y, nominal_hz=100.0, damped=True)
    assert fit.amplitude > 0
    # Wrapped into the half-open interval [-pi, pi), the usual convention
    # for (x + pi) %% (2*pi) - pi.
    assert -math.pi <= fit.phase_rad < math.pi


def test_too_few_points_refuses_rather_than_overfits():
    t = nutation_times(1250.0, 1250.0, 5)
    with pytest.raises(CestError):
        search_nutation(t, np.arange(5.0), nominal_hz=100.0, damped=True)


def test_constant_and_nonfinite_series_are_rejected():
    t = nutation_times(1250.0, 1250.0, 16)
    with pytest.raises(CestError):
        search_nutation(t, np.ones(16), nominal_hz=100.0, damped=False)
    bad = np.ones(16)
    bad[3] = np.nan
    with pytest.raises(CestError):
        search_nutation(t, bad, nominal_hz=100.0, damped=False)


def test_mismatched_axis_and_data_are_rejected():
    with pytest.raises(CestError):
        search_nutation(np.arange(10.0), np.arange(9.0), nominal_hz=100.0, damped=False)


def test_undersampled_field_warns_rather_than_reporting_cleanly():
    """Above Nyquist a fast nutation aliases; the fit must say so."""
    t, y = _synthetic(field_hz=380.0, t2=1.0)
    fit = search_nutation(t, y, nominal_hz=100.0, damped=False)
    assert fit.warnings


# ----------------------------------------------------------- row measurement

def _fake_rows():
    ppm = np.linspace(0.0, -10.0, 501)          # descending, Bruker order
    centre = -5.0
    peak = np.exp(-((ppm - centre) ** 2) / (2 * 0.05 ** 2))
    scale = np.array([1.0, 0.5, -0.5, -1.0])
    return scale[:, None] * peak[None, :], ppm, centre


def test_window_indices_handle_a_descending_axis():
    _, ppm, centre = _fake_rows()
    lo, hi = window_indices(ppm, centre, 0.2)
    assert lo < hi
    assert ppm[lo] > centre > ppm[hi - 1]


def test_window_is_clamped_at_the_edges_not_wrapped():
    _, ppm, _ = _fake_rows()
    lo, hi = window_indices(ppm, 100.0, 0.2)      # entirely off the axis
    assert 0 <= lo < hi <= ppm.size


def test_window_rejects_a_nonpositive_width():
    _, ppm, centre = _fake_rows()
    with pytest.raises(CestError):
        window_indices(ppm, centre, 0.0)


def test_integration_keeps_the_sign():
    """The sign inversion across rows IS the nutation measurement."""
    rows, ppm, centre = _fake_rows()
    values = measure_rows(rows, ppm, centre, 0.2, mode="integral")
    assert values[0] > 0 and values[3] < 0
    assert values[0] == pytest.approx(-values[3])


def test_measurement_modes_agree_on_sign_and_ordering():
    rows, ppm, centre = _fake_rows()
    for mode in ("integral", "height", "fixed"):
        values = measure_rows(rows, ppm, centre, 0.2, mode=mode)
        assert values[0] > values[1]


def test_unknown_measurement_mode_is_rejected():
    rows, ppm, centre = _fake_rows()
    with pytest.raises(CestError):
        measure_rows(rows, ppm, centre, 0.2, mode="magnitude")


def test_find_peak_ppm():
    rows, ppm, centre = _fake_rows()
    assert find_peak_ppm(rows[0], ppm) == pytest.approx(centre, abs=0.05)
    with pytest.raises(CestError):
        find_peak_ppm(rows[0], ppm[:-1])


# ------------------------------------------------------------- Z-spectrum

def _fake_z(depth=0.9, centre=0.0, noise=0.0):
    offsets = np.array(
        [-8000.0] + list(np.arange(-1000.0, 1001.0, 100.0)) + [8000.0, 9000.0]
    )
    intensity = 100.0 * (
        1.0 - depth / (1.0 + (2.0 * (offsets - centre) / 200.0) ** 2)
    )
    if noise:
        intensity = intensity + np.random.default_rng(1).normal(0, noise, offsets.shape)
    return offsets, intensity


def test_normalisation_picks_remote_offsets_and_reports_scatter():
    offsets, intensity = _fake_z()
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    assert int(z.reference_mask.sum()) == 3
    assert z.intensity.max() == pytest.approx(1.0, abs=0.02)
    # Not exactly zero: the Lorentzian has a tail at the reference offsets.
    assert z.i0_scatter == pytest.approx(0.0, abs=1e-3)


def test_output_is_sorted_by_offset():
    offsets, intensity = _fake_z()
    shuffled = np.random.default_rng(0).permutation(offsets.size)
    z = normalise_z(intensity[shuffled], offsets[shuffled], sfo1_mhz=564.62)
    assert np.all(np.diff(z.offsets_hz) > 0)


def test_reference_mask_travels_with_the_sort():
    """Sorting must not divorce the mask from its points."""
    offsets, intensity = _fake_z()
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    assert set(np.abs(z.offsets_hz[z.reference_mask])) == {8000.0, 9000.0}


def test_single_reference_gives_nan_scatter_not_a_crash():
    offsets = np.array([-4000.0, -100.0, 0.0, 100.0])
    z = normalise_z(np.array([10.0, 9.0, 1.0, 9.0]), offsets, sfo1_mhz=564.62,
                    reference_min_hz=3000.0)
    assert math.isnan(z.i0_scatter)


def test_ppm_conversion_uses_sfo1():
    offsets, intensity = _fake_z()
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    assert z.offsets_ppm == pytest.approx(z.offsets_hz / 564.62)


def test_normalisation_failure_modes():
    offsets, intensity = _fake_z()
    with pytest.raises(CestError):
        normalise_z(intensity[:-1], offsets, sfo1_mhz=564.62)
    with pytest.raises(CestError):
        normalise_z(np.array([]), np.array([]), sfo1_mhz=564.62)
    with pytest.raises(CestError):
        normalise_z(np.zeros_like(intensity), offsets, sfo1_mhz=564.62)
    with pytest.raises(CestError):
        normalise_z(intensity, offsets, sfo1_mhz=564.62, reference_min_hz=1e9)
    bad = intensity.copy()
    bad[2] = np.inf
    with pytest.raises(CestError):
        normalise_z(bad, offsets, sfo1_mhz=564.62)


def test_dip_is_found_and_fitted_at_the_right_place():
    offsets, intensity = _fake_z(centre=300.0, noise=0.3)
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    seeds = seed_dips(z, min_depth=0.1)
    assert seeds
    fitted = fit_dip(z, seeds[0][0])
    assert fitted.centre_hz == pytest.approx(300.0, abs=25.0)
    assert fitted.centre_ppm == pytest.approx(300.0 / 564.62, abs=0.05)
    assert fitted.width_hz == pytest.approx(200.0, rel=0.35)


def test_shallow_noise_is_not_reported_as_a_dip():
    offsets, intensity = _fake_z(depth=0.0, noise=0.5)
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    assert seed_dips(z, min_depth=0.1) == []


def test_reference_points_are_excluded_from_dip_search():
    """A remote reference must never be mistaken for a dip."""
    offsets, intensity = _fake_z()
    intensity[0] *= 0.2                       # depress the -8000 Hz reference
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    assert all(abs(hz) < 4000 for hz, _ in seed_dips(z, min_depth=0.05))


def test_lorentzian_rejects_a_nonpositive_width():
    with pytest.raises(CestError):
        lorentzian_dip(np.array([0.0]), 1.0, 0.5, 0.0, 0.0)


def test_dip_fit_needs_enough_points():
    z = ZSpectrum(
        offsets_hz=np.array([0.0, 1.0]), intensity=np.array([1.0, 0.5]),
        reference_mask=np.array([False, False]), i0=1.0, i0_scatter=float("nan"),
        sfo1_mhz=564.62,
    )
    with pytest.raises(CestError):
        fit_dip(z, 0.0)


# ------------------------------------------------------------ classification

@pytest.mark.parametrize("pulprog,expected", [
    ("<19f_calib_nut.iw>", "nutation"),
    ("19f_calib_nut.cw", "nutation"),
    ("<19f_cest.iw>", "cest"),
    ("19F_CEST", "cest"),
    ("zg30", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_pulse_programme_classification(pulprog, expected):
    """Sites rename pulse programs; matching exact published names fails.

    The shipped test data is `19f_calib_nut.iw` and `19f_cest.iw`, renamed
    from the `.cw` originals.
    """
    assert classify(pulprog) == expected


# ------------------------------------------------- errors, noise, candidates

def test_row_noise_ignores_other_resonances():
    """MAD over the whole non-peak region is fooled by a second peak.

    On real 19F data that read 2.8x the true scatter, because a spectrum
    routinely holds resonances besides the one being measured. Taking the
    quietest chunk is what makes the figure a noise estimate.
    """
    rng = np.random.default_rng(0)
    ppm = np.linspace(0.0, -20.0, 4096)
    rows = rng.normal(0.0, 1.0, size=(3, ppm.size))
    # A big interfering peak occupying a third of the spectrum.
    rows += 500.0 * np.exp(-((ppm + 15.0) ** 2) / (2 * 1.5 ** 2))[None, :]
    from helspin.domain.cest import row_noise

    sigma = row_noise(rows, ppm, centre_ppm=-5.0, exclude_ppm=1.0)
    assert sigma == pytest.approx(1.0, rel=0.35)


def test_row_noise_rejects_impossible_arguments():
    from helspin.domain.cest import row_noise

    ppm = np.linspace(0.0, -10.0, 512)
    rows = np.zeros((2, 512))
    with pytest.raises(CestError):
        row_noise(rows, ppm[:-1], -5.0, 1.0)
    with pytest.raises(CestError):
        row_noise(rows, ppm, -5.0, 0.0)
    with pytest.raises(CestError):
        row_noise(rows, ppm, -5.0, 100.0)      # excludes everything


def test_errors_are_anchored_to_the_reference_scatter():
    """Quoted errors must match observed run-to-run variance."""
    offsets, intensity = _fake_z(noise=0.5)
    raw = np.full(offsets.shape, 5.0)          # deliberately wrong scale
    raw[3] *= 3.0                              # one genuinely noisier row
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62, intensity_error=raw)
    assert z.error is not None
    assert float(np.median(z.error)) == pytest.approx(z.i0_scatter, rel=1e-6)
    # Relative weighting survives the rescale.
    assert z.error.max() > 2.5 * float(np.median(z.error))


def test_error_length_must_match():
    offsets, intensity = _fake_z()
    with pytest.raises(CestError):
        normalise_z(intensity, offsets, sfo1_mhz=564.62,
                    intensity_error=np.ones(3))


def test_noise_falls_back_when_there_is_no_reference_scatter():
    """One reference gives no scatter, but a noise figure is still needed."""
    offsets = np.array([-9000.0, -200.0, -100.0, 0.0, 100.0, 200.0])
    intensity = np.array([10.0, 9.9, 9.8, 2.0, 9.9, 10.1])
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62, reference_min_hz=5000.0)
    assert math.isnan(z.i0_scatter)
    assert math.isfinite(z.noise) and z.noise > 0


def test_candidates_are_reported_in_sigma_not_absolute_depth():
    from helspin.domain.cest import dip_candidates

    offsets, intensity = _fake_z(centre=300.0, noise=0.2)
    z = normalise_z(intensity, offsets, sfo1_mhz=564.62)
    loose = dip_candidates(z, min_sigma=1.0)
    strict = dip_candidates(z, min_sigma=50.0)
    assert len(loose) >= len(strict)
    for _, _, significance in loose:
        assert significance >= 1.0


def test_removing_a_dip_flattens_it_and_leaves_the_rest():
    """The point of the residual view: a second dip must survive it."""
    from helspin.domain.cest import dip_candidates, remove_dip

    offsets = np.array([-8000.0] + list(np.arange(-1500.0, 1501.0, 25.0)) + [8000.0])
    big = 0.9 / (1.0 + (2.0 * offsets / 200.0) ** 2)
    small = 0.05 / (1.0 + (2.0 * (offsets - 700.0) / 100.0) ** 2)
    z = normalise_z(100.0 * (1.0 - big - small), offsets, sfo1_mhz=564.62)
    fitted = fit_dip(z, 0.0)
    flat = remove_dip(z, fitted.baseline, fitted.depth,
                      fitted.centre_hz, fitted.width_hz)
    # The big dip is gone...
    near_zero = np.abs(flat.offsets_hz) < 60.0
    assert flat.intensity[near_zero].min() > 0.5
    # ...and the small one is still there, and now findable.
    found = [c[0] for c in dip_candidates(flat, min_sigma=2.0)]
    assert any(abs(offset - 700.0) < 120.0 for offset in found)
