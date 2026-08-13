# HelSpin

Compare Bruker NMR spectra and build publication figures. Previously
developed under the working name VertaaNMR.

**Status: dataset browser and application shell working. No comparison
canvas yet.**

```
python -m pytest                          # 383 tests
python -m pytest --cov=helspin.domain --cov-branch
```

`shell_screenshot.png` is a real offscreen render of the running shell:
toolbar, dataset browser (left), canvas placeholder (centre), ppm
adjustment bar (bottom).

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
| `ui/dataset_model.py` | lazy `QAbstractItemModel`: data root → sample → expno, async population, drag source |
| `ui/dataset_filter.py` | recursive type-to-filter on sample name / PULPROG |
| `ui/browser.py` | the browser widget: filter box + tree, wires model to a `QThreadPool` |
| `ui/adjustment_bar.py` | bottom ppm-range bar: Full / typed range / recent ranges |
| `ui/canvas_placeholder.py` | honest stand-in for the centre panel until rendering exists |
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

## What is next

- **Rendering** — `services/render_block.py`: block → matplotlib axes,
  replacing `CanvasPlaceholder`'s content. Headless-testable.
- **`read_1d`/`read_2d`** in `nmrglue_reader.py` — deliberately stubbed with
  `NotImplementedError` in this slice; only `probe()` was needed for the
  browser and drag payload.
- **New Figure dialog and Preferences dialog** — currently honest stubs in
  `__main__.py` (`_new_figure`, `_preferences`) that say plainly what isn't
  built yet rather than doing nothing silently.
- **Drop target on the canvas, paste, export, undo, the TopSpin bridge.**

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

## Licensing

Qt (via PySide6) is the only copyleft dependency: LGPLv3, which permits
commercial sale. Package **one-dir, not one-file**, so Qt's shared libraries
stay replaceable, and never patch Qt. nmrglue, NumPy and SciPy are BSD;
matplotlib is BSD-style; Pillow is HPND.
