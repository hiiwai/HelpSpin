"""HelSpin package.

__version__ is read from installed package metadata (which comes from
pyproject.toml at build time) rather than duplicated as a separate string
here -- two copies of a version number is a classic source of "which one do
I believe" bugs the moment either gets bumped without the other.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# The one place the contact address is written. The About dialog, the licence
# text shipped in resources/, and the packaging metadata all read it from
# here, so a future change of address is a one-line edit rather than a hunt
# through documents that would otherwise silently disagree with each other.
#
# A domain address, not a personal mailbox: it can be repointed at a
# different inbox without republishing the application, and every copy
# already installed keeps working.
CONTACT_EMAIL = "iwai@ligsciss.com"


def _bundled_version() -> str | None:
    """The VERSION file a frozen (PyInstaller) build ships beside this module.

    A bundle carries no package metadata, so without this the built app's
    title and About said "0.0.0+unknown". The spec writes the file from
    pyproject at build time; a source checkout has none.
    """
    try:
        text = (Path(__file__).resolve().parent / "VERSION").read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeDecodeError):
        return None
    text = text.strip()
    return text or None


try:
    __version__ = version("helspin")
except PackageNotFoundError:
    # A frozen build, or running from source without an install (e.g.
    # `python -m helspin` from a checkout that was never pip-installed). Not
    # an error condition; just no installed-metadata version to report.
    __version__ = _bundled_version() or "0.0.0+unknown"
