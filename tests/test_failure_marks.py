"""No failure mark outlives a success -- for every kind of node.

0.9.25: experiments 20 and 21 stayed grey after a failed drop although their
data was complete, through Refresh and re-opening. An audit found the same
pattern for data roots and samples, and the opposite for first-time
failures (the mark was wiped at once, so the reason never showed). This file
is the guard: every place that can mark a failure has a test here showing
the mark is VISIBLE, and that a later success CLEARS it. Add one for any new
failure mark.
"""

from __future__ import annotations

import pytest

from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")


def idle(browser) -> bool:
    p = browser._populator
    return not p._inflight and not p._bg_queue and not p._bg_active


@pytest.fixture
def browser(qtbot, tmp_path):
    root = tmp_path / "nmr"
    make_expno(root / "S", 1)
    make_expno(root / "S", 2, dim=2)
    b = DatasetBrowser([DataRoot(name="L2026", path=root)])
    qtbot.addWidget(b)
    node = b.model.root_nodes()[0]
    qtbot.waitUntil(lambda: bool(node.children) and idle(b), timeout=10000)
    sample = node.children[0]
    b._request_populate(b.model._index_for(sample))
    qtbot.waitUntil(lambda: bool(sample.children) and idle(b), timeout=10000)
    yield b, node, sample, root
    b.shutdown()


def settle(qtbot, b):
    qtbot.waitUntil(lambda: idle(b) and not b._populator._refreshing, timeout=10000)


# ------------------------------------------------------------- data root


def test_root_failure_is_visible_and_cleared_by_a_later_search(qtbot, browser):
    b, node, _sample, _root = browser
    b._populator._on_failed(None, node, "share not mounted")
    assert node.failed and node.error == "share not mounted"
    b.refresh_all()
    settle(qtbot, b)
    assert not node.failed and node.error == ""


def test_a_first_time_root_failure_keeps_its_reason(qtbot, tmp_path):
    b = DatasetBrowser([DataRoot(name="L2026", path=tmp_path / "nmr")])
    qtbot.addWidget(b)
    node = b.model.root_nodes()[0]
    node.children = None
    b._populator._on_failed(None, node, "share not mounted")
    assert node.children == [] and node.failed and node.error == "share not mounted"
    b.shutdown()


# ---------------------------------------------------------------- sample


def test_sample_failure_is_cleared_by_a_later_refresh(qtbot, browser):
    b, _node, sample, _root = browser
    b._populator._on_failed(None, sample, "permission denied")
    assert sample.failed
    b.refresh_all()
    settle(qtbot, b)
    assert not sample.failed and sample.error == ""


# ----------------------------------------------------------------- expno


@pytest.mark.parametrize("name", ["1", "2"])
def test_failed_drop_is_cleared_when_the_data_is_there(qtbot, browser, name):
    b, _node, sample, root = browser
    assert b.mark_dataset_failed(str(root / "S" / name), "could not load")
    row = next(c for c in sample.children if c.display_name == name)
    assert row.failed
    b.refresh_all()
    settle(qtbot, b)
    assert not row.failed and row.loadable


def test_metadata_failure_is_cleared_by_a_later_good_read(qtbot, browser):
    """A metadata read happens once per row; one that failed (acqus being
    written, a share hiccup) is retried by the next disk check."""
    b, _node, sample, _root = browser
    row = sample.children[0]
    row.probe_pending = True
    b.model.apply_meta(row, {"error": "acqus unreadable", "has_processed": True})
    assert row.failed
    b.refresh_all()
    settle(qtbot, b)
    assert not row.failed and row.entry.nucleus == "1H"   # read again, successfully


def test_a_failure_with_nothing_on_disk_stays(qtbot, browser):
    b, _node, sample, root = browser
    (root / "S" / "1" / "pdata" / "1" / "1r").unlink()
    b.mark_dataset_failed(str(root / "S" / "1"), "could not load")
    b.refresh_all()
    settle(qtbot, b)
    row = next(c for c in sample.children if c.display_name == "1")
    assert not row.loadable                        # grey, and rightly so


def test_every_failure_mark_in_the_code_is_covered_here():
    """If someone adds a new place that sets `.failed = True`, this fails
    until it is reviewed and a clearing test added above."""
    import re
    from pathlib import Path

    import helspin.ui.dataset_model as dm

    source = Path(dm.__file__).read_text()
    sites = len(re.findall(r"\.failed = True", source))
    assert sites == 3, (
        f"{sites} places now set .failed = True (expected 3: _on_failed, "
        "mark_expno_failed, and fetchMore's synchronous scan). Add a test above "
        "proving the new mark is shown and cleared by a later success, then "
        "update this count.")


def test_a_synchronous_scan_failure_is_cleared_by_refresh(qtbot, tmp_path):
    """fetchMore without a scheduler (the synchronous path)."""
    from helspin.ui.dataset_model import DatasetTreeModel

    root = tmp_path / "later"
    model = DatasetTreeModel([DataRoot(name="R", path=root)])
    node = model.root_nodes()[0]
    import helspin.ui.dataset_model as dm

    real = model._scan_children
    model._scan_children = lambda n: (_ for _ in ()).throw(OSError("not mounted"))
    model.fetchMore(model.index(0, 0))
    assert node.failed and "not mounted" in node.error
    model._scan_children = real
    make_expno(root / "S", 1)
    model.refresh(node)
    assert not node.failed and node.error == ""
    assert dm  # module imported for the count test
