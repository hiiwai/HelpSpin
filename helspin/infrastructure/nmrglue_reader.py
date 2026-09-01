"""nmrglue-backed dataset reader.

Only nmrglue lives in this module (plus numpy for read_1d/read_2d later) --
everything else, including the browser and the drag/drop payload, depends on
domain.ports.SpectrumReader, never on this file directly. Swapping or
vendoring nmrglue means editing here and nowhere else.

Scope for this slice: `probe()` only, which is all the browser needs. It reads
acqus as a text parse -- cheap, no array load -- and is what populates browser
rows and drag payloads. `read_1d`/`read_2d` (the actual FID/pdata array load)
belong to the rendering milestone and are stubbed here with a clear marker
rather than guessed at.
"""

from __future__ import annotations

from pathlib import Path

from ..domain.errors import (
    DatasetNotFound,
    EmptySpectrum,
    MissingParameter,
    UnsupportedDimension,
)
from ..domain.metadata import read_title, resolve_barcode, strip_jcamp
from ..domain.paths import is_expno, procnos_in
from ..domain.ports import DataRoot, DatasetInfo
from ..domain.project import Dimensionality

_JCAMP_ENCODINGS = ("utf-8", "latin-1")

# Everything a browser row, a drag payload or a label can need out of acqus.
# Anything whose key contains BARCODE is kept too (resolve_barcode scans for
# it), as are the USERA slots it falls back to.
_ROW_KEYS = frozenset(
    {
        "NUC1", "SOLVENT", "PULPROG", "NS", "RG", "HOLDER", "DATE", "PARMODE",
        "USERA1", "USERA2", "USERA3", "USERA4", "USERA5", "INSTRUM", "PROBHD",
    }
)


def read_acqus_fast(path: Path) -> dict:
    """acqus, parsed for the handful of keys a browser row needs.

    nmrglue's read_jcamp is thorough: it parses every parameter in the file,
    including the long numeric arrays (P, D, SP, CPDPRG...), converting each
    element. That is the right behaviour for loading a spectrum and pure waste
    for filling a table cell -- measured at ~3 ms per file against ~0.4 ms
    here, which is 20+ seconds of pure CPU across a few thousand experiments,
    on top of the read itself.

    The parse is a single read and a line scan. Array parameters continue on
    following lines that do not start with '##', so skipping non-'##$' lines
    drops their payloads without any state machine. Values keep their JCAMP
    angle brackets; strip_jcamp removes them, exactly as for the full parse.

    Encoding is handled the same way as read_acqus: acqus files are ASCII in
    practice but not guaranteed, and latin-1 accepts any byte sequence, so
    this never raises on encoding grounds.
    """
    with open(path, "rb") as handle:
        raw = handle.read()

    text: str | None = None
    for encoding in _JCAMP_ENCODINGS:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:                                  # pragma: no cover
        raise DatasetNotFound(f"could not decode {path}")

    out: dict = {}
    for line in text.splitlines():
        if not line.startswith("##$"):
            continue
        key, separator, value = line[3:].partition("=")
        if not separator:
            continue
        if key in _ROW_KEYS or "BARCODE" in key:
            out[key] = value.strip()
    return out


