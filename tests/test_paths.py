"""Structural path resolution.

Built against synthetic Bruker trees including the awkward real-world layout
with an extra 'data' segment, which is what breaks positional parsers.
"""

from pathlib import Path

import pytest

from helspin.domain.paths import (
    Resolved,
    TopSpinIdentifier,
    expnos_in,
    is_expno,
    is_foreign_path,
    is_procno,
    is_sample_dir,
    normalise_pasted,
    parse_expno_spec,
    parse_topspin_identifier,
    procnos_in,
    resolve,
    scan_for_datasets,
)


def make_expno(sample: Path, expno: int, procnos=(1,), dim=1) -> Path:
    d = sample / str(expno)
    d.mkdir(parents=True, exist_ok=True)
    (d / "acqus").write_text("##$NS= 16\n##$RG= 101\n")
    if dim == 2:
        (d / "acqu2s").write_text("##$TD= 256\n")
        (d / "ser").write_bytes(b"\x00" * 16)
    else:
        (d / "fid").write_bytes(b"\x00" * 16)
    for p in procnos:
        pd = d / "pdata" / str(p)
        pd.mkdir(parents=True, exist_ok=True)
        (pd / "procs").write_text("##$SI= 1024\n")
        (pd / ("2rr" if dim == 2 else "1r")).write_bytes(b"\x00" * 16)
    return d


@pytest.fixture
def tree(tmp_path):
    """Mirrors the real layout, extra 'data' segment and all:

        <root>/data/IW/nmr/data/260728_SampleB_25uM_FT2/{11,12,21}
    """
    sample = tmp_path / "data" / "IW" / "nmr" / "data" / "260728_SampleB_25uM_FT2"
    make_expno(sample, 11)
    make_expno(sample, 12)
    make_expno(sample, 21, dim=2)
    return tmp_path, sample


# --- predicates -------------------------------------------------------------


def test_is_expno_requires_integer_name_and_acqus(tree, tmp_path):
    _, sample = tree
    assert is_expno(sample / "11")
    assert not is_expno(sample)

    named = sample / "notanumber"
    named.mkdir()
    (named / "acqus").write_text("x")
    assert not is_expno(named)

    bare = sample / "99"
    bare.mkdir()
    assert not is_expno(bare)


def test_is_expno_on_a_file_is_false(tree):
    _, sample = tree
    assert not is_expno(sample / "11" / "acqus")


def test_is_sample_dir(tree):
    root, sample = tree
    assert is_sample_dir(sample)
    assert not is_sample_dir(sample / "11")
    assert not is_sample_dir(root)


def test_is_procno(tree):
    _, sample = tree
    assert is_procno(sample / "11" / "pdata" / "1")
    assert not is_procno(sample / "11" / "pdata")
    assert not is_procno(sample / "11")


def test_expnos_sorted_numerically(tmp_path):
    sample = tmp_path / "s"
    for n in (2, 11, 1, 100):
        make_expno(sample, n)
    assert [p.name for p in expnos_in(sample)] == ["1", "2", "11", "100"]


def test_procnos_listed(tree):
    _, sample = tree
    single = make_expno(sample.parent / "other", 5, procnos=(1, 2, 3))
    assert [p.name for p in procnos_in(single)] == ["1", "2", "3"]


def test_procnos_absent_returns_empty(tmp_path):
    d = tmp_path / "7"
    d.mkdir()
    (d / "acqus").write_text("x")
    assert procnos_in(d) == []


# --- resolution -------------------------------------------------------------


def test_resolves_every_level_to_the_same_expno(tree):
    _, sample = tree
    expected = sample / "11"
    for candidate in (
        sample / "11",
        sample / "11" / "pdata",
        sample / "11" / "pdata" / "1",
    ):
        r = resolve(candidate)
        assert r is not None and r.expno == expected


def test_procno_is_captured(tree):
    _, sample = tree
    r = resolve(sample / "11" / "pdata" / "1")
    assert r.procno == 1


def test_sample_level_needs_the_picker(tree):
    _, sample = tree
    r = resolve(sample)
    assert r.needs_picker and not r.is_expno
    assert r.sample == sample


def test_non_standard_nesting_resolves(tree):
    """The extra 'data' segment must not defeat resolution."""
    _, sample = tree
    assert "nmr" in sample.parts and sample.parts.count("data") == 2
    assert resolve(sample / "11").expno == sample / "11"


def test_walks_up_from_below_the_dataset(tree):
    _, sample = tree
    deep = sample / "11" / "pdata" / "1"
    assert resolve(deep).expno == sample / "11"


def test_walk_up_is_bounded(tmp_path):
    sample = tmp_path / "s"
    make_expno(sample, 1)
    far = sample / "1" / "pdata" / "1" / "a" / "b" / "c" / "d"
    far.mkdir(parents=True)
    assert resolve(far, max_up=2) is None


