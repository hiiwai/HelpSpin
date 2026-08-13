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
