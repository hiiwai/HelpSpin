"""Persisted preferences.

Only data roots are persisted in this slice -- per the handoff (4.3.0), first
run should require configuring exactly one thing, and this is it. Everything
else ships with a working default and has no settings surface yet.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QSettings

from ..domain.ports import DataRoot, SampleNamePattern

_ORG = "HelSpin"
_APP = "HelSpin"
_KEY = "data_roots"


def _to_dict(root: DataRoot) -> dict:
    return {
        "name": root.name,
        "path": str(root.path),
        "enabled": root.enabled,
        "barcode_key": root.barcode_key,
        "default_procno": root.default_procno,
        "show_2d": root.show_2d,
        "name_pattern": {
            "regex": root.name_pattern.regex,
            "enabled": root.name_pattern.enabled,
        },
    }


def _from_dict(data: dict) -> DataRoot:
    pattern = data.get("name_pattern") or {}
    return DataRoot(
        name=data["name"],
        path=Path(data["path"]),
        enabled=data.get("enabled", True),
        barcode_key=data.get("barcode_key"),
        default_procno=data.get("default_procno", 1),
        show_2d=data.get("show_2d", True),
        name_pattern=SampleNamePattern(
            regex=pattern.get("regex", ""), enabled=pattern.get("enabled", False)
        ),
    )


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def load_data_roots() -> list[DataRoot]:
    """Never raises: a corrupt or absent settings value yields an empty list,
    which the caller treats the same as first run."""
    raw = _settings().value(_KEY, "")
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    roots = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            roots.append(_from_dict(item))
        except (KeyError, TypeError, AttributeError):
            continue
    return roots


def save_data_roots(roots: list[DataRoot]) -> None:
    payload = json.dumps([_to_dict(r) for r in roots])
    settings = _settings()
    settings.setValue(_KEY, payload)
    settings.sync()


_STYLES_KEY = "appearance/slot_styles"


def load_slot_styles() -> list[dict] | None:
    """Per-slot appearance saved from Preferences, or None if never saved.

    Returns None (rather than defaults) so the caller can tell "never
    configured" from "configured to look like the defaults". Malformed stored
    values degrade to None rather than raising -- a corrupt settings file must
    not stop the application starting.
    """
    raw = _settings().value(_STYLES_KEY, "")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    out = []
    for entry in parsed:
        if not isinstance(entry, dict):
            return None
        color = entry.get("color")
        style = entry.get("style")
        width = entry.get("width")
        if not isinstance(color, str) or not isinstance(style, str):
            return None
        try:
            width = float(width)
        except (TypeError, ValueError):
            return None
        out.append({"color": color, "style": style, "width": width})
    return out


def save_slot_styles(styles: list[dict]) -> None:
    """Persist per-slot appearance so it is the default next run."""
    settings = _settings()
    settings.setValue(_STYLES_KEY, json.dumps(list(styles)))
    settings.sync()


_DISPLAY_KEY = "display/preferences"

# Only these keys are stored, and each is coerced on the way back in. A
# settings file is user-editable and survives upgrades, so anything read from
# it is treated as untrusted input: a bad value must degrade to the default
# rather than reach the canvas.
_DISPLAY_FIELDS = {
    "grid_spacing_ppm": float,
    "grid_spacing_y": float,
    "x_decimals": int,
    "label_scale": float,
    "opacity": float,
    "cursor_decimals": int,
    "palette": str,
}


def load_display_prefs() -> dict:
    """Display preferences saved from the dialog, or {} if never saved.

    Everything except the slot styles used to be applied to the canvas and
    never written anywhere, so grid spacing, opacity, cursor decimals and the
    rest silently reverted on the next launch -- the dialog appeared to work
    and did not.
    """
    raw = _settings().value(_DISPLAY_KEY, "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    out = {}
    for key, cast in _DISPLAY_FIELDS.items():
        if key not in parsed or parsed[key] is None:
            continue
        try:
            out[key] = cast(parsed[key])
        except (TypeError, ValueError):
            continue      # a bad value falls back to the default
    return out


def save_display_prefs(values: dict) -> None:
    """Persist display preferences so they are the default next run."""
    keep = {
        key: values[key]
        for key in _DISPLAY_FIELDS
        if key in values and values[key] is not None
    }
    settings = _settings()
    settings.setValue(_DISPLAY_KEY, json.dumps(keep))
    settings.sync()


_RECENT_RANGES_KEY = "plot/recent_ppm_ranges"

# Seeded rather than starting empty. A first run offered an empty "Recent
# ranges" list, which is a control that does nothing until you have already
# done by hand the thing it exists to save you doing.
#
# The three windows a 1H spectrum is actually read in: the standard sweep,
# the same with a margin either side for peaks that sit just outside it, and
# the downfield half on its own for aromatics and amides.
#
# Ordered most-used first, because that is the order they appear in the menu.
#
# Stored high-to-low because the ppm axis descends; values typed in either
# order are normalised the same way on Apply.
DEFAULT_RECENT_RANGES: list[tuple[float, float]] = [
    (12.0, 0.0),      # standard 1H sweep
    (13.0, -1.0),     # the same, with a margin at each end
    (13.0, 5.0),      # downfield only: aromatics, amides
]


def load_recent_ranges() -> list[tuple[float, float]]:
    """Recent ppm ranges, most recent first.

    Falls back to the defaults when nothing is stored AND when the stored
    value is unusable. A corrupted or hand-edited settings entry should cost
    the user a stale list, not an empty control or a crash on startup.
    """
    raw = _settings().value(_RECENT_RANGES_KEY)
    if not raw:
        return list(DEFAULT_RECENT_RANGES)
    try:
        entries = json.loads(raw)
    except (TypeError, ValueError):
        return list(DEFAULT_RECENT_RANGES)
    ranges: list[tuple[float, float]] = []
    for entry in entries if isinstance(entries, list) else []:
        try:
            left, right = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue        # skip the bad row, keep the good ones
        if not (math.isfinite(left) and math.isfinite(right)):
            continue
        if left == right:
            continue        # a zero-width window would draw nothing
        ranges.append((left, right))
    return ranges or list(DEFAULT_RECENT_RANGES)


def save_recent_ranges(ranges) -> None:
    """Persist the recent ppm ranges."""
    clean = []
    for entry in ranges:
        try:
            left, right = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(left) and math.isfinite(right) and left != right:
            clean.append([left, right])
    _settings().setValue(_RECENT_RANGES_KEY, json.dumps(clean))
