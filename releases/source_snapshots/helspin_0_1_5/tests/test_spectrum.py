"""Axis calibration and spectrum invariants."""

import numpy as np
import pytest

from helspin.domain.errors import EmptySpectrum, InvalidAxis
from helspin.domain.spectrum import (
    AxisCalibration,
    Spectrum1D,
    Spectrum2D,
    ranges_overlap,
    union_ppm_range,
)


def axis(size=1024, sw_hz=6000.0, obs_mhz=600.0, car_hz=3000.0, nucleus="1H"):
    return AxisCalibration(size, sw_hz, obs_mhz, car_hz, nucleus)


# --- construction -----------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"size": 0},
        {"size": -1},
        {"sw_hz": 0.0},
        {"sw_hz": -1.0},
        {"obs_mhz": 0.0},
        {"obs_mhz": -600.0},
    ],
)
def test_invalid_axis_rejected(kwargs):
    with pytest.raises(InvalidAxis):
        axis(**kwargs)


def test_size_one_axis_is_legal():
    a = axis(size=1)
    assert a.ppm_scale().shape == (1,)
    assert a.ppm_to_index(999.0) == 0
    assert a.index_to_ppm(0) == pytest.approx(a.centre_ppm)


# --- ppm ordering -----------------------------------------------------------


def test_ppm_scale_descends():
    scale = axis().ppm_scale()
    assert scale[0] > scale[-1]
    assert np.all(np.diff(scale) < 0)


def test_ppm_limits_left_greater_than_right():
    left, right = axis().ppm_limits()
    assert left > right


def test_scale_endpoints_match_limits():
    a = axis()
    left, right = a.ppm_limits()
    scale = a.ppm_scale()
    assert scale[0] == pytest.approx(left)
    assert scale[-1] == pytest.approx(right)


def test_sw_and_centre_in_ppm():
    a = axis(sw_hz=6000.0, obs_mhz=600.0, car_hz=3000.0)
    assert a.sw_ppm == pytest.approx(10.0)
    assert a.centre_ppm == pytest.approx(5.0)
    assert a.ppm_limits() == pytest.approx((10.0, 0.0))


# --- round trips ------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1, 17, 512, 1022, 1023])
def test_index_ppm_round_trip(index):
    """Off-by-one here puts peaks at the wrong shift and nothing crashes."""
    a = axis()
    assert a.ppm_to_index(a.index_to_ppm(index)) == index


def test_round_trip_at_both_ends():
    a = axis()
    left, right = a.ppm_limits()
    assert a.ppm_to_index(left) == 0
    assert a.ppm_to_index(right) == a.size - 1


def test_ppm_to_index_clamps_outside_range():
    a = axis()
    assert a.ppm_to_index(1e6) == 0
    assert a.ppm_to_index(-1e6) == a.size - 1


def test_index_to_ppm_is_not_clamped():
    a = axis()
    left, _ = a.ppm_limits()
    assert a.index_to_ppm(-1) > left


# --- slicing ----------------------------------------------------------------


def test_slice_covers_window_inclusively():
    a = axis()
    s = a.slice_for(8.0, 6.0)
    scale = a.ppm_scale()[s]
    assert scale[0] >= 6.0 and scale[-1] <= 8.0
    assert len(scale) > 1


def test_slice_accepts_reversed_bounds():
    """Rubber-band dragged right-to-left must still work."""
    a = axis()
    assert a.slice_for(6.0, 8.0) == a.slice_for(8.0, 6.0)


def test_degenerate_slice_is_single_point():
    a = axis()
    s = a.slice_for(5.0, 5.0)
    assert s.stop - s.start == 1


# --- spectra ----------------------------------------------------------------


def test_spectrum1d_rejects_size_mismatch():
    with pytest.raises(InvalidAxis):
        Spectrum1D(real=np.zeros(10), axis=axis(size=1024))


def test_spectrum1d_rejects_empty():
    with pytest.raises(EmptySpectrum):
        Spectrum1D(real=np.zeros(0), axis=axis(size=1))


def test_spectrum1d_rejects_2d_data():
    with pytest.raises(InvalidAxis):
        Spectrum1D(real=np.zeros((4, 4)), axis=axis(size=4))


def test_all_zero_spectrum_is_legal():
    """Legal to load; normalisation must guard separately."""
    s = Spectrum1D(real=np.zeros(8), axis=axis(size=8))
    assert s.real.sum() == 0


def test_can_rephase_reflects_imaginary_part():
    a = axis(size=8)
    assert not Spectrum1D(real=np.zeros(8), axis=a).can_rephase
    assert Spectrum1D(
        real=np.zeros(8), axis=a, complex_spectrum=np.zeros(8, dtype=complex)
    ).can_rephase


def test_spectrum2d_shape_checked_per_axis():
    f1, f2 = axis(size=64), axis(size=128)
    Spectrum2D(real=np.zeros((64, 128)), axis_f1=f1, axis_f2=f2)
    with pytest.raises(InvalidAxis):
        Spectrum2D(real=np.zeros((128, 64)), axis_f1=f1, axis_f2=f2)


def test_spectrum2d_rejects_1d_data():
    with pytest.raises(InvalidAxis):
        Spectrum2D(real=np.zeros(64), axis_f1=axis(size=64), axis_f2=axis(size=64))


# --- range combination ------------------------------------------------------


def test_union_not_intersection():
    """Full shows everything; dead space is preferable to hidden data."""
    assert union_ppm_range([(10.0, 0.0), (12.0, 2.0)]) == (12.0, 0.0)


def test_union_of_one():
    assert union_ppm_range([(8.0, 1.0)]) == (8.0, 1.0)


def test_union_handles_reversed_input():
    assert union_ppm_range([(0.0, 10.0)]) == (10.0, 0.0)


def test_union_of_nothing_raises():
    with pytest.raises(ValueError):
        union_ppm_range([])


def test_overlap_detection():
    assert ranges_overlap([(10.0, 0.0), (12.0, 2.0)])
    assert not ranges_overlap([(10.0, 8.0), (4.0, 2.0)])
    assert ranges_overlap([(10.0, 0.0)])
    assert ranges_overlap([])


def test_touching_ranges_count_as_overlapping():
    assert ranges_overlap([(10.0, 5.0), (5.0, 0.0)])
