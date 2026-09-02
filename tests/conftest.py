import pytest

"""Test configuration.

Forces the offscreen Qt platform so the suite runs without a display (CI, this
sandbox). Must be set before any PySide6 import happens anywhere in the
process, hence doing it here rather than in an individual test module.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _stop_background_workers():
    """Stop every DatasetPopulator when a test ends.

    A browser built in a test keeps worker threads reading the filesystem
    after the test returns. Left running, they are still holding Node objects
    when the collector frees the whole graph, and the process segfaults inside
    an unrelated later test -- which is exactly how this fixture came to
    exist. The application does the same thing at close; this is the test
    harness honouring the same contract.
    """
    yield
    from helspin.ui.dataset_model import shutdown_all_populators

    shutdown_all_populators()


@pytest.fixture(autouse=True)
def _isolated_index_cache(tmp_path_factory, monkeypatch):
    """Keep the dataset index cache out of the developer's real cache dir.

    Without this every test run would write index files to ~/.cache/helspin
    and, worse, could read a stale one and pass for the wrong reason.
    """
    cache = tmp_path_factory.mktemp("helspin-index-cache")
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(cache))


@pytest.fixture(autouse=True, scope="session")
def _isolate_qsettings(tmp_path_factory):
    """Point QSettings at a throwaway directory for the whole run.

    Without this the suite reads and writes the REAL settings of whoever runs
    it. That is bad twice over: a test can quietly overwrite a developer's own
    data roots and preferences, and state left behind by one run leaks into
    the next, so a test that passes on a clean machine fails on the second
    run. Exactly that happened when recent ppm ranges became persistent --
    they accumulated across runs until the assertions broke.

    Session-scoped and autouse: it has to be in place before the first
    QSettings call, and no test should have to remember to ask for it.
    """
    from PySide6.QtCore import QSettings

    directory = tmp_path_factory.mktemp("qsettings")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(directory))
    yield


@pytest.fixture(autouse=True)
def _clean_qsettings(_isolate_qsettings):
    """Start every test from empty settings.

    Redirecting QSettings to a temp directory stops the suite touching the
    real one, but the whole run still shares that directory -- so a test that
    persists something changes what the NEXT test sees, and the order tests
    happen to run in starts deciding whether they pass. Clearing per test is
    what makes each one independent.
    """
    from PySide6.QtCore import QSettings

    QSettings().clear()
    QSettings("HelSpin", "HelSpin").clear()
    yield
