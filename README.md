# HelSpin

Compare Bruker NMR spectra and build publication figures. Previously
developed under the working name VertaaNMR.

**Status: working tool.** Browse a data root, open 1D and 2D spectra,
overlay or stack them, adjust scale and position per spectrum, zoom either
axis, and export a publication figure. Sessions save and restore; every
adjustment is undoable.

```
python -m pytest                          # 931 tests
python -m pytest --cov=helspin.domain --cov-branch
```

See `MANUAL.md` (and `MANUAL.pdf`) for how to use it.

## Which folder to add as a data root

There is **no fixed depth**. A folder counts as a *sample* when it contains at
least one numbered experiment folder (`1`, `2`, `10`...), and HelSpin searches
downwards until it finds them — so pointing at the share, at one user's `nmr`
folder, or at a single sample all work.

```
Z:\                          <- works
 └─ data\
     └─ iwai\
         └─ nmr\             <- works (and is the usual choice)
             └─ 260728_SampleA_100uM     <- works: this IS a sample
                 ├─ 1\       <- an experiment
                 └─ 10\
```

Two limits worth knowing:

- **Eight folder levels below the root.** Deeper samples are not found. The cap
  exists so that mistakenly adding `C:\` cannot walk the whole disk. If your
  layout is deeper than that, add a folder further in.
- **5000 samples.** Beyond that the scan stops and the status bar says so;
  point at a more specific folder.

Cost scales with the number of *folders visited*, not experiments, so a root
one or two levels above your samples is much faster than one near the drive
root. If nothing is found, the status bar says so and states how deep it
looked.

## Installing on Linux

See [INSTALL.md](INSTALL.md#linux-one-extra-step). One system library
(`libxcb-cursor0` or your distribution's equivalent) must be installed before
the first run — pip cannot supply it, and without it Qt fails to start with a
misleading "platform plugin could not be initialized" message.

Symlinked data roots are supported: if your root is a folder of links to the
real instrument mounts, HelSpin walks through them, and links pointing back at
a parent are detected rather than followed forever.

## Installing on Windows 11

Development is macOS-first; Windows runs from a Python environment (there is
no bundled installer yet). Everything below is PowerShell.

```powershell
# 1. Python 3.12 from python.org or the Store. Tick "Add python.exe to PATH".
python --version                     # expect 3.11 or newer

# 2. A virtual environment, so HelSpin cannot disturb anything else
cd $HOME
python -m venv helspin-env
.\helspin-env\Scripts\Activate.ps1

# 3. Install from the delivered zip
pip install --upgrade pip
pip install path\to\helspin-0.4.8.zip

