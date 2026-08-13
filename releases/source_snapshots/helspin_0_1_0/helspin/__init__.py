"""HelSpin package.

__version__ is read from installed package metadata (which comes from
pyproject.toml at build time) rather than duplicated as a separate string
here -- two copies of a version number is a classic source of "which one do
I believe" bugs the moment either gets bumped without the other.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("helspin")
except PackageNotFoundError:
    # Running from source without an install (e.g. `python -m helspin`
    # from a checkout that was never pip-installed). Not an error condition;
    # just means there's no installed-metadata version to report.
    __version__ = "0.0.0+unknown"
