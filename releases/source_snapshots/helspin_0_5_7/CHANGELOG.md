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

## [0.5.7] — 2026-08-04

### Y offset in stacked mode made spectra vanish

Two faults compounded. The offset spin step was **5% of the raw intensity
span** — about 1e9 for a 2e10 spectrum — so a single arrow click threw the
trace a billion units, and the range was effectively unbounded, which is how a
value like -34,517,863,967 (straight from the screenshots) got entered at all.
And in stacked mode the frame is built from the drawn lane positions, which
include each trace's offset, but the frame was only recomputed when the trace
*set* changed — so a nudged trace moved while its frame did not, and slid off
the canvas. The reported "peak disappeared".

Now: one click nudges by **2% of the spectrum's own height**, the offset is
capped at a few spectrum-heights (beyond which it only ejects the trace), and
an offset change in stacked mode re-fits the frame so the moved trace stays on
screen. Overlay is untouched — its frame ignores offset by design.

### Hot-cold no longer runs through white

The diverging ramp passed through near-white at its centre (`#F7F7F7` and
neighbours), which is invisible on the white plot background — so the middle
spectra of a series disappeared. The centre is now a visible teal→olive
transition; the ends stay blue and red.

### Greyscale palette leans on dashes

For black-and-white figures, the greyscale palette now cycles solid, dashed,
dotted and dash-dot so that traces are told apart by dash pattern — the only
thing that survives greyscale printing — rather than by near-identical greys.
The greys themselves were also spread wider in tone.

### New icon

The full HelSpin logo — sphere, spin ring, vector arrow and the HelSpin
wordmark — from the supplied artwork, as the app and taskbar icon, with a
matching multi-resolution ICO.

### Tests

799 (was 796).

## [0.5.6] — 2026-08-04

### Ten spectrum slots, not eight

Preferences now has ten colour/style/width rows and the palettes fill ten, so
up to ten overlaid spectra each get their own colour. The two extra default
colours (a grey and a deep teal) continue the colour-blind-safe set.

### Two ordered palettes: Rainbow and Hot-cold

The existing palettes are sets chosen so their members stay *distinguishable*
— the right thing for unrelated samples. These two are ordered ramps, for a
series where the colour should track the order:

- **Rainbow** — spectral red→violet, for a plain progression (a titration, a
  time course). Wrong for unrelated samples, since neighbours in the sequence
  land on adjacent hues.
- **Hot-cold** — a blue→white→red diverging ramp, for a series with a
  meaningful centre: difference spectra either side of zero, a variable swept
  above and below a midpoint. Blue and red read as the two directions, white
  as the middle. Not for unrelated samples — the pale central colours vanish
  on white.

### Default line style confirmed solid

A fresh configuration was already all solid, and now is across all ten slots.
Only the greyscale palette, chosen deliberately, introduces dashes — and Reset
puts every slot back to solid.

### New icon

The mark plus a **12Spin** wordmark set at 45°, white with a red outline, on
the navy field. Regenerated as both the PNG and the multi-resolution ICO.

### Tests

796 (was 792).

## [0.5.5] — 2026-08-04

### Windows installer scaffolding

Everything needed to build a one-click `setup.exe`, under `packaging/`:

- `helspin.spec` — PyInstaller, one-directory (see below), bundling the
  licence, notices and artwork at the path the licence dialog reads them from.
- `helspin.iss` — Inno Setup: a per-user install needing no administrator
  rights, a Start Menu entry, the licence shown and accepted before install,
  and an uninstaller.
- `build_installer.py` — runs both stages, taking the version from
  `pyproject.toml` so nothing drifts, and refusing to run if a tool is
  missing.
- `BUILD.md` — the guide, including the two non-negotiables.
- A multi-resolution `icon.ico` (16–256 px), so the taskbar and Explorer have
  a crisp size rather than a badly down-sampled 512.

The spec was dry-run to completion here: it resolves every import, bundles the
resources to `helspin/resources/`, and produces the collected app directory.
That validates the spec. **The actual Windows build must run on Windows** —
PyInstaller bundles the platform it runs on — so the produced `.exe` is
untested by me and you are its first runner.

Two things the guide is firm about, both already established in this project:

- **One-directory, never one-file.** Qt is LGPL and a recipient must be able
  to relink it; a one-file exe unpacks to a temp dir and cannot be. One-dir
  keeps every Qt DLL replaceable.
- **The installer is unsigned.** It works, but SmartScreen warns and managed
  machines block it — the same publisher rule that blocked `helspin-gui.exe`.
  Signing needs a certificate the project does not have yet, and the
  pip-install route stays the reliable path onto a managed machine until then.
  Don't buy a certificate until demand justifies it.

### Tests

792, unchanged — packaging adds no runtime code.

## [0.5.4] — 2026-08-04

### Proprietary identifiers removed from the source

A scan before preparing the repository for GitHub found real employer data in
docstrings, documentation and test fixtures: compound and target codes, a
spectrometer path, and the employer's name in a code comment. All replaced
with neutral equivalents of the same shape, so the examples still illustrate
what they were written to illustrate.

This mattered before publishing anywhere, and it is the kind of thing that is
invisible until someone looks for it — the codes had been copied into tests as
realistic-looking sample names months ago.

Two tests searched for text that no longer existed and were repaired rather
than deleted.

### Ready to push

`.gitignore` covers Python artefacts, environments, and — deliberately —
HelSpin's own runtime state (`index-*.json`, `licence.json`, `trial.json`,
`*.helspin`) and anything that looks like real spectra (`data/`, `nmr/`,
`*.fid`, `*.ser`, `1r`, `2rr`). Committing a colleague's dataset by accident
should require effort.

The delivered archive contains an initialised repository with one commit and
the tag `v0.5.4`, so publishing is three commands and no reconstruction.

### Tests

792, unchanged.

## [0.5.3] — 2026-08-04

### Preferences were only half-saved

Only the slot styles were persisted. Grid spacing, x decimals, label size,
opacity, cursor decimals and grid Y were applied to the canvas and **never
written anywhere**, so the dialog appeared to work and silently reverted on
the next launch. All of them are stored now and restored at start-up.

Values read back from settings are treated as untrusted input — a settings
file is user-editable and survives upgrades, so a bad entry falls back to the
default rather than reaching the canvas, and one bad field does not discard
the good ones.

### Choose a palette instead of eight colours

**Preferences → Palette**, with six published schemes:

| | |
|---|---|
| Okabe–Ito | the current default; the standard colour-blind-safe set |
| Tol bright | more saturated, holds up on a projector |
| Tol muted | lower chroma, easier as thin lines on white paper |
| High contrast | for projection and poor lighting |
| Matplotlib tab10 | familiar from most Python figures; **not** colour-blind safe |
| Print (greyscale) | for journals that print in black and white |

Published schemes rather than hand-picked colours, because each was designed
so its members stay distinguishable — the property that matters when four
spectra are overlaid and two nearly coincide. Five of the six are safe for the
common forms of colour blindness; tab10 is included because familiarity is
sometimes worth more, but that trade should be made knowingly.

Two details:

- **Applying is an explicit button press**, so opening the dropdown to read
  the names cannot overwrite colours already chosen by hand.
- **The greyscale palette also sets line styles.** In a black-and-white figure
  hue conveys nothing and the dash pattern is the only thing telling two
  spectra apart, so that palette is unusable without them. Every other palette
  leaves the styles alone.

The chosen palette is saved with the other preferences.

### Tests

792 (was 785), including that every palette offers eight distinct colours —
a repeat would make two spectra indistinguishable.

## [0.5.2] — 2026-08-04

### Attribution

Copyright and authorship are now stated where they need to be: the LICENSE
header, the NOTICE, the package metadata, and the About box — *Copyright ©
2026 H. Iw-ai. All rights reserved. Written and developed by H. Iw-ai.* The
licence's commercial-enquiry clause names the copyright holder, since a
licence nobody can address an enquiry to is not much use.

The About box also lost a claim that had been wrong for many versions: that
the build contained the browser only and the comparison canvas was not
implemented.

### Defaults confirmed

Grid off, labels on — both were already correct, and both now have tests, so
neither drifts. A grid is a reading aid the user asks for; spectrum names are
needed the moment there is more than one trace.

### Tests

785 (was 781), including that opacity applies to the spectra and not to the
labels.

## [0.5.1] — 2026-08-04

### The grid was half-drawn in 1D and absent in 2D

**1D** drew vertical rules only — `grid(False, axis="y")` was explicit, on the
reasoning that a horizontal grid cuts across a stacked plot and reads as part
of the data. That is true when stacked, and wrong in overlay, where the y axis
is a single shared intensity scale and a reader wants to measure against it.
Now: **both axes in overlay, x only when stacked**. Stacked is left alone
deliberately — every spectrum there has its own offset and scale, so a y value
means something different for each one, and ruling lines across them would
imply a shared scale that does not exist.

**2D** had no grid code at all, in either the overlay or the panel path, so
the toolbar toggle silently did nothing there. Both axes are chemical shifts
in 2D and reading a peak position off a contour map is exactly what a grid is
for, so both are drawn.

