"""Y scaling and difference spectra, including the two silent-bug traps."""

import numpy as np
import pytest

from helspin.domain.errors import EmptySpectrum, MissingParameter
from helspin.domain.overlay import (
    acquisition_scale,
    common_range,
    difference,
    fit_k,
    noise_rms,
    scale_factors,
    stack_offsets,
)
from helspin.domain.project import YScaleMode
from helspin.domain.spectrum import AxisCalibration, Spectrum1D


def spec(size=1024, sw_hz=6000.0, obs_mhz=600.0, car_hz=3000.0, ns=16, rg=101.0,
         peaks=((5.0, 1.0),), noise=0.0, seed=0, width=0.05):
    """Synthetic spectrum.

    width defaults to 0.05 ppm: at 1024 points over 10 ppm the grid step is
    ~0.01 ppm, so a narrower peak would be undersampled and interpolation
    comparisons would fail for reasons of fixture quality, not code.
    """
    axis = AxisCalibration(size, sw_hz, obs_mhz, car_hz, "1H")
    ppm = axis.ppm_scale()
    data = np.zeros(size)
    for centre, height in peaks:
        data += height * np.exp(-((ppm - centre) ** 2) / (2 * width**2))
    if noise:
        data = data + np.random.default_rng(seed).normal(0, noise, size)
    return Spectrum1D(real=data, axis=axis, ns=ns, rg=rg)


# --- acquisition scaling ----------------------------------------------------


def test_acquisition_scale_is_ns_times_rg():
    assert acquisition_scale(spec(ns=16, rg=101.0)) == pytest.approx(1616.0)


@pytest.mark.parametrize("ns,rg", [(0, 101.0), (16, 0.0), (-1, 101.0), (16, -2.0)])
def test_acquisition_scale_rejects_nonpositive(ns, rg):
    with pytest.raises(MissingParameter):
        acquisition_scale(spec(ns=ns, rg=rg))


def test_absolute_mode_equalises_a_16_vs_256_scan_pair():
    """The trap: without dividing by NS, a 256-scan spectrum just looks stronger."""
    quick = spec(ns=16, rg=100.0, peaks=((5.0, 16.0),))
    long = spec(ns=256, rg=100.0, peaks=((5.0, 256.0),))
    fa, fb = scale_factors([quick, long], YScaleMode.ABSOLUTE)
    assert np.max(quick.real) * fa == pytest.approx(np.max(long.real) * fb, rel=1e-9)


def test_absolute_mode_without_correction_would_differ():
    """Guards the guard: confirm the raw comparison really is misleading."""
    quick = spec(ns=16, peaks=((5.0, 16.0),))
    long = spec(ns=256, peaks=((5.0, 256.0),))
    assert np.max(quick.real) != pytest.approx(np.max(long.real))


# --- max mode ---------------------------------------------------------------


def test_max_mode_normalises_each_trace_to_one():
    a, b = spec(peaks=((5.0, 1.0),)), spec(peaks=((5.0, 50.0),))
    for s, f in zip((a, b), scale_factors([a, b], YScaleMode.MAX)):
        assert np.max(np.abs(s.real)) * f == pytest.approx(1.0)


def test_max_mode_on_all_zero_spectrum_does_not_divide_by_zero():
    flat = Spectrum1D(real=np.zeros(64), axis=AxisCalibration(64, 6000.0, 600.0))
    assert scale_factors([flat], YScaleMode.MAX) == [1.0]


def test_max_mode_handles_negative_peaks():
    axis = AxisCalibration(64, 6000.0, 600.0)
    s = Spectrum1D(real=np.full(64, -4.0), axis=axis)
    assert scale_factors([s], YScaleMode.MAX)[0] == pytest.approx(0.25)


# --- SNR mode ---------------------------------------------------------------


