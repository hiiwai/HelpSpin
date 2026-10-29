"""STSR/STSI strip handling.

A spectrum processed with STSR (strip start) and STSI (strip size) stores only
a cropped sub-region, but nmrglue's ``guess_udic`` returns the sweep width and
carrier of the WHOLE spectrum -- its docstring warns the values are incorrect
when STSI/STSR are set. Built into an axis unchanged, that spreads the full
spectral width across the cropped points, so the ppm calibration is completely
wrong. These tests pin the correction (``_strip_corrected``) and its guards.
"""

from __future__ import annotations

import numpy as np

from helspin.infrastructure.nmrglue_reader import NmrglueReader, _strip_corrected


# --- the helper in isolation -------------------------------------------------


def test_strip_corrected_narrows_a_stripped_axis():
    """A strip keeping 1/4 of the points must span 1/4 of the sweep width."""
    sw_full, car_full = 30000.0, 0.0
    ftsize, stsi, stsr = 32768, 8192, 10000
    sw, car = _strip_corrected(
        sw_full, car_full, {"FTSIZE": ftsize, "STSI": stsi, "STSR": stsr}, stsi
    )
    assert sw == np.float64(sw_full * stsi / ftsize)
    # the carrier has moved to the strip's centre, so it is no longer 0
    assert car != car_full


def test_strip_corrected_leaves_an_unstripped_axis_untouched():
    """STSI = 0 is the normal, unstripped case. Correcting it would set the
    sweep width to zero and break every ordinary spectrum."""
    sw, car = _strip_corrected(
        30000.0, 123.0, {"FTSIZE": 32768, "STSI": 0, "STSR": 0}, 32768
    )
    assert sw == 30000.0 and car == 123.0


def test_strip_corrected_ignores_stale_metadata():
    """If STSI no longer matches the stored data length, the parameters are
    stale -- leave the axis alone rather than corrupt a correct one."""
    sw, car = _strip_corrected(
        30000.0, 5.0, {"FTSIZE": 32768, "STSI": 8192, "STSR": 0}, 32768
    )
    assert sw == 30000.0 and car == 5.0


def test_strip_corrected_ignores_missing_parameters():
    sw, car = _strip_corrected(30000.0, 5.0, {}, 4096)
    assert sw == 30000.0 and car == 5.0

    sw, car = _strip_corrected(30000.0, 5.0, {"STSI": 4096}, 4096)  # no FTSIZE
    assert sw == 30000.0 and car == 5.0


# --- end to end through read_1d ----------------------------------------------


def _write_1d(expno, *, si, ftsize, stsr, stsi, offset):
    """Write a minimal Bruker 1D expno. A strip is expressed by STSI < FTSIZE
    with the 1r holding STSI points; an unstripped spectrum has STSI = 0 and
    SI = FTSIZE."""
    pdata = expno / "pdata" / "1"
    pdata.mkdir(parents=True)
    x = np.arange(si)
    spec = 1500.0 / (1 + ((x - si * 0.4) / 5.0) ** 2)
    (expno / "acqus").write_text(
        "##$NUC1= <1H>\n##$PULPROG= <zg30>\n##$NS= 8\n##$RG= 64\n"
        "##$SW_h= 6000.0\n##$SFO1= 600.13\n##$O1= 0.0\n##$BF1= 600.13\n##END=\n"
    )
    (pdata / "procs").write_text(
        f"##$SI= {si}\n##$SW_p= 6000.0\n##$SF= 600.13\n##$OFFSET= {offset}\n"
        f"##$FTSIZE= {ftsize}\n##$STSR= {stsr}\n##$STSI= {stsi}\n"
        "##$NC_proc= 0\n##$BYTORDP= 0\n##$XDIM= 0\n##END=\n"
    )
    (pdata / "1r").write_bytes(spec.astype("<i4").tobytes())
    return expno


def test_read_1d_strip_spans_the_sub_region_not_the_whole_spectrum(tmp_path):
    reader = NmrglueReader()

    full = reader.read_1d(
        _write_1d(tmp_path / "full", si=1024, ftsize=1024, stsr=0, stsi=0,
                  offset=5.0)
    )
    full_ppm = full.axis.ppm_scale()
    full_width = float(full_ppm[0] - full_ppm[-1])

    strip = reader.read_1d(
        _write_1d(tmp_path / "strip", si=256, ftsize=1024, stsr=256, stsi=256,
                  offset=2.5)
    )
    strip_ppm = strip.axis.ppm_scale()
    strip_width = float(strip_ppm[0] - strip_ppm[-1])

    # the strip keeps a quarter of the points, so a quarter of the width
    assert strip_width == np.float64(full_width * 256 / 1024).item() or abs(
        strip_width - full_width * 256 / 1024
    ) < 0.01
    # the regression guard: before the fix this came out as the FULL width
    assert strip_width < full_width * 0.5, (
        "a stripped axis must not span the whole spectrum"
    )
    # resolution (ppm per point) is unchanged by cropping
    assert abs(strip_width / 256 - full_width / 1024) < 1e-4
    # ppm axes still descend
    assert strip_ppm[0] > strip_ppm[-1]


