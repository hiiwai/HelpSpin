"""Sample metadata extraction: barcode, title, and label field assembly.

Pure stdlib (pathlib, re) plus dict lookups -- no nmrglue, no Qt. The actual
parsed acqus dict is produced in infrastructure/nmrglue_reader.py; everything
here operates on that dict or on plain files, so it is fully unit-testable
without nmrglue installed.
"""

from __future__ import annotations

import re
from pathlib import Path

_JCAMP_STRING = re.compile(r"^\s*<(.*)>\s*$")
_ENCODINGS = ("utf-8", "latin-1")


def strip_jcamp(value) -> str | None:
    """Remove JCAMP angle brackets: '<CDCl3>' -> 'CDCl3'.

    acqus string values are sometimes still wrapped like this; a raw '<>' must
    never reach a legend. Idempotent: nmrglue's own reader already strips
    brackets for most string fields, so this is safe to call again.

    Also coerces non-string values to str. A field like HOLDER is written
    numerically in acqus (e.g. '5') and nmrglue parses it as a Python int, not
    a string -- and the handoff rule is explicit that HOLDER and barcodes must
    never be treated as numbers (leading zeros, SampleJet's "1 E12 - 193"
    form). Coercing here means every caller gets a string, always.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    match = _JCAMP_STRING.match(value)
    return match.group(1) if match else value.strip()


def resolve_barcode(acqus: dict, configured_key: str | None = None) -> str | None:
    """Resolve a barcode from a parsed acqus dict.

    Never casts to int: barcodes carry meaningful leading zeros and are
    frequently alphanumeric. A configured key always wins; otherwise a
    candidate list is tried so most facilities need no configuration at all.
    Absence is normal, not an error.
    """
    if configured_key:
        return strip_jcamp(acqus.get(configured_key))

    for key in acqus:
        if "BARCODE" in key.upper():
            value = strip_jcamp(acqus[key])
            if value:
                return value

    for key in ("USERA1", "USERA2", "USERA3", "USERA4", "USERA5"):
        value = strip_jcamp(acqus.get(key))
        if value:
            return value

    return None


def read_title(path: Path) -> str | None:
    """First non-empty line of a Bruker title file.

    Bruker title files have inconsistent encoding -- some UTF-8, older ones
    Latin-1 -- so a naive read_text() can raise UnicodeDecodeError on a degree
    sign or an umlaut. This must never raise; a failed read degrades to None,
    and the caller falls through to a less specific label source.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    for encoding in _ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None
    return None


def label_fields(
    *,
    sample: str,
    expno: int,
    procno: int = 1,
    acqus: dict | None = None,
    title: str | None = None,
    barcode: str | None = None,
    parsed_name: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assemble the field dict a LabelTemplate renders against.

    Precedence when a parsed-name group collides with a built-in token: the
    parsed name wins, since it is the more specific, user-configured source.
    """
    acqus = acqus or {}
    fields: dict[str, str] = {
        "sample": sample,
        "expno": str(expno),
        "procno": str(procno),
        "title": title or "",
        "nucleus": strip_jcamp(acqus.get("NUC1")) or "",
        "solvent": strip_jcamp(acqus.get("SOLVENT")) or "",
        "holder": strip_jcamp(acqus.get("HOLDER")) or "",
        "barcode": barcode or "",
    }
    fields.update(parsed_name or {})
    return fields