def test_identical_snr_equalises_noise_levels():
    quiet = spec(noise=0.01, seed=1)
    loud = spec(noise=0.10, seed=2)
    fa, fb = scale_factors([quiet, loud], YScaleMode.IDENTICAL_SNR)
    assert noise_rms(quiet.real) * fa == pytest.approx(noise_rms(loud.real) * fb, rel=0.3)


def test_snr_mode_on_noise_free_spectra_falls_back_to_unity():
    clean = Spectrum1D(real=np.zeros(128), axis=AxisCalibration(128, 6000.0, 600.0))
    assert scale_factors([clean, clean], YScaleMode.IDENTICAL_SNR) == [1.0, 1.0]


def test_noise_rms_auto_window_avoids_the_peak():
    s = spec(peaks=((5.0, 100.0),), noise=0.05, seed=3)
    assert noise_rms(s.real) < 1.0


def test_noise_rms_with_explicit_window():
    s = spec(peaks=((5.0, 100.0),), noise=0.05, seed=4)
    on_peak = noise_rms(s.real, s.axis.slice_for(5.05, 4.95))
    off_peak = noise_rms(s.real, s.axis.slice_for(9.5, 9.0))
    assert on_peak > off_peak


def test_noise_rms_rejects_empty():
    with pytest.raises(EmptySpectrum):
        noise_rms(np.zeros(0))


def test_noise_rms_rejects_empty_window():
    with pytest.raises(EmptySpectrum):
        noise_rms(np.ones(64), slice(10, 10))


def test_noise_rms_on_single_point():
    assert noise_rms(np.array([3.0])) == pytest.approx(3.0)


# --- reference mode ---------------------------------------------------------


def test_reference_mode_normalises_on_a_window():
    a, b = spec(peaks=((5.0, 1.0),)), spec(peaks=((5.0, 3.0),))
    window = a.axis.slice_for(5.5, 4.5)
    fa, fb = scale_factors([a, b], YScaleMode.REFERENCE, reference_window=window)
    area_a = np.trapezoid(np.abs(a.real[window])) * fa
    area_b = np.trapezoid(np.abs(b.real[window])) * fb
    assert area_a == pytest.approx(area_b, rel=1e-9)


def test_reference_mode_requires_a_window():
    with pytest.raises(ValueError):
        scale_factors([spec()], YScaleMode.REFERENCE)


def test_reference_mode_with_empty_region_falls_back():
    flat = Spectrum1D(real=np.zeros(64), axis=AxisCalibration(64, 6000.0, 600.0))
    assert scale_factors([flat], YScaleMode.REFERENCE, reference_window=slice(0, 10)) == [1.0]


def test_scale_factors_of_nothing():
    assert scale_factors([], YScaleMode.MAX) == []


# --- stacking ---------------------------------------------------------------


def test_stack_offsets_are_evenly_spaced():
    assert stack_offsets(3, 0.5, 2.0) == [0.0, 1.0, 2.0]


def test_stack_offsets_recompute_from_visible_count():
    """Hiding a middle trace must not leave a gap."""
    assert stack_offsets(2, 0.5, 2.0) == [0.0, 1.0]


def test_stack_offsets_of_zero_spacing_is_overlay():
    assert stack_offsets(4, 0.0, 2.0) == [0.0, 0.0, 0.0, 0.0]


# --- difference: the interpolation trap -------------------------------------


def test_difference_of_identical_spectra_is_zero():
    s = spec(noise=0.0)
    _, d = difference(s, s)
    assert np.allclose(d, 0.0, atol=1e-12)


def test_difference_interpolates_across_different_point_counts():
    """The classic silent bug: index subtraction on mismatched grids."""
    a = spec(size=1024, peaks=((5.0, 1.0),))
    b = spec(size=2048, peaks=((5.0, 1.0),))
    assert a.real.size != b.real.size
    ppm, d = difference(a, b)
    assert ppm.size == a.real.size
    assert np.max(np.abs(d)) < 0.02


