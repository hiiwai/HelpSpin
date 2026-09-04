# HelSpin — User Manual

Version 0.5.17

Compare Bruker NMR spectra and build publication figures.

Contact: iwai@ligsciss.com

---

## 1. What HelSpin is for

You have a folder of Bruker datasets and a question that needs several of them
side by side: did the reaction go, does this batch match the reference, which
of six conditions gave the cleanest product. HelSpin opens them together,
lets you line them up, and exports the result at publication quality.

It reads Bruker data directly from wherever it already lives, including a
mounted spectrometer share. **It never writes to your data.** Everything you
adjust — scale, position, colour, zoom — is display state, held in HelSpin and
saved in its own session files.

---

## 2. Installing

On macOS, if you have `HelSpin-<version>-<arch>.dmg`: open it, drag HelSpin to
Applications, and on first launch **right-click the app and choose Open** to
get past Gatekeeper (once only — the app is not signed with an Apple Developer
ID). No Python or conda is needed. Check the architecture in the filename:
`arm64` for Apple Silicon, `x86_64` for Intel; they are not interchangeable.

Otherwise, and on Windows and Linux, see `INSTALL.md`. Linux needs one system
library (`libxcb-cursor0`) before the first run.

---

## 3. Getting started

### Adding a data root

**File → Add Data Root…**, then pick the folder your datasets sit under. Point
it at a directory that *contains* sample folders, not at a single dataset.
HelSpin walks down looking for the Bruker structure (a sample folder holding
numbered experiment folders) and shows what it finds.

You can add more than one root — a network share and a local archive, say —
and they persist between sessions.

Symlinks are followed, so a root made of links to the real instrument mounts
works normally. Links pointing back at a parent are detected and not followed
twice, so a loop cannot hang the scan.

### The browser

The left panel is the tree: root → sample → experiment. Type in the filter box
to narrow by sample name or pulse programme; the filter reaches samples that
have not been expanded yet.

The tree does **not** watch the filesystem. Network shares make that
unreliable, so refreshing is explicit: right-click a node to refresh it, or
**Refresh All** / **F5**.

### Opening spectra

Drag a dataset onto the canvas, or double-click it. Repeat to add more. Each
one becomes a *trace* listed in the right-hand panel.

If a dataset does not appear, ask HelSpin what it sees on disk:

```
helspin --check /path/to/SAMPLE
```

That reports, experiment by experiment, whether a plottable spectrum is
actually there.

---

## 4. Arrangements: overlay and stacked

**Overlay** draws every spectrum on one set of axes, sharing a baseline. Use it
to compare peak positions and relative intensities directly.

![Overlay](docs/img/overlay.png)

**Stacked** gives each spectrum its own lane, evenly spaced. Use it when the
traces would otherwise obscure each other, and for showing a progression.

In a stack each spectrum's name sits **beside its own trace**, so a reader
does not have to match colours to work out which is which. The name is
anchored to the lane's baseline, so it stays put when you scale or magnify the
spectrum, and follows the lane when you zoom. Drag a name to reposition it;
**Reset label positions** puts them back.

![Stacked](docs/img/stacked.png)

Switch with the arrangement control. The frame is re-fitted on switching,
because offsets that suit one layout rarely suit the other.

---

## 5. Adjusting a spectrum

Select a trace in the right-hand panel first — adjustments apply to the
selection.

### Y scale — the wheel

With both zoom toggles off, the **mouse wheel over the plot scales the selected
spectrum** vertically. This changes that spectrum's height relative to the
others, which is what you want when one sample is far more concentrated than
another.

Nothing happens if no trace is selected. That is deliberate: guessing which
spectrum you meant would be worse than asking you to click one.

### Y offset

Moves the selected spectrum up or down. Pure translation — it does not rescale
anything and does not re-fit the view.

**To bottom** drops the selected spectrum to the baseline; **Bottom all** does
every spectrum at once.

### X offset — shifting along ppm

**Spectrum list → X offset**, in ppm. Shifts the selected spectrum
horizontally. Two uses: correcting spectra that were referenced slightly
differently, and skewing a stack into a cascade.

