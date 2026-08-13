"""Structural detection of Bruker datasets.

The textbook layout is <DIR>/data/<USER>/nmr/<NAME>/<EXPNO>/pdata/<PROCNO>, but
real data roots deviate -- e.g.

    D:\\NMR600data\\data\\IW\\nmr\\data\\260728_PXR-SRC-1_26-1_FT2\\11

has an extra 'data' segment. Any resolver that counts path segments or assumes
USER sits at a fixed index will break on real installations.

So: detect by STRUCTURE, never by position. An expno is a directory whose name
is an integer and which contains acqus. A sample directory is one whose children
include an expno.

Shared by the browser, drag-and-drop and paste, so this module is worth its
weight in tests: a bug here surfaces in three places at once.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlparse

MAX_WALK_UP = 4
# 6, not 4: a real root reaches the expno at
# <root>/data/<user>/nmr/data/<sample>/<expno> -- six levels. A depth of 4 finds
# nothing at all there, which the test suite caught.
DEFAULT_SCAN_DEPTH = 6
# Expno cap for the legacy expno-level scan. Sample listing no longer uses it.
DEFAULT_SCAN_LIMIT = 200
# Samples a data root may contribute to the browser. Generous: a busy root
# holds hundreds, and silently hiding them is far worse than a slower scan.
DEFAULT_SAMPLE_LIMIT = 5000


def is_expno(path: Path) -> bool:
    """A Bruker experiment directory: integer name, containing acqus."""
    try:
        return path.is_dir() and path.name.isdigit() and (path / "acqus").is_file()
    except OSError:
        # Unreachable mount, permission denied: not an expno, never a crash.
        return False


def is_procno(path: Path) -> bool:
    """A processed-data directory: integer name, inside a 'pdata' directory."""
    try:
        return (
            path.is_dir()
            and path.name.isdigit()
            and path.parent.name == "pdata"
            and is_expno(path.parent.parent)
        )
    except OSError:
        return False


def is_sample_dir(path: Path) -> bool:
    """A directory holding at least one expno."""
    try:
        if not path.is_dir():
            return False
        return any(is_expno(child) for child in path.iterdir())
    except OSError:
        return False


def expnos_in(path: Path) -> list[Path]:
    """Expnos directly inside a sample directory, sorted numerically."""
    try:
        found = [c for c in path.iterdir() if is_expno(c)]
    except OSError:
        return []
    return sorted(found, key=lambda p: int(p.name))


def procnos_in(expno: Path) -> list[Path]:
    """Procnos inside an expno's pdata directory, sorted numerically."""
    pdata = expno / "pdata"
    try:
        if not pdata.is_dir():
            return []
        found = [c for c in pdata.iterdir() if c.is_dir() and c.name.isdigit()]
    except OSError:
        return []
    return sorted(found, key=lambda p: int(p.name))


# Processed-data file names Bruker writes: 1r for 1D, 2rr for 2D
# (3rrr/4rrrr exist for higher dimensions but are not supported here).
PROCESSED_FILES = ("1r", "2rr", "3rrr", "4rrrr")


def has_processed_data(expno: Path) -> bool:
    """True if any procno under this expno contains a processed spectrum.

    An expno with only a raw FID and no pdata cannot be displayed -- the app
    plots PROCESSED data, the same as TopSpin. Listing such expnos in the
    browser only produces "cannot find" errors when they are dropped, so they
    are filtered out at the source instead.
    """
    for procno in procnos_in(expno):
        try:
            for name in PROCESSED_FILES:
                if (procno / name).is_file():
                    return True
        except OSError:
            continue
    return False


def expnos_with_data(path: Path) -> list[Path]:
    """expnos_in(), restricted to those that actually have processed data."""
    return [e for e in expnos_in(path) if has_processed_data(e)]


@dataclass(frozen=True)
class Resolved:
    """Outcome of resolving an arbitrary path.

    Exactly one of expno / sample is set. When ``sample`` is set the caller
    shows the expno picker; when ``expno`` is set it can load directly.
    """

    expno: Path | None = None
    sample: Path | None = None
    procno: int | None = None

    @property
    def is_expno(self) -> bool:
        return self.expno is not None

    @property
    def needs_picker(self) -> bool:
        return self.sample is not None


def resolve(path: Path, max_up: int = MAX_WALK_UP) -> Resolved | None:
    """Resolve any level of the tree to an expno or a sample directory.

    Handles .../11/pdata/1, .../11, and .../ABC-124 alike, on any data-root
    layout. Returns None when nothing Bruker-shaped is found within ``max_up``
    levels.
    """
    if is_procno(path):
        return Resolved(expno=path.parent.parent, procno=int(path.name))
    if path.name == "pdata" and is_expno(path.parent):
        return Resolved(expno=path.parent)
    if is_expno(path):
        return Resolved(expno=path)
    if is_sample_dir(path):
        return Resolved(sample=path)

    # Walk up: the user may have pasted a path below the dataset.
    current = path
    for _ in range(max_up):
        parent = current.parent
        if parent == current:  # filesystem root
            break
        if is_expno(parent):
            return Resolved(expno=parent)
        if is_sample_dir(parent):
            return Resolved(sample=parent)
        current = parent
    return None


def scan_for_datasets(
    root: Path,
    depth: int = DEFAULT_SCAN_DEPTH,
    limit: int = DEFAULT_SCAN_LIMIT,
) -> list[Path]:
    """Bounded search for expnos beneath ``root``.

    Depth- and count-limited so that dropping a whole data root cannot try to
    enumerate thousands of datasets. Callers run this in a worker with a
    cancellable progress dialog.
    """
    found: list[Path] = []

    def walk(directory: Path, remaining: int) -> None:
        if remaining < 0 or len(found) >= limit:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if len(found) >= limit:
                return
            if is_expno(child):
                found.append(child)
            elif child.is_dir() and child.name != "pdata":
                walk(child, remaining - 1)

    if is_expno(root):
        return [root]
    walk(root, depth)
    return found