def test_index_subtraction_would_have_been_wrong():
    """Confirms the trap is real: same peak, different SW, index-wise disagrees."""
    a = spec(size=1024, sw_hz=6000.0, peaks=((5.0, 1.0),))
    b = spec(size=1024, sw_hz=9000.0, peaks=((5.0, 1.0),))
    naive = a.real - b.real
    _, correct = difference(a, b)
    assert np.max(np.abs(naive)) > 10 * np.max(np.abs(correct))


def test_difference_respects_k():
    a = spec(peaks=((5.0, 2.0),))
    b = spec(peaks=((5.0, 1.0),))
    _, d = difference(a, b, k=2.0)
    assert np.allclose(d, 0.0, atol=1e-12)


def test_k_zero_returns_the_minuend():
    a, b = spec(peaks=((5.0, 2.0),)), spec(peaks=((5.0, 1.0),))
    _, d = difference(a, b, k=0.0)
    assert np.allclose(d, a.real)


def test_negative_k_sums():
    """Negative k adds. Tolerance is loose because the Gaussian apex falls
    between grid points, so the sampled maximum is slightly under 2.0."""
    a, b = spec(peaks=((5.0, 1.0),)), spec(peaks=((5.0, 1.0),))
    _, d = difference(a, b, k=-1.0)
    assert np.max(d) == pytest.approx(2.0, rel=1e-2)


def test_shift_is_applied_before_subtraction():
    """Deliberately offset pair: shifting must cancel the peak."""
    a = spec(peaks=((5.0, 1.0),))
    b = spec(peaks=((4.5, 1.0),))
    _, unshifted = difference(a, b)
    _, shifted = difference(a, b, shift_b=0.5)
    assert np.max(np.abs(shifted)) < np.max(np.abs(unshifted)) / 10


def test_difference_restricted_to_the_overlap():
    a = spec(sw_hz=6000.0, car_hz=3000.0)
    b = spec(sw_hz=3000.0, car_hz=3000.0)
    ppm, d = difference(a, b)
    b_left, b_right = b.axis.ppm_limits()
    assert ppm.max() <= b_left + 1e-9
    assert ppm.min() >= b_right - 1e-9
    assert ppm.size < a.real.size


def test_non_overlapping_spectra_raise():
    a = spec(car_hz=0.0, sw_hz=600.0)
    b = spec(car_hz=60000.0, sw_hz=600.0)
    with pytest.raises(EmptySpectrum):
        difference(a, b)


def test_common_range_orders_descending():
    left, right = common_range(spec(), spec())
    assert left > right


# --- k fitting --------------------------------------------------------------


def test_fit_k_recovers_a_known_ratio():
    a = spec(peaks=((5.0, 3.0),), noise=0.0)
    b = spec(peaks=((5.0, 1.0),), noise=0.0)
    assert fit_k(a, b) == pytest.approx(3.0, rel=1e-6)


def test_fit_k_over_a_window():
    a = spec(peaks=((5.0, 2.0), (7.0, 9.0)), noise=0.0)
    b = spec(peaks=((5.0, 1.0), (7.0, 1.0)), noise=0.0)
    assert fit_k(a, b, window=(5.5, 4.5)) == pytest.approx(2.0, rel=1e-3)


def test_fit_k_on_a_silent_window_returns_unity():
    """<B,B> ~ 0 must not divide by zero."""
    a = spec(peaks=((5.0, 1.0),), noise=0.0)
    b = Spectrum1D(real=np.zeros(1024), axis=a.axis, ns=16, rg=101.0)
    assert fit_k(a, b) == 1.0


def test_fit_k_empty_window_raises():
    a, b = spec(), spec()
    with pytest.raises(EmptySpectrum):
        fit_k(a, b, window=(100.0, 99.0))


def test_fit_k_then_difference_flattens_the_residual():
    a = spec(peaks=((5.0, 2.5),), noise=0.0)
    b = spec(peaks=((5.0, 1.0),), noise=0.0)
    _, d = difference(a, b, k=fit_k(a, b))
    assert np.max(np.abs(d)) < 1e-6
