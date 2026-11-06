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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.paths import holds_experiments as is_sample_dir
from ..domain.paths import is_number_name, numbered_names_hold_experiments

# 2: expno entries gained structural flags and cached metadata. An older
# cache is simply ignored and rebuilt -- it holds nothing worth migrating.
INDEX_FORMAT = 2

# Which rule found the samples in a cached index. 2 (0.9.18): a sample holds
# numbered EXPERIMENT folders. An index from an earlier rule is still loaded
# -- its experiment lists and metadata are valid -- but counts as incomplete,
# so the root is searched again once and wrongly detected "samples" (a folder
# that merely held a "2026") are replaced by what is really below them.
DISCOVERY_RULE = 2

# A root deeper than this is almost certainly a wrong directory choice rather
# than a real layout; bounded so a mistake cannot walk a whole filesystem.
MAX_DEPTH = 8

# Samples handed to the caller at a time during discovery. Small enough that
# the first rows appear almost immediately, large enough that a 5000-sample
# root does not cost 5000 thread hops.
DISCOVER_BATCH = 40

# Most samples a data root may hold before discovery stops. A guard against
# pointing HelSpin at "/" or a whole server, not a size anyone should reach:
# the walk is sorted by name, so the samples cut off are the LAST ones --
# with date-prefixed names, the newest data. 5000 (until 0.9.15) was low
# enough for a real instrument archive to hit, at which point new data
# silently never appeared however often Refresh was pressed.
#
# Raising it costs nothing for smaller roots: every cost scales with the
# samples actually present, never with this number. What has to stay true
# for a root this size is listed in HANDOFF.md, "Fast finding".
SAMPLE_LIMIT = 100_000

# A root this close to the limit is reported too, while there is still time
# to split it before data starts being cut off.
NEAR_LIMIT_FRACTION = 0.9

# Directory checks run in parallel during discovery. On a share every check
# is a round trip, and they are independent, so this divides the time to find
# all samples by roughly this number; on a local disk it changes nothing.
DISCOVER_WORKERS = 16

# A sample changed within this long is re-listed at start-up if it has any
# experiment still in progress (see SampleEntry.unsettled), even though its
# own folder's date did not change -- processing writes inside the experiment
# folder, which a sample-level check cannot see.
RECENT_SECONDS = 14 * 24 * 3600


def _decode_flags(flags: int) -> tuple:
    """as_row()'s flag bits -> the eight flag fields, in declaration order:
    has_acqus, has_pdata, has_fid, has_ser, has_acqu2s, has_acqu3s, meta,
    has_processed (None unless bit 256 says it was looked at)."""
    return (bool(flags & 1), bool(flags & 2), bool(flags & 4), bool(flags & 8),
            bool(flags & 16), bool(flags & 32), bool(flags & 64),
            bool(flags & 128) if flags & 256 else None)


_FLAG_BITS = tuple(_decode_flags(f) for f in range(512))


# slots: a 100,000-sample root holds about a million of these. Without slots
# each carries its own attribute dict (~3x the memory) and is slower to build
# when the cache is loaded.
@dataclass(slots=True)
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
        if len(row) >= 8 and 0 <= flags < len(_FLAG_BITS):
            # The common, current form, on the fast path: positional fields
            # and a pre-decoded flag tuple. Loading a 100,000-sample cache
            # builds a million of these, and the keyword form below took
            # three times as long.
            return cls(name, *_FLAG_BITS[flags], row[7] or "", row[2] or "",
                       row[3] or "", row[4] or "", row[5] or "",
                       int(row[6] or 0), "")
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


