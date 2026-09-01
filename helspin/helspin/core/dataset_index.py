"""Persistent index of a Bruker data root.

Why this exists
---------------
Browsing lazily -- one directory listing per expansion -- is fine on local
disk and painful on a network share, where every call is a round trip. Opening
a sample meant a listing for the sample, then two more per experiment to decide
whether it had processed data. Hundreds of samples meant thousands of
round trips spread across the session, each one felt by the user.

The 0.3.0 answer was to pay that cost ONCE, in a single bulk walk, and cache
the result. That fixed the second session and made the FIRST one worse: the
walk visits every experiment directory of every sample (roughly one round trip
per experiment) before a single row can be drawn, so on a real root -- 400
samples, 8000 experiments, 20 ms per round trip -- nothing appeared for
minutes. A user cannot tell that apart from a hang.

So the walk is now split into three tiers, cheapest first, each one
independently cached:

1. **Discovery** (`discover_samples`): which directories are samples. A
   directory is a sample when it holds any integer-named subdirectory, and the
   check stops at the first one, so this costs ONE `os.scandir` per directory
   visited and nothing per experiment. Results are streamed to a callback in
   batches, so rows appear while the walk is still running instead of after it.
2. **Detail** (`scan_expnos`): one sample's experiments and which files each
   holds. One listing per experiment, paid only for samples the user actually
   opens -- or, later, by the background indexer.
3. **Metadata** (stored here, read elsewhere): PULPROG/nucleus/date per
   experiment. One file read each, filled in by background workers and cached,
   so a second session shows full metadata with no I/O at all -- which is also
   what lets the PULPROG filter reach samples that were never expanded.

Every tier writes into the same on-disk index, so an interrupted session still
leaves the next one faster than the last.

Staleness is decided by directory modification times, which is one cheap stat
per sample rather than a re-walk. A user-driven Refresh always rebuilds.

This module is deliberately stdlib-only: no Qt, no nmrglue. It is called from
worker threads, and everything it returns is plain data that the GUI thread
can apply to the model itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# 2: expno entries gained structural flags and cached metadata. An older
# cache is simply ignored and rebuilt -- it holds nothing worth migrating.
INDEX_FORMAT = 2

# A root deeper than this is almost certainly a wrong directory choice rather
# than a real layout; bounded so a mistake cannot walk a whole filesystem.
MAX_DEPTH = 8

# Samples handed to the caller at a time during discovery. Small enough that
# the first rows appear almost immediately, large enough that a 5000-sample
# root does not cost 5000 thread hops.
DISCOVER_BATCH = 40


@dataclass
class ExpnoEntry:
    """One experiment, as known from directory listings plus (later) acqus.

    The structural flags all come from the SAME single listing of the expno
    directory -- recording them costs nothing extra and is what lets the
    browser answer "is this 1D or 2D?" without going back to the disk.
    """

    name: str
    has_acqus: bool = False
    has_pdata: bool = False
    has_fid: bool = False
    has_ser: bool = False
    has_acqu2s: bool = False
    has_acqu3s: bool = False

    # Filled in by the metadata pass; `meta` says the read happened, so a
    # genuinely blank PULPROG is not mistaken for "not read yet".
    meta: bool = False
    # None = not looked; True/False = a procno does / does not hold 1r or 2rr.
    # The pdata DIRECTORY existing is not the same thing: an experiment that
    # was acquired but never processed has pdata/1 full of parameter files and
    # no spectrum, and listing it as droppable produces a failure on drop with
    # nothing to explain it.
    has_processed: bool | None = None
    processed_note: str = ""
    pulprog: str = ""
    nucleus: str = ""
    solvent: str = ""
    date: str = ""
    dim: int = 0        # 0 unknown, 1, 2, 3+ (3+ is listed but not loadable)
    error: str = ""

    @property
    def displayable(self) -> bool:
        """Shown in the browser at all. pdata's presence is the cheap test."""
        return self.has_pdata

    @property
    def loadable(self) -> bool:
        """Can actually be plotted. False only once we have LOOKED and found
        no processed spectrum -- an unchecked experiment is offered, because
        refusing to drop something that would have worked is worse than a
        failure message."""
        return self.has_processed is not False

    @property
    def structural_dim(self) -> int:
        """Dimensionality from the files present, or 0 when they do not say.

        `ser` + `acqu2s` is 2D and a bare `fid` is 1D -- that covers datasets
        that still have their raw data. Processed-only datasets (raw deleted
        after processing, which is common) show nothing here and fall through
        to the metadata pass, which reads PARMODE out of acqus.
        """
        if self.has_acqu3s:
            return 3
        if self.has_ser and self.has_acqu2s:
            return 2
        if self.has_fid:
            return 1
        return 0

    @property
    def best_dim(self) -> int:
        """Dimensionality from any source: metadata first, then structure."""
        return self.dim or self.structural_dim

    def as_row(self) -> list:
        flags = (
            (1 if self.has_acqus else 0)
            | (2 if self.has_pdata else 0)
            | (4 if self.has_fid else 0)
            | (8 if self.has_ser else 0)
            | (16 if self.has_acqu2s else 0)
            | (32 if self.has_acqu3s else 0)
            | (64 if self.meta else 0)
            | (128 if self.has_processed else 0)
            | (256 if self.has_processed is not None else 0)
        )
        return [
            self.name, flags, self.pulprog, self.nucleus, self.solvent,
            self.date, self.dim, self.processed_note,
        ]

    @classmethod
    def from_row(cls, row) -> ExpnoEntry:
        """Rebuild from the compact cache form.

        Tolerant of the 0.3.0 three-element `[name, has_acqus, has_pdata]`
        form so a stray old row degrades to "less is known" rather than to a
        parse failure that discards the whole cache.
        """
        name = row[0]
        if len(row) == 3 and isinstance(row[1], bool):
            return cls(name=name, has_acqus=bool(row[1]), has_pdata=bool(row[2]))
        flags = int(row[1]) if len(row) > 1 else 0
        entry = cls(
            name=name,
            has_acqus=bool(flags & 1),
            has_pdata=bool(flags & 2),
            has_fid=bool(flags & 4),
            has_ser=bool(flags & 8),
            has_acqu2s=bool(flags & 16),
            has_acqu3s=bool(flags & 32),
            meta=bool(flags & 64),
            has_processed=bool(flags & 128) if flags & 256 else None,
        )
        if len(row) >= 7:
            entry.pulprog = row[2] or ""
            entry.nucleus = row[3] or ""
            entry.solvent = row[4] or ""
            entry.date = row[5] or ""
            entry.dim = int(row[6] or 0)
        if len(row) >= 8:
            entry.processed_note = row[7] or ""
        return entry