![X offset](docs/img/xoffset.png)

The step size and range come from the spectrum's own width, so the control
behaves sensibly for a ¹H spectrum spanning 12 ppm and a ¹³C one spanning 200.
You can shift up to half a spectrum width in either direction.

> **A shifted spectrum says so.** An X offset moves a trace along the chemical
> shift axis, so its peaks no longer read at their true values. HelSpin marks
> any shifted trace in the spectrum list *and on the plot label* —
> `2 h  [+0.150 ppm]` — so a figure never quietly claims a peak sits somewhere
> it does not. The marker disappears when the shift returns to zero.
>
> The underlying data is never modified. Peak readout always reports true,
> unshifted ppm, and **Clear X offsets** returns everything to its real
> chemical shift in one action.

### Appearance

Colour, line width and line style are per-spectrum, in the spectrum list.
Defaults come from the slot palette in Preferences, so the first spectrum you
open always looks the same way.

---

## 6. Zooming

Two independent toggles in the bottom bar: **X zoom** and **Y zoom**. They are
not exclusive — with both on, the wheel zooms both axes at once.

| Toggle | What the wheel does |
|---|---|
| Both off | Scales the **selected spectrum** vertically |
| X zoom | Zooms the ppm axis about the cursor |
| Y zoom (overlay) | Zooms the intensity axis about the cursor |
| Y zoom (stacked) | **Magnifies all spectra**, leaving the lanes where they are |
| Both on | Zooms both axes together |

Zooming keeps the point under the cursor fixed, so a peak of interest does not
walk off the edge.

With **X zoom** on, you can also drag a box on the plot to zoom into it.

### Y zoom in stacked mode is different, on purpose

In a stack, the layout *is* a set of lanes at fixed baselines. Narrowing the
vertical window there would not magnify the stack — it would crop it, pushing
whole spectra off the canvas.

So in stacked mode the wheel **magnifies the traces** and leaves the baselines
exactly where they are. Peaks grow in place, overflow their lane, and can run
off the top of the canvas.

![Stacked and magnified](docs/img/stacked_zoom.png)

That overflow is intended. Bringing up a weak signal beside a strong one can
need magnification of many orders of magnitude, so **there is no limit** in
either direction.

### Getting back

- **Fit Y** — re-frames the vertical axis and clears any Y zoom or
  magnification.
- **Full** — shows everything, in every axis.
- **Ctrl+Z** — undoes it, including Fit Y.

A zoom is sticky: it survives loading another spectrum, removing one, changing
the ppm window and redrawing. Turning the toggle off does not undo it. Only
Fit Y, Full, reset or undo clear it.

---

## 7. Setting the ppm window

The bottom bar takes a typed range. ppm axes descend, so the **left** box is
the higher value. **Apply** sets it; **Full** clears it.

Recently used ranges are remembered **between runs**, so returning to a window
you use often is one click. The list starts seeded with three common windows
rather than empty:

| Range | Typical use |
|---|---|
| 0 to 12 ppm | the standard ¹H sweep |
| -1 to 13 ppm | the same, with a margin at each end |
| 5 to 13 ppm | downfield only: aromatics, amides |

Ranges you apply are added to the front, so the list becomes yours with use.

Narrowing the window re-fits the vertical frame to the data actually in view —
otherwise zooming into a quiet region would keep the scale set by peaks
outside it and show you a flat line. An explicit Y zoom outranks that and is
kept.

---

## 8. Undo

**Ctrl+Z** / **Ctrl+Y** (or **Ctrl+Shift+Z**). Forty steps.

Undo covers display state — scales, offsets, zooms, colours, adding and
removing spectra. It never re-reads a file, so undoing a change costs nothing
even with data on a slow share, and undoing a removal brings the spectrum back
instantly with its data intact.

Typing into a spin box is one undo step, not one per keystroke.

---

## 9. Sessions

**File → Save Session…** writes everything about the current view: which
datasets are open, and every scale, offset, colour, zoom and window. **File →
Open Session…** restores it.

