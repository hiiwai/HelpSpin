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

import nmrglue as ng

from ..domain.errors import (
    DatasetNotFound,
    EmptySpectrum,
    MissingParameter,
    UnsupportedDimension,
)
from ..domain.metadata import read_title, resolve_barcode, strip_jcamp
from ..domain.paths import is_expno, procnos_in
from ..domain.project import Dimensionality
from ..domain.ports import DataRoot, DatasetInfo

_JCAMP_ENCODINGS = ("utf-8", "latin-1")


def read_acqus(path: Path) -> dict:
    """acqus/acqu2s/acqu3s as a dict, tolerant of encoding.

    nmrglue's read_jcamp defaults to the system locale encoding and raises
    UnicodeDecodeError on a non-ASCII byte outside that encoding -- the same
    trap as the title file (domain.metadata.read_title), just one level
    lower in the stack. Retried under latin-1, which accepts any byte
    sequence, so this never raises on encoding grounds.
    """
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

        configured_key = data_root.barcode_key if data_root else None
        barcode = resolve_barcode(acqus, configured_key)

        title_path = path / "pdata" / str(procno) / "title"
        title = read_title(title_path)

        return DatasetInfo(
            path=path,
            expno=int(path.name),
            procno=procno,
            dimensionality=dimensionality,
            nucleus=strip_jcamp(acqus.get("NUC1")) or "",
            pulse_program=strip_jcamp(acqus.get("PULPROG")) or "",
            solvent=strip_jcamp(acqus.get("SOLVENT")) or "",
            date=_epoch_to_date(acqus.get("DATE")),
            ns=int(acqus.get("NS", 1) or 1),
            rg=float(acqus.get("RG", 1.0) or 1.0),
            title=title,
            holder=strip_jcamp(acqus.get("HOLDER")),
            barcode=barcode,
            acqus=acqus,
        )

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
        import numpy as np
        import nmrglue as ng

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
        import numpy as np
        import nmrglue as ng

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