def _as_int(value, default: int = 1) -> int:
    """acqus numbers arrive as int (nmrglue) or str (the fast parse), and a
    field like NS is occasionally written '16.0'. int('16.0') raises, so every
    numeric read goes through float first."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _nmrglue():
    """Import nmrglue on first use, not at start-up.

    nmrglue costs ~0.8 s to import (it pulls in scipy), and on a Mac with a
    conda environment on a spinning or networked home directory it is several
    times that. That was being paid before the window appeared, on EVERY
    launch, even though nothing on the browsing path needs it any more:
    browser rows are filled by read_acqus_fast, which is pure stdlib. Only
    actually loading a spectrum needs nmrglue, and by then the user has a
    window to look at.
    """
    import nmrglue as ng

    return ng


def read_acqus(path: Path) -> dict:
    """acqus/acqu2s/acqu3s as a dict, tolerant of encoding.

    nmrglue's read_jcamp defaults to the system locale encoding and raises
    UnicodeDecodeError on a non-ASCII byte outside that encoding -- the same
    trap as the title file (domain.metadata.read_title), just one level
    lower in the stack. Retried under latin-1, which accepts any byte
    sequence, so this never raises on encoding grounds.
    """
    ng = _nmrglue()
    last_error: Exception | None = None
    for encoding in _JCAMP_ENCODINGS:
        try:
            return ng.bruker.read_jcamp(str(path), encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise DatasetNotFound(f"could not decode {path}: {last_error}")


def _dimensionality(expno: Path) -> Dimensionality:
    """1D vs 2D from the files actually present, not from a guess.

    A ser file plus acqu2s is 2D; a bare fid is 1D. Higher dimensionality is
    rejected explicitly rather than silently mis-plotted.
    """
    if (expno / "acqu3s").is_file():
        raise UnsupportedDimension(f"{expno} is 3D or higher, not supported")
    if (expno / "ser").is_file() and (expno / "acqu2s").is_file():
        return Dimensionality.TWO_D
    if (expno / "fid").is_file():
        return Dimensionality.ONE_D

    # No raw data. That is common and legitimate: raw FIDs are often deleted
    # or never copied off the spectrometer, leaving only the processed
    # spectrum. Since the processed data is what gets displayed anyway, the
    # dimensionality is taken from it. Without this, a processed-only 2D
    # dataset was listed in the browser (it has 2rr) but then refused to
    # probe, so it could never be opened.
    from ..domain.paths import procnos_in

    for procno in procnos_in(expno):
        if (procno / "3rrr").is_file() or (procno / "4rrrr").is_file():
            raise UnsupportedDimension(
                f"{expno} is 3D or higher, not supported"
            )
        if (procno / "2rr").is_file():
            return Dimensionality.TWO_D
        if (procno / "1r").is_file():
            return Dimensionality.ONE_D

    raise DatasetNotFound(
        f"{expno} has no raw (fid/ser) and no processed (1r/2rr) data"
    )


def _dimensionality_from_flags(structure) -> Dimensionality | None:
    """Dimensionality from directory flags already gathered by the index.

    ``structure`` is anything exposing has_ser/has_acqu2s/has_fid/has_acqu3s
    (core.dataset_index.ExpnoEntry does) -- so the same listing that told the
    browser the experiment exists also tells it what shape the data is, with
    no further filesystem access at all. That is the difference between four
    stat round trips per row and none.

    None means "these flags do not settle it" (a processed-only dataset whose
    raw fid/ser were deleted), and the caller falls back to PARMODE.
    """
    if structure is None:
        return None
    if getattr(structure, "has_acqu3s", False):
        raise UnsupportedDimension("3D or higher is not supported")
    if getattr(structure, "has_ser", False) and getattr(structure, "has_acqu2s", False):
        return Dimensionality.TWO_D
    if getattr(structure, "has_fid", False):
        return Dimensionality.ONE_D
    return None


def _dimensionality_from_parmode(acqus: dict) -> Dimensionality | None:
    """Bruker records the experiment's dimensionality in acqus as PARMODE:
    0 = 1D, 1 = 2D, 2+ = 3D and up. Reading it costs nothing extra because
    acqus has already been read for PULPROG, and it is the one source that
    still works for a processed-only dataset."""
    if "PARMODE" not in acqus:
        return None
    value = acqus.get("PARMODE")
    try:
        parmode = int(float(str(value).strip("<> ")))
    except (TypeError, ValueError):
        return None
    if parmode <= 0:
        return Dimensionality.ONE_D
    if parmode == 1:
        return Dimensionality.TWO_D
    raise UnsupportedDimension(f"PARMODE {parmode} is 3D or higher, not supported")


def _epoch_to_date(value) -> str:
    """acqus DATE is a Unix epoch integer. Best-effort only -- used for
    browser sorting, not for anything safety-critical, so any failure just
    yields an empty string rather than propagating."""
    try:
        import datetime

        return datetime.datetime.fromtimestamp(int(value)).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


class NmrglueReader:
    """The only SpectrumReader implementation in scope so far.

    Satisfies domain.ports.SpectrumReader structurally (a Protocol, so no
    explicit inheritance is required).
    """

    def can_read(self, path: Path) -> bool:
        return is_expno(path)

    def probe(
        self,
        path: Path,
        procno: int = 1,
        data_root: DataRoot | None = None,
    ) -> DatasetInfo:
        """Cheap metadata read for a browser row. Never loads array data."""
        if not is_expno(path):
            raise DatasetNotFound(f"{path} is not a Bruker expno")

        acqus = read_acqus(path / "acqus")
        dimensionality = _dimensionality(path)

        title_path = path / "pdata" / str(procno) / "title"
        title = read_title(title_path)

        return self._build_info(
            path, procno, acqus, dimensionality, title, data_root
        )

    def probe_row(
        self,
        path: Path,
        procno: int = 1,
        data_root: DataRoot | None = None,
        structure=None,
    ) -> DatasetInfo:
        """probe() for a browser row: ONE file read, no stat calls at all.

        The difference matters entirely because of network shares. probe()
        costs nine round trips per row -- two to confirm the directory is an
        expno, one to read acqus, up to four to work out the dimensionality
        from which raw files exist, one for the title, and more if it has to
        look inside pdata. At 20 ms a round trip that is ~180 ms per row, and
        a sample with forty experiments spends seconds filling in its columns.

        This version costs one read, because everything else is already known:

        * the caller only asks about rows the index built from real directory
          listings, so re-confirming that acqus exists is pure superstition;
        * ``structure`` carries the file flags from that same listing, so
          dimensionality is free, with PARMODE inside acqus as the fallback
          for processed-only datasets;
        * the title is not shown in any browser column, so reading it is a
          round trip spent on nothing. (Loading a spectrum still uses probe(),
          which reads it.)

        The returned DatasetInfo carries the PARTIAL acqus dict from
        read_acqus_fast: enough for every browser column, the drag payload and
        barcode resolution, and explicitly not a substitute for the full parse
        that read_1d/read_2d do.
        """
        try:
            acqus = read_acqus_fast(Path(path) / "acqus")
        except OSError as exc:
            raise DatasetNotFound(f"cannot read acqus under {path}: {exc}") from exc

        dimensionality = _dimensionality_from_flags(structure)
        if dimensionality is None:
            dimensionality = _dimensionality_from_parmode(acqus)
        if dimensionality is None:
            # Nothing cheap settled it: fall back to the stat-based check,
            # which is the only remaining source and still bounded.
            dimensionality = _dimensionality(Path(path))

        return self._build_info(
            Path(path), procno, acqus, dimensionality, None, data_root
        )

    def _build_info(
        self,
        path: Path,
        procno: int,
        acqus: dict,
        dimensionality: Dimensionality,
        title: str | None,
        data_root: DataRoot | None,
    ) -> DatasetInfo:
        """The one place DatasetInfo is assembled, so the full and the fast
        probe cannot drift apart in what they report."""
        configured_key = data_root.barcode_key if data_root else None
        try:
            expno = int(path.name)
        except ValueError:
            expno = 0       # not an integer-named directory: reported, not fatal
        return DatasetInfo(
            path=path,
            expno=expno,
            procno=procno,
            dimensionality=dimensionality,
            nucleus=strip_jcamp(acqus.get("NUC1")) or "",
            pulse_program=strip_jcamp(acqus.get("PULPROG")) or "",
            solvent=strip_jcamp(acqus.get("SOLVENT")) or "",
            date=_epoch_to_date(acqus.get("DATE")),
            ns=_as_int(acqus.get("NS", 1), 1),
            rg=_as_float(acqus.get("RG", 1.0), 1.0),
            title=title,
            holder=strip_jcamp(acqus.get("HOLDER")),
            barcode=resolve_barcode(acqus, configured_key),
            acqus=acqus,
        )

    def read_auto(self, path: Path, procno: int = 1):
        """Load a spectrum whose dimensionality the caller does not know.

        Returns (1, Spectrum1D) or (2, Spectrum2D). Dropping a row whose
        metadata had not been read yet used to be impossible -- the drag
        payload simply omitted it -- so this is the path that makes a drag
        work the instant a row appears rather than a second or two later.
        """
        dimensionality = _dimensionality(Path(path))
        if dimensionality is Dimensionality.TWO_D:
            return 2, self.read_2d(path, procno)
        return 1, self.read_1d(path, procno)

    def probe_procnos(self, expno: Path) -> list[int]:
        """Procnos available for an expno, for the (rare) case of >1."""
        return [int(p.name) for p in procnos_in(expno)]

    def read_1d(self, path: Path, procno: int = 1):
        """Load a processed 1D spectrum from pdata/<procno>.

        Reads the PROCESSED data (pdata), not the raw FID: that is what
        TopSpin displays and what a comparison figure should show. nmrglue's
        guess_udic + uc_from_udic give the ppm calibration, from which the
        domain AxisCalibration is rebuilt so all downstream ppm maths stays in
        the domain layer rather than depending on nmrglue's converter.
        """
        import nmrglue as ng
        import numpy as np

        from ..domain.spectrum import AxisCalibration, Spectrum1D

        pdata_dir = Path(path) / "pdata" / str(procno)
        if not pdata_dir.is_dir():
            raise DatasetNotFound(f"no pdata/{procno} under {path}")

        dic, data = ng.bruker.read_pdata(str(pdata_dir))
        data = np.asarray(data, dtype=np.float64)
        if data.ndim != 1:
            raise UnsupportedDimension(
                f"expected 1D processed data, got {data.ndim}D at {pdata_dir}"
            )
        if data.size == 0:
            raise EmptySpectrum(f"empty processed data at {pdata_dir}")

        udic = ng.bruker.guess_udic(dic, data)
        u0 = udic[0]
        sw_hz = float(u0.get("sw") or 0.0)
        obs_mhz = float(u0.get("obs") or 0.0)
        car_hz = float(u0.get("car") or 0.0)
        nucleus = str(u0.get("label") or "")

        if sw_hz <= 0 or obs_mhz <= 0:
            raise MissingParameter(
                f"cannot calibrate ppm axis for {pdata_dir}: "
                f"sw={sw_hz}, obs={obs_mhz}"
            )

        acqus_values = read_acqus(Path(path) / "acqus")
        ns = int(float(acqus_values.get("NS", 1) or 1))
        rg = float(acqus_values.get("RG", 1.0) or 1.0)

        axis = AxisCalibration(
            size=data.size,
            sw_hz=sw_hz,
            obs_mhz=obs_mhz,
            car_hz=car_hz,
            nucleus=nucleus,
        )
        return Spectrum1D(real=data, axis=axis, ns=ns, rg=rg)

    def read_2d(self, path: Path, procno: int = 1):
        """Load a processed 2D spectrum from pdata/<procno>."""
        import nmrglue as ng
        import numpy as np

        from ..domain.spectrum import AxisCalibration, Spectrum2D

        pdata_dir = Path(path) / "pdata" / str(procno)
        if not pdata_dir.is_dir():
            raise DatasetNotFound(f"no pdata/{procno} under {path}")

        dic, data = ng.bruker.read_pdata(str(pdata_dir))
        data = np.asarray(data, dtype=np.float64)
        if data.ndim != 2:
            raise UnsupportedDimension(
                f"expected 2D processed data, got {data.ndim}D at {pdata_dir}"
            )
        if data.size == 0:
            raise EmptySpectrum(f"empty processed data at {pdata_dir}")

        udic = ng.bruker.guess_udic(dic, data)

        def axis_for(dim: int, size: int) -> AxisCalibration:
            u = udic[dim]
            sw_hz = float(u.get("sw") or 0.0)
            obs_mhz = float(u.get("obs") or 0.0)
            if sw_hz <= 0 or obs_mhz <= 0:
                raise MissingParameter(
                    f"cannot calibrate ppm axis (dim {dim}) for {pdata_dir}"
                )
            return AxisCalibration(
                size=size,
                sw_hz=sw_hz,
                obs_mhz=obs_mhz,
                car_hz=float(u.get("car") or 0.0),
                nucleus=str(u.get("label") or ""),
            )

        # udic dim 0 is the INDIRECT (F1) dimension, dim 1 is direct (F2);
        # data is indexed [f1, f2], so sizes line up with that order.
        axis_f1 = axis_for(0, data.shape[0])
        axis_f2 = axis_for(1, data.shape[1])

        acqus_values = read_acqus(Path(path) / "acqus")
        ns = int(float(acqus_values.get("NS", 1) or 1))
        rg = float(acqus_values.get("RG", 1.0) or 1.0)

        return Spectrum2D(
            real=data, axis_f1=axis_f1, axis_f2=axis_f2, ns=ns, rg=rg
        )