Sessions store *paths*, not spectra. If the underlying data has moved, the
session cannot find it — keep the data root stable, or re-add the root.

Restoring clears the undo history, so an opened session cannot be undone into
the state that preceded it.

---

## 10. Exporting a figure

**Save Image…**. Choose PNG, PDF, or SVG by the extension you type; PDF and
SVG stay vector and are what you want for a manuscript.

- **300 dpi** by default.
- **Transparent background** is available for slides.
- The exported figure matches what is on screen, including any X offset
  markers.

---

## 11. Where HelSpin keeps its files

| What | macOS / Linux | Windows |
|---|---|---|
| Settings | `~/.config/HelSpin/` | registry |
| Index cache, licence | `~/.cache/helspin/` | `%LOCALAPPDATA%\HelSpin\cache` |

On Linux, `XDG_CACHE_HOME` is honoured — worth setting if `/home` is a slow
network mount, since the cache belongs on local disk. `HELSPIN_CACHE_DIR`
overrides it explicitly on any platform.

Deleting the cache is safe; it is rebuilt on the next scan.

---

## 12. Troubleshooting

**A sample is missing from the tree.** Run `helspin --check /path/to/SAMPLE`.
Note that Bruker writes `acqus`, `pdata` and `1r` in lower case and Linux
filesystems are case-sensitive, so data copied from Windows by a tool that
changed case will not be recognised.

**Permission denied on a share.** Confirm you can read it outside HelSpin
first (`ls /mnt/nmr`). An unreadable subtree is stepped over silently by
design, so one locked folder does not cost you the rest of the share.

**Linux: the application will not start**, with a Qt platform plugin error.
Install `libxcb-cursor0` (Debian/Ubuntu), `xcb-util-cursor` (Fedora/Arch).
See INSTALL.md.

**Linux: the window never appears, no error.** Check for a leftover
`QT_QPA_PLATFORM=offscreen` in your shell.

**A spectrum vanished after zooming.** Press **Fit Y**.

**The plot looks wrong and you cannot say why.** **Full**, then **Fit Y**,
returns to a neutral view without closing anything.

---

## 13. Known limitations

Stated plainly, because finding these out mid-figure is worse.

- **Differences and sums ignore an X offset.** If you align two spectra
  horizontally and then subtract, the subtraction uses their true ppm axes,
  not the aligned ones.
- **No automatic file-watching.** Refresh is explicit, by design — see §2.
- **The TopSpin bridge is not wired up.** TopSpin identifiers parse, but there
  is no live connection to a running TopSpin.
- **Windows and macOS are not yet verified end to end.** Development and the
  test suite currently run on Linux. The Windows installer path in particular
  has not been exercised from start to finish.

---

## 14. Licence

Free for academic research, teaching and personal use. Commercial use requires
a licence — enquiries to **iwai@ligsciss.com**. Full terms in `LICENSE`, and
readable in the application at **Help → Licence**.

Qt (via PySide6) is LGPLv3 and is packaged one-directory so its shared
libraries stay replaceable. nmrglue, NumPy and SciPy are BSD; matplotlib is
BSD-style.

---

## CEST mode

Toolbar **CEST…**, or File → CEST…. Opens its own window. If exactly one
experiment is selected in the browser it is loaded straight away.

The panel handles both Waudby-lab pseudo-2D experiments and picks the tab
from PULPROG: `19f_calib_nut` opens the calibration tab, `19f_cest` the
Z-spectrum tab.

### Which data it reads

**Raw `ser` by default.** `xf2` stores SI(F1) rows, and TopSpin defaults SI
to a power of two — so a 42-offset series processed at SI(F1) = 32 silently
loses ten offsets. The resulting `2rr` is a valid file with no record of the
loss. The panel treats the frequency list as the authority on how many rows
should exist, and says so in the notes box when pdata disagrees.

Set **Source** to *Processed 2rr* if you want the phasing you set in
TopSpin; the panel will still warn if rows are missing. SI(F1) larger than
TD(F1) is fine — the surplus rows are blank padding and are trimmed.

