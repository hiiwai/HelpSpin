# Installing HelSpin

**Status of this build:** dataset browser only — you can point it at Bruker
data, browse, filter, and drag datasets. The comparison canvas (dropping
spectra, overlays, differences, export) is not implemented yet; see the
README's "what is next" section. This zip is a developer/preview build, not a
packaged end-user installer (no `.app` / `.exe`, no code signing).

---

## Requirements

- **Python 3.11 or newer**, on Windows 11 or macOS 13+ (Linux also works if
  you're testing this on a dev machine).
- No other software needed — everything else (Qt, matplotlib, nmrglue) installs
  automatically via pip.

Check your Python version first:

```
python3 --version        # macOS / Linux
py --version              # Windows (or "python --version")
```

If it's below 3.11, install a newer Python from https://python.org before
continuing.

---

## Install

### 1. Unzip

Unzip `helspin.zip` anywhere convenient, e.g. your home folder or Desktop.
You should end up with a folder containing `pyproject.toml`, `README.md`, and
a `helspin/` subfolder.

### 2. Open a terminal there

- **Windows:** open the unzipped folder in Explorer, then File → Open
  Windows Terminal (or hold Shift, right-click empty space, "Open PowerShell
  window here").
- **macOS:** open Terminal, then `cd` into the folder, e.g.:
  ```
  cd ~/Desktop/helspin
  ```

### 3. Create a virtual environment

This keeps HelSpin's dependencies separate from anything else on your
system — recommended, not optional, especially on macOS where the system
Python is locked down.

**macOS / Linux:**
```
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**
```
py -m venv .venv
.venv\Scripts\Activate.ps1
```
If PowerShell refuses to run the activation script, run this once first:
```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

You'll know it worked because your prompt now starts with `(.venv)`.

#### Using conda instead

If you already use conda, let it provide the interpreter only and hand
everything else to pip — installing PySide6 via *both* conda and pip in the
same environment causes duplicate Qt installations and hard-to-diagnose
plugin errors:

```
conda create -n helspin python=3.11
conda activate helspin
```

Then continue with step 4 below exactly as written. This also means the
identical commands work unchanged if you move to Windows later — nothing
about this project's dependencies is OS-specific.

### 4. Install

```
pip install --upgrade pip
pip install .
```

This downloads and installs PySide6 (Qt), matplotlib, nmrglue, numpy, scipy,
and Pillow, then installs HelSpin itself. It takes a minute or two —
PySide6 alone is a fairly large download (a few hundred MB).

If you plan to work on the code rather than just run it, install in
editable mode with the dev tools instead:
```
pip install -e ".[dev]"
```

---

## Run it

With the virtual environment still active (prompt shows `(.venv)`):

```
helspin
```

A window titled **HelSpin** should open. The status bar will say
*"Use File → Add Data Root… to point HelSpin at your Bruker data."*

Use **File → Add Data Root…**, pick the folder that contains your sample
directories (the one holding things like `260728_SampleB_25uM_FT2`), and
give it a short name (e.g. "600 MHz"). HelSpin remembers this between
runs — you only do this once per data root.

Expand a sample in the tree to see its experiments (expno, pulse program,
nucleus, dimension). You can select one or more expno rows and drag them —
there's just nowhere to drop them onto yet in this build.

**To run it again later:** open a terminal in the same folder and:
```
source .venv/bin/activate      # macOS/Linux
.venv\Scripts\Activate.ps1     # Windows
helspin
```

---

## Running the test suite

To confirm everything installed correctly (325+ tests, a few seconds):

```
pip install -e ".[dev]"
pytest
```

All tests should pass. If PySide6-related tests fail with a display error,
set `QT_QPA_PLATFORM=offscreen` first — the tests don't need a real display,
but some environments (headless servers, some CI) need to be told that
explicitly:
```
export QT_QPA_PLATFORM=offscreen     # macOS/Linux
$env:QT_QPA_PLATFORM="offscreen"     # Windows PowerShell
pytest
```

---

## Checking your version

```
helspin --version
```

Prints something like `HelSpin 0.1.0.dev0` and exits immediately — no
window opens. Useful for confirming what actually got installed, and it
works even if the GUI itself won't start (see below), since it never touches
Qt at all. Cross-check against `pip show helspin` if you want a second
opinion; the two should always agree.

## Troubleshooting

**The terminal doesn't return to the prompt after running `helspin`**
This is normal — a GUI application blocks its terminal until you close the
window; that's what `Ctrl+C` or closing the window is for, not a sign of a
hang. The real question is whether a window ever appeared. Before assuming
it's frozen:

1. **Check for the window somewhere unexpected.** Press `Cmd+Tab` to see if
   "helspin" or "Python" appears in the app switcher, and check Mission
   Control for another Space. On macOS, a window opened by a plain Python
   script (rather than a signed `.app`) can appear *behind* every other
   window with no visible signal that it happened — this is the single most
   common cause of "it hangs with nothing showing." Recent builds call
   `raise_()`/`activateWindow()` to force it to the front automatically; if
   you're on an older build, this is worth an update.
2. **Check whether it's actually frozen** or just running normally: open
   Activity Monitor and look for a `python` or `helspin` process. Near-zero
   CPU means it's sitting idle in its event loop (normal, waiting for you to
   close a window you can't see); pegged at 100% for a long time suggests a
   genuine hang.
3. **Check your shell for nested environments.** A prompt like
   `(helspin) ((.venv) )` means a venv was activated *inside* an already
   active conda environment. Exit both and activate only one:
   ```
   deactivate            # if a venv is active
   conda deactivate
   conda activate helspin
   which python
   which helspin
   ```
   `which helspin` should point into the same environment as `which
   python`; if it doesn't, `pip install` and the `helspin` command are
   talking to two different environments.

**The command runs but no window appears — no error at all**
This is almost always one specific cause: the environment variable
`QT_QPA_PLATFORM` is set to `offscreen` somewhere in your shell. That value
tells Qt to render into memory instead of onto your screen — deliberately
useful for automated testing, silently broken for actual use. Check:
```
echo $QT_QPA_PLATFORM
```
If that prints `offscreen`, run `unset QT_QPA_PLATFORM` and try again. If it
comes back after opening a new terminal, it's being set in a startup file —
check `~/.zshrc`, `~/.bash_profile`, or (if you're using conda) that
environment's `etc/conda/activate.d/` scripts, and remove the line setting it.
`helspin --version` will still print correctly even while this is broken,
since it doesn't initialize Qt at all — that's by design, precisely so you
have one command that works regardless of what's wrong with the GUI half.

**"externally-managed-environment" error on `pip install`**
You skipped the virtual environment step, or it isn't activated. Go back to
step 3 — your terminal prompt should show `(.venv)` before you run `pip
install`.

**The window doesn't appear / nothing happens**
Some remote-desktop or SSH setups don't have a display available. HelSpin
needs a real display (or, for testing, the offscreen mode above, which won't
show you an actual window). Try running it on a local machine first.

**`pip install .` fails while building PySide6**
This means pip is trying to compile PySide6 from source rather than using a
prebuilt wheel — usually because your Python version or OS/architecture isn't
one PySide6 ships a wheel for. Check https://pypi.org/project/PySide6/ for
supported platforms, or try a different Python 3.11–3.13 installation.

**A dataset shows `—` instead of its pulse program / nucleus**
That expno's `acqus` file couldn't be parsed (missing, corrupted, or an
unusual encoding). Hover the row for a tooltip with the specific error; other
datasets in the same sample are unaffected.

---

## Uninstalling

```
pip uninstall helspin
```
Then just delete the unzipped folder. Your configured data roots live in
your OS's standard settings location (registry on Windows, `~/Library/
Preferences` on macOS) under the name "HelSpin" — remove them the same way
you'd remove any application's settings, if you want a completely clean
slate.