def test_unrelated_directory_resolves_to_none(tmp_path):
    d = tmp_path / "holiday_photos"
    d.mkdir()
    assert resolve(d) is None


def test_nonexistent_path_resolves_to_none(tmp_path):
    assert resolve(tmp_path / "nope" / "nope") is None


def test_resolved_flags_are_exclusive():
    assert Resolved(expno=Path("/x")).is_expno
    assert not Resolved(expno=Path("/x")).needs_picker
    assert Resolved(sample=Path("/x")).needs_picker


# --- scanning ---------------------------------------------------------------


def test_scan_finds_all_expnos(tree):
    root, sample = tree
    assert len(scan_for_datasets(root)) == 3


def test_scan_on_an_expno_returns_itself(tree):
    _, sample = tree
    assert scan_for_datasets(sample / "11") == [sample / "11"]


def test_scan_respects_the_limit(tmp_path):
    sample = tmp_path / "s"
    for n in range(1, 21):
        make_expno(sample, n)
    assert len(scan_for_datasets(sample, limit=5)) == 5


def test_scan_respects_depth(tree):
    root, _ = tree
    assert scan_for_datasets(root, depth=1) == []


def test_scan_does_not_descend_into_pdata(tree):
    root, _ = tree
    for path in scan_for_datasets(root):
        assert "pdata" not in path.parts


def test_scan_of_empty_directory(tmp_path):
    assert scan_for_datasets(tmp_path) == []


# --- expno specs ------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("11", [11]),
        ("11-14", [11, 12, 13, 14]),
        ("11,12,15", [11, 12, 15]),
        ("11, 12 , 15", [11, 12, 15]),
        ("1-3,7", [1, 2, 3, 7]),
        ("11-11", [11]),
        ("11,11", [11]),
        ("", []),
        ("abc", []),
        ("11abc", []),
        ("14-11", []),
    ],
)
def test_parse_expno_spec(text, expected):
    assert parse_expno_spec(text) == expected


# --- paste normalisation ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('"/data/ABC/11"', "/data/ABC/11"),
        ("'/data/ABC/11'", "/data/ABC/11"),
        ("  /data/ABC/11  ", "/data/ABC/11"),
        ("/data/ABC/11/", "/data/ABC/11"),
        ("/data/ABC/11///", "/data/ABC/11"),
        ("file:///data/ABC/11", "/data/ABC/11"),
        ("file:///data/my%20samples/11", "/data/my samples/11"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalise_pasted(raw, expected):
    assert normalise_pasted(raw) == expected


def test_normalise_keeps_unc_prefix():
    assert normalise_pasted(r"\\host\share\ABC\11") == r"\\host\share\ABC\11"


def test_normalise_does_not_strip_a_root():
    assert normalise_pasted("/") == "/"


def test_foreign_path_detected(monkeypatch):
    monkeypatch.setattr("os.name", "posix")
    assert is_foreign_path(r"D:\NMRdata\ABC\11")
    assert is_foreign_path(r"\\host\share\x")
    assert not is_foreign_path("/Volumes/data/ABC/11")


# --- TopSpin identifier row -------------------------------------------------


def test_parse_topspin_identifier():
    got = parse_topspin_identifier("ABC-124 11 1 /data/nmr IW")
    assert got == TopSpinIdentifier("ABC-124", 11, 1, "/data/nmr", "IW")
    assert got.to_path() == Path("/data/nmr/data/IW/nmr/ABC-124/11")


def test_parse_topspin_identifier_with_spaces_in_dir():
    got = parse_topspin_identifier("ABC 11 1 /data/my nmr data IW")
    assert got.directory == "/data/my nmr data"
    assert got.user == "IW"


@pytest.mark.parametrize(
    "text",
    ["", "ABC-124", "ABC-124 11", "ABC-124 11 1 /data", "ABC-124 x 1 /data IW"],
)
def test_parse_topspin_identifier_rejects_malformed(text):
    assert parse_topspin_identifier(text) is None


def test_topspin_windows_path_reconstruction():
    """A Windows identifier row reconstructs a Windows path.

    On POSIX that path is foreign and unresolvable, which is correct behaviour:
    the caller must report 'cannot be resolved on this platform' rather than
    fail silently. So assert the shape, not the host-flavoured parts.
    """
    got = parse_topspin_identifier(r"ABC 11 1 D:\NMRdata IW")
    text = str(got.to_path())
    assert text.endswith("11")
    assert "ABC" in text and "IW" in text
    assert is_foreign_path(text) or text.startswith("D:")


def test_scan_depth_default_reaches_a_realistic_layout(tree):
    """Regression: depth 4 found nothing in <root>/data/<user>/nmr/data/<sample>."""
    root, _ = tree
    assert len(scan_for_datasets(root)) == 3
    assert scan_for_datasets(root, depth=4) == []
