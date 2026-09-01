"""Licence status: which licence is in force, and when it lapses.

**Nothing here enforces anything.** It reads a licence file if one is present,
records the start of a trial if one is not, and reports what it found. No
caller blocks a feature, refuses to start, or nags. That is deliberate: the
mechanism is worth having in place and settled before it is ever relied upon,
and switching it on is then a decision rather than a scramble.

Where the file lives
--------------------
`HELSPIN_LICENCE` overrides everything, which is what a site-wide deployment
or a test needs. Otherwise `licence.json` beside the index cache, so a user
has one place to look for HelSpin's state.

The trial
---------
Six months from first run, recorded the first time the application starts.
Dated from FIRST RUN rather than from the build, so someone who installs a
year-old copy still gets a fair six months rather than an expired one.

An honest warning about what this is not
----------------------------------------
The licence file is plain JSON and unsigned, so a user can open it and change
the date. That is fine while nothing is enforced. If enforcement is ever
switched on, the file must carry a signature (Ed25519 over the payload, public
key compiled in) and `verify` below must check it -- otherwise the licence
system is decoration. The `signature` field and the `verified` flag exist now
so that adding this does not change the file format or every call site.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

TRIAL_DAYS = 183          # ~6 months
KIND_TRIAL = "trial"
KIND_ACADEMIC = "academic"
KIND_COMMERCIAL = "commercial"

# Kinds that never lapse. Academic use is granted by the licence itself, so an
# academic licence file carries no expiry unless one is written into it.
PERPETUAL = (KIND_ACADEMIC,)


@dataclass
class Licence:
    kind: str = KIND_TRIAL
    licensee: str = ""
    issued: date | None = None
    expires: date | None = None
    source: str = ""                 # the file it came from, or "" for a trial
    signature: str = ""              # unverified; see the module docstring
    verified: bool = False
    problem: str = ""                # why a file was ignored, if it was

    @property
    def expired(self) -> bool:
        return self.expires is not None and date.today() > self.expires

    @property
    def days_remaining(self) -> int | None:
        """None when the licence does not expire."""
        if self.expires is None:
            return None
        return (self.expires - date.today()).days

    def describe(self) -> str:
        """One line for a status bar or an About box."""
        who = f" ({self.licensee})" if self.licensee else ""
        if self.expires is None:
            return f"{self.kind.capitalize()} licence{who} — no expiry"
        left = self.days_remaining
        if left is None or left < 0:
            return (
                f"{self.kind.capitalize()} licence{who} — expired "
                f"{self.expires.isoformat()}"
            )
        return (
            f"{self.kind.capitalize()} licence{who} — {left} days remaining "
            f"(to {self.expires.isoformat()})"
        )


def config_dir() -> Path:
    """Where HelSpin keeps its own state. Shares the cache location so there
    is one place to look, and one place to clear."""
    from .dataset_index import cache_dir

    return cache_dir()


def licence_path() -> Path:
    override = os.environ.get("HELSPIN_LICENCE")
    return Path(override) if override else config_dir() / "licence.json"


def trial_path() -> Path:
    return config_dir() / "trial.json"


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def verify(payload: dict, signature: str) -> bool:
    """Placeholder for signature checking. Always False for now.

    Returning False rather than True is the safe default: when enforcement is
    added, an unverified licence is the one case that must not silently pass.
    """
    return False


def read_licence_file(path: Path | None = None) -> Licence | None:
    """The licence from disk, or None if there is not a usable one."""
    path = Path(path) if path is not None else licence_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    kind = str(data.get("kind", KIND_COMMERCIAL)).lower()
    expires = _parse_date(data.get("expires"))
    if expires is None and kind not in PERPETUAL:
        # A file with no readable expiry is not treated as unlimited: that is
        # the failure mode a typo would silently create.
        return Licence(
            kind=kind, source=str(path),
            problem="no readable expiry date in the licence file",
            issued=_parse_date(data.get("issued")),
        )
    return Licence(
        kind=kind,
        licensee=str(data.get("licensee", "")),
        issued=_parse_date(data.get("issued")),
        expires=expires,
        source=str(path),
        signature=str(data.get("signature", "")),
        verified=verify(data, str(data.get("signature", ""))),
    )


def start_or_resume_trial(today: date | None = None) -> Licence:
    """The trial licence, starting it on first call.

    Dated from first run so a copy installed long after it was built still
    gets its full six months. An unreadable or unwritable record yields a
    trial dated today rather than an error: this must never stop the
    application starting.
    """
    today = today or date.today()
    path = trial_path()
    started = None
    try:
        with open(path, encoding="utf-8") as handle:
            started = _parse_date(json.load(handle).get("started"))
    except (OSError, ValueError, AttributeError):
        started = None

    if started is None:
        started = today
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"started": started.isoformat()}, handle)
        except OSError:
            pass

    return Licence(
        kind=KIND_TRIAL,
        issued=started,
        expires=started + timedelta(days=TRIAL_DAYS),
        source="",
    )


def current_licence() -> Licence:
    """The licence in force: a file if there is one, otherwise the trial."""
    from_file = read_licence_file()
    if from_file is not None and not from_file.problem:
        return from_file
    trial = start_or_resume_trial()
    if from_file is not None:
        trial.problem = from_file.problem
    return trial