@dataclass
class SampleEntry:
    path: str
    mtime: float = 0.0
    expnos: list[ExpnoEntry] = field(default_factory=list)
    # False until this sample's experiments have been listed. Discovery finds
    # samples without listing them, so a freshly discovered sample is known to
    # exist long before its contents are.
    detailed: bool = False

    # Lowercase "name + every PULPROG + every nucleus", built on demand and
    # dropped whenever the entry changes. The filter tests one short string
    # per sample instead of walking its experiments on every keystroke.
    _haystack: str | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return Path(self.path).name

    def invalidate(self) -> None:
        self._haystack = None

    def haystack(self) -> str:
        if self._haystack is None:
            parts = [self.name]
            for expno in self.expnos:
                if expno.pulprog:
                    parts.append(expno.pulprog)
                if expno.nucleus:
                    parts.append(expno.nucleus)
            self._haystack = "\n".join(parts).lower()
        return self._haystack

    @property
    def meta_complete(self) -> bool:
        return self.detailed and all(e.meta for e in self.expnos if e.displayable)


@dataclass
class RootIndex:
    root: str
    built_at: float = 0.0
    truncated: bool = False
    # False while discovery is still running (or was interrupted). A partial
    # index is still worth caching: showing 300 known samples instantly and
    # finding the rest in the background beats showing nothing.
    complete: bool = False
    samples: list[SampleEntry] = field(default_factory=list)
    _by_path: dict = field(default_factory=dict, repr=False, compare=False)

    def rebuild_map(self) -> None:
        self._by_path = {s.path: s for s in self.samples}

    def find(self, path: str) -> SampleEntry | None:
        """O(1) sample lookup. A linear scan was fine at 5 samples and is not
        at 5000 -- and it ran on every single expansion."""
        if len(self._by_path) != len(self.samples):
            self.rebuild_map()
        return self._by_path.get(path)

    def add(self, sample: SampleEntry) -> bool:
        """Append unless the path is already indexed. True if it was added."""
        if self.find(sample.path) is not None:
            return False
        self.samples.append(sample)
        self._by_path[sample.path] = sample
        return True

    def drop(self, path: str) -> None:
        entry = self.find(path)
        if entry is None:
            return
        self.samples.remove(entry)
        self._by_path.pop(path, None)

    def to_dict(self) -> dict:
        return {
            "format": INDEX_FORMAT,
            "root": self.root,
            "built_at": self.built_at,
            "truncated": self.truncated,
            "complete": self.complete,
            "samples": [
                {
                    "path": s.path,
                    "mtime": s.mtime,
                    "detailed": s.detailed,
                    "expnos": [e.as_row() for e in s.expnos],
                }
                for s in self.samples
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RootIndex | None:
        if not isinstance(data, dict) or data.get("format") != INDEX_FORMAT:
            return None
        try:
            index = cls(
                root=data["root"],
                built_at=float(data.get("built_at", 0.0)),
                truncated=bool(data.get("truncated", False)),
                complete=bool(data.get("complete", True)),
                samples=[
                    SampleEntry(
                        path=s["path"],
                        mtime=float(s.get("mtime", 0.0)),
                        detailed=bool(s.get("detailed", True)),
                        expnos=[
                            ExpnoEntry.from_row(e) for e in s.get("expnos", [])
                        ],
                    )
                    for s in data.get("samples", [])
                ],
            )
        except (KeyError, TypeError, ValueError, IndexError):
            # A corrupt cache must never stop the app; rebuilding is cheap
            # compared with failing to start.
            return None
        index.rebuild_map()
        return index


# --- tier 2: one sample's experiments ----------------------------------------


def scan_expnos(sample_dir: str) -> list[ExpnoEntry]:
    """Experiments in one sample, one directory listing each.

    Reading every interesting filename from the experiment's OWN listing means
    `acqus`, `pdata`, `fid`, `ser` and `acqu2s`/`acqu3s` presence all come from
    one round trip instead of one each. That single listing is what later
    answers "1D or 2D?" for free.
    """
    out: list[ExpnoEntry] = []
    try:
        with os.scandir(sample_dir) as entries:
            candidates = [
                e for e in entries
                if e.name.isdigit() and e.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return out

    for candidate in sorted(candidates, key=lambda e: int(e.name)):
        entry = ExpnoEntry(name=candidate.name)
        try:
            with os.scandir(candidate.path) as inner:
                for child in inner:
                    name = child.name
                    if name == "acqus":
                        entry.has_acqus = True
                    elif name == "pdata":
                        entry.has_pdata = True
                    elif name == "fid":
                        entry.has_fid = True
                    elif name == "ser":
                        entry.has_ser = True
                    elif name == "acqu2s":
                        entry.has_acqu2s = True
                    elif name == "acqu3s":
                        entry.has_acqu3s = True
        except OSError:
            pass
        out.append(entry)
    return out


PROCESSED_FILES = ("1r", "2rr", "3rrr", "4rrrr")


def inspect_processed(expno_dir, procno: int = 1):
    """Is there a processed spectrum here? Returns (state, reason).

    ``state`` is True, False, or **None for "could not tell"** -- and that
    third value is the whole point. The first version returned a plain bool
    and answered False when the directory could not be listed at all, so a
    momentary share hiccup or a permissions quirk was recorded, and CACHED, as
    "this experiment has no data" -- greying out a perfectly good dataset for
    the rest of the session and every session after it. Not knowing and
    knowing there is nothing are different answers and must stay different.

    ``reason`` is a short human sentence naming what was actually found, so
    the browser can say why a row is dimmed instead of leaving the user to
    guess. "Some spectra cannot be shown" with no explanation is barely better
    than them silently failing to open.
    """
    pdata = Path(expno_dir) / "pdata"
    wanted = set(PROCESSED_FILES)

    def look(directory):
        """(found, listing) -- listing is None when the read itself failed."""
        try:
            with os.scandir(directory) as entries:
                names = [e.name for e in entries]
        except OSError:
            return None, None
        return any(name in wanted for name in names), names

    default_names = None
    found, names = look(pdata / str(procno))
    if found:
        return True, ""
    if found is False:
        # An EMPTY pdata/1 is not the end of the search. Bruker experiments
        # routinely put their result somewhere else: an STD difference writes
        # on-resonance, off-resonance and the difference into separate
        # procnos, and pdata/1 can hold parameters and nothing plottable.
        # Concluding "no data" here marked exactly those datasets unusable --
        # caught by `helspin --check`, which reported NO for an experiment
        # whose spectrum was sitting in pdata/3.
        default_names = names

    try:
        with os.scandir(pdata) as entries:
            candidates = sorted(
                (e for e in entries if e.name.isdigit() and e.is_dir()),
                key=lambda e: int(e.name),
            )
    except FileNotFoundError:
        return False, (
            "no pdata directory in this experiment -- it has been acquired "
            "but never processed."
        )
    except OSError as exc:
        return None, f"could not read pdata ({exc.strerror or exc})"

    if not candidates:
        return False, "pdata exists but contains no procno directories."

    unreadable = 0
    for candidate in candidates:
        if candidate.name == str(procno) and default_names is not None:
            continue        # already listed above, and it held nothing
        found, _ = look(candidate.path)
        if found:
            return True, ""
        if found is None:
            unreadable += 1
    if unreadable:
        return None, "some procnos could not be read; leaving this undecided"
    listed = ", ".join(c.name for c in candidates[:8])
    if default_names is not None:
        shown = ", ".join(sorted(default_names)[:6]) or "nothing"
        return False, (
            f"no 1r or 2rr in any procno (checked: {listed}; "
            f"pdata/{procno} holds: {shown}). "
            "Process the experiment in TopSpin, then Refresh."
        )
    return False, (
        f"no 1r or 2rr under any procno (checked: {listed}). "
        "Process the experiment in TopSpin, then Refresh."
    )


def processed_present(expno_dir, procno: int = 1):
    """True / False / None. See inspect_processed for what None means."""
    return inspect_processed(expno_dir, procno)[0]


# The pre-0.4.0 private name, kept so nothing that reaches for it breaks.
_scan_expnos = scan_expnos


def sample_mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


# --- tier 1: which directories are samples -----------------------------------


def _has_integer_child_dir(path: str) -> bool:
    """True if this directory holds at least one integer-named subdirectory.

    Stops at the FIRST match: identifying a sample does not require knowing
    how many experiments it has, and enumerating them all is what made
    discovery cost a round trip per experiment instead of one per directory.
    """
    try:
        with os.scandir(path) as entries:
            for child in entries:
                if child.name.isdigit() and child.is_dir(follow_symlinks=False):
                    return True
    except OSError:
        return False
    return False


def discover_samples(
    root,
    limit: int = 5000,
    on_batch=None,
    should_stop=None,
    depth: int = MAX_DEPTH,
    batch_size: int = DISCOVER_BATCH,
) -> tuple[list[str], bool]:
    """Find sample directories under ``root``. Returns (paths, truncated).

    ``on_batch`` is called with a list of paths every ``batch_size`` finds (and
    once at the end with the remainder), so the caller can show them while the
    walk continues. ``should_stop`` is polled between directories so a closed
    or reconfigured browser does not leave a worker walking a share for
    minutes.

    Cost is one listing per directory visited, and a sample is a leaf -- the
    walk never descends into one.
    """
    root = str(root)
    found: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending and on_batch is not None:
            on_batch(list(pending))
        pending.clear()

    def emit(path: str) -> None:
        found.append(path)
        pending.append(path)
        if len(pending) >= batch_size:
            flush()

    def stopped() -> bool:
        return should_stop is not None and should_stop()

    def walk(directory: str, remaining: int) -> bool:
        """False means: stop the whole walk (limit reached or cancelled)."""
        if remaining < 0:
            return True
        if stopped():
            return False
        try:
            with os.scandir(directory) as entries:
                children = sorted(
                    (e for e in entries if e.is_dir(follow_symlinks=False)),
                    key=lambda e: e.name,
                )
        except OSError:
            return True     # an unreadable subtree must not abort the scan

        for child in children:
            if len(found) >= limit:
                return False
            if stopped():
                return False
            if child.name == "pdata":
                continue
            if _has_integer_child_dir(child.path):
                emit(child.path)
                continue    # a sample is a leaf for this purpose
            if not walk(child.path, remaining - 1):
                return False
        return True

    if _has_integer_child_dir(root):
        emit(root)
        flush()
        return found, False

    completed = walk(root, depth)
    flush()
    # Cancellation is not truncation: the caller asked to stop, and saying
    # "your root is too big" for that would be a lie shown in the status bar.
    truncated = (not completed) and (not stopped()) and len(found) >= limit
    return found, truncated


def build_index(root, limit: int = 5000, progress=None) -> RootIndex:
    """Walk a data root once and record every sample and experiment.

    The eager, all-tiers-at-once build. The application no longer calls this
    on the browsing path -- it discovers samples first and details them on
    demand -- but it remains the simplest way to produce a complete index in
    one call, which is what the tests and any future "index this root now"
    action want.

    ``progress`` is called with the running sample count so a long build can be
    shown rather than looking like a hang.
    """
    root = Path(root)
    index = RootIndex(root=str(root), built_at=time.time())

    paths, truncated = discover_samples(root, limit=limit)
    for path in paths:
        entry = SampleEntry(path=path, mtime=sample_mtime(path))
        entry.expnos = scan_expnos(path)
        entry.detailed = True
        index.add(entry)
        if progress is not None:
            progress(len(index.samples))
    index.truncated = truncated
    index.complete = not truncated
    return index


# --- on-disk cache -----------------------------------------------------------


def cache_dir() -> Path:
    base = os.environ.get("HELSPIN_CACHE_DIR")
    if base:
        return Path(base)
    if sys.platform == "win32":
        # LOCALAPPDATA is the documented non-roaming per-user location. A
        # dot-directory in the profile would be copied around by roaming
        # profiles in some deployments, which is exactly what a cache of a
        # machine-local network mount should not be.
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "HelSpin" / "cache"
    return Path.home() / ".cache" / "helspin"


def cache_path(root) -> Path:
    """One cache file per root, named by a hash of its path.

    Hashed rather than sanitised so two roots cannot collide, and so the name
    stays valid whatever the path contains.
    """
    digest = hashlib.sha1(str(Path(root)).encode("utf-8")).hexdigest()[:16]
    return cache_dir() / f"index-{digest}.json"


def save_payload(root, payload: dict) -> None:
    """Write an already-serialised index dict for ``root``.

    Split from save_index so the GUI thread can do the `to_dict()` (it owns
    the live index and is the only thread allowed to read it while it is being
    mutated) and hand a plain, private dict to a worker for the actual write.
    Passing the live RootIndex to a worker instead is how a "dictionary
    changed size during iteration" crash gets shipped.
    """
    path = cache_path(root)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(temporary)
        except OSError:
            pass


def save_index(index: RootIndex) -> None:
    path = cache_path(Path(index.root))
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a temporary file so an interrupted write cannot leave a
        # half-written cache that fails to parse next time. The temp name
        # carries the pid: two HelSpin windows on the same root would
        # otherwise share one temp file and could interleave their bytes.
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(index.to_dict(), handle)
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        # A cache that cannot be written is a slower start, never an error the
        # user should see. Clean up the partial file if there is one.
        try:
            os.unlink(temporary)
        except OSError:
            pass


def load_index(root) -> RootIndex | None:
    path = cache_path(root)
    try:
        with open(path, encoding="utf-8") as handle:
            return RootIndex.from_dict(json.load(handle))
    except (OSError, ValueError):
        return None


def stale_samples(index: RootIndex) -> list[SampleEntry]:
    """Samples whose directory changed since the index was built.

    One stat per sample -- far cheaper than re-walking, and enough to catch
    new or removed experiments. Samples that have never been detailed are
    skipped: there is nothing cached to invalidate, and they are listed on
    first use anyway.
    """
    changed = []
    for sample in index.samples:
        if not sample.detailed:
            continue
        try:
            if os.stat(sample.path).st_mtime > sample.mtime:
                changed.append(sample)
        except OSError:
            changed.append(sample)    # vanished: treat as changed
    return changed


def refresh_sample(sample: SampleEntry) -> None:
    """Re-read one sample's experiments in place, keeping known metadata.

    Metadata costs a file read each and does not change because an experiment
    appeared next door, so already-read PULPROG/nucleus values are carried
    across to the new listing rather than discarded and read again.
    """
    previous = {e.name: e for e in sample.expnos}
    fresh = scan_expnos(sample.path)
    for entry in fresh:
        old = previous.get(entry.name)
        if old is not None and old.meta:
            entry.meta = True
            entry.pulprog = old.pulprog
            entry.nucleus = old.nucleus
            entry.solvent = old.solvent
            entry.date = old.date
            entry.dim = old.dim
            entry.error = old.error
            # has_processed is deliberately NOT carried over. Processing an
            # experiment in TopSpin writes inside <expno>/pdata, which does
            # not touch the sample directory's mtime -- so if a refresh kept
            # the old answer, a dataset that was just processed would stay
            # marked "no data" for ever, and Refresh (the one thing the user
            # would try) would appear to do nothing.
    sample.expnos = fresh
    sample.detailed = True
    sample.mtime = sample_mtime(sample.path)
    sample.invalidate()
