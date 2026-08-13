"""NmrglueReader.probe() against realistic Bruker fixtures.

Only probe() is tested here: it is what the browser and drag/drop payload
need, and it never loads array data, so these tests stay fast.
"""

from pathlib import Path

import pytest

from helspin.domain.errors import DatasetNotFound, UnsupportedDimension
from helspin.domain.ports import DataRoot
from helspin.domain.project import Dimensionality
from helspin.infrastructure.nmrglue_reader import NmrglueReader, read_acqus

ACQUS_TEMPLATE = """##TITLE= Parameter file, TopSpin 3.6
##$NUC1= <{nucleus}>
##$SOLVENT= <{solvent}>
##$PULPROG= <{pulprog}>
##$NS= {ns}
##$RG= {rg}
##$SFO1= 600.13
##$SW_h= 12019.230
##$O1= 3000.5
##$HOLDER= {holder}
##$USERA1= <{usera1}>
##$USERA2= <{usera2}>
##$DATE= {date}
##END=
"""


def make_expno(
    root: Path,
    expno: int,
    *,
    dim: int = 1,
    procnos=(1,),
    nucleus="1H",
    solvent="CDCl3",
    pulprog="zg30",
    ns=16,
    rg=101,
    holder=5,
    usera1="",
    usera2="",
    date=1719561600,
    title: str | None = "My sample",
    acqus_encoding="utf-8",
) -> Path:
    d = root / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    text = ACQUS_TEMPLATE.format(
        nucleus=nucleus, solvent=solvent, pulprog=pulprog, ns=ns, rg=rg,
        holder=holder, usera1=usera1, usera2=usera2, date=date,
    )
    (d / "acqus").write_bytes(text.encode(acqus_encoding))
    if dim == 2:
        (d / "acqu2s").write_text("##$TD= 256\n##END=\n")
        (d / "ser").write_bytes(b"\x00" * 32)
    else:
        (d / "fid").write_bytes(b"\x00" * 32)
    for p in procnos:
        pd = d / "pdata" / str(p)
        pd.mkdir(parents=True, exist_ok=True)
        (pd / "procs").write_text("##$SI= 1024\n##END=\n")
        (pd / ("2rr" if dim == 2 else "1r")).write_bytes(b"\x00" * 32)
        if title is not None:
            (pd / "title").write_text(title + "\n")
    return d


@pytest.fixture
def reader():
    return NmrglueReader()


# --- can_read -----------------------------------------------------------------


def test_can_read_a_real_expno(tmp_path, reader):
    expno = make_expno(tmp_path, 11)
    assert reader.can_read(expno)


def test_cannot_read_a_non_expno(tmp_path, reader):
    d = tmp_path / "not_a_dataset"
    d.mkdir()
    assert not reader.can_read(d)


# --- probe: happy path ----------------------------------------------------


def test_probe_extracts_the_expected_fields(tmp_path, reader):
    expno = make_expno(
        tmp_path, 11, nucleus="1H", solvent="CDCl3", pulprog="zg30", ns=16, rg=101,
    )
    info = reader.probe(expno)
    assert info.expno == 11
    assert info.procno == 1
    assert info.dimensionality is Dimensionality.ONE_D
    assert info.nucleus == "1H"
    assert info.solvent == "CDCl3"
    assert info.pulse_program == "zg30"
    assert info.ns == 16
    assert info.rg == 101.0


def test_probe_reads_the_title(tmp_path, reader):
    expno = make_expno(tmp_path, 11, title="SampleB fraction 2")
    info = reader.probe(expno)
    assert info.title == "SampleB fraction 2"


def test_probe_with_no_title_file(tmp_path, reader):
    expno = make_expno(tmp_path, 11, title=None)
    info = reader.probe(expno)
    assert info.title is None


def test_probe_2d_dimensionality(tmp_path, reader):
    expno = make_expno(tmp_path, 21, dim=2)
    info = reader.probe(expno)
    assert info.dimensionality is Dimensionality.TWO_D


def test_probe_3d_is_rejected(tmp_path, reader):
    expno = make_expno(tmp_path, 31, dim=1)
    (expno / "acqu3s").write_text("##END=\n")
    with pytest.raises(UnsupportedDimension):
        reader.probe(expno)


def test_probe_with_neither_fid_nor_ser_raises(tmp_path, reader):
    expno = tmp_path / "40"
    expno.mkdir()
    (expno / "acqus").write_text(ACQUS_TEMPLATE.format(
        nucleus="1H", solvent="D2O", pulprog="zg", ns=1, rg=1, holder=0,
        usera1="", usera2="", date=0,
    ))
    with pytest.raises(DatasetNotFound):
        reader.probe(expno)


def test_probe_on_a_non_expno_raises(tmp_path, reader):
    d = tmp_path / "not_bruker"
    d.mkdir()
    with pytest.raises(DatasetNotFound):
        reader.probe(d)


# --- HOLDER as int (the real trap) ------------------------------------------


