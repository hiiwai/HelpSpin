"""Licence status. Nothing here is enforced -- these pin the reporting."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from helspin.core import licence as lic

pytestmark = pytest.mark.usefixtures("qapp")


def test_the_default_is_a_six_month_trial(tmp_path, monkeypatch):
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("HELSPIN_LICENCE", raising=False)

    current = lic.current_licence()
    assert current.kind == lic.KIND_TRIAL
    assert current.days_remaining == lic.TRIAL_DAYS
    assert not current.expired


def test_the_trial_is_dated_from_first_run_not_from_the_build(tmp_path, monkeypatch):
    """Someone installing a year-old copy still gets a fair six months."""
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("HELSPIN_LICENCE", raising=False)

    first = lic.start_or_resume_trial()
    later = lic.start_or_resume_trial(today=date.today() + timedelta(days=30))
    assert later.issued == first.issued, "the start date must not move"
    assert later.expires == first.expires


def test_an_expired_trial_is_reported_as_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("HELSPIN_LICENCE", raising=False)
    started = date.today() - timedelta(days=lic.TRIAL_DAYS + 10)
    (tmp_path / "trial.json").write_text(json.dumps({"started": started.isoformat()}))

    current = lic.current_licence()
    assert current.expired
    assert current.days_remaining < 0
    assert "expired" in current.describe().lower()


def test_a_licence_file_overrides_the_trial(tmp_path, monkeypatch):
    path = tmp_path / "licence.json"
    path.write_text(json.dumps({
        "kind": "commercial",
        "licensee": "Example Pharma Ltd",
        "issued": "2026-01-01",
        "expires": "2027-01-01",
    }))
    monkeypatch.setenv("HELSPIN_LICENCE", str(path))
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))

    current = lic.current_licence()
    assert current.kind == "commercial"
    assert current.licensee == "Example Pharma Ltd"
    assert current.expires == date(2027, 1, 1)
    assert "Example Pharma" in current.describe()


def test_an_academic_licence_does_not_expire(tmp_path, monkeypatch):
    path = tmp_path / "licence.json"
    path.write_text(json.dumps({"kind": "academic", "licensee": "Some University"}))
    monkeypatch.setenv("HELSPIN_LICENCE", str(path))
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))

    current = lic.current_licence()
    assert current.expires is None
    assert current.days_remaining is None
    assert not current.expired
    assert "no expiry" in current.describe()


def test_a_commercial_file_with_no_expiry_is_not_treated_as_unlimited(
    tmp_path, monkeypatch
):
    """The failure mode a typo would silently create: a missing date must not
    read as "never expires"."""
    path = tmp_path / "licence.json"
    path.write_text(json.dumps({"kind": "commercial", "licensee": "X"}))
    monkeypatch.setenv("HELSPIN_LICENCE", str(path))
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))

    current = lic.current_licence()
    assert current.kind == lic.KIND_TRIAL, "falls back to the trial"
    assert "expiry" in current.problem


def test_a_corrupt_licence_file_falls_back_to_the_trial(tmp_path, monkeypatch):
    path = tmp_path / "licence.json"
    path.write_text("{ this is not json")
    monkeypatch.setenv("HELSPIN_LICENCE", str(path))
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))

    assert lic.current_licence().kind == lic.KIND_TRIAL


def test_an_unwritable_config_dir_still_yields_a_trial(tmp_path, monkeypatch):
    """This must never stop the application starting."""
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "blocked"))
    monkeypatch.delenv("HELSPIN_LICENCE", raising=False)
    # A FILE where the directory should be, so mkdir and the write both fail.
    (tmp_path / "blocked").write_text("not a directory")

    current = lic.start_or_resume_trial()
    assert current.kind == lic.KIND_TRIAL
    assert current.days_remaining == lic.TRIAL_DAYS


def test_signatures_are_not_yet_trusted():
    """Returning False is the safe default: when enforcement arrives, an
    unverified licence is the one case that must not silently pass."""
    assert lic.verify({"kind": "commercial"}, "anything") is False


def test_nothing_enforces_the_licence_yet(tmp_path, monkeypatch):
    """An expired trial must not stop the application working. Enforcement is
    a later decision, and this pins that it has not happened by accident."""
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("HELSPIN_LICENCE", raising=False)
    started = date.today() - timedelta(days=lic.TRIAL_DAYS + 100)
    (tmp_path / "trial.json").write_text(json.dumps({"started": started.isoformat()}))

    from helspin.ui.spectrum_canvas import SpectrumCanvas

    canvas = SpectrumCanvas()
    assert canvas is not None, "an expired trial must not block construction"
    assert lic.current_licence().expired


def test_the_status_line_survives_a_broken_licence_subsystem(monkeypatch):
    from helspin.ui import licence_dialog

    monkeypatch.setattr(
        lic, "current_licence",
        lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert "unavailable" in licence_dialog.status_line().lower()


def test_the_licence_names_its_copyright_holder():
    """A licence that does not say who owns the work is not much of a
    licence, and the name is the thing a commercial enquiry is addressed to."""
    from helspin.ui.licence_dialog import licence_text

    text = licence_text()
    assert "Copyright (c) 2026 H. Iw-ai" in text
    assert "Written and developed by H. Iw-ai" in text


def test_the_about_box_carries_the_attribution(qtbot, monkeypatch):
    from helspin import __main__ as main_module

    seen = {}
    monkeypatch.setattr(
        main_module.QMessageBox, "about",
        lambda parent, title, text: seen.update(text=text),
    )
    monkeypatch.setattr(main_module, "load_data_roots", lambda: [])
    window = main_module.MainWindow()
    qtbot.addWidget(window)
    window._about()

    assert "H. Iw-ai" in seen["text"]
    assert "Copyright" in seen["text"]
    # The old text claimed the canvas was unimplemented, which stopped being
    # true many versions ago.
    assert "not yet implemented" not in seen["text"]
