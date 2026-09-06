"""Reader and panel tests against synthetic Bruker datasets.

The SI(F1) vs TD(F1) cases here are the reason this module exists. A real
19F CEST series with TD(F1) = 42 processed at SI(F1) = 32 lost ten offsets,
including both high-frequency I0 references, and nothing in the processed
data recorded the loss.
"""

from __future__ import annotations

import numpy as np
import pytest

from helspin.domain.cest import CestError, measure_rows, normalise_z
from helspin.domain.errors import DatasetNotFound
from helspin.infrastructure.cest_reader import (
    find_fq_list,
    load_pseudo_2d,
    nominal_nutation_field,
    nutation_axis,
    read_offsets,
)

from .cest_fixtures import DEFAULT_OFFSETS, make_dataset

N = len(DEFAULT_OFFSETS)


@pytest.fixture
def truncated(tmp_path):
    """SI(F1) < TD(F1): xf2 discarded rows. The real-world failure."""
    return make_dataset(tmp_path / "trunc", n_rows=N, si_f1=16, offsets=DEFAULT_OFFSETS)


@pytest.fixture
def exact(tmp_path):
    return make_dataset(tmp_path / "exact", n_rows=N, si_f1=N, offsets=DEFAULT_OFFSETS)


@pytest.fixture
def zero_filled(tmp_path):
    """SI(F1) > TD(F1): harmless padding, must be trimmed not rejected."""
    return make_dataset(tmp_path / "pad", n_rows=N, si_f1=64, offsets=DEFAULT_OFFSETS)


def test_truncated_pdata_falls_back_to_ser(truncated):
    data = load_pseudo_2d(truncated)
    assert data.source == "ser"
    assert data.n_rows == N
    assert any("only 16 of" in note for note in data.notes)


def test_complete_pdata_is_used_directly(exact):
    data = load_pseudo_2d(exact)
    assert data.source == "2rr"
    assert data.n_rows == N
    assert data.notes == ()


def test_zero_filled_pdata_is_trimmed_and_used(zero_filled):
    """Setting SI = 64 for 42 offsets is the RECOMMENDED fix, so it must work."""
    data = load_pseudo_2d(zero_filled)
    assert data.source == "2rr"
    assert data.n_rows == N
    assert any("zero-filled" in note for note in data.notes)


def test_blank_padding_rows_never_reach_the_z_spectrum(zero_filled):
    """A retained all-zero row would plot as spurious total saturation."""
    data = load_pseudo_2d(zero_filled)
    assert np.all(np.any(data.rows != 0, axis=1))


def test_forcing_2rr_on_a_truncated_set_is_allowed_but_flagged(truncated):
    data = load_pseudo_2d(truncated, prefer="2rr")
    assert data.source == "2rr"
    assert data.n_rows == 16
    assert any("TRUNCATED" in note for note in data.notes)


def test_forcing_ser_ignores_a_complete_pdata(exact):
    assert load_pseudo_2d(exact, prefer="ser").source == "ser"


def test_unknown_source_preference_is_rejected(exact):
    with pytest.raises(CestError):
        load_pseudo_2d(exact, prefer="magnitude")


def test_missing_directory_raises(tmp_path):
    with pytest.raises(DatasetNotFound):
        load_pseudo_2d(tmp_path / "nope")


def test_offsets_come_from_the_list_in_the_experiment(exact):
    offsets, path = read_offsets(exact, 564.62)
    assert offsets.size == N
    assert path.name == "synth.list"
    assert offsets.min() == -8000.0


def test_missing_frequency_list_is_reported_not_guessed(tmp_path):
    root = make_dataset(tmp_path / "nolist", n_rows=N, si_f1=N, offsets=None)
    assert find_fq_list(root) is None
    assert read_offsets(root, 564.62) == (None, None)


def test_ser_processing_reproduces_pdata_shape(exact):
    """Both routes must yield the same rows and the same axis length."""
    from_ser = load_pseudo_2d(exact, prefer="ser")
    from_pdata = load_pseudo_2d(exact, prefer="2rr")
    assert from_ser.rows.shape == from_pdata.rows.shape
    assert from_ser.ppm.shape == from_pdata.ppm.shape


def test_ppm_axis_descends_and_ignores_the_bogus_f1_axis(exact):
    """acqu2s claims NUC1 = 1H and OFFSET = 4.7 even for a 19F series.

    Nothing is transformed in F1, so that axis is meaningless and must not
    leak into the direct dimension's calibration.
    """
    data = load_pseudo_2d(exact)
    assert np.all(np.diff(data.ppm) < 0)
    assert data.sfo1_mhz == pytest.approx(564.62)
    assert data.ppm[0] == pytest.approx(-100.0)


def test_nutation_axis_from_a_real_acqus(tmp_path):
    root = make_dataset(tmp_path / "nut", n_rows=16, si_f1=16,
                        pulprog="19f_calib_nut.iw", offsets=None)
    data = load_pseudo_2d(root)
    t = nutation_axis(data)
    assert t[0] == pytest.approx(1250e-6)
    assert t[1] == pytest.approx(2500e-6)
    assert nominal_nutation_field(data) == pytest.approx(100.0)


def test_row_offset_mismatch_is_never_silently_padded(truncated):
    """Pairing intensities with the wrong offsets gives a plausible lie."""
    data = load_pseudo_2d(truncated, prefer="2rr")
    offsets, _ = read_offsets(truncated, data.sfo1_mhz)
    intensity = measure_rows(data.rows, data.ppm, data.ppm[len(data.ppm) // 2], 0.5,
                             mode="height")
    assert intensity.size != offsets.size
    with pytest.raises(CestError):
        normalise_z(intensity, offsets, sfo1_mhz=data.sfo1_mhz)


def test_flat_ser_is_reshaped_not_collapsed_to_one_row(exact, monkeypatch):
    """nmrglue 0.11 returns a pseudo-2D ser FLAT; 0.12 shapes it.

    Left alone, np.atleast_2d turns a flat array into one enormous row and
    the panel reports "1 rows from ser" and plots nothing -- observed on a
    real machine running 0.11. TD(F1) is enough to split it correctly.
    """
    import nmrglue as ng

    real_read = ng.bruker.read

    def flat_read(path, *args, **kwargs):
        dic, data = real_read(path, *args, **kwargs)
        return dic, np.asarray(data).reshape(-1)      # pretend to be 0.11

    monkeypatch.setattr(ng.bruker, "read", flat_read)
    data = load_pseudo_2d(exact, prefer="ser")
    assert data.n_rows == N
    assert data.rows.shape[1] > 1


def test_flat_ser_that_does_not_divide_is_refused(exact, monkeypatch):
    """A truncated or still-downloading file must not be reshaped by guess."""
    import nmrglue as ng

    real_read = ng.bruker.read

    def ragged_read(path, *args, **kwargs):
        dic, data = real_read(path, *args, **kwargs)
        return dic, np.asarray(data).reshape(-1)[:-3]   # not divisible by TD

    monkeypatch.setattr(ng.bruker, "read", ragged_read)
    with pytest.raises(CestError, match="does not divide"):
        load_pseudo_2d(exact, prefer="ser")