All three paths now share one `_apply_grid`, which is why the toggle could be
on with nothing on screen: there was no single place that answered "draw the
grid".

### Grid spacing for the vertical axis

**Preferences → Grid Y**, alongside the existing x spacing, 0 for automatic.
Separate values because in 2D the axes cover quite different ranges — 10 ppm
of proton against 150 of carbon — so one number cannot suit both. Saved with
the session.

### Licence file and trial expiry — recorded, not enforced

`helspin/core/licence.py` reads a licence file if present and otherwise starts
a **six-month trial**, dated from FIRST RUN rather than from the build, so a
copy installed a year late still gets a fair six months. The status shows at
the top of Help → Licence…

**Nothing enforces it.** No caller blocks a feature, refuses to start or nags,
and there is a test asserting that an expired trial does not prevent the
canvas being built — so enforcement cannot arrive by accident. Switching it on
later is then a decision rather than a scramble.

Two deliberate choices worth knowing:

- **A commercial licence file with no readable expiry falls back to the
  trial** rather than being treated as unlimited. That is the failure a typo
  would otherwise create silently.
- **`verify()` returns False and signatures are not trusted.** The file is
  plain JSON, so a user can edit the date — which is fine while nothing is
  enforced, and *not* fine the moment it is. If enforcement is switched on,
  the file must carry an Ed25519 signature and `verify` must check it,
  otherwise the whole mechanism is decoration. The `signature` field and
  `verified` flag exist now so adding that changes neither the file format nor
  any call site.

### Tests

781 (was 766), including an expired trial, a corrupt file, a missing expiry,
an unwritable config directory, and the grid on every path.

## [0.5.0] — 2026-08-04

Minor rather than patch: the licence changed, which is not a bug fix.

### Licence: free for academic use, commercial use by arrangement

The project previously declared **MIT**, which permits anyone — including a
company — to use, modify and sell it. That was never the intent, and the
window to change it is now: nothing has been published, so no copy exists in
the wild under the old terms. MIT cannot be retracted from copies already
distributed.

`LICENSE` is a plain-language, source-available licence: free for academic
research, teaching, personal use and 90-day evaluation; commercial use
requires a separate licence. It says explicitly that this is **not** an
open-source licence and asks that HelSpin not be described as one, since
restricting commercial use disqualifies it under the Open Source Definition.
A university group doing company-funded research is covered by the academic
grant — the test is who does the work, not who pays.

### Licence readable from inside the application

**Help → Licence…**, with a second tab for third-party notices. Two reasons:
a user cannot honour a condition they have never seen, and Qt's LGPL requires
its licence and the offer of source to travel with the binary — a packaged
build may have no visible LICENSE file at all, so a menu entry is how that
obligation is met.

The dialog reads the texts from the installed package rather than embedding
them, so what a user reads is byte-for-byte what was shipped. A missing file
falls back to a summary rather than failing to open.

`NOTICE` records every dependency's licence, including the parts that are
easy to miss: scipy's binary wheels bundle libgfortran (GPL v3 *with* the GCC
Runtime Library Exception) and libquadmath (LGPL v2.1). It also records the
build rule — one-directory, never one-file — that keeps Qt replaceable.

### Labels toggle

**Labels** in the toolbar, beside Grid, switches the on-plot spectrum names.
On by default, because an overlay of unlabelled traces is unreadable. Off for
a figure whose caption already names the spectra, and for a crowded 2D map
where the names sit over the data. Applies to the 2D overlay's corner labels
as well.

### Tests

766 (was 762).

## [0.4.17] — 2026-08-04

Documentation only, no code change.

Records the launch route for managed Windows machines, where policy blocks
unsigned executables from a user profile path. pip generates
`helspin-gui.exe` at install time, unsigned, in the environment's `Scripts`
folder — precisely what such a rule catches, which surfaces as "Access is
denied" when running it. `pythonw.exe` ships with conda and is signed, so
`pythonw -m helspin` runs the identical application through a permitted
launcher. Verified against an installed copy, `--check` and `--version`
included.

## [0.4.16] — 2026-08-04

### Undo appeared to do nothing — two separate faults

**A spin box emits `valueChanged` on every keystroke.** Typing `21.114` fired
five changes and pushed five snapshots, so one undo moved the scale from
21.114 to 21.11 — indistinguishable from undo doing nothing at all. Repeated
changes to the same property within a second are now folded into the snapshot
already taken, which is the state *before* the burst began, so one undo
reverts the whole edit. Applies to scale, offset, ppm range and wheel zoom, so
a zoom gesture of several notches is also one step.

**Opening a session left the previous history in place.** A second Ctrl+Z
after opening a file swapped the whole restored session out for whatever had
been on the canvas beforehand. Opening a session is a new document and now
starts with an empty history.

Both were reproduced before being fixed, and both have tests: typing five
values must produce one undo step, and an opened session must survive an undo.

### File before Edit

Menus appear in creation order and the Edit block had been inserted above the
File one. File-then-Edit is the order every desktop application uses, and
menus are found by position as much as by name.

### Shorter cursor readouts

Two decimals by default instead of four, and **configurable in Preferences**
(0–6). Four places filled the status bar and the plot margin for no extra
information — two is enough to place a peak.

The unit is dropped where it is redundant: the plot's crosshair labels sit
directly against an axis already labelled `ppm`, and the 2D status readout is
prefixed `F2`/`F1`, which already says these are chemical shifts. It is kept
in the 1D status readout, where the second number is an intensity and the pair
would otherwise be ambiguous.

### Tests

762 (was 759).

## [0.4.15] — 2026-08-04

### An empty data root now explains itself

A root containing nothing recognisable sat there refusing to expand, with no
message — indistinguishable from one still loading, or from a bug. It now says
what it looked for and how deep it went.

### Documented: how deep a data root is searched

There is no fixed depth, and the behaviour is now pinned by tests: a folder
counts as a sample when it holds at least one numbered experiment folder, and
discovery searches down until it finds them. Pointing at the share, at one
user's `nmr` folder, or at a single sample all work. Measured limits: **eight
folder levels** below the root (the cap exists so a mistaken root cannot walk
a whole disk) and **5000 samples** (the status bar reports truncation). Cost
scales with folders visited, not experiments. Written up in the README.

### Tests

759 (was 756), including samples planted at depths 1, 3, 8 and beyond the cap.

## [0.4.14] — 2026-08-04

### Windows taskbar icon

The icon was never removed — it ships in 0.4.11, 0.4.12 and 0.4.13 alike, and
the installed wheel in each contains the same 336 KB `resources/icon.png`.
What was missing is Windows-specific: without an explicit **AppUserModelID**,
Windows attributes the taskbar button to the host interpreter rather than to
the application. The window's own title-bar icon is correct, while the taskbar
and Alt-Tab show a generic Python icon and group HelSpin with any other Python
window — which reads as "the icon is gone".

The process now claims the identity `HelSpin.NMR.Viewer` before any window is
created. The ID is deliberately not version-stamped, so a pinned taskbar
shortcut survives an upgrade. No-op on macOS and Linux, and a failure is
ignored: a wrong icon is not worth refusing to start over.

### Tests

756 (was 755).

## [0.4.13] — 2026-08-04

### The crosshair now labels both axes

Corrects a misreading on my part in 0.4.12: the request was for the value on
the PLOT, beside the crosshair, not in the status bar.

The vertical line had carried its ppm value since the crosshair existed; the
horizontal one never had, so half the crosshair was decoration. The y value is
now drawn at the right-hand edge, tracking the horizontal line — a mirror of
the x label above the frame, pinned in axes coordinates on one axis and data
coordinates on the other.

In 2D both read as chemical shifts (`0.5963 ppm` above, `17.9706 ppm` at the
right), which matters most for the indirect dimension, since F1 is often the
one being read off. In 1D the second number is an intensity and is formatted
as one.

Two details that took a measurement rather than a guess:

- **A reserved right margin.** The label sits outside the axes, and
  `tight_layout` cannot know about an artist added after it runs — so the
  first version placed the label correctly and matplotlib then clipped it off
  at the figure edge. Verified against the renderer's window extent, and a
  test now asserts no crosshair label extends past the figure.
- **A translucent backing box**, so a long value or a narrow window leaves the
  number readable where it meets the frame.

### Tests

755 (was 751).

## [0.4.12] — 2026-08-04

### The 2D cursor readout only named one dimension

In 2D both numbers under the cursor are chemical shifts, but the second was
printed bare and formatted as if it were an intensity — so the F1 position was
on screen the whole time without saying what it was or carrying a unit. Now
reads `F2 2.9813 ppm    F1 18.7500 ppm`. The 1D readout is unchanged, where
the second number really is an intensity.

### Undo and redo

In the Edit menu, on **Ctrl+Z** and **Ctrl+Shift+Z** (with Ctrl+Y also
accepted). Both entries grey out when there is nothing to go back to, so the
menu states what is possible rather than offering a no-op.

Covers removing and clearing spectra, Subtract/Add, scale and offset changes,
Bottom All and To bottom, arrangement changes, and every zoom including the
wheel — so "unzoom" is Ctrl+Z.

