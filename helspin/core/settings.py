"""Persisted preferences.

Only data roots are persisted in this slice -- per the handoff (4.3.0), first
run should require configuring exactly one thing, and this is it. Everything
else ships with a working default and has no settings surface yet.
"""

from __future__ import annotations

import json
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


_STYLES_KEY = "plot/slot_styles"


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
