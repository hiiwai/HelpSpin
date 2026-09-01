"""SampleNamePattern and DataRoot preferences."""

from pathlib import Path

from helspin.domain.ports import DataRoot, SampleNamePattern

# --- SampleNamePattern -------------------------------------------------------


def test_disabled_pattern_parses_nothing():
    p = SampleNamePattern(regex=r"(?P<project>\w+)", enabled=False)
    assert p.parse("ABC-124") == {}


def test_enabled_pattern_parses_named_groups():
    p = SampleNamePattern(
        regex=r"(?P<date>\d{6})_(?P<project>[A-Za-z0-9-]+)_(?P<batch>[\w-]+)_(?P<fraction>\w+)",
        enabled=True,
    )
    got = p.parse("260728_SampleB_25uM_FT2")
    assert got == {
        "date": "260728",
        "project": "SampleB",
        "batch": "25uM",
        "fraction": "FT2",
    }


def test_non_matching_name_returns_empty_not_an_error():
    p = SampleNamePattern(regex=r"(?P<project>\d+)_only", enabled=True)
    assert p.parse("does_not_match") == {}


def test_empty_regex_returns_empty():
    p = SampleNamePattern(regex="", enabled=True)
    assert p.parse("anything") == {}


def test_invalid_regex_does_not_raise():
    p = SampleNamePattern(regex="(unclosed", enabled=True)
    assert p.parse("anything") == {}
    assert p.validate() is not None


def test_valid_regex_validates_clean():
    p = SampleNamePattern(regex=r"(?P<x>\w+)")
    assert p.validate() is None


def test_group_names_lists_named_groups():
    p = SampleNamePattern(regex=r"(?P<a>\d+)_(?P<b>\w+)")
    assert set(p.group_names()) == {"a", "b"}


def test_group_names_empty_for_no_regex():
    assert SampleNamePattern().group_names() == []


def test_group_names_empty_for_invalid_regex():
    assert SampleNamePattern(regex="(unclosed").group_names() == []


# --- DataRoot -----------------------------------------------------------------


def test_data_root_defaults():
    root = DataRoot(name="600 MHz", path=Path("/data/600"))
    assert root.enabled is True
    assert root.default_procno == 1
    assert root.show_2d is True
    assert root.barcode_key is None
    assert root.name_pattern.enabled is False


def test_data_root_per_root_pattern_and_barcode_key():
    root = DataRoot(
        name="400 MHz",
        path=Path("/data/400"),
        name_pattern=SampleNamePattern(regex=r"(?P<x>\w+)", enabled=True),
        barcode_key="MYBARCODE",
    )
    assert root.name_pattern.enabled
    assert root.barcode_key == "MYBARCODE"
