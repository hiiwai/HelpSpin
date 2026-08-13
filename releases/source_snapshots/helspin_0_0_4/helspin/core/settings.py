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
