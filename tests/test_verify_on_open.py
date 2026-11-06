"""0.9.23: an opened sample always shows what is on disk.

Reported (`helspin --check` on the reporter's data): experiment 10 had its
2rr but the explorer greyed it out; 1 and 2 had no 1r any more but were
offered. The explorer answered from its saved index, and nothing told it the
data had changed: processing writes inside the experiment folder, and
`rsync -a` keeps the old folder dates, so no date check can notice.
Now opening a sample shows the saved answer at once and then checks the
experiments against the disk, correcting the rows.
"""

from __future__ import annotations

import os
import time

import pytest

from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")


def idle(browser) -> bool:
    p = browser._populator
    return not p._inflight and not p._bg_queue and not p._bg_active


def first_session(qtbot, root):
    for sample in root.iterdir():
        like_rsync_a(sample)
    browser = DatasetBrowser([DataRoot(name="L2026", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    qtbot.waitUntil(lambda: node.children is not None and idle(browser),
                    timeout=10000)
    browser.shutdown()


def open_sample(qtbot, root):
    browser = DatasetBrowser([DataRoot(name="L2026", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    qtbot.waitUntil(lambda: bool(node.children) and idle(browser), timeout=10000)
    sample = node.children[0]
    browser._request_populate(browser.model._index_for(sample))
    qtbot.waitUntil(lambda: bool(sample.children) and idle(browser), timeout=10000)
    return browser, sample


OLD = time.time() - 60 * 24 * 3600


def like_rsync_a(folder):
    """rsync -a gives copied folders their ORIGINAL dates: weeks old, so
    neither the start-up re-check nor any date comparison sees a change."""
    os.utime(folder, (OLD, OLD))


def rows(sample):
    return {c.display_name: c.loadable for c in sample.children}


def test_processed_later_is_no_longer_grey(qtbot, tmp_path):
    root = tmp_path / "nmr"
    sample = root / "261008_2mMFoxA1_Me1"
    make_expno(sample, 30, dim=2)
    ten = make_expno(sample, 10, dim=2)
    (ten / "pdata" / "1" / "2rr").unlink()           # not processed yet
    first_session(qtbot, root)
    (ten / "pdata" / "1" / "2rr").write_bytes(b"\0" * 8)   # processed later
    like_rsync_a(sample)
    browser, node = open_sample(qtbot, root)
    assert rows(node) == {"10": True, "30": True}
    browser.shutdown()
    assert di.load_index(root).find(str(sample)).expnos[0].has_processed is True


def test_deleted_data_is_no_longer_offered(qtbot, tmp_path):
    root = tmp_path / "nmr"
    sample = root / "S"
    one = make_expno(sample, 1)
    make_expno(sample, 2)
    first_session(qtbot, root)
    (one / "pdata" / "1" / "1r").unlink()             # processed data removed
    like_rsync_a(sample)
    browser, node = open_sample(qtbot, root)
    assert rows(node) == {"1": False, "2": True}
    browser.shutdown()


def test_an_experiment_added_later_appears_on_opening(qtbot, tmp_path):
    root = tmp_path / "nmr"
    sample = root / "S"
    make_expno(sample, 1)
    first_session(qtbot, root)
    make_expno(sample, 5)
    like_rsync_a(sample)
    browser, node = open_sample(qtbot, root)
    assert set(rows(node)) == {"1", "5"}
    browser.shutdown()


def test_the_rows_are_repainted(qtbot, tmp_path):
    root = tmp_path / "nmr"
    sample = root / "S"
    one = make_expno(sample, 1)
    first_session(qtbot, root)
    (one / "pdata" / "1" / "1r").unlink()
    browser = DatasetBrowser([DataRoot(name="L2026", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    qtbot.waitUntil(lambda: bool(node.children) and idle(browser), timeout=10000)
    changed = []
    browser.model.dataChanged.connect(lambda a, b, *r: changed.append((a, b)))
    s = node.children[0]
    browser._request_populate(browser.model._index_for(s))
    qtbot.waitUntil(lambda: bool(s.children) and idle(browser) and bool(changed),
                    timeout=10000)
    assert any(a.parent().internalPointer() is s for a, _b in changed)
    browser.shutdown()


def test_an_unreadable_answer_keeps_what_was_known(qtbot, tmp_path, monkeypatch):
    root = tmp_path / "nmr"
    make_expno(root / "S", 1)
    first_session(qtbot, root)
    monkeypatch.setattr(di, "inspect_processed",
                        lambda *a, **k: (None, "could not read"))
    browser, node = open_sample(qtbot, root)
    assert rows(node) == {"1": True}
    browser.shutdown()


def test_opening_again_soon_does_not_read_the_disk_again(qtbot, tmp_path, monkeypatch):
    root = tmp_path / "nmr"
    make_expno(root / "S", 1)
    first_session(qtbot, root)
    browser, node = open_sample(qtbot, root)
    scans = []
    real = di.scan_expnos
    monkeypatch.setattr(di, "scan_expnos", lambda p: scans.append(p) or real(p))
    browser._populator.verify_sample(node)            # within 30 s: skipped
    assert scans == []
    browser._populator.verify_sample(node, force=True)
    qtbot.waitUntil(lambda: idle(browser), timeout=5000)
    assert len(scans) == 1
    browser.shutdown()


def test_a_cut_short_2rr_is_explained_not_drawn_scrambled(tmp_path):
    import numpy as np

    from helspin.domain.errors import EmptySpectrum
    from helspin.infrastructure.nmrglue_reader import NmrglueReader

    expno = tmp_path / "10"
    pdata = expno / "pdata" / "1"
    pdata.mkdir(parents=True)
    (expno / "acqus").write_text(
        "##$NUC1= <1H>\n##$SFO1= 600.13\n##$SW_h= 6000\n##END=\n")
    (expno / "acqu2s").write_text(
        "##$NUC1= <15N>\n##$SFO1= 60.8\n##$SW_h= 2000\n##END=\n")
    procs = ("##$SI= {si}\n##$XDIM= {x}\n##$SF= 600.13\n##$SW_p= 6000\n"
             "##$OFFSET= 10\n##$BYTORDP= 0\n##$DTYPP= 0\n##$NC_proc= 0\n"
             "##$FT_mod= 6\n##END=\n")
    (pdata / "procs").write_text(procs.format(si=64, x=16))
    (pdata / "proc2s").write_text(procs.format(si=32, x=8))
    (pdata / "2rr").write_bytes(np.zeros(64 * 32 // 2, "<i4").tobytes())  # cut short
    with pytest.raises(EmptySpectrum, match="does not match its own size"):
        NmrglueReader().read_2d(expno)