Snapshots record the **view state**, not the data: trace objects are held by
reference and only their display fields are copied. The obvious alternative —
reusing `session_state()`/`restore_session()` — reloads every file from disk,
which would make undoing a scale change cost a round trip per spectrum on a
network share. Holding the removed Trace objects alive in the stack is also
what makes undoing a Remove instant and exact, data included; there is a test
asserting that an undo performs no reads at all. The stack is bounded at 40,
and any new action discards the redo path, since once history branches the old
forward path no longer describes anything reachable.

### Tests

751 (was 745).

## [0.4.11] — 2026-08-04

### Full did nothing in 2D

It reset `_ppm_range` only — and in 2D the view is driven by `_f1_range` and
`_f2_range`, which nothing cleared. So the button cleared a value nothing was
reading. All three are reset now, and the range boxes are told about it.

### Quit left the detached explorer open

Qt exits when the LAST window closes, so a detached explorer kept the process
alive with no main window left to quit from. Closing the main window now
re-attaches the explorer first and then closes: re-attaching rather than
hiding means the explorer's own close path runs as it always does, and the
widget cannot be left parented to a window that is going away.

### Application icon

The supplied artwork now ships inside the package and is set on the
application (Windows taskbar and Alt-Tab), the main window, and the detached
explorer. A missing resource costs the icon, not the application.

**The wordmark is not in the icon**, which is also the answer to the "(p)"
question. The icon is the mark alone — sphere, spin ring and vector arrow —
cropped square from the artwork above the lettering. Two reasons: at 16-32px,
where a window and taskbar icon actually live, a wordmark is illegible
whatever colour it is; and the lettering is 3D-rendered with bevels, shadows
and reflections, so deleting the parentheses and recolouring the "p" by
editing pixels would have left visible damage. Attempts at compositing a
replacement background are what the discarded intermediate versions showed.
The full artwork ships too, as `resources/logo.png`, for use where there is
room for it. If the wordmark is wanted without the parentheses, it is worth
re-rendering from whatever produced it rather than retouching the PNG.

### Tests

745 (was 742).

## [0.4.10] — 2026-08-04

### F1 top and bottom were the wrong way round

My fault in 0.4.9. Renaming the F1 boxes from "left/right" to "top/bottom" was
right, but the values stayed where they were — and F1 is drawn with the LOW
ppm value at the top (`set_ylim(high, low)`). So the box marked "top" held 35
while 35 sat along the bottom axis. The pair now reads down the plot: top box
= what is at the top. The accessor still hands the canvas `(high, low)`, which
is what `set_ylim` needs; only the box each value lands in moved.

### Zoom mode

A **Zoom** toggle sits next to the range boxes it drives. Off by default, so
the wheel keeps its existing meaning (scaling the selected spectrum). On:

- **the wheel zooms about the cursor** — the point under the pointer stays
  put, so a peak of interest does not walk off the edge and turn one gesture
  into three;
- **dragging draws a box** and zooms into it, in 1D and 2D alike;
- in 2D both axes zoom together, so F1 and F2 stay consistent;
- **the range boxes follow**, in both dimensions. A zoom done on the plot that
  did not reach the boxes would leave them showing a range that is no longer
  displayed, and the next Apply would jump the view back. Typing numbers still
  works exactly as before.

A toggle rather than a modifier key: scaling with the wheel and zooming with
the wheel are both wanted often, and holding a key while scrolling is awkward.
A drag below a minimum size is treated as a click, so a tremor cannot zoom to
a few points of noise with no obvious way back.

### Opacity in Preferences

0.05 to 1.00, applied to every line and contour. It matters most for the
superimposed 2D maps added in 0.4.9: opaque contours hide each other exactly
where they cross, which is where the comparison is being made. Clamped above
zero — a fully transparent spectrum is an invisible one with no clue why.

### Tests

742 (was 735).

## [0.4.9] — 2026-08-04

Three observations from the report, all three correct.

### Overlay and Stacked did the same thing in 2D

`_redraw_2d` ignored the arrangement setting entirely and always drew adjacent
panels, so there was **no way to superimpose two contour maps at all**. That is
the comparison that matters most in 2D: chemical-shift perturbation work — apo
against ligand-bound, exactly the pair in the report — is read by seeing how
far each peak *moved*, and that only shows when the maps share axes.

The code carried a docstring asserting that overlaying contour maps is
unreadable. It is not; it is standard practice, and the user was right.

- **Overlay** now superimposes every 2D map on one set of axes, each in its
  own colour, each named in the corner in that colour (superimposed maps have
  no panel titles to tell them apart).
- **Stacked** keeps the adjacent panels, which remain the right answer for
  maps too crowded to superimpose.
- A 1D trace still gets its own panel in either mode: it cannot share a
  vertical axis with a contour map, since one is intensity and the other ppm.

### F1 range was labelled "left" and "right"

F1 is the indirect dimension and is drawn vertically. Now "top" and "bottom",
reading top-down to match the plot.

### Scaling one stacked spectrum shrank all the others

Confirmed, and worse than described. The lane grid was recomputed from the
tallest scaled span on **every** redraw, so scaling one spectrum re-laid the
entire stack. Measured with three spectra, scaling the middle one:

| | neighbours' height |
|---|---|
| all at scale 1 | 29% of canvas |
| middle x5 | 8% |
| middle x20 | **2%** |

The grid is now remembered. Scaling a spectrum grows it within its lane and,
past a point, clips it at the canvas edge — which is what the report asked for
and what a stacked display is expected to do. Same measurement after the fix:
the neighbours stay at 29% and hold their baselines exactly, while the scaled
trace reaches 576% and truncates.

**Fit Y re-establishes the lane grid**, so there is a way back once the
heights no longer suit the layout. Bottom All and switching arrangement do the
same, since both are layout actions.

### Tests

734 (was 732).

## [0.4.8] — 2026-08-03

Windows 11 preparation. Two real bugs found by auditing the package for
Windows before writing install instructions, rather than after.

- **Exported figures could be written with no extension.** The export dialog
  checked for an existing extension with `path.rsplit("/", 1)[-1]`, but Qt
  returns NATIVE separators — so on Windows the entire path came back as the
  "filename", and a dot anywhere in it ("OneDrive - Company Corp", a versioned
  folder, a dotted user name) read as an extension already being present. Now
  uses `Path(path).suffix`, which is correct on both platforms.
- **Launching the app opened a console window behind it.** `[project.scripts]`
  produces a console entry point. Added `[project.gui-scripts]` with
  `helspin-gui`, which is linked against pythonw and opens no console. The
  console `helspin` is kept, because `--version` and `--check` need a terminal
  to print to. On macOS and Linux the two are equivalent.

Audited and found already correct: no POSIX-only calls anywhere in the
package; the index cache uses `%LOCALAPPDATA%\HelSpin\cache` on Windows;
`domain/paths.py` already recognises both drive-letter and UNC forms; cache
writes go through a pid-stamped temporary plus `os.replace`, which is atomic
on Windows too.

**Still not done:** there is no standalone installer or bundled `.exe`. Windows
runs from a Python environment, as documented in the README.

### Tests

732.

## [0.4.7] — 2026-08-03

### Vertical scale is decided by what is on screen

The follow-up screenshot showed the surviving spectrum flattened into a line
along the top of the plot. That is a second fault, independent of the stacked
framing fixed in 0.4.6, and it explains a lot of what came before it.

Every vertical calculation measured the **whole** spectrum. A 19F spectrum
runs from -29 to -180 ppm and is routinely examined over a 10 ppm slice, so a
strong peak a hundred ppm outside the view was setting the frame height, the
stack lane height, and the Fit Y result. Measured on a synthetic case matching
the report — a 4e12 peak at -60 ppm, viewing -120 to -130 — the data actually
being looked at occupied **0.9% of the canvas**.

Everything that decides a vertical extent now measures only the visible ppm
window: the overlay frame, the stacked frame, the stack lane height, the
bottoming floor (already done in 0.4.4, now sharing one helper), and
`fit_to_drawn`. Same case after the fix: **89%** of the canvas in overlay,
and ~93% of its own lane in a three-way stack.

Changing the horizontal window now also refits the vertical one, since one is
derived from the other. Zooming into a quiet region used to keep the scale set
by the peaks outside it, which is the same flat line by another route.

### Also

`full_range()` refits vertically too, for the same reason.

### Tests

731 (was 726). The reported sequence end to end — Bottom All in overlay, then
switch to Stacked, with an out-of-view peak present — asserting that neither
spectrum vanishes and neither is squashed flat; plus lane sizing, zoom
refitting, and Fit Y respecting the window.

## [0.4.6] — 2026-08-03

### Stacked mode lost a spectrum

Switching to Stacked made one spectrum vanish. The stacked frame was built
from **raw** data extents plus a **scaled** stack step, and ignored `y_offset`
entirely — so the frame and the traces ended up on different scales. Measured
with the reported values (y_scale 21, y_offset −2.04e12): the frame came out
as −1.9e11 … 2.5e12 while the lower trace was drawn at −2.04e12 … 7.1e10,
entirely below it.

