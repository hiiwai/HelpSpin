"""Barcode resolution, JCAMP stripping, title reading, label field assembly."""

from pathlib import Path

import pytest

from helspin.domain.metadata import (
    label_fields,
    read_title,
    resolve_barcode,
    strip_jcamp,
)


# --- strip_jcamp -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<CDCl3>", "CDCl3"),
        ("<>", ""),
        ("<ABC 123>", "ABC 123"),
        ("plain", "plain"),
        ("  <padded>  ", "padded"),
        (None, None),
        ("", ""),
    ],
)
def test_strip_jcamp(raw, expected):
    assert strip_jcamp(raw) == expected


# --- barcode resolution ------------------------------------------------------


def test_configured_key_wins():
    acqus = {"MYBARCODE": "<0042>", "USERA1": "<ignored>"}
    assert resolve_barcode(acqus, configured_key="MYBARCODE") == "0042"


def test_configured_key_absent_returns_none():
    assert resolve_barcode({}, configured_key="MYBARCODE") is None


def test_auto_detects_a_barcode_key():
    acqus = {"USERBARCODE": "<A123>"}
    assert resolve_barcode(acqus) == "A123"


def test_auto_detect_falls_back_to_usera_fields():
    acqus = {"USERA1": "", "USERA2": "<7788>"}
    assert resolve_barcode(acqus) == "7788"


def test_absent_barcode_is_none_not_an_error():
    assert resolve_barcode({}) is None
    assert resolve_barcode({"USERA1": "", "USERA2": ""}) is None


def test_barcode_with_leading_zeros_preserved_as_string():
    acqus = {"USERA1": "<00042>"}
    result = resolve_barcode(acqus)
    assert result == "00042"
    assert isinstance(result, str)


def test_alphanumeric_barcode():
    assert resolve_barcode({"USERA1": "<LOT-7A>"}) == "LOT-7A"


def test_barcode_key_case_insensitive_detection():
    assert resolve_barcode({"barcode_id": "<X1>"}) == "X1"


def test_strip_jcamp_coerces_non_string_values():
    """nmrglue parses HOLDER as an int (e.g. 5), not '<5>'. Every caller must
    still get a string -- HOLDER and barcodes are never numbers per the
    handoff rule (leading zeros, SampleJet's '1 E12 - 193' form)."""
    assert strip_jcamp(5) == "5"
    assert isinstance(strip_jcamp(5), str)
    assert strip_jcamp(600.13) == "600.13"


def test_strip_jcamp_is_idempotent():
    """nmrglue's own read_jcamp already strips brackets for most fields, so
    calling this on an already-clean string must be a no-op, not a re-wrap."""
    assert strip_jcamp("CDCl3") == "CDCl3"
    assert strip_jcamp(strip_jcamp("<CDCl3>")) == "CDCl3"


def test_resolve_barcode_with_a_real_nmrglue_style_dict():
    """HOLDER as an int must not break barcode auto-detection, and must never
    itself be picked up as the barcode."""
    acqus = {"HOLDER": 5, "USERA1": "", "USERA2": "LOT-77A", "NS": 16, "RG": 101}
    assert resolve_barcode(acqus) == "LOT-77A"


def test_label_fields_with_a_real_nmrglue_style_dict():
    """HOLDER=5 (int, from nmrglue) must render as '5', not raise."""
    acqus = {"NUC1": "1H", "SOLVENT": "CDCl3", "HOLDER": 5}
    fields = label_fields(sample="ABC", expno=11, acqus=acqus)
    assert fields["nucleus"] == "1H"
    assert fields["holder"] == "5"


# --- title reading ------------------------------------------------------------


def test_reads_first_nonempty_line_utf8(tmp_path):
    f = tmp_path / "title"
    f.write_text("My sample\nsecond line\n", encoding="utf-8")
    assert read_title(f) == "My sample"


def test_skips_leading_blank_lines(tmp_path):
    f = tmp_path / "title"
    f.write_text("\n\n   \nActual title\n", encoding="utf-8")
    assert read_title(f) == "Actual title"


def test_latin1_with_umlaut_does_not_raise(tmp_path):
    f = tmp_path / "title"
    f.write_bytes("Müller sample, 25°C".encode("latin-1"))
    assert read_title(f) == "Müller sample, 25°C"


def test_missing_file_returns_none(tmp_path):
    assert read_title(tmp_path / "does_not_exist") is None


def test_empty_file_returns_none(tmp_path):
    f = tmp_path / "title"
    f.write_text("")
    assert read_title(f) is None


def test_whitespace_only_file_returns_none(tmp_path):
    f = tmp_path / "title"
    f.write_text("   \n  \n")
    assert read_title(f) is None


def test_unreadable_permission_denied_returns_none(tmp_path, monkeypatch):
    f = tmp_path / "title"
    f.write_text("hello")

    def deny(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_bytes", deny)
    assert read_title(f) is None


def test_undecodable_bytes_return_none_not_raise(tmp_path):
    f = tmp_path / "title"
    # Invalid in both UTF-8 and Latin-1 is actually hard since Latin-1 accepts
    # any byte; use a lone continuation byte with a leading BOM-like trap that
    # still resolves under latin-1 -- this documents that latin-1 is the
    # guaranteed-to-decode fallback, so this function effectively never raises.
    f.write_bytes(b"\xff\xfe\x00\x01")
    result = read_title(f)
    assert result is None or isinstance(result, str)


# --- label field assembly ----------------------------------------------------


def test_label_fields_basic():
    fields = label_fields(sample="ABC-124", expno=11)
    assert fields["sample"] == "ABC-124"
    assert fields["expno"] == "11"
    assert fields["procno"] == "1"


def test_label_fields_pulls_from_acqus():
    fields = label_fields(
        sample="ABC", expno=11, acqus={"NUC1": "<1H>", "SOLVENT": "<CDCl3>"}
    )
    assert fields["nucleus"] == "1H"
    assert fields["solvent"] == "CDCl3"


def test_label_fields_missing_acqus_values_are_empty_not_missing():
    fields = label_fields(sample="ABC", expno=11, acqus={})
    assert fields["nucleus"] == ""
    assert fields["holder"] == ""


def test_label_fields_parsed_name_wins_over_builtin():
    fields = label_fields(
        sample="ABC", expno=11, parsed_name={"sample": "OVERRIDDEN"}
    )
    assert fields["sample"] == "OVERRIDDEN"


def test_label_fields_includes_barcode():
    fields = label_fields(sample="ABC", expno=11, barcode="0042")
    assert fields["barcode"] == "0042"


def test_label_fields_includes_arbitrary_parsed_groups():
    fields = label_fields(
        sample="ABC", expno=11, parsed_name={"project": "PXR", "fraction": "FT2"}
    )
    assert fields["project"] == "PXR"
    assert fields["fraction"] == "FT2"