@dataclass(slots=True)
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

    @property
    def unsettled(self) -> bool:
        """Has an experiment that may still change on disk: no pdata yet
        (acquiring, or not processed), pdata without a processed spectrum,
        or no acqus. Such changes happen INSIDE the experiment folder and do
        not touch the sample folder's date, so a date check alone misses
        them -- the reason Refresh did not show newly processed data."""
        return self.detailed and any(
            (not e.has_pdata) or e.has_processed is False or not e.has_acqus
            for e in self.expnos
        )

    def settled_names(self) -> set:
        """Experiments whose cached metadata cannot have changed: read,
        processed, with acqus. A re-listing need not read them again."""
        return {
            e.name for e in self.expnos
            if e.meta and e.has_pdata and e.has_acqus and e.has_processed is True
            and not e.error
        }


@dataclass
class RootIndex:
    root: str
    built_at: float = 0.0
    truncated: bool = False
    # The sample limit a truncated search stopped at (0: not recorded).
    limit: int = 0
    # Folders the last search could not read: [[path, error], ...].
    problems: list = field(default_factory=list)
    # The last sample an unfinished search reached. The next search resumes
    # AFTER it instead of starting again from the top: a volume too big to
    # search in one sitting used to restart every session and so never
    # reached its last folders (by name: the newest year).
    resume_after: str = ""
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
            "rule": DISCOVERY_RULE,
            "root": self.root,
            "built_at": self.built_at,
            "truncated": self.truncated,
            "limit": self.limit,
            "problems": [list(p) for p in self.problems],
            "resume_after": self.resume_after,
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
                limit=int(data.get("limit", 0) or 0),
                problems=[tuple(p) for p in data.get("problems", [])][:MAX_PROBLEMS],
                resume_after=str(data.get("resume_after", "") or ""),
                complete=bool(data.get("complete", True))
                and int(data.get("rule", 1)) >= DISCOVERY_RULE,
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
            # Symlinks followed, to stay consistent with the discovery
            # walk: if _has_integer_child_dir counted a linked expno as
            # evidence that this directory IS a sample, then excluding the
            # same link here would list that sample with zero experiments --
            # a row that opens onto nothing, which is worse than either
            # consistent answer.
            candidates = [
                e for e in entries
                if is_number_name(e.name) and e.is_dir()
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
                (e for e in entries if is_number_name(e.name) and e.is_dir()),
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


def _link_identity(entry):
    """Identity of a SYMLINK's target, for loop detection. None if unusable.

    Called for symlinks ONLY, and that restriction is a performance rule as
    much as a correctness one.

    `DirEntry.stat()` is a real round trip -- unlike `is_dir()` and
    `is_symlink()`, which answer from the directory read that has already
    happened. Calling it on every child directory added roughly one round
    trip per directory: on a 400-sample share that measured as 448 extra
    stats, about a second over SMB, on the one operation this application
    exists to make fast. Symlinks are rare, so paying it only for them costs
    nothing on a share that has none.

    It is also the correct restriction: a tree of real directories cannot
    contain a cycle, so real directories never need identifying.

    (device, inode) where the filesystem provides a real inode. Where it does
    not -- some SMB/CIFS and FUSE mounts report st_ino as 0 for every entry --
    the resolved path is used instead: weaker, since text can differ between
    runs, but enough to break a self-referential loop, which is the case that
    must not be allowed to run forever. None means the link is broken or its
    target unreadable, and it is skipped.
    """
    try:
        info = entry.stat()          # follows the link: we want the TARGET
    except OSError:
        return None                  # broken link, or unreadable target
    if info.st_ino:
        return (info.st_dev, info.st_ino)
    try:
        return os.path.realpath(entry.path)
    except OSError:
        return None


# Folders a search could not read, kept with the index and shown by
# `helspin --why` / `--scan`. Capped: a volume full of them says enough at 200.
MAX_PROBLEMS = 200


def _note(problems, path, error) -> None:
    if problems is not None and len(problems) < MAX_PROBLEMS:
        problems.append((str(path), f"{type(error).__name__}: {error}"))


def _sub_directories(directory: str, problems=None):
    """Child directories of ``directory``, symlinks included, sorted by name.

    Symlinks ARE followed. On Linux and macOS a data root very often holds
    symlinks to the real spectrometer mounts -- ``spect600 ->
    /mnt/raw/spect600`` -- and skipping them made an entire instrument's data
    silently invisible in the browser, with no error and no dimmed row to
    suggest anything had been missed.

    Errors are handled ONE ENTRY at a time. Until 0.9.20 a single entry
    whose type could not be read (a link into a folder without permission,
    a file the share would not stat) made this return NOTHING for the whole
    folder -- silently dropping every sample below it, on exactly the large
    shared volumes where some such entry is almost certain. A listing that
    fails outright is retried once (shares hiccup), then whatever was read
    is kept and the failure recorded in ``problems``.
    """
    found = []
    for attempt in (1, 2):
        found = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir():
                            found.append(entry)
                    except OSError as exc:
                        _note(problems, entry.path, exc)
        except OSError as exc:
            if attempt == 2:
                _note(problems, directory, exc)
            continue
        break
    return sorted(found, key=lambda e: e.name)


def _has_integer_child_dir(path: str) -> bool:
    """Kept for callers of the old name: now the experiment-based rule."""
    return is_sample_dir(path)


def discover_samples(
    root,
    limit: int = 5000,
    on_batch=None,
    should_stop=None,
    depth: int = MAX_DEPTH,
    batch_size: int = DISCOVER_BATCH,
    workers: int = 1,
    problems: list | None = None,
    resume_after: str | None = None,
) -> tuple[list[str], bool]:
    """Find sample directories under ``root``. Returns (paths, truncated).

    ``on_batch`` is called with a list of paths every ``batch_size`` finds (and
    once at the end with the remainder), so the caller can show them while the
    walk continues. ``should_stop`` is polled between directories so a closed
    or reconfigured browser does not leave a worker walking a share for
    minutes.

    Cost is one listing per directory visited, and a sample is a leaf -- the
    walk never descends into one. With ``workers`` > 1 the sample checks of
    one directory's children run concurrently; results are still reported
    in the same sorted, depth-first order as a serial walk.

    Nothing about one folder can stop the search of the others: unreadable
    folders and entries are skipped and, if ``problems`` is given, recorded
    there as (path, error). (Until 0.9.20 one odd entry could raise and end
    the whole search, so everything after it in name order -- on a volume,
    the newest year -- was never reached.)

    ``resume_after`` (a sample path found by an earlier, unfinished search)
    skips everything up to and including it, in the same order: folders
    wholly before it are not even listed, and only its ancestors are walked
    through. The result is the tail a full search would have produced.
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

    # Symlink targets already followed, so two links to one directory do not
    # walk it twice. Only symlinks are recorded: see _link_identity for why
    # touching real directories here would be both unnecessary and slow.
    seen_links: set = set()
    # The root's own resolved path, computed once. A link whose target lies
    # inside the root is not followed at all -- everything under it is already
    # reachable by its real path, so following it could only produce
    # duplicates. That single comparison replaces de-duplicating the results,
    # which cost a resolve per sample found (400 round trips on a 400-sample
    # share) the moment one link appeared anywhere.
    try:
        root_real = os.path.realpath(root)
    except OSError:
        root_real = root

    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    # A second pool for entry type checks inside one listing. Where a share
    # does not report entry types each is_dir() is a round trip; done one
    # after another, a folder of 200 sample folders cost 200 sequential round
    # trips. (Separate from `pool`: classify() already runs there, and
    # waiting on the same pool from inside it can deadlock.)
    type_pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None

    def is_folder(entry) -> bool:
        try:
            return entry.is_dir()
        except OSError as exc:
            _note(problems, entry.path, exc)
            return False
    # Checked a chunk at a time, so a cancel or the limit is noticed within
    # one chunk rather than after a directory of ten thousand children.
    chunk = max(1, workers * 4)

    def listing(path: str) -> list:
        """All entries of one folder, read ONCE. A failed read is retried
        once (shares hiccup); then whatever was read is kept and the
        failure recorded."""
        entries: list = []
        for attempt in (1, 2):
            entries = []
            try:
                with os.scandir(path) as it:
                    entries.extend(it)
            except OSError as exc:
                if attempt == 2:
                    _note(problems, path, exc)
                continue
            break
        return entries

    def classify(path: str):
        """(is_sample, subfolders) from ONE listing of ``path``.

        Each folder is listed exactly once: deciding "is it a sample?" and
        "what is below it?" used to be two listings of every folder that was
        not a sample. And a sample's own entries are never type-checked --
        only its lowest numbered names are looked into.
        """
        try:
            entries = listing(path)
            numbered = [e.name for e in entries if is_number_name(e.name)]
            if numbered and numbered_names_hold_experiments(path, numbered):
                return True, None
            if type_pool is not None and len(entries) > 1:
                flags = list(type_pool.map(is_folder, entries))
            else:
                flags = [is_folder(e) for e in entries]
            subfolders = [e for e, f in zip(entries, flags, strict=True) if f]
            subfolders.sort(key=lambda e: e.name)
            return False, subfolders
        except Exception as exc:      # noqa: BLE001 -- one folder, not the search
            _note(problems, path, exc)
            return False, []

    def candidates(subfolders: list) -> list:
        out = []
        for child in subfolders:
            if child.name == "pdata":
                continue
            try:
                is_link = child.is_symlink()
            except OSError as exc:
                _note(problems, child.path, exc)
                is_link = False
            # is_symlink() is free -- it comes from the directory read. Only
            # inside this branch is a stat paid for.
            if is_link:
                identity = _link_identity(child)
                if identity is None:
                    continue        # broken link: nothing to descend into
                if identity in seen_links:
                    continue        # this target has already been followed
                seen_links.add(identity)
                try:
                    target = os.path.realpath(child.path)
                except OSError:
                    continue
                # Pointing back inside the root: a loop, or a second name for
                # something the walk reaches anyway. Skipping it is what
                # bounds a self-referential link, and it costs one resolve
                # per link rather than one per sample.
                if target == root_real or target.startswith(root_real + os.sep):
                    continue
            out.append(child)
        return out

    resume: tuple | None = None
    if resume_after:
        try:
            resume = Path(resume_after).relative_to(root).parts or None
        except ValueError:
            resume = None           # not below this root: search it all

    def walk(subfolders: list, remaining: int, parts: tuple = ()) -> bool:
        """Search below a folder whose subfolders are already listed.
        False means: stop the whole walk (limit reached or cancelled)."""
        if remaining < 0:
            return True
        if stopped():
            return False
        children = candidates(subfolders)
        through = None              # an ancestor of the resume point
        if resume is not None:
            todo = []
            for child in children:
                here = parts + (child.name,)
                head = resume[:len(here)]
                if here < head:
                    continue        # searched in an earlier session
                if here == head:
                    if len(here) < len(resume):
                        through = child  # walk through it, no sample check
                    continue        # (or it IS the resume point: known)
                todo.append(child)
            children = todo
            if through is not None:
                _sample, below = classify(through.path)
                if not walk(below or [], remaining - 1,
                            parts + (through.name,)):
                    return False
        for start in range(0, len(children), chunk):
            if stopped():
                return False
            part = children[start:start + chunk]
            # The children are listed in parallel; each listing serves both
            # the sample check and, for a non-sample, the walk below it.
            if pool is not None and len(part) > 1:
                results = list(pool.map(classify, [c.path for c in part]))
            else:
                results = [classify(c.path) for c in part]
            for child, (is_sample, below) in zip(part, results, strict=True):
                if len(found) >= limit:
                    return False
                if stopped():
                    return False
                if is_sample:
                    emit(child.path)
                    continue    # a sample is a leaf for this purpose
                try:
                    if not walk(below or [], remaining - 1,
                                parts + (child.name,)):
                        return False
                except Exception as exc:  # noqa: BLE001 -- skip it, go on
                    _note(problems, child.path, exc)
        return True

    try:
        is_sample, below = classify(root)
        if is_sample:
            emit(root)
            flush()
            return found, False
        completed = walk(below or [], depth)
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
        if type_pool is not None:
            type_pool.shutdown(wait=True)
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


def explain_path(root, target) -> list[str]:
    """Why the explorer does or does not find ``target`` under ``root``.

    Retraces the search from the root down to the target folder by folder,
    with the explorer's own rules, and says at each step what it decided --
    so "I cannot find my data" gets a definite answer instead of a guess.
    Costs a couple of listings per level; it never walks the whole root.
    """
    from ..domain.paths import numbered_subfolders

    root = Path(root).expanduser()
    target = Path(target).expanduser()
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return [f"{target} is not inside the data root {root}."]
    if not root.is_dir():
        return [f"The data root {root} is not reachable (unmounted share?)."]
    lines = [f"Data root: {root}"]
    if is_sample_dir(str(root)):
        return lines + ["The data root itself is a sample (it holds numbered "
                        "experiment folders), so it is the only sample shown."]
    try:
        root_real = os.path.realpath(root)
    except OSError:
        root_real = str(root)
    current = root
    for level, part in enumerate(parts, start=1):
        folder = current / part
        shown = "/".join(parts[:level])
        if part == "pdata":
            lines.append(f"  {shown}: a pdata folder; never searched.")
            break
        if level > MAX_DEPTH + 1:
            lines.append(f"  {shown}: NOT SEARCHED -- deeper than {MAX_DEPTH} "
                         "folder levels below the data root. Add a folder "
                         "further in as the data root.")
            break
        if not folder.is_dir():
            lines.append(f"  {shown}: not a folder, or not readable.")
            break
        # What the SEARCH sees: its own listing of the parent. A folder can
        # exist and still be missing from it (an unreadable sibling entry
        # used to empty the whole listing).
        problems: list = []
        listed = {c.name for c in _sub_directories(str(current), problems)}
        if part not in listed:
            lines.append(f"  {shown}: NOT IN THE SEARCH'S LISTING of "
                         f"{current.name or current} -- the search never sees it.")
            lines.extend(f"    problem: {p} -- {e}" for p, e in problems[:10])
            break
        if folder.is_symlink() and not (
                os.path.realpath(folder) == root_real
                or os.path.realpath(folder).startswith(root_real + os.sep)):
            try:
                os.stat(folder)
                lines.append(f"  {shown}: a link to {os.path.realpath(folder)} "
                             "(followed).")
            except OSError as exc:
                lines.append(f"  {shown}: a link whose target cannot be read "
                             f"({exc}); the search skips it.")
                break
        if folder.is_symlink():
            try:
                real = os.path.realpath(folder)
            except OSError:
                real = "?"
            if real == root_real or real.startswith(root_real + os.sep):
                lines.append(f"  {shown}: a link back inside the data root "
                             f"(to {real}); searched under that real path "
                             "instead.")
                break
        if is_sample_dir(str(folder)):
            numbers = [e.name for e in numbered_subfolders(str(folder))]
            lines.append(f"  {shown}: SAMPLE -- found (experiments "
                         f"{', '.join(numbers[:8])}"
                         f"{', ...' if len(numbers) > 8 else ''}).")
            if level < len(parts) and not is_number_name(parts[level]):
                lines.append(f"  The search does not look inside a sample, so "
                             f"'{parts[level]}' below it is never reached.")
            return lines
        numbered = [e.name for e in numbered_subfolders(str(folder))]
        note = (f" (its numbered folders {', '.join(numbered[:5])} hold no "
                "experiment files, so it is not a sample)" if numbered else "")
        lines.append(f"  {shown}: folder, searched{note}.")
        current = folder
    else:
        lines.append(f"  {target} holds no numbered experiment folders, so it "
                     "is not a sample.")
        return lines
    return lines


def explain_cache(root, target) -> str:
    """What the explorer's cached index says about ``target``'s sample."""
    index = load_index(root)
    if index is None:
        return "No cached index for this root yet: the next start searches it."
    target = str(Path(target).expanduser())
    for sample in index.samples:
        if target == sample.path or target.startswith(sample.path + os.sep):
            return f"In the cached index: yes ({sample.path})."
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(index.built_at)) \
        if index.built_at else "an earlier session"
    extra = ""
    if index.truncated:
        limit = f"{index.limit:,}-sample " if index.limit else ""
        extra = (f" That search stopped at the {limit}limit of the version "
                 f"then in use (now {SAMPLE_LIMIT:,}), and folders are searched "
                 "in name order, so the last ones -- often the newest -- were "
                 "not reached.")
    if not index.complete:
        extra += (" It is incomplete or from an older HelSpin, so the next "
                  "start searches this root again by itself.")
    if index.problems:
        extra += (f" {len(index.problems)} folder(s) could not be read in that "
                  "search, e.g.: " + "; ".join(
                      f"{p} ({e})" for p, e in index.problems[:3]))
    return ("In the cached index: NO. The explorer shows this root from its "
            f"last search ({when}); press Refresh (F5) to search again."
            + extra)


def scan_report(root, find=None, out=print, progress=None) -> int:
    """Run the explorer's own search of ``root`` now and report on it.

    The same function, limit and parallelism as the application -- not a
    re-implementation -- so what this finds is exactly what the explorer
    would find. ``find`` (any path below a sample) is looked for among the
    results. Returns the number of samples found.
    """
    root = Path(root).expanduser()
    problems: list = []
    # How long one request to the share takes -- what every listing costs.
    timings = []
    for _ in range(5):
        tick = time.perf_counter()
        try:
            os.stat(root)
        except OSError:
            break
        timings.append(time.perf_counter() - tick)
    if timings:
        typical = sorted(timings)[len(timings) // 2] * 1000
        out(f"One request to this share takes ~{typical:.0f} ms.")
    started = time.monotonic()
    count = [0]

    def on_batch(found):
        count[0] += len(found)
        if progress is not None:
            progress(count[0], found[-1])

    paths, truncated = discover_samples(
        root, limit=SAMPLE_LIMIT, on_batch=on_batch,
        workers=DISCOVER_WORKERS, problems=problems,
    )
    took = time.monotonic() - started
    rate = len(paths) / took if took > 0 else 0.0
    out(f"Searched {root} in {took:.0f} s ({rate:.0f} samples/s): {len(paths):,} samples"
        + (f" -- STOPPED at the {SAMPLE_LIMIT:,}-sample limit" if truncated else "")
        + ".")
    by_top: dict = {}
    for path in paths:
        try:
            top = Path(path).relative_to(root).parts[0]
        except (ValueError, IndexError):
            top = "."
        by_top[top] = by_top.get(top, 0) + 1
    if by_top:
        out("Samples per top-level folder:")
        for top in sorted(by_top):
            out(f"  {top}: {by_top[top]:,}")
    if problems:
        out(f"{len(problems)} folder(s) or entries could not be read "
            "(skipped):")
        for path, error in problems[:30]:
            out(f"  {path} -- {error}")
    else:
        out("Every folder could be read.")
    if find is not None:
        target = str(Path(find).expanduser())
        hit = next((p for p in paths
                    if target == p or target.startswith(p + os.sep)), None)
        out(f"{find}: " + (f"FOUND as sample {hit}" if hit else
                            "NOT found by this search."))
    return len(paths)


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
    elif sys.platform.startswith("linux"):
        # XDG_CACHE_HOME is the Linux equivalent of LOCALAPPDATA, and on a
        # managed or shared machine it is frequently redirected away from the
        # home directory -- to local disk when /home is a slow NFS mount, or
        # to a tmpfs that is cleared at logout. Ignoring it writes a cache of
        # a network mount onto the network, which is the one place it must not
        # go. Relative values are ignored, as the spec requires.
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg and os.path.isabs(xdg):
            return Path(xdg) / "helspin"
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
