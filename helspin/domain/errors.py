"""Domain errors.

Every failure the UI must present differently gets its own type. The rule from
the handoff: fail loudly on malformed data rather than silently guessing, and
never let a numpy IndexError or UnicodeDecodeError reach the user.
"""


class HelSpinError(Exception):
    """Base for every domain error."""


# --- data -------------------------------------------------------------------


class DatasetNotFound(HelSpinError):
    """Path does not resolve to a Bruker dataset."""


class UnsupportedDimension(HelSpinError):
    """3D or higher, or a dimensionality the caller cannot handle."""


class InvalidAxis(HelSpinError):
    """Axis calibration is inconsistent with its data."""


class EmptySpectrum(HelSpinError):
    """Spectrum has no points, or is all zero where that is fatal."""


class MissingParameter(HelSpinError):
    """A required acqus/procs parameter is absent."""


# --- project ----------------------------------------------------------------


class DimensionalityMismatch(HelSpinError):
    """1D and 2D may never share a block."""


class NucleusMismatch(HelSpinError):
    """Panels showing different nuclei cannot share a ppm link group."""


class SlotNotFound(HelSpinError):
    """Referenced slot is absent from the block."""


class BrokenReference(HelSpinError):
    """A difference operand or dataset reference no longer resolves."""
