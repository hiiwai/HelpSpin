"""load_data_roots / save_data_roots round-trip, via a real QSettings backend."""

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

import helspin.core.settings as settings_module
from helspin.core.settings import load_data_roots, save_data_roots
from helspin.domain.ports import DataRoot, SampleNamePattern

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Route QSettings to a scratch ini file so tests never touch the real
    user's registry/plist/config, and never bleed into each other.

    Patches the module's own _settings() factory rather than QSettings
    itself -- QSettings is a QObject subclass and does not support having its
    __new__ swapped the way a plain Python class would.
    """
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(
        settings_module, "_settings", lambda: QSettings(ini, QSettings.IniFormat)
    )
    yield


def test_empty_on_first_run():
    assert load_data_roots() == []


def test_round_trips_a_single_root():
    save_data_roots([DataRoot(name="600 MHz", path=Path("/data/600"))])
    loaded = load_data_roots()
    assert len(loaded) == 1
    assert loaded[0].name == "600 MHz"
    assert loaded[0].path == Path("/data/600")


def test_round_trips_multiple_roots_in_order():
    roots = [
        DataRoot(name="600 MHz", path=Path("/data/600")),
        DataRoot(name="400 MHz", path=Path("/data/400")),
    ]
    save_data_roots(roots)
    loaded = load_data_roots()
    assert [r.name for r in loaded] == ["600 MHz", "400 MHz"]


def test_round_trips_disabled_flag():
    save_data_roots([DataRoot(name="off", path=Path("/x"), enabled=False)])
    assert load_data_roots()[0].enabled is False


def test_round_trips_barcode_key_and_name_pattern():
    root = DataRoot(
        name="600",
        path=Path("/data/600"),
        barcode_key="MYBARCODE",
        name_pattern=SampleNamePattern(regex=r"(?P<x>\w+)", enabled=True),
    )
    save_data_roots([root])
    loaded = load_data_roots()[0]
    assert loaded.barcode_key == "MYBARCODE"
    assert loaded.name_pattern.enabled
    assert loaded.name_pattern.regex == r"(?P<x>\w+)"


def test_saving_overwrites_rather_than_appends():
    save_data_roots([DataRoot(name="a", path=Path("/a"))])
    save_data_roots([DataRoot(name="b", path=Path("/b"))])
    assert [r.name for r in load_data_roots()] == ["b"]


def test_corrupt_settings_value_yields_empty_list_not_a_crash():
    settings_module._settings().setValue("data_roots", "{not valid json")
    assert load_data_roots() == []


def test_settings_value_that_is_not_a_list_of_dicts():
    settings_module._settings().setValue("data_roots", '["just a string"]')
    assert load_data_roots() == []


def test_partial_dict_missing_required_keys_is_skipped_not_fatal():
    settings_module._settings().setValue("data_roots", '[{"name": "no path field"}]')
    assert load_data_roots() == []