# --- paste parsing ----------------------------------------------------------

_EXPNO_LIST = re.compile(r"^\d+(?:\s*[,-]\s*\d+)*$")


def parse_expno_spec(text: str) -> list[int]:
    """Parse '11', '11-14' or '11,12,15' into expno numbers.

    Ranges are inclusive. Returns [] for anything unparseable, so the caller
    falls through to the picker rather than erroring.
    """
    text = text.strip()
    if not text or not _EXPNO_LIST.match(text):
        return []
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            lo_s, _, hi_s = part.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                continue
            if lo <= hi:
                out.extend(range(lo, hi + 1))
        elif part.isdigit():
            out.append(int(part))
    # De-duplicate, preserving order.
    seen: set[int] = set()
    return [n for n in out if not (n in seen or seen.add(n))]


def normalise_pasted(text: str) -> str:
    """Strip the decorations a pasted path arrives with.

    Quotes, trailing separators, and the file:// scheme with percent-encoding.
    """
    text = text.strip()
    if not text:
        return ""
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            text = text[1:-1]
            break
    if text.lower().startswith("file:"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
        # file:///C:/data -> /C:/data on some platforms; drop the leading slash.
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    text = text.strip()
    while len(text) > 3 and text[-1] in "/\\":
        text = text[:-1]
    return text


def looks_like_windows_path(text: str) -> bool:
    """Drive-letter or UNC form."""
    return bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith("\\\\")


def looks_like_posix_path(text: str) -> bool:
    return text.startswith("/")


def is_foreign_path(text: str) -> bool:
    """True when the text is a path for the other platform.

    Such a paste must produce an explicit 'cannot be resolved on this platform'
    message, never a silent failure.
    """
    import os

    if os.name == "nt":
        return looks_like_posix_path(text)
    return looks_like_windows_path(text)


@dataclass(frozen=True)
class TopSpinIdentifier:
    """The NAME EXPNO PROCNO DIR USER row from TopSpin's own UI."""

    name: str
    expno: int
    procno: int
    directory: str
    user: str

    def to_path(self) -> Path:
        """Reconstruct the textbook layout.

        This is the ONLY positional reconstruction in the codebase, and its
        output must be verified with is_expno() before use.
        """
        base = PureWindowsPath if looks_like_windows_path(self.directory) else PurePosixPath
        return Path(
            str(base(self.directory) / "data" / self.user / "nmr" / self.name / str(self.expno))
        )


def parse_topspin_identifier(text: str) -> TopSpinIdentifier | None:
    """Parse a whitespace-separated NAME EXPNO PROCNO DIR USER row."""
    parts = text.split()
    if len(parts) < 5:
        return None
    name, expno_s, procno_s = parts[0], parts[1], parts[2]
    if not (expno_s.isdigit() and procno_s.isdigit()):
        return None
    user = parts[-1]
    directory = " ".join(parts[3:-1])
    if not directory:
        return None
    return TopSpinIdentifier(name, int(expno_s), int(procno_s), directory, user)


# --- fast sample discovery ---------------------------------------------------


def _has_integer_child_dir(entry_path: str) -> bool:
    """True if this directory holds at least one integer-named subdirectory.

    Stops at the FIRST match. Identifying a sample does not require knowing
    how many expnos it has, and enumerating them all was the main cost of the
    old scan.
    """
    try:
        with os.scandir(entry_path) as entries:
            for child in entries:
                if child.name.isdigit() and child.is_dir(follow_symlinks=False):
                    return True
    except OSError:
        return False
    return False


def scan_for_samples(
    root: Path,
    depth: int = DEFAULT_SCAN_DEPTH,
    limit: int = DEFAULT_SAMPLE_LIMIT,
) -> tuple[list[Path], bool]:
    """Sample directories beneath ``root``. Returns (samples, truncated).

    Why this exists, rather than reusing scan_for_datasets:

    The browser only needs the SAMPLE list to draw its first level. The old
    route found every expno and took their parents, which meant two stat calls
    per expno (is_dir + acqus). On a share with 3000 experiments that is 6000
    round trips to answer a question about 400 directories -- and, because the
    expno cap applied, most samples never appeared at all.

    This walks with os.scandir, whose DirEntry carries the directory-type flag
    from the single directory read, so no extra stat is needed. A directory
    containing any integer-named subdirectory is a sample; the check exits at
    the first one, and the walk does not descend into a sample. Whether each
    expno is usable is settled later, on expansion, where the cost is paid only
    for what the user actually opens.

    ``truncated`` is returned rather than silently capping, so the caller can
    say so instead of quietly showing a partial tree.
    """
    samples: list[Path] = []
    root = Path(root)

    if _has_integer_child_dir(str(root)):
        return [root], False

    def walk(directory: str, remaining: int) -> bool:
        """Returns False when the limit is reached."""
        if remaining < 0:
            return True
        try:
            with os.scandir(directory) as entries:
                children = sorted(
                    (e for e in entries if e.is_dir(follow_symlinks=False)),
                    key=lambda e: e.name,
                )
        except OSError:
            return True     # unreadable subtree must not abort the whole scan

        for child in children:
            if len(samples) >= limit:
                return False
            if child.name == "pdata":
                continue
            if _has_integer_child_dir(child.path):
                samples.append(Path(child.path))
                continue    # a sample is a leaf for this purpose
            if not walk(child.path, remaining - 1):
                return False
        return True

    completed = walk(str(root), depth)
    return samples, not completed
