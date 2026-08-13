"""Architectural gates.

The handoff specifies a grep for forbidden imports in domain/. Grep matches
prose in docstrings too, so this walks the AST instead and inspects real import
statements only.
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "helspin"

FORBIDDEN_IN_DOMAIN = {"PySide6", "nmrglue", "matplotlib", "scipy"}
FORBIDDEN_IN_SERVICES = {"PySide6", "nmrglue", "matplotlib"}


def imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by a file."""
    tree = ast.parse(path.read_text(), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def python_files(subpackage: str) -> list[Path]:
    directory = PACKAGE / subpackage
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", python_files("domain"), ids=lambda p: p.name)
def test_domain_imports_numpy_and_stdlib_only(path):
    """domain/ must stay testable without a GUI or any file-format library."""
    offending = imported_roots(path) & FORBIDDEN_IN_DOMAIN
    assert not offending, f"{path.name} imports {sorted(offending)}"


@pytest.mark.parametrize("path", python_files("services"), ids=lambda p: p.name)
def test_services_depend_on_ports_not_implementations(path):
    offending = imported_roots(path) & FORBIDDEN_IN_SERVICES
    assert not offending, f"{path.name} imports {sorted(offending)}"


def test_domain_does_not_import_infrastructure():
    """Dependencies point inward only: domain never reaches outward.

    AST-based, like the gates above -- a substring search matches prose in
    docstrings and produces false failures.
    """
    for path in python_files("domain"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "infrastructure" not in node.module, path.name
                assert node.module.split(".")[0] not in {"ui", "app"}, path.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "infrastructure" not in alias.name, path.name


def test_no_matplotlib_loc_best_anywhere():
    """loc='best' relocates the legend when data changes, so a series of
    figures comes out inconsistent. Banned outright."""
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        for bad in ('loc="best"', "loc='best'"):
            assert bad not in text, f"{path.name} uses matplotlib {bad}"


def test_no_bbox_inches_tight_anywhere():
    """bbox_inches='tight' silently changes output dimensions, so the exported
    figure stops matching what was designed."""
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text()
        assert "bbox_inches" not in text, f"{path.name} uses bbox_inches"


def test_no_browser_storage_or_localstorage():
    """Not applicable to Python, but guards against a copied web snippet."""
    for path in PACKAGE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        assert "localStorage" not in path.read_text(), path.name
