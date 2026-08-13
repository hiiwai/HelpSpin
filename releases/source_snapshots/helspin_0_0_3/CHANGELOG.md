# Changelog

All notable changes to HelSpin are recorded here. Versions follow
[Semantic Versioning](https://semver.org/). The project is pre-1.0 and
pre-rendering: the spectrum rendering engine is not implemented yet, so
these are early releases, not a finished tool.

**Versioning convention:** the version in `pyproject.toml` is the single
source of truth; `helspin --version`, the delivered zip's filename, and this
file must always agree with it. **Every delivery bumps the version** so any
build can be traced back to a specific entry here.

- **patch** (`0.0.X`) — bug fixes and incremental improvements at the current
  pre-rendering stage.
- **minor** (`0.X.0`) — reserved for `0.1.0` = spectrum rendering working
  (the first genuinely usable release), then each substantial feature after.
- **major** (`X.0.0`) — a build ready to hand to others as finished.

> Earlier snapshots used a `0.1.0.devN` suffix. That was dropped in favour of
> plain `0.0.X` patch numbers, which are more honest about the project being
> pre-rendering — `0.1.0` (no suffix) is now reserved for when rendering
> works. `0.0.1` corresponds to what was previously built as `0.1.0.dev1`.

## [0.0.2] — 2026-08-02

Bug-fix release. The instant-expansion feature added in 0.0.1 had a real
defect in the actual GUI (as opposed to the tests): metadata columns never
filled in, and datasets could not be dragged. Both are fixed here, along with
refresh no longer collapsing the tree. Still no spectrum **rendering** — that
remains the trigger for `0.1.0`.

### Fixed
- **Metadata columns stuck on "…" and drag not working.** Two compounding
  causes, both real:
  - Qt calls `fetchMore` synchronously the moment a node is expanded. That
    synchronous fetch populated the expno rows and marked the node fetched
    *before* the browser could schedule the background metadata probes — so
    the probes were never scheduled. Rows appeared but stayed unprobed, their
    columns stuck on the "…" placeholder, and because an unprobed row has
    unknown dimensionality it was not draggable. Fixed by driving all
    population explicitly through the async populator (`canFetchMore` is now
    always False) so expansion always schedules probes.
  - The background probe worker mutated the model and emitted `dataChanged`
    from the worker thread, which Qt item models forbid. Split into a
    pure-I/O half (safe on the worker) and an apply half (GUI thread only).
    Also fixed the probe task's result signal never being connected, and the
    task being garbage-collected mid-flight.
- **Refresh collapsed the whole tree.** Refresh now merges in place — adding
  new files, removing vanished ones, and keeping existing node objects — so
  expanded folders stay open and already-loaded metadata is not thrown away.
  Previously it cleared and rebuilt, collapsing everything, and (via a
  separate `_index_for` bug) refreshing a data root silently did nothing.

### Notes
- The empty-looking behaviour was GUI-only; the existing tests passed because
  they populated via a code path that bypassed Qt's synchronous fetch. A new
  regression test expands *through the tree*, the way a user does, and would
  fail without this fix (verified by reverting the fix — the PULPROG column
  stays "…").

## [0.0.1] — 2026-08-02

First release under the plain (non-`devN`) scheme. Browser-and-layout tool;
no spectrum **rendering** yet (a filled slot shows its colour and label, not
a plotted trace) — that remains the next milestone and the trigger for
`0.1.0`.

### Added
- **Resizable Name column.** The Name column is now user-widenable, fixing
  long Bruker sample names being clipped with no way to see them. Metadata
  columns size sensibly and `stretchLastSection` is off so Date does not
  re-squeeze Name.
- **Quit on the toolbar.** Quit is now an explicit toolbar button (far
  right) as well as in the File menu — on macOS a menu action literally
  named "Quit" gets auto-relocated into the system application menu, which
  made it look missing.
- **`CHANGELOG.md`** (this file) and **version-stamped zip filenames**
  (`helspin-<version>.zip`) so deliveries are traceable.

### Changed
- **Instant sample expansion.** Expanding a sample no longer reads every
  expno's `acqus` file up front. Rows appear from directory structure alone
  (zero file reads); PULPROG/nucleus/dimensionality/date columns fill in via
  background per-row probes. Measured ~2400x faster to first paint than the
  old probe-then-show path on a simulated 50 ms/read network share (30
  expnos: ~1.5 s to first row -> ~0.6 ms).
- **Faster drag start.** Dragging a dataset from the browser now uses a
  small custom drag pixmap instead of Qt's default full-row render, which
  re-rendered every column in the native style before the drag could begin.

### Fixed
- The browser's left/centre splitter inverted itself after a figure was
  created (handed most of the window to the browser); it now preserves the
  intended proportions.
- An emptiness check that used `mime.formats()` (always truthy, because the
  model always sets the format) is now a decoded-payload check, so a
  non-draggable selection no longer starts an empty drag.

## Baseline (pre-0.0.1)

Initial development, before the per-delivery version convention. Established
the core of the application.

### Working
- **Dataset browser** — data root -> sample -> expno tree over Bruker data,
  async population, type-to-filter (sample name / PULPROG).
- **Explicit refresh** — right-click a node, "Refresh All", or F5. No
  automatic file-watching (unreliable over SMB/NFS shares).
- **Application shell** — toolbar, browser (left), canvas (centre), ppm
  adjustment bar (bottom).
- **New Figure dialog** — 1D/2D counts and arrangement (Overlay / Stacked /
  Tiled / Subtracted for 1D; Overlay / Tiled for 2D) with live validation;
  generates an empty, colour-assigned slot layout.
- **Drag-and-drop** — drag one or more expnos onto slots; multi-select fills
  sequential slots.
- `helspin --version` prints the version without needing a display.

### Notable fixes during early development
See the "Bugs the tests (or a genuinely fresh install) caught" section of
`README.md` for the full list — including two real test-suite hangs from
blocking Qt modals, a `numpy<2.5` pin (nmrglue crash on fresh install), a
`pyproject.toml` entry point that referenced a non-existent module, and
`load_data_roots` crashing on malformed settings.

### Not yet implemented (as of 0.0.1)
- Spectrum **rendering** (`read_1d`/`read_2d`, matplotlib canvas) — the next
  milestone, and the trigger for version 0.1.0.
- Preferences dialog (honest stub), paste, export (SVG/EPS/PS/TIFF/JPEG),
  undo/redo, the TopSpin bridge, 2D contour rendering.