def test_read_1d_strip_matches_nmrglues_own_strip_correction(tmp_path):
    """nmrglue's ``strip_fake`` is the reference-correct answer. HelSpin's axis
    must match it, which checks position as well as width."""
    import nmrglue as ng

    from helspin.domain.spectrum import AxisCalibration

    expno = _write_1d(tmp_path / "s", si=256, ftsize=1024, stsr=300, stsi=256,
                      offset=2.0)
    actual = NmrglueReader().read_1d(expno).axis.ppm_scale()

    dic, data = ng.bruker.read_pdata(str(expno / "pdata" / "1"))
    u = ng.bruker.guess_udic(dic, data, strip_fake=True)[0]
    ref = AxisCalibration(
        size=data.size, sw_hz=float(u["sw"]), obs_mhz=float(u["obs"]),
        car_hz=float(u["car"]), nucleus="1H",
    ).ppm_scale()

    assert np.allclose(actual, ref, atol=1e-6)


# --- end to end through read_2d (strip F2 only) ------------------------------


def _write_2d(expno, *, si_f1, si_f2, ftsize_f2, stsr_f2, stsi_f2):
    """A 2D expno whose DIRECT (F2) dimension is stripped while F1 is not."""
    pdata = expno / "pdata" / "1"
    pdata.mkdir(parents=True)
    peak = np.exp(-((np.arange(si_f2) - si_f2 * 0.4) ** 2) / (2 * 8.0 ** 2))
    data = (np.linspace(1.0, 0.4, si_f1)[:, None] * peak[None, :] * 1e6)
    (expno / "acqus").write_text(
        "##$NUC1= <1H>\n##$PULPROG= <hsqc>\n##$NS= 4\n##$RG= 64\n"
        "##$SW_h= 6000.0\n##$SFO1= 600.13\n##$O1= 0.0\n##$BF1= 600.13\n##END=\n"
    )
    (expno / "acqu2s").write_text(
        "##$NUC1= <15N>\n##$SW_h= 2000.0\n##$SFO1= 60.8\n##$O1= 0.0\n"
        "##$BF1= 60.8\n##END=\n"
    )
    (pdata / "procs").write_text(
        f"##$SI= {si_f2}\n##$SW_p= 6000.0\n##$SF= 600.13\n##$OFFSET= 2.5\n"
        f"##$FTSIZE= {ftsize_f2}\n##$STSR= {stsr_f2}\n##$STSI= {stsi_f2}\n"
        f"##$NC_proc= 0\n##$BYTORDP= 0\n##$XDIM= {si_f2}\n##END=\n"
    )
    (pdata / "proc2s").write_text(
        f"##$SI= {si_f1}\n##$SW_p= 2000.0\n##$SF= 60.8\n##$OFFSET= 30.0\n"
        f"##$FTSIZE= {si_f1}\n##$STSR= 0\n##$STSI= 0\n"
        f"##$NC_proc= 0\n##$BYTORDP= 0\n##$XDIM= {si_f1}\n##END=\n"
    )
    data.astype("<i4").tofile(pdata / "2rr")
    return expno


def test_read_2d_strips_f2_and_leaves_f1_alone(tmp_path):
    reader = NmrglueReader()

    full = reader.read_2d(
        _write_2d(tmp_path / "full", si_f1=128, si_f2=1024,
                  ftsize_f2=1024, stsr_f2=0, stsi_f2=0)
    )
    full_f2 = full.axis_f2.ppm_scale()
    full_f2_width = float(full_f2[0] - full_f2[-1])
    full_f1_width = float(
        full.axis_f1.ppm_scale()[0] - full.axis_f1.ppm_scale()[-1]
    )

    strip = reader.read_2d(
        _write_2d(tmp_path / "strip", si_f1=128, si_f2=256,
                  ftsize_f2=1024, stsr_f2=256, stsi_f2=256)
    )
    strip_f2 = strip.axis_f2.ppm_scale()
    strip_f2_width = float(strip_f2[0] - strip_f2[-1])
    strip_f1_width = float(
        strip.axis_f1.ppm_scale()[0] - strip.axis_f1.ppm_scale()[-1]
    )

    # F2 was cropped to a quarter -> a quarter of the direct-dimension width
    assert strip_f2_width < full_f2_width * 0.5, "F2 strip must narrow F2"
    assert abs(strip_f2_width - full_f2_width * 256 / 1024) < 0.02
    # F1 was NOT stripped -> its calibration must be identical, not zeroed
    assert strip_f1_width > 0
    assert abs(strip_f1_width - full_f1_width) < 1e-6