To keep TopSpin's own view complete, set `1 SI` to the next power of two
above your offset count (64 for 42 offsets) before `xf2`, and accept the
blank trailing rows.

### Calibration tab

Choose a peak centre and half-width, then **Fit nutation**. The window is
applied identically to every row and the result stays signed, because the
sign inversion across rows is the measurement.

- **Measurement** — integration is the default and the right choice here.
  A narrow window works best: on 19F test data, residuals fell from 7.0% at
  ±0.20 ppm to 3.9% at ±0.03 ppm.
- **Model** — *Automatic* compares a plain sine against a damped one on
  AICc and reports which won and by how much.
- **Target field** — the field you want. It need not match this
  calibration's CNST8.

Results end with a **TO SET ON THE SPECTROMETER** table giving the `CNST`
value to type for each true field, with the pulse length and power the
sequence will derive from it. Lowering `CNST` is the whole adjustment, and
in `19f_cest` it is the only one available — that sequence recomputes
`plw25` unconditionally, so anything typed into `PLW25` is overwritten when
the pulse program compiles. Record the true field in the title, since acqus
will show the lowered `CNST` rather than the field you applied.

Results also give the fitted B1 with an error bar, T2 effective, residuals,
and two power figures kept deliberately apart: the **correction factor**
`(nominal/fitted)²`, which multiplies any nominal power on the same probe
and tuning and so transfers to a CEST experiment at a different field; and
the absolute power for your target field, which also changes the field and
therefore does not transfer. **Export record…** writes a record sheet
matching Part 8 of the 19F CEST manual.

### Z-spectrum tab

Saturation offsets are read from the experiment's own `lists/f1/` copy of
the FQ1LIST. Bare Hz, ppm (`P`), absolute MHz (`SFO`) and `BF` lists are all
understood, as are comments and Windows line endings. Without a list the
panel says so rather than plotting against row number. A series with fewer
rows than offsets is plotted against the offsets it has, with the missing
range named; more rows than offsets is refused, since the extras cannot be
matched to anything.

- **Measurement** — peak height is the default and usually correct. On 19F
  test data, repeated reference offsets scattered by 0.007 using height
  against 0.090 using the integral: a saturated peak leaves a drifting
  baseline that an integral accumulates without bound, far enough to drive
  I/I₀ negative.
- **Reference from** — offsets at least this far out are averaged to give
  I₀. *Automatic* picks the remote ones. Two or more give a scatter figure,
  which is a direct measure of the noise on every other point — include a
  couple of remote offsets in your list for this reason.
- **Fit deepest dip** — a Lorentzian on the points near the dip, reporting
  its centre in Hz and ppm from the carrier. A dip sitting on the carrier is
  flagged as likely direct saturation rather than exchange. Every other
  minimum clearing the sigma threshold is listed with its significance.
- **Error bars from spectrum noise** — one sigma per point, anchored to the
  spread of the repeated I0 references. A shaded band marks ±1 sigma.
- **Subtract fitted dip** — removes the fitted profile so direct-saturation
  wings stop hiding a smaller dip beside them. For locating a feature, not
  for quantifying it.
- **X range / Y range / Full range** — the direct-saturation dip runs to
  near zero, so the baseline where a bound-state dip would sit is
  compressed. Zooming Y to roughly 0.95–1.02 is usually what makes one
  visible.

### Hunting a bound-state dip

1. Zoom Y to about 0.95–1.02 so the baseline fills the plot.
2. Tick **Subtract fitted dip** to flatten the direct-saturation wings.
3. Read the candidate list. Three sigma is worth following up, five is
   convincing.
4. Confirm it: a real exchange feature persists in a repeat, moves
   predictably with saturation field, and is absent from a partner-free
   control. Direct saturation sits on the carrier; a bound state does not.

If nothing clears the threshold, the limit is noise rather than the tool.
More scans, longer `D18`, or higher concentration lower the sigma figure,
and the candidate list will follow.

This locates dips; it does not measure exchange. Turning a centre and depth
into kex and a populated fraction needs Bloch-McConnell fitting against
several saturation fields, which is not attempted.