Stacked framing now uses the **drawn** positions — scale, offset and stack
step together — so a lane cannot fall outside the frame. That is the contract
of stacked mode: N spectra, N lanes, all of them on the canvas.

### "Bottom" in a stack means the bottom of that spectrum's lane

With the frame coherent, `y_offset = anchor − floor` places each trace on the
floor of its own lane, because the drawing already adds the stack step for its
position. No special case, and adding one would double-count the step.
Measured across 2, 3 and 4 spectra:

| spectra | baselines (fraction of canvas height) |
|---|---|
| 2 | 5%, 51% |
| 3 | 5%, 36%, 66% |
| 4 | 5%, 28%, 51%, 73% |

Evenly spaced, the second of two in the middle, and repeating Bottom All does
not move anything.

### Two more disappearing-spectrum paths, found while testing

- **Scaling a trace in stacked mode** could push it off the canvas. The lane
  height is the tallest scaled span, so scaling changes the layout itself, not
  just one trace inside a fixed frame — the frame now follows.
- **Switching back to Overlay** after bottoming a stack hid everything: the
  offsets that suit a stack are large, and the overlay frame is built from raw
  data and ignores offsets by design. Changing arrangement now frames what was
  actually drawn.

### Removed: Match NS·RG

Removed at the user's request, and fairly. In the reported case both spectra
were the same experiment from the same series, so NS and RG matched, the
factor was 1.0, and the button appeared to do nothing — while silently
overwriting the manual `y_scale` that had been dialled in. A correction that
usually does nothing and occasionally undoes your work is not worth a toolbar
slot.

What is kept is the part that answers the question without acting on it: **NS
and RG still travel with each spectrum and appear in the list tooltip**, so
when two spectra differ wildly in height the reason is visible. If it is ever
wanted back, the useful form is multiplying the existing scale rather than
replacing it, and only when the values actually differ.

### Tests

726. Stacked lanes across 2/3/4 spectra, everything staying on canvas through
switch → bottom → re-bottom → scale → switch back.

## [0.4.5] — 2026-08-03

"Two different 1D spectra have a large difference in scaling." Checked, and
the honest answer is in two parts.

### The display is not lying to you

Bruker stores processed data as integers with a separate exponent, `NC_proc`,
and forgetting it is the classic source of spurious factors of 2^n. Verified
directly: two datasets holding the same true intensity, stored with `NC_proc`
0 and 8, load at a ratio of **1.000**. That path is correct.

### The difference is real, and HelSpin gave you no way to remove it

Bruker intensities scale linearly with the number of scans and the receiver
gain. NS 16 / RG 101 against NS 512 / RG 2050 is a factor of **~640** before
any chemistry is involved, so comparing the raw numbers compares the
acquisition rather than the sample.

`domain/overlay.py` has had `acquisition_scale()` since 0.1, with a docstring
saying in as many words that raw comparison between two spectra is meaningless
without it — **and nothing ever called it**. The reader parsed NS and RG,
stored them on `Spectrum1D`, and then dropped them at the worker boundary,
because the load signal carried only path, ppm, intensity and label. The
values existed at every stage except the one that could use them.

Now:

- **NS and RG cross the boundary with the data** and live on the trace, and
  they survive a session round trip.
- **"Match NS·RG" in the toolbar** scales every 1D trace by 1/(NS × RG),
  expressed relative to the first trace so it keeps a scale of 1.0 and the
  numbers in the panel stay readable. It works through the ordinary `y_scale`,
  so the correction is visible and editable rather than a hidden adjustment,
  and it finishes with a fit so the result is on screen.
- **A spectrum with no NS/RG recorded is named, not guessed.** Giving it a
  factor of 1 would place it on a scale it is not on — precisely the failure
  the action exists to prevent.
- **The list tooltip shows NS and RG**, so when two spectra differ wildly in
  height the reason is visible instead of mysterious.

One caveat worth knowing: if a dataset's `procs` cannot be parsed, nmrglue
warns and returns *unscaled* data — a silent factor of 2^NC_proc, commonly
256×. That warning currently goes nowhere a user would see it.

### Tests

725 (was 719): the parameters surviving the load and the session, the ~640×
case being corrected exactly, the first trace staying at 1.0, and a spectrum
without parameters being left alone and reported.

## [0.4.4] — 2026-08-03

"To bottom" / "Bottom All" misplaced spectra and could push them off the
canvas. Three separate faults, all of which bite hardest on exactly the data
in the report: 19F spectra with intensities around 1e12, a scale of 28 from
"Same noise", and a narrow window (-120 to -130 ppm) inside a very wide sweep
(-29 to -180 ppm).

### The offset control could not represent the value it was showing

`Y offset` was a spin box limited to ±1e12. Intensities of 1e11–1e12 are
ordinary, so the offset separating two such spectra is easily larger — and a
spin box does not refuse an out-of-range value, it silently **displays the
clamped one**. A trace positioned at 3.4e12 read `1000000000000.0`, which is
exactly the round number in the report. One touch of the control would then
have applied that wrong number and thrown the spectrum across the canvas. The
range is now sized from the data (and never below ±1e12). The `Y scale` box
was capped at 1e6 against a canvas limit of 1e9, with the same display
problem; both now match the canvas.

### The baseline was taken from data that is not on screen

`move_to_bottom` used `nanmin` of the **whole** spectrum. A 19F spectrum shown
from -120 to -130 ppm can span -29 to -180 ppm in full, and the global minimum
may be an artefact hundreds of ppm outside the window — so the trace was
aligned to a baseline nobody could see and the visible part landed somewhere
arbitrary. It now uses the lowest point within the visible ppm window, with
`y_scale` applied, because that is what is actually drawn.

### Aligning them one at a time made them disagree

`move_all_to_bottom` looped over `move_to_bottom`, which redrew and refitted
the frame between traces — so each spectrum was placed against a frame the
previous one had just moved, and the offsets came out different. Worse, the
anchor was the *current axis* floor, so pressing "To bottom" twice walked the
spectrum down the canvas one click at a time. The anchor is now taken from the
raw-data frame, which does not move when offsets change: one anchor, computed
once, applied to every trace, then a single redraw. Repeatable and idempotent.

### And the result is now visible

A trace scaled up by 28x extends far beyond the raw envelope the frame is
derived from, so it landed correctly and was still off-screen. Bottoming now
finishes with `fit_to_drawn()`, framing what is actually plotted.

### Tests

719 (was 713), covering all four: the common baseline across different
scales, the scaled trace staying inside the canvas, idempotency under repeated
presses, the out-of-window artefact being ignored, and the offset control
showing a 3.4e12 offset intact.

## [0.4.3] — 2026-08-03

"I still do not understand why some spectra cannot be shown." Fair — and while
investigating, the dimming turned out to be wrong as often as it was right.

### The check was concluding "no data" from three situations that are not that

- **A spectrum in a non-default procno was missed.** If `pdata/1` existed and
  held no `1r`/`2rr`, the search stopped there. Bruker routinely puts the
  result elsewhere: an STD difference (`stddiffesgp`) writes on-resonance,
  off-resonance and the difference into *separate* procnos, and `pdata/1` can
  hold parameters and nothing plottable. Those datasets — very likely the
  dimmed `stddiffesgp.3` rows in the report — were marked unusable while their
  spectra sat in `pdata/2` or `pdata/3`. Every procno is checked now. Found by
  the new `--check` command on its first run.
- **"Could not read" was recorded as "nothing there".** An `OSError` from a
  share hiccup or a permissions quirk returned `False` and was **cached**, so
  one momentary glitch greyed a good dataset out for that session and every
  session after. The answer is now tri-state — True / False / **None for
  "could not tell"** — and only a definite answer is stored.
- **Refresh never re-checked.** Processing an experiment writes inside
  `<expno>/pdata`, which does not change the *sample* directory's mtime, so
  nothing would ever revisit the verdict. A dataset processed in TopSpin after
  HelSpin first looked stayed marked "no data" for ever, and Refresh — the one
  thing you would try — appeared to do nothing. Refresh now clears it.

### Dimming warns, it no longer blocks

A dimmed row is **draggable again**. Given the three failure modes above, a
wrong guess should not turn into "this file cannot be opened at all". The drop
attempt is the final word and reports the real error; the dimming is advice.

### It now says why

- The tooltip names **what was actually found**, e.g. *"no 1r or 2rr in any
  procno (checked: 1, 2; pdata/1 holds: procs, outd, title)"*, or *"no pdata
  directory — acquired but never processed"*.
- **Selecting a row puts that reason in the status bar**, so it takes a click
  rather than knowing to hover.
- **`helspin --check <sample-or-experiment>`** prints a per-experiment report
  from the shell — OK / NO / ?? plus the reason — without opening a window.
  Run it on a sample whose rows look wrong and it will say exactly what is on
  disk, rather than what HelSpin concluded.

### Tests

713 (was 707). The non-default-procno case, the unreadable-pdata case, the
refresh-re-checks case, and the `--check` report.

## [0.4.2] — 2026-08-03

### Sessions keep their difference spectra

