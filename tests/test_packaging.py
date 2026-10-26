"""The PyInstaller build: entry point, version inside the bundle, .exe version.

0.9.7's specs produced an app that died on launch (the entry script was
helspin/__main__.py, whose relative imports fail outside the package), and a
bundle has no package metadata, so the version read "0.0.0+unknown". The
Windows version resource was a hand-written file stuck at 0.5.4.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

import helspin

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"


def _support():
    spec = importlib.util.spec_from_file_location(
        "spec_support", PACKAGING / "spec_support.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- version source ---------------------------------------------------------------


def test_read_version_matches_the_installed_package():
    assert _support().read_version(ROOT) == helspin.__version__


def test_read_version_only_reads_the_project_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.other]\n"
        'version = "9.9.9"\n'
        "[project]\n"
        'name = "helspin"\n'
        'version = "1.2.3"  # trailing comment\n'
        "[tool.mypy]\n"
        'python_version = "3.11"\n'
    )
    assert _support().read_version(tmp_path) == "1.2.3"


def test_read_version_without_one_stops_the_build(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    with pytest.raises(SystemExit):
        _support().read_version(tmp_path)


@pytest.mark.parametrize(
    "version, expected",
    [
        ("0.9.8", (0, 9, 8, 0)),
        ("1.0.0rc1", (1, 0, 0, 0)),
        ("2", (2, 0, 0, 0)),
        ("1.2.3.4.5", (1, 2, 3, 4)),
        ("70000.1", (65535, 1, 0, 0)),  # 16-bit fields
        ("a.b", (0, 0, 0, 0)),
    ],
)
def test_version_tuple(version, expected):
    assert _support().version_tuple(version) == expected


def test_build_files_are_written_under_build(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "3.4.5"\n')
    version, version_file, info_file = _support().write_build_files(tmp_path)
    assert version == "3.4.5"
    assert version_file == tmp_path / "build" / "helspin-version" / "VERSION"
    assert version_file.read_text(encoding="utf-8") == "3.4.5\n"
    text = info_file.read_text(encoding="utf-8")
    assert "filevers=(3, 4, 5, 0)" in text
    assert "StringStruct('ProductVersion', '3.4.5')" in text
    # Running twice (a rebuild) overwrites rather than failing.
    _support().write_build_files(tmp_path)


def test_windows_version_file_parses_with_pyinstaller():
    pytest.importorskip("PyInstaller")
    pytest.importorskip("pefile")
    import PyInstaller.compat as compat

    if not hasattr(compat, "win32api"):
        compat.win32api = None  # only needed for writing resources on Windows
    from PyInstaller.utils.win32 import versioninfo

    info_text = _support().windows_version_info("0.9.8")
    path = ROOT / "build" / "test-version-info.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(info_text, encoding="utf-8")
    try:
        info = versioninfo.load_version_info_from_text_file(str(path))
    finally:
        path.unlink()
    ms, ls = info.ffi.fileVersionMS, info.ffi.fileVersionLS
    assert (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF) == (0, 9, 8, 0)
    strings = {s.name: s.val for s in info.kids[0].kids[0].kids}
    assert strings["FileVersion"] == strings["ProductVersion"] == "0.9.8"


# -- the version inside a bundle ----------------------------------------------------


@pytest.mark.parametrize(
    "content, expected",
    [("0.9.8\n", "0.9.8"), ("  \n", None), (None, None), (b"\xff\xfe\x00", None)],
)
def test_bundled_version_file(tmp_path, monkeypatch, content, expected):
    package = tmp_path / "helspin"
    package.mkdir()
    if isinstance(content, str):
        (package / "VERSION").write_text(content, encoding="utf-8")
    elif isinstance(content, bytes):
        (package / "VERSION").write_bytes(content)
    monkeypatch.setattr(helspin, "__file__", str(package / "__init__.py"))
    assert helspin._bundled_version() == expected


def _import_without_metadata(tmp_path, version_text):
    """Import a copy of helspin/__init__.py as a bundle would see it."""
    package = tmp_path / "helspin"
    package.mkdir()
    (package / "__init__.py").write_text(
        (ROOT / "helspin" / "__init__.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if version_text is not None:
        (package / "VERSION").write_text(version_text, encoding="utf-8")
    code = (
        "import importlib.metadata as m\n"
        "def missing(name):\n"
        "    raise m.PackageNotFoundError(name)\n"
        "m.version = missing\n"
        "import helspin\n"
        "print(helspin.__version__)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env=env, cwd=tmp_path, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_without_metadata_the_bundled_version_is_used(tmp_path):
    assert _import_without_metadata(tmp_path, "0.9.8\n") == "0.9.8"


def test_without_metadata_or_file_the_version_is_unknown(tmp_path):
    assert _import_without_metadata(tmp_path, None) == "0.0.0+unknown"


# -- specs and entry point -----------------------------------------------------------


@pytest.mark.parametrize("spec", ["helspin.spec", "helspin_macos.spec"])
def test_specs_use_the_package_safe_entry_and_ship_version(spec):
    text = (PACKAGING / spec).read_text(encoding="utf-8")
    assert '"packaging" / "helspin_entry.py"' in text
    assert '"helspin" / "__main__.py"' not in text
    assert "write_build_files(ROOT)" in text
    assert '(str(VERSION_FILE), "helspin")' in text


def test_windows_spec_uses_the_generated_version_resource():
    text = (PACKAGING / "helspin.spec").read_text(encoding="utf-8")
    assert "version=str(VERSION_INFO)" in text
    assert not (PACKAGING / "version_info.txt").exists()


def test_entry_script_runs_main_inside_the_package():
    """The real check: run the entry script as a top-level script, as
    PyInstaller does, and ask for the version."""
    result = subprocess.run(
        [sys.executable, str(PACKAGING / "helspin_entry.py"), "--version"],
        capture_output=True, text=True, timeout=120,
        env={k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"HelSpin {helspin.__version__}"


def test_running_main_module_as_a_script_is_what_failed(tmp_path):
    """Documents the 0.9.7 failure the entry script avoids."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "helspin" / "__main__.py"), "--version"],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "relative import" in result.stderr