# 4. Run it
helspin-gui                          # no console window
helspin --version                    # console version, for --version/--check
```

If `Activate.ps1` is blocked, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and reopen the
terminal.

### If `helspin-gui` will not run ("Access is denied")

On a managed machine, policy often blocks **unsigned executables from a user
profile path**. pip generates `helspin-gui.exe` at install time, unsigned,
into the environment's `Scripts` folder, so it is exactly what such a rule
catches. `pythonw.exe` came from the conda installer and is signed, so it is
permitted — launch through it instead:

```powershell
pythonw -m helspin          # the GUI, no console window
python -m helspin --version # the console form, for --version and --check
```

These are the same application; only the launcher differs. For a permanent
shortcut, set the target to:

```
%CONDA_PREFIX%\pythonw.exe -m helspin
```

This affects every pip-installed command-line tool on such a machine, not just
HelSpin, so the same substitution works elsewhere.

### Spectrometer shares

Add the data root by its UNC path (`\\spectrometer\data\nmr`) or by a mapped
drive (`Z:\nmr`); both are recognised. A mapped drive reconnects on login,
which is usually less trouble.

**Enable long paths.** Bruker trees are deep and sample names are long, and
Windows truncates at 260 characters by default. Once, as Administrator:

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

### Where the index cache lives

`%LOCALAPPDATA%\HelSpin\cache` on Windows, `~/.cache/helspin` on macOS and
Linux (`XDG_CACHE_HOME` is honoured on Linux, which matters when `/home` is a
network mount). Delete it to force a full re-index; the app rebuilds it in the
background.

### Checking a dataset from the shell

```powershell
helspin --check "Z:\nmr\260728_SampleA_100uM"
```

Reports, per experiment, whether a plottable spectrum is present.

## What exists

| Module | Purpose |
|---|---|
| `domain/spectrum.py` | ppm axis calibration, `Spectrum1D`/`Spectrum2D`, union ranges |
| `domain/project.py` | slots, blocks, boxes, link groups, labels, palette |
| `domain/layout.py` | New Figure request → laid-out project; difference attachment |
| `domain/overlay.py` | y-scaling modes, difference computation, k fitting |
| `domain/paths.py` | structural Bruker path resolution, paste parsing |
| `domain/metadata.py` | barcode resolution, JCAMP stripping, title reading, label fields |
| `domain/ports.py` | `SpectrumReader`/`FigureExporter` protocols, `DataRoot`, `SampleNamePattern` |
| `domain/errors.py` | domain error hierarchy (`HelSpinError` and subclasses) |
| `infrastructure/nmrglue_reader.py` | `probe()` — real acqus parsing via nmrglue |
| `core/settings.py` | `QSettings`-backed `DataRoot` persistence |
| `ui/dataset_model.py` | lazy `QAbstractItemModel`: data root → sample → expno, async population, deferred per-row `acqus` probes, drag source, `refresh()` |
| `ui/dataset_filter.py` | type-to-filter on sample name / PULPROG, index-backed so it reaches unexpanded samples |
| `ui/browser.py` | the browser widget: filter box + tree, right-click Refresh / Refresh All, wires model to a `QThreadPool` |
| `ui/adjustment_bar.py` | bottom ppm-range bar: Full / typed range / recent ranges |
| `ui/canvas_placeholder.py` | honest stand-in for the centre panel until a figure is created |
| `ui/box_canvas.py` | renders a `Project`'s boxes/slots and is the real drag-and-drop target |
| `ui/new_figure_dialog.py` | dimensionality/count/arrangement dialog over `domain.layout` |
| `__main__.py` | entry point: toolbar + browser/canvas/adjustment-bar shell, `--version` |

94% branch coverage on `domain/`. `domain/` imports numpy and stdlib only —
enforced by `tests/test_architecture.py` via AST inspection, not grep (grep
matched its own docstrings on the first attempt).

## Running it

```
pip install -e ".[dev]"
helspin                # opens the shell
helspin --version      # prints the version and exits, no display needed
```

See `INSTALL.md` for full setup instructions, including conda and
troubleshooting a window that appears to hang or not start.
`MANUAL.md` / `MANUAL.pdf` is the user manual: arrangements, scaling,
X and Y offsets, zooming, sessions and export.

## What is next

- **Preferences dialog** — partially built; more display defaults to move into
  it.
- **The TopSpin bridge** — pasting a TopSpin identifier is parsed
  (`domain/paths.py`) but not yet wired to a live TopSpin session.
- **Automatic file-watching** was deliberately not built (see
  `DatasetTreeModel.refresh`'s docstring): `inotify`/`FSEvents` are
  unreliable over the network shares Bruker data commonly lives on.
  Refreshing is explicit — right-click a node, or Refresh All / F5.
- **Difference and sum operations ignore an X offset.** Aligning two spectra
  horizontally and then subtracting uses their true ppm axes, not the aligned
  ones. Deliberate, pending a decision — see the 0.5.10 changelog entry.
- **Windows and macOS verification.** Development and testing currently run on
  Linux; the Windows installer path in particular has not been exercised end
  to end.

## Design rules the code enforces

- **Colour binds to the slot instance, not its index.**
  `test_deleting_a_slot_does_not_recolour_survivors`.
- **Absolute intensity divides by NS and RG.**
  `test_absolute_mode_equalises_a_16_vs_256_scan_pair`.
- **Differences interpolate onto a common ppm axis**, never index-subtract.
  `test_index_subtraction_would_have_been_wrong`.
- **Shift before subtract.** `test_shift_is_applied_before_subtraction`.
- **Path detection is structural, never positional**, including the real
  extra-`data`-segment layout as a fixture.
- **`Full` means union, not intersection.**
- **HOLDER, and every acqus value, is coerced to a string** before use — even
  though nmrglue parses `##$HOLDER= 5` as a Python `int`.
  `test_holder_survives_as_a_string_not_an_int`.