Saving a session and reopening it silently threw away every subtracted (and
summed) spectrum. `restore_session` skipped any trace marked `is_difference`,
because a difference has no file behind it — its `path` is synthetic
(`<a>::-::<b>`) — and there was nothing to re-read.

The session format already stores paths rather than arrays, on the grounds
that the data is on disk and re-reading keeps a saved view honest if the
processing is redone. A difference now follows the same principle one level
up: what is stored is the **recipe** — the two source paths, the operator, and
the y-scales the sources carried when the operation was performed — and
restore re-derives the trace from it.

Details that matter:

- **The saved scales are used, not the current ones.** The arrays on screen
  were computed with the scaling in force when Subtract was pressed, and
  changing a source's scale afterwards does not retroactively change the
  difference. Re-deriving with whatever the sources happen to be scaled to at
  load time would quietly produce a different spectrum from the one that was
  saved. Verified byte-identical against the original arrays.
- **Order is preserved.** Restore fills one slot per saved entry and compacts
  afterwards, rather than appending as it goes. "Bottom All" reorders traces,
  so a difference is not necessarily below its sources.
- **A difference whose sources are missing is reported, not invented.** It
  appears in the "could not be reloaded" list alongside missing files.
- **Sessions written by 0.4.1 and earlier still load.** Their derived entries
  carry no recipe, so those traces are reported as unrecoverable while the
  rest of the session restores normally.
- The interpolation maths moved into one module-level `combine_arrays()` used
  by both Subtract/Add and the restore path, so a re-derived difference cannot
  drift from the one that was saved.

Also removed a duplicated block of five fields in the `Trace` dataclass
(`pulse_program`, `nucleus`, `label_offset`, `label_base_pos`,
`is_difference` were each declared twice) — harmless, but the kind of thing
that makes the next edit land in the wrong copy.

### Tests

707 (was 703), including a byte-identical round trip, the ordering case, the
saved-scales case, the missing-sources case, and the old-format case.

## [0.4.1] — 2026-08-03