def test_holder_survives_as_a_string_not_an_int(tmp_path, reader):
    """nmrglue parses ##$HOLDER= 5 as the Python int 5. The DatasetInfo field
    must be a string regardless -- HOLDER is a position, never a number to
    compute with."""
    expno = make_expno(tmp_path, 11, holder=5)
    info = reader.probe(expno)
    assert info.holder == "5"
    assert isinstance(info.holder, str)


def test_holder_with_leading_zero_in_usera_barcode(tmp_path, reader):
    expno = make_expno(tmp_path, 11, usera2="00042")
    info = reader.probe(expno)
    assert info.barcode == "00042"


# --- barcode resolution ------------------------------------------------------


def test_probe_resolves_barcode_via_usera_fallback(tmp_path, reader):
    expno = make_expno(tmp_path, 11, usera1="", usera2="LOT-77A")
    info = reader.probe(expno)
    assert info.barcode == "LOT-77A"


def test_probe_uses_configured_barcode_key(tmp_path, reader):
    expno = make_expno(tmp_path, 11, usera1="ignored", usera2="also-ignored")
    root = DataRoot(name="600", path=tmp_path, barcode_key="USERA1")
    info = reader.probe(expno, data_root=root)
    assert info.barcode == "ignored"


def test_probe_with_no_barcode_present(tmp_path, reader):
    expno = make_expno(tmp_path, 11, usera1="", usera2="")
    info = reader.probe(expno)
    assert info.barcode is None


# --- acqus encoding -----------------------------------------------------------


def test_probe_handles_latin1_encoded_acqus(tmp_path, reader):
    """Same trap as the title file, one layer lower: a facility whose acqus
    carries a non-ASCII byte in a free-text field must not crash probe()."""
    expno = make_expno(
        tmp_path, 11, solvent="CDCl3", usera2="Müller", acqus_encoding="latin-1",
    )
    info = reader.probe(expno)
    assert info.barcode == "Müller"


def test_read_acqus_falls_back_to_latin1(tmp_path):
    d = tmp_path / "acqus"
    text = "##$SOLVENT= <CDCl3>\n##$USERA1= <25°C batch>\n##END=\n"
    d.write_bytes(text.encode("latin-1"))
    dic = read_acqus(d)
    assert "25" in str(dic.get("USERA1", ""))


# --- date, NS/RG defaults -----------------------------------------------------


def test_date_epoch_converted_to_iso(tmp_path, reader):
    expno = make_expno(tmp_path, 11, date=1719561600)
    info = reader.probe(expno)
    assert info.date == "2024-06-28"


def test_missing_date_yields_empty_string_not_an_error(tmp_path, reader):
    expno = tmp_path / "50"
    expno.mkdir()
    (expno / "acqus").write_text(
        "##$NUC1= <1H>\n##$SOLVENT= <D2O>\n##$PULPROG= <zg>\n"
        "##$NS= 1\n##$RG= 1\n##$HOLDER= 0\n##END=\n"
    )
    (expno / "fid").write_bytes(b"\x00" * 8)
    info = reader.probe(expno)
    assert info.date == ""


def test_probe_procnos_lists_available_procnos(tmp_path, reader):
    expno = make_expno(tmp_path, 11, procnos=(1, 2, 3))
    assert reader.probe_procnos(expno) == [1, 2, 3]


# --- array loading is deferred ------------------------------------------------


def test_read_1d_loads_processed_data(tmp_path):
    """read_1d is implemented now (rendering milestone): it loads the
    PROCESSED spectrum from pdata, which is what TopSpin shows and what a
    comparison figure should plot."""
    import numpy as np

    from helspin.infrastructure.nmrglue_reader import NmrglueReader

    expno = tmp_path / "11"
    pdata = expno / "pdata" / "1"
    pdata.mkdir(parents=True)
    n = 512
    x = np.arange(n)
    spec = 1500.0 / (1 + ((x - 200) / 5.0) ** 2)
    (expno / "acqus").write_text(
        "##$NUC1= <1H>\n##$PULPROG= <zg30>\n##$NS= 8\n##$RG= 64\n"
        "##$SW_h= 6000.0\n##$SFO1= 600.13\n##$O1= 2400.0\n##$BF1= 600.13\n##END=\n"
    )
    (pdata / "procs").write_text(
        f"##$SI= {n}\n##$SW_p= 6000.0\n##$SF= 600.13\n##$OFFSET= 12.0\n"
        "##$NC_proc= 0\n##$BYTORDP= 0\n##$XDIM= 0\n##END=\n"
    )
    (pdata / "1r").write_bytes(spec.astype("<i4").tobytes())

    s = NmrglueReader().read_1d(expno)
    assert s.real.size == n
    assert s.axis.size == n
    assert s.ns == 8
    assert s.rg == 64.0
    ppm = s.axis.ppm_scale()
    assert ppm[0] > ppm[-1]     # ppm axes descend


def test_read_1d_missing_pdata_raises(tmp_path):
    from helspin.domain.errors import DatasetNotFound
    from helspin.infrastructure.nmrglue_reader import NmrglueReader

    expno = tmp_path / "11"
    expno.mkdir(parents=True)
    with pytest.raises(DatasetNotFound):
        NmrglueReader().read_1d(expno)