- **The browser never lets one bad dataset hide its siblings.**
  `test_scan_skips_an_unreadable_subtree`, `test_expno_that_fails_to_probe_still_appears`.
- **The directory scan is bounded, always.** An earlier draft had an
  unbounded `rglob()` fallback that would have silently defeated
  `scan_for_datasets`'s own depth/count limits — caught and removed.
- **`loc="best"` and `bbox_inches="tight"` are banned.** Enforced in
  `test_architecture.py`.
- **The version is read once, from installed package metadata**
  (`importlib.metadata`), never duplicated as a separate string that could
  drift out of sync with `pip show helspin`.
- **The window is forced to the front on show()** (`raise_()` +
  `activateWindow()`), since a Python-launched Qt window on macOS can open
  genuinely behind everything else with no visible signal — the most common
  cause of "the app hangs with no window."
- **Refreshing the browser is explicit, never automatic.** A node's
  populated children stay cached indefinitely; `QFileSystemWatcher`-style
  auto-detection was deliberately not built because `inotify`/`FSEvents`
  frequently do not fire at all over the SMB/NFS shares Bruker data commonly
  lives on. Right-click Refresh, Refresh All, or F5.
- **The index has three tiers, and only the cheapest runs before rows are
  drawn.** Discovery answers "which directories are samples?" at one listing
  per directory visited and streams its results, so the tree fills while the
  walk runs. A sample's experiments are listed when it is opened; the
  per-experiment metadata is read once, in the background, and cached. On a
  400-sample root that is ~800 round trips before the first row instead of
  ~9600 — the difference between a few seconds and several minutes on a
  share, and the second session costs nothing at all.
- **A row is draggable as soon as it appears.** The dimensionality comes from
  the files the listing already saw (`fid` vs `ser`+`acqu2s`), so nothing has
  to be read first. Metadata columns show a `…` placeholder until their read
  lands, and rows on screen are read before rows that are not. A
  processed-only dataset reports no dimensionality and the canvas settles it
  at load time.
- **Filtering keeps a matched sample openable.** An experiment under a sample
  that matched by name is always shown. (Qt's recursive filtering only
  propagates matches upwards, which meant filtering by sample name hid every
  experiment underneath it and the sample could not be opened at all.)

## Bugs the tests (or a genuinely fresh install) caught during construction

1. **Scan depth of 4 found nothing** in a realistic root (six levels to the
   expno). Default raised to 6.
2. **Windows path reconstruction on POSIX** returned an unusable `Path`; now
   routed through the foreign-path guard.
3. **`HOLDER` parses as `int`, not a bracketed string**, in a real nmrglue
   dict — `strip_jcamp` now coerces every value to `str`.
4. **An unbounded `rglob()` fallback** in the tree model's data-root scan
   would have made the depth/count limits meaningless on a real network
   share. Removed.
5. **numpy 2.5 hard-crashes nmrglue's (unused) `tecmag.py` module** on
   import, via a dtype alias numpy fully removed. Only surfaced on a
   genuinely fresh `pip install` — the dev sandbox's already-installed numpy
   2.4 only warned. Fixed with a `numpy<2.5` ceiling in `pyproject.toml`.
6. **The entry point referenced in `pyproject.toml` didn't exist** the first
   time this was packaged — `pip install` would have succeeded and then the
   `helspin` command would have failed on first run. Built the actual
   missing `__main__.py` rather than just fixing the manifest.
7. **`load_data_roots` crashed on a malformed settings value** that was a
   list of strings rather than dicts, instead of degrading to an empty list
   as intended. Caught by a test that deliberately fed it garbage.
8. **The browser's own splitter inverted itself.** After swapping in a real
   `BoxCanvas`, `QSplitter` recomputed each panel's share from size hints and
   flipped the layout from `[260, 736]` to `[702, 294]` -- handing most of
   the window to the browser exactly when the user asked to see a figure.
   Measured directly (not assumed from a screenshot), fixed by explicitly
   reasserting `setSizes()` after the swap, locked in with a regression test.