Five issues reported against 0.4.0. Four were real bugs; one ("could load 3
spectra but not more") was a genuine failure that the UI threw away.

### Failures were invisible, which made everything look like a different bug

The position readout under the cursor called `statusBar().showMessage()` — the
**same slot every warning uses** — on every mouse movement over the plot. So
"Could not load …", "Canvas is in 1D mode, clear it first" and every other
explanation survived for as long as it took to move the mouse, usually
milliseconds. With a clean terminal and a wiped status bar, a refused or
failed drop was indistinguishable from a drag that never registered. The
readout now has its own permanent widget on the right of the status bar, and
warnings stay up for 15 seconds.

**There is no limit on how many spectra can be loaded** — tested with ten
separate drops, plus remove/re-drop/clear cycles. What stopped at three were
loads that failed and could not say so.

### An unprocessed experiment looked exactly like a usable one

The index treated "the `pdata` directory exists" as "there is a spectrum
here". An experiment that was acquired but never processed has a `pdata/1`
full of parameter files and no `1r`, so it was listed as droppable and then
failed on drop. The index now records whether a procno really holds `1r`/
`2rr` — checked in the background pass, so nothing the user waits for gets
slower — and such rows are **dimmed, non-draggable, and explain themselves in
a tooltip** rather than being silently omitted. A drop that fails anyway now
marks the row it came from, greyed out with the reason.

### Also fixed

- **Contour controls (Contours / Factor / Base σ) showed in 1D.**
  `set_mode()` had the logic but was only reached through the canvas's
  `modeChanged` signal, which fires on a *change* — so at start-up, in 1D,
  nothing had ever called it. The panel now starts in 1D on its own.
- **A failed load leaked its pending metadata.** The entry keyed on that path
  was never cleared, so a later successful load of the same dataset picked up
  the PULPROG/nucleus stashed by the attempt that failed.
- **Start-up was dominated by imports, not indexing.** `nmrglue` was imported
  at module level and costs ~0.8 s (it pulls in scipy) — and nothing on the
  start-up path needs it any more, since browser rows are filled by the
  stdlib-only `read_acqus_fast`. Made lazy: **app import 1428 ms → 574 ms**,
  with neither nmrglue nor scipy loaded until a spectrum is actually read.
  (The test suite got 30% faster as a side effect.)

### Tests

703 (was 692). `tests/test_reported_issues_040.py` covers all five reports;
two of the bugs above were found by those tests rather than by inspection.

## [0.4.0] — 2026-08-03

Browsing rework. 0.3.0 was reported as unusable on a real network share: the
tree took minutes to show anything, samples could not be opened at all, and
rows could not be dragged. Those were four separate faults, and all four are
fixed here.

### The four faults

**1. Nothing appeared for minutes.** `build_index()` walked into every
experiment directory of every sample before a single row could be drawn:
measured at 1808 filesystem operations for 120 samples / 1440 experiments,
i.e. roughly one round trip per experiment. Scaled to a real root (400
samples, 8000 experiments) that is ~9600 round trips — three minutes at 20 ms
of share latency, nearly eight at 50 ms — with a blank tree throughout. If the
app was closed before it finished, nothing was cached and the next launch
started over.

Indexing is now three tiers, cheapest first, each cached independently:

* **discovery** stops at the first integer-named child, so it costs one
  listing per DIRECTORY VISITED and nothing per experiment — 128 operations
  where the old build took 1808, a 14x reduction — and it *streams*, so rows
  appear while the walk is still running instead of after it;
* **detail** (a sample's experiments) is read when a sample is opened, and
  records every interesting filename from the one listing it already pays for;
* **metadata** (PULPROG/nucleus/date) is read once per experiment in the
  background and cached, so a second session needs no reads at all.

Projected time to the first visible row on a 400-sample root: 184s → 16s at
20 ms latency, 460s → 40s at 50 ms. Opening a sample whose detail is cached
now costs **zero** filesystem access.

**2. A filtered sample could not be opened.** With text in the filter box, the
proxy accepted a matching sample row and then tested each experiment row
against the same text — "2607" is not in "1", "11" or "21" — so every child
was filtered out, the expander arrow vanished, and the row collapsed. This is
what "it does not open at all" was. An experiment under a sample that matched
BY NAME is now always shown; a PULPROG query still narrows within a sample.

**3. Rows could not be dragged until their metadata read returned.**
`mimeData()` skipped any row whose probe had not completed, so the drag
payload was empty and the drag silently did nothing — on a share, for as long
as the read took. The payload never needed the probe: the index already knows
which raw files each experiment has, and `fid` vs `ser`+`acqu2s` is the
dimensionality. Rows are draggable the moment they appear. A processed-only
dataset (no raw data, so nothing structural to go on) reports dimensionality
0 and the canvas settles it when it loads, via the new `read_auto`.

**4. Metadata reads queued ahead of the spectrum being dropped.** Every row
cost nine round trips — re-confirming the directory is an expno, reading
acqus, up to four stats for dimensionality, and a title file no column
displays — and all of it went on `QThreadPool.globalInstance()`, the same
queue the canvas loads dropped spectra from. A drop could sit behind hundreds
of directory listings. Now: `probe_row()` costs **one** read (asserted by
test), acqus is parsed for the dozen keys a row needs rather than all ~415
(3.1 ms → 0.38 ms per file), and there are three separate queues — interactive
work, background indexing, and the canvas's own global pool.

### Also fixed

- **Double free on shutdown (present in 0.3.0).** Qt deletes a `QRunnable` as
  soon as `run()` returns, while the browser and the canvas both kept Python
  references to in-flight tasks. Dropping those references freed the C++
  object a second time: a segfault with no Python traceback, arriving whenever
  a close raced real work. Every task now sets `setAutoDelete(False)`.
- **Workers outliving the objects they read.** Nothing was stopping background
  work at teardown, so the collector could free Node objects while a pool
  thread was still walking their paths. Populators are now held strongly until
  shut down, shutdown drains the pools, and the browser and main window both
  shut down on close.
- **`parent()` reported row 0 for every data root**, because a root node has
  no parent Node and its `row` property answers 0 whatever its real position
  is. With two roots configured the proxy mapped the second root's children
  onto the first.
- **`applyChildren` announced a row insert even when there were none**
  (`beginInsertRows(idx, 0, max(len-1, 0))`), leaving the view's row count one
  ahead of the model's for every empty sample.
- **`Node.__eq__` recursed without bound.** The generated dataclass equality
  compared `parent` and `children`, which are cyclic; two nodes with the same
  path recursed until the stack ran out. Reachable by adding the same data
  root twice. Nodes now compare by identity, which is also what makes
  `children.index(node)` a pointer comparison.
- **Refreshing a data root froze the GUI** for the length of a full re-walk.
  Refresh is asynchronous now, and still merges in place, so expansion state
  and already-read metadata survive.
- Non-integer NS/RG values (`NS= 16.0`) no longer raise from `int()`.
- The index cache uses `%LOCALAPPDATA%` on Windows rather than a dot-directory
  that roaming profiles would copy around; temp files carry the pid so two
  HelSpin windows on one root cannot interleave a write.

### New

- **Background indexing.** Once a root's samples are listed, its experiment
  lists and metadata are read quietly on a low-priority pool and cached. This
  is what makes the second look instant, and it is also what lets the PULPROG
  filter reach samples that were never expanded — the limitation 0.3.0
  documented as a follow-up.
- **Progress in the status bar** while discovering and indexing. Silence for
  two minutes is indistinguishable from a hang.
- **Visible rows are read first.** Scrolling schedules metadata reads for
  what is on screen plus a screenful below, so the rows being looked at do not
  queue behind rows that are not.
- **Staleness is checked in the background** — one stat per indexed sample,
  once per root per session — so an experiment that finishes while HelSpin is
  open appears without pressing Refresh.

### Tests

692 (was 674). New `tests/test_browser_speed.py` asserts round-trip COUNTS
rather than wall-clock times: a timing assertion on a build machine says
nothing about a share, but the number of round trips is exactly what the
latency multiplies.

## [0.3.0] — 2026-08-02

Minor bump: how the browser reads a data root has been redesigned.

### Fixed
- **Experiments never appeared under a sample.** Opening a sample listed its
  directory and then made two more calls per experiment to decide whether it
  had processed data. On a share that is slow enough to look broken.
- **Only part of a data root was listed**, and what appeared did not match the
  directory on disk.

### Added: a persistent index
Both problems had the same root cause -- browsing lazily, one directory
listing per expansion, with every call a network round trip. That cost is now
paid **once**, by a single bulk walk, and kept:

- one `os.scandir` per directory, never a `stat` per file (DirEntry already
  carries the type flag from the directory read);
- one listing per experiment, from which `acqus` and `pdata` presence are both
  read, instead of a separate call for each;
- the result written to a JSON cache keyed by the root's path, so every later
  open is a local file read rather than a network walk.

Measured on a simulated share at 2 ms per call, 90 samples / 620 experiments:

| | time |
|---|---|
| first build (walks the share, then caches) | 1.9 s |
| every later open, from cache | **6 ms** |
| expanding a sample | **5 ms** |

Staleness is checked with one `stat` per sample rather than a re-walk, and
Refresh always rebuilds. A cache that is corrupt, truncated or from an older
format is ignored and rebuilt rather than causing a failure. Writes go through
a temporary file, so an interrupted write cannot leave an unreadable cache.

A sample added after the index was built still opens: it is read directly
rather than showing as empty.

### Notes
- The cache lives in `~/.cache/helspin` (override with `HELSPIN_CACHE_DIR`).
- Experiments with no `pdata` are not listed, since only processed data can be
  displayed.

## [0.2.1] — 2026-08-02

### Fixed
- **Most samples were invisible.** The browser listed samples by enumerating
  every EXPNO and taking their parent directories, and that scan stopped after
  200 expnos. With 8 experiments per sample, a data root holding 400 samples
  showed **25**. Measured, not guessed: a synthetic 400-sample root listed 25.

  Sample discovery is now its own scan. A directory containing any
  integer-named subdirectory IS a sample, so the walk stops at the first one
  and never descends into it. The limit is on SAMPLES (5000, generous) and
  when it is hit the tree reports truncation rather than quietly showing part
  of the data. Same root now lists all 400.

- **A data root could not be removed.** Right-click a data root -> "Remove
  data root". It removes the configured entry only, never any files, and the
  change is saved so it does not return on the next run.

### Performance
The old route needed two stat calls per experiment to answer a question about
directories. `os.scandir` carries the directory-type flag from the single
directory read, so no extra stat is needed, and identifying a sample exits at
its first experiment instead of enumerating all of them.

Measured on a simulated share at 2 ms per filesystem call, 120 samples x 8
experiments: **477 calls -> 122 calls**. Note the old figure covers only the
25 samples it managed to reach before its cap; scanning the whole root the old
way would have taken roughly 2000 calls, so the real-world difference on a
large root is far larger than the raw ratio suggests. Whether each experiment
is usable is still settled on expansion, where the cost is paid only for what
you actually open.

## [0.2.0] — 2026-08-02

Minor bump: saving and restoring a working view is a substantial feature.

### Added
- **Save / Open Session** (File menu, Ctrl+S / Ctrl+O) writing a `.helspin`
  file that restores the whole view: which spectra are loaded, their scales,
  offsets, colours, line styles, visibility, dragged names, arrangement, ppm
  and F1/F2 ranges, grid, axis decimals and contour settings.

  Spectra are stored as PATHS, not as copied arrays: the data already exists
  on disk, the arrays are large, and re-reading keeps a saved session honest
  if the processing is later redone. Files that cannot be re-read are listed
  in a warning rather than silently dropped -- a session that quietly loses
  half its spectra is worse than one that says so. Derived spectra
  (differences and sums) are skipped on restore, since their path is
  synthetic; recreate them with Subtract / Add.

  Y limits are deliberately NOT restored: they are derived from the data and a
  saved pair could be stale after reprocessing.

- **Adjustable 2D contour settings**, per spectrum in the panel and applicable
  to all at once:
  - **Contours** -- how many levels to draw.
  - **Factor** -- the multiplication ratio between successive levels. Values
    of 1 or below are refused, as they would repeat the same level forever.
  - **Base sigma** -- the lowest contour as a multiple of the noise estimate.
    Below about 3 the plot fills with noise contours.

  The controls are hidden in 1D, where they mean nothing, and colours remain
  per-spectrum through the existing Colour button and Preferences.

## [0.1.5] — 2026-08-02

### Fixed
- **Preferences for ppm decimals, grid spacing and name size did nothing.**
  The dialog collected all three and the handler never applied them -- an
  earlier edit that was supposed to add those calls silently failed to match
  and was never verified. All three are applied now, and survive later
  redraws.
- **Panel buttons were truncated** to "iubtrac" / "temove". Five buttons never
  fit one row in a side panel; they are now a 2-column grid, each showing its
  full label at any sensible width.

### Changed
- **Range bar reordered**: Full first, then the left and right ppm boxes, then
  Apply.
- **Recent ranges read low to high** ("0 -> 10 ppm"). The remembered list is a
  description of a range, so it is written from the smaller number to the
  larger; the two boxes keep the axis order (left box = higher ppm) because
  they map onto the plot's edges.
- **Clear is bold** and carries a tooltip, since it discards every loaded
  spectrum and should not look like the adjustments beside it.

### Added
- About now credits: written and developed by H.Iw-ai.

## [0.1.4] — 2026-08-02

### Fixed
- **Subtract stopped working (regression from 0.1.2).** Selecting a spectrum
  called `select_trace`, which emitted `tracesChanged`, which rebuilt the
  spectra list -- and the rebuild wiped the selection. So picking a second
  spectrum silently deselected the first and Subtract never saw two. Two
  changes: `select_trace` no longer emits `tracesChanged` (it only changes a
  highlight), and a rebuild now restores the whole selection rather than just
  the current row.
- **Selecting the wrong number of spectra said nothing**, which looked like a
  broken button. It now explains what is needed, including that Ctrl-click
  (Cmd-click on macOS) adds to a selection.
- **ppm range still read as reversed.** The root cause was that different
  parts used different orders: the plot descended, the boxes kept whatever was
  typed, and the recent list showed ascending. Now ONE convention throughout,
  the NMR one -- left box is the left edge of the plot and therefore the
  HIGHER ppm; boxes normalise on Apply; the recent list shows "12.00 -> 1.00
  ppm"; `set_range` normalises too. Either typing order is still accepted.

### Added
- **Add spectra**, alongside Subtract, sharing exactly the same machinery:
  interpolation onto a common ppm axis, y-scale applied first, same
  validation. Labelled with a delta so a derived spectrum is unmistakable.
- **Real 2D panel controls.** In 2D the bottom bar gains an independent F1
  range and the main pair becomes F2, both seeded from the data and both
  normalised to descending. The F1 controls are hidden in 1D, where the
  indirect dimension is meaningless.

## [0.1.3] — 2026-08-02

### Fixed
- **Dropping a 2D spectrum crashed** with "zero-size array to reduction
  operation fmax which has no identity". 2D traces keep EMPTY ppm/intensity
  arrays -- their data lives in matrix/ppm_f1/ppm_f2 -- and several 1D-only
  routines called `np.nanmax` on them regardless. Introduced with the 2D work
  in 0.1.0 and present in 0.1.1 and 0.1.2.

  Rather than patch the one reported traceback, every method that touches
  1D data was audited. Four had the same latent crash: `ppm_bounds`,
  `autoscale_traces`, `move_to_bottom` and `normalise_to_noise`. All now go
  through a single `_visible_1d()` guard that excludes 2D and empty traces,
  so the class of bug is closed rather than one instance of it.

### Tests
- Regression coverage for every affected path with only 2D loaded, with a
  hidden 2D trace alongside visible 1D data, and on an empty canvas.
- 633 tests.

## [0.1.2] — 2026-08-02

### Fixed
- **Recent ranges read backwards.** The stored pair is (high, low) because
  that is the order the descending ppm axis needs, but the list displayed that
  order literally, so "1 to 12 ppm" came back as "12.00 – 1.00". Entries are
  now shown low-to-high, the way a range is read aloud, while the plot keeps
  the NMR convention of high ppm on the left.
- **Spectrum names could not be moved.** Dragging alone was unreliable,
  particularly for a name sitting underneath a trace.

### Added
- **Explicit legend offset per spectrum** (Name x / y in the spectra panel).
  Reproducible, reaches names that are awkward to grab, clamped inside the
  plot, and preserved when the spectrum is rescaled. Dragging still works.
- **Pulse programme in the on-plot name** — two experiments on the same
  sample differ only by pulse programme, which is exactly when a bare name is
  ambiguous.
- **Difference spectra are marked** with a delta and a true minus sign
  ("Δ A − B") so they are unmistakable in the legend, and carry an
  `is_difference` flag.
- **Explicit 1D / 2D canvas mode.** The mode is derived from the data (no
  switch to remember) and shown in the title bar and status line. Dropping a
  2D spectrum onto a 1D canvas -- or the reverse -- is refused with a clear
  message rather than silently accepted: a contour map and a 1D trace share no
  meaningful vertical axis, so the mixed figure would look fine and mean
  nothing.
- **Separate F1 and F2 ranges in 2D**, each normalised to descending.

## [0.1.1] — 2026-08-02

### Fixed
- **Closing the detached explorer wrecked the main window.** `QDockWidget` was
  the wrong tool: a floated dock kept a stale geometry, re-docking left the
  central area not re-laid-out, and the tree could end up drawn over the main
  window. Replaced with explicit re-parenting between the splitter and a plain
  top-level window, which is predictable. Closing that window simply hands the
  explorer back and restores the three-panel proportions.
- **2D datasets could be listed but never opened.** Dimensionality was
  determined only from raw `fid`/`ser` files, which are routinely deleted or
  never copied off the spectrometer. A processed-only 2D dataset therefore
  appeared in the browser (it has `2rr`) and then failed to probe. It now
  falls back to the processed data -- which is what gets displayed anyway.

### Added
- **Difference spectra.** Select exactly two spectra and press **Subtract** to
  add A − B as a new spectrum. The two are INTERPOLATED onto a common ppm axis
  first: subtracting index-by-index is the classic silent error here, because
  two spectra rarely share a point count and the result then compares
  different chemical shifts. Each spectrum's Y scale is applied first, so a
  difference taken after "Same noise" reflects what is on screen.
- **Draggable spectrum names.** Drag a name to move it beside its trace.
  Positions are held in axes fractions, so a dragged name stays where it was
  put when the spectrum is rescaled.

### Note on 2D
There is no separate "2D mode" to switch to, by design: the app reads each
dataset's dimensionality and arranges accordingly. 2D spectra get contour
panels side by side, 1D spectra share a panel, and a mixed selection gets
both.

## [0.1.0] — 2026-08-02

**2D spectra display.** This was the milestone reserved for `0.1.0`: the app
can now show the data it reads, in both dimensionalities.

### Added
- **2D contour display.** Dropping a 2D dataset renders it as a contour map.
  Two or three 2D spectra are placed in **side-by-side panels with shared
  axes**, so zooming one moves all and peaks stay aligned for comparison.
  Overlaying contour maps was deliberately not done -- overlapping them is
  unreadable, which is why NMR software compares 2D spectra in adjacent
  panels.
- Contour levels follow a **geometric ladder from a noise-based floor** (MAD
  estimate). Evenly spaced levels either drown the plot in noise contours or
  show only the tallest peak. The Y scale control doubles as the usual
  "contour level" adjustment.
- **Mixed 1D and 2D** in one figure: each 2D spectrum gets a panel, all 1D
  spectra share one alongside. Hiding every 2D returns to the plain 1D view.
- 2D figures export through the same right-click Save image path.

### Fixed
- **The explorer did not return properly when its window was closed.** Qt
  hides a closed dock by default, so a floating explorer vanished with no
  obvious way back, and clearing the floating flag alone left it mis-sized.
  The dock now intercepts its own close and re-docks, and re-docking removes
  and re-adds it so Qt rebuilds the layout and restores a sane width.
- **EMF removed from the export menu.** matplotlib cannot write EMF, and the
  external-Inkscape route silently produced nothing when Inkscape was absent.
  Offering a format that usually fails is worse than not offering it; SVG, PDF
  and EPS remain and are vector with editable text.

## [0.0.10] — 2026-08-02

### Fixed
- **The explorer could not be re-docked** once floated. The button's label was
  the only record of the state, so dragging the dock out by its title bar left
  label and reality disagreeing and the button then did the wrong thing. State
  is now read from the dock itself. Closing a floating explorer re-docks it
  rather than losing it.
- **Dead space around the plot.** The canvas now has an explicit expanding
  size policy, so it fills the window instead of keeping a stale size after
  the explorer was floated.
- **The cursor crosshair was baked into exported images.** It is a UI overlay
  and is cleared before every save.
- **Yellow was still in the default palette** (slot 5, from the domain
  palette, which the earlier change missed). Removed: it has the lowest
  contrast against white of the Okabe-Ito set and vanishes at publication line
  widths. Order is now by contrast on white, black then strong blue.

### Added
- **"Same noise" button** — scales every spectrum so their NOISE levels match,
  making peak heights directly comparable across different scan counts or
  receiver gain. Noise is estimated by median absolute deviation, not standard
  deviation, because the peaks are outliers and a plain sigma would measure
  signal rather than noise. Declines honestly (with a message) when noise
  cannot be estimated.
- **PowerPoint (.pptx) export** — one slide sized to the figure.
- **EMF export via Inkscape.** matplotlib genuinely cannot write EMF; the
  figure is exported as SVG and converted. Without Inkscape the error says
  exactly that instead of writing a mislabelled file.
- **ppm axis decimal places** in Preferences: automatic, or 1 / 1.0 / 1.00.
- **Spectrum-name size** in Preferences, which also spaces the names further
  apart so a long list stops overlapping the traces.

### Not done in this release
- **2D spectra still are not displayed** (side-by-side, stacked or overlay).
  This is a substantial piece of work and is the next milestone; shipping a
  half-working contour view would be worse than not shipping one.

## [0.0.9] — 2026-08-02

### Added
- **Save image, from a right-click on the spectrum** — PNG, JPEG, TIFF, SVG,
  PDF, EPS and PS. Written exactly as displayed: the tight-bounding-box option
  and any layout change are deliberately avoided, because they silently
  re-crop and re-scale, so the file would not match the canvas.
- **Vector exports keep text editable.** matplotlib converts text to outlines
  by default, which defeats the point of vector output; SVG now carries real
  `<text>` and PDF/PS/EPS embed TrueType, so labels can be edited downstream.
  Background is white by default, with a transparent option.
- **Detachable data explorer.** The browser is now a dock: "Detach Explorer"
  opens it as its own resizable window, so long Bruker sample names are
  readable without squeezing the plot.
- **ppm value shown at the cursor**, drawn on the plot above the crosshair
  rather than only in the status bar.

### Changed
- **Auto Y now fits every spectrum inside the canvas.** It scales each
  spectrum and then frames what is actually drawn, including offsets, in both
  overlay and stacked. Previously a scaled trace could run off the top.
- **Default colours reordered**: slot 2 is now a strong blue rather than
  orange, which was muddy against black both on screen and in print. Yellow
  (#F0E442) removed entirely -- nearly invisible as a thin line on white.

## [0.0.8] — 2026-08-02

### Fixed
- **Y-scale ceiling made strong/weak pairs impossible to compare.** The scale
  was clamped at 1000x, but a 5 mM reference beside a 50 uM sample is ~1000x
  on its own, so autoscale was silently clipped. Range widened to 1e-9..1e9.
- **Unticking a spectrum rescaled everything**, and re-ticking did not restore
  the previous view. Visibility is a viewing action and no longer re-fits the
  frame; only adding/removing spectra or switching arrangement does.
- **The grid drew horizontal lines too.** A horizontal grid cuts across every
  stacked spectrum and reads as part of the data. Now X (ppm) only.
- **The ppm boxes rewrote what you typed.** Enter "0 to 12" and they flipped
  to "12 to 0" under you. The boxes now keep exactly what was entered; the
  plot interprets the pair and always draws high ppm on the left, per NMR
  convention.

### Added
- **Auto Y** — scales each spectrum individually so they are all legible, and
  runs automatically when a second spectrum is dropped. Re-fitting the frame
  alone cannot rescue a weak spectrum, since the frame must fit the strong
  one. Fully adjustable afterwards.
- **Move to bottom**, per spectrum ("To bottom") and for all at once
  ("Bottom All"), putting spectra on a common baseline.
- **Grid spacing in Preferences** — a fixed interval in ppm (e.g. 0.5), or
  Automatic.

## [0.0.7] — 2026-08-02

Regression fixes. Three controls that 0.0.5/0.0.6 claimed to add were in fact
broken in the running application, despite passing tests — the tests drove
code paths the GUI does not use. Root causes and the lesson are recorded in
`HANDOFF.md`.

### Fixed
- **Y scale showed almost no signal.** The vertical range was computed at the
  FIRST drop and never recomputed, so a spectrum loaded afterwards with a much
  smaller intensity was drawn as a flat line at zero against the first one's
  scale. The range is now re-fitted whenever the set of spectra changes
  (autoscale on drop and on removal) while still being held fixed during
  scale/offset edits, which is what keeps those adjustments visible.
- **Scroll-wheel scaling never worked.** `canvas.wheelEvent = fn` was assigned
  on the *instance*; Qt dispatches virtual methods through the class, so it was
  ignored — and it shadowed the backend's own handler, killing matplotlib's
  `scroll_event` as well. Now handled through `scroll_event`. Verified with a
  real `QWheelEvent`, not a direct call.
- **Dragging a spectrum did nothing.** Motion events were not being delivered
  without a button held. The canvas subclass now sets `setMouseTracking(True)`.
  Verified with real `QMouseEvent`s.
- **`Full` did not update the ppm boxes**, leaving them showing a range that
  was not what was on screen.

### Added
- **Global line width in Preferences** — one value plus "Apply to all",
  instead of setting eight boxes by hand. Per-slot widths remain
  individually overridable afterwards.
- **Preferences are saved and reused as the default next run** (colour, line
  style and width for all eight slots), via `QSettings`. A corrupt stored
  value degrades to defaults rather than preventing startup.
- **`HANDOFF.md`** — architecture, hard-won decisions with the reasons, known
  bugs, repeated failure modes, and a delivery checklist, for continuing in a
  fresh session.

## [0.0.6] — 2026-08-02

Corrections to six issues reported against 0.0.5, several of them fixes from
that release that had not actually landed correctly.

### Fixed
- **Arrangement buttons read backwards.** A single button labelled with the
  *current* mode was ambiguous — it was impossible to tell whether the label
  named the state or the action. Replaced with two checkable buttons,
  Overlay and Stacked, with exactly one lit at any time.
- **Spectrum names moved when a spectrum was scaled.** The 0.0.5 labels were
  anchored to each trace's data maximum, so scaling dragged the name around
  the plot — the opposite of what was asked for. They are now pinned in
  axes-fraction coordinates, top-left, one per line, and never move.
- **Y offset could not be set.** Dragging was restricted to stacked mode, so
  in overlay it silently did nothing, and there was no way to type an exact
  value. Dragging now works in both arrangements, and a Y offset box sits
  beside Y scale in the spectra panel. Its range covers real NMR intensities
  (±1e12) rather than a token ±100.
- **The ppm range would not accept "0 to 10".** Editing either box applied a
  range immediately, built from that value plus whatever stale value was in
  the other box — so a two-box range could never be entered cleanly. Nothing
  is applied now until **Apply** is pressed, at which point both values are
  read together and normalised to the descending order the axis uses.

### Changed
- **Line style and width moved out of the canvas panel into Preferences**,
  where the rest of the appearance settings live.
- **Preferences now covers eight spectrum slots**, each with its own colour,
  line style (solid / dashed / dotted / dash-dot) and line width. Slot N
  applies to the Nth spectrum loaded, so appearance is configurable before
  anything is dropped, not only afterwards.
- The spectra panel keeps only what is genuinely per-loaded-spectrum:
  selection, visibility, Y scale, Y offset, an individual colour override,
  and Remove.

### Known limitations
- 2D spectra still are not displayed; dropping one says so.
- Preferences are not persisted between runs.

## [0.0.5] — 2026-08-02

Plot usability pass, addressing eight reported issues.

### Fixed
- **Y scale had no visible effect.** matplotlib autoscales the y-axis, so
  multiplying a trace's intensity simply grew the axis to match and the
  picture looked identical. Y limits are now pinned once established, so
  scaling actually changes what you see. "Reset Y" recomputes them.
- **The ppm range would not accept "0 to 12".** Typing the range in ascending
  order was rejected outright, which just looked broken. Either order is now
  accepted and normalised to the descending order the ppm axis uses, with the
  boxes updated to match what was applied.
- **Datasets that cannot be displayed are no longer listed.** The browser
  showed every expno, including ones with no processed data, which then failed
  with "cannot find" on drop. Expnos are now filtered on the presence of an
  actual processed-data file (`1r` for 1D, `2rr` for 2D) at the source.

### Added
- **Spectrum name drawn on the plot**, at each trace's own top-left in its own
  colour, so it travels with its spectrum instead of sitting in a legend.
- **Crosshair cursor** with a live ppm / intensity readout in the status bar.
- **Drag to reposition** the selected spectrum vertically in stacked mode.
  Deliberately inert in overlay, where which trace a drag refers to would be
  ambiguous.
- **Line style per spectrum** — solid, dashed, dotted, dash-dot — alongside
  the existing per-spectrum colour.
- **Grid toggle**, deliberately faint (alpha 0.25) so it reads as an aid
  rather than competing with the peaks.

### Known limitations
- 2D spectra still are not displayed; dropping one says so.
- Preferences are not persisted between runs.

## [0.0.4] — 2026-08-02

Usability pass on the spectrum canvas: spectra are identifiable, individually
scalable, and the appearance is configurable.

### Fixed
- **Legend showed a bare expno number** ("1", "30") with no sample name, which
  is useless when comparing — every sample has an expno 1. Labels are now
  "<sample>/<expno>", and the drag payload also carries the sample name, pulse
  program, and nucleus.
- **The ppm range boxes started at 0.000/0.000.** Typing into them before
  anything was loaded produced a degenerate near-zero-width view. They are now
  seeded from the loaded spectra's actual ppm span the first time data arrives.
- **Hiding every spectrum crashed the redraw** (`max()` on an empty sequence).
  The empty-state guard checked whether any traces existed rather than whether
  any were *visible*. Now both "nothing loaded" and "all hidden" render an
  empty canvas with an appropriate message.

### Added
- **Per-spectrum vertical scaling.** Select a spectrum and scroll the wheel
  over the plot, or type an exact value in the Y scale box. Scaling is
  per-trace, so a weak spectrum can be brought up without touching the others.
  Refuses zero, negative, and non-finite values, and is clamped so a fast
  scroll cannot drive it to zero or overflow.
- **Loaded-spectra panel** on the right: select which spectrum the scale
  controls act on, toggle visibility, change an individual colour, or remove
  it. The selected trace is drawn thicker so it is obvious which one is active.
- **Real Preferences dialog** — line width and the colour cycle, applied to
  spectra already drawn as well as new ones. Replaces the previous honest
  stub. Cancel genuinely changes nothing.
- **Per-trace y offset** (`set_y_offset` / `nudge_y_offset`) for moving a
  single trace vertically.

### Changed
- **One arrangement toggle instead of two buttons.** Its label always names
  the current mode (Overlay / Stacked) and the tooltip says what a click will
  do, rather than two checkable buttons where both could look unset.
- **Clear is now a true reset** — traces, selection, ppm range, and the range
  boxes. Leaving a stale range behind made the next drop appear on a
  nonsensical axis.
- **Stacked spacing accounts for scaling**, so a scaled-up trace no longer
  overlaps its neighbour.

### Known limitations
- 2D spectra still are not displayed (contour rendering to come); dropping one
  says so rather than failing silently.
- Preferences are not yet persisted between runs.

## [0.0.3] — 2026-08-02

**Spectra are now actually displayed.** The layout-first flow (define a figure
with N slots, then fill them) is gone: drag one or more datasets straight onto
the canvas and they are loaded and drawn. Arrangement is a toggle you flip
afterwards, not a decision forced up front.

### Fixed
- **REGRESSION from 0.0.2: samples could not be expanded at all.** Making
  `canFetchMore` always return False removed the expander arrows entirely --
  `QSortFilterProxyModel` derives `hasChildren()` from `rowCount()` when
  `canFetchMore()` is False, so every unexpanded sample looked childless.
  `canFetchMore` now correctly reports True for unloaded nodes; the
  GUI-thread-blocking concern it was meant to solve is handled in `fetchMore`
  instead, which delegates the scan to the async populator (and therefore
  still schedules the metadata probes that make rows draggable).

### Added
- **Real spectrum rendering.** `read_1d` / `read_2d` are implemented against
  nmrglue's processed-data reader (pdata -- what TopSpin shows), with the ppm
  calibration rebuilt into the domain's `AxisCalibration`.
- **`SpectrumCanvas`**: a matplotlib canvas that accepts dataset drops
  directly and plots them. Loading runs on a worker thread, so dropping
  several spectra does not block on the slowest one, and each appears as it
  arrives.
- **Overlay / Stacked toggle** and **Clear** on the toolbar; the ppm range
  controls are live from the start rather than gated behind creating a figure.
- Load failures are reported in the status bar instead of failing silently.
  Dropping a 2D dataset says plainly that 2D display is not implemented yet.

### Removed
- The **New Figure** dialog and the slot/box layout flow are no longer wired
  into the app. The modules remain in the tree for a future "more control"
  mode, but the default path is now just: drag spectra on, look at them.

### Known limitations
- **2D spectra are not displayed yet** (contour rendering is still to come);
  dropping one reports this rather than silently ignoring it.
- Export, undo, and the TopSpin bridge remain unbuilt.

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