9. **Two genuine test-suite hangs**, both from the same root cause: a real
   Qt modal (`QDialog.exec()`, then later `QMenu.exec()`) called with
   nothing to dismiss it. The `QDialog` case was fixable by monkeypatching
   `exec` on the *custom subclass*; the `QMenu` case was not -- patching
   `.exec` on a built-in Shiboken-wrapped class did not reliably override
   real dispatch, confirmed by a standalone script that still hung under a
   hard 10-second OS-level `timeout` with the class patched. Fix: split
   menu/dialog *construction* from *showing* it, and test the construction
   directly, never calling the real blocking `exec()` in the test suite at
   all.
10. **A screenshot verification script silently misreported success and
    failure both**, on different occasions: once, `pixmap.save()` returned
    `False` and the code printed "saved" anyway without checking the return
    value; separately, a debug print used `[:6]` to shorten output and
    accidentally hid the exact x-range where real content lived, producing a
    false "nothing rendered" alarm. Both are more general reminders: check
    return values, and don't truncate diagnostic output you're about to
    reason from.
11. **Dragging a dataset from the browser was reported as extremely slow.**
    Qt's default `QTreeView.startDrag()` renders the dragged row -- across
    every column, in the real widget style -- into a pixmap once, at the
    moment the drag starts, before the drag can visually begin at all. Fixed
    with a small hand-drawn pixmap instead (`DatasetTreeView._build_drag`),
    which is strictly cheaper regardless of whether it's the whole story.
    Building this fix surfaced a second, real bug: the emptiness check used
    `mime.formats()`, but the model's `mimeData()` calls
    `setData(MIME_DATASET, ...)` *unconditionally* -- even when the encoded
    payload is `[]` -- so the format is present even for a non-draggable
    selection. Fixed to check the decoded payload's actual length. The
    lesson from #9 about never calling a real blocking `exec()` in a test
    was applied here from the start (`_build_drag` is tested directly;
    `drag.exec()` itself is never invoked), rather than being relearned.
12. **"Quit" was reported missing from the menu.** On macOS, Qt
    auto-relocates a plain `QAction` literally named "Quit" out of a regular
    menu and into the system application menu, based on its text alone --
    reasonable in general, but easy to miss if you're looking at the File
    menu specifically and it silently isn't there. Quit is now also an
    explicit toolbar button (far right, matching the reference screenshot
    this shell is modelled on) with `setMenuRole(NoRole)`, so it stays
    exactly where it's put regardless of platform.
13. **Expanding a sample took "forever" over a network share.** The scan
    read every expno's `acqus` file, sequentially, before returning a single
    row -- one blocking round-trip per experiment. Measured on a simulated
    50ms/read share with 30 expnos: the first row appeared after ~1.5
    seconds. Fixed by separating structure discovery (one directory listing,
    zero file reads) from metadata (`acqus`): rows now appear from structure
    alone and the metadata columns fill in via background per-row probes.
    Same simulated setup, after the fix: all 30 rows appear in ~0.6ms,
    ~2400x faster to first paint, with columns filling in as reads complete.
14. **The Name column could not be widened and long sample names were
    clipped.** The column was set to `Stretch`, which fills leftover space
    but has the side effect of making the section non-resizable -- the drag
    handle did nothing, and the metadata columns squeezed Name below the
    width real Bruker sample names need. Fixed: Name is now `Interactive`
    (freely draggable) with a generous default width; the metadata columns
    have fixed sensible defaults; `stretchLastSection` is off so Date does
    not expand to re-squeeze Name from the right. A horizontal scrollbar
    appears if the user widens Name past the panel, rather than clipping.

## Licensing

Free for academic research, teaching and personal use. Commercial use requires
a licence — enquiries to **iwai@ligsciss.com**. The full terms are in
[LICENSE](LICENSE), and are also readable inside the app at Help → Licence.

Qt (via PySide6) is the only copyleft dependency: LGPLv3, which permits
commercial sale. Package **one-dir, not one-file**, so Qt's shared libraries
stay replaceable, and never patch Qt. nmrglue, NumPy and SciPy are BSD;
matplotlib is BSD-style; Pillow is HPND.
