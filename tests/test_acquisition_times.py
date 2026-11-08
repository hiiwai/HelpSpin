"""0.9.27: AQ (t2max) and t1max in the explorer, for comparing experiments.

AQ = TD / (2 SW_h) from acqus; t1max = TD1 / (2 SW_h1) from acqu2s for
complex F1 (FnMODE 2-6), TD1 / SW_h1 for QF, none for pseudo-2D (FnMODE 0).
"""

from __future__ import annotations

import pytest

from helspin.core import dataset_index as di
from helspin.domain import acquisition as acq
from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser
from helspin.ui.dataset_model import COL_AQ, COL_T1MAX, COLUMNS

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")


# ------------------------------------------------------------ the formulas


def test_aq_is_td_over_twice_sw():
    # 600 MHz 1H: TD 32768, SW_h 12019.23 Hz -> AQ 1.3631 s (TopSpin shows 1.3631)
    aq = acq.aq_seconds({"TD": "32768", "SW_h": "12019.2307692308"})
    assert aq == pytest.approx(1.36315, abs=1e-5)


@pytest.mark.parametrize("mode, expected", [
    ("4", 128 / (2 * 2000.0)),      # States
    ("5", 128 / (2 * 2000.0)),      # States-TPPI
    ("6", 128 / (2 * 2000.0)),      # echo-antiecho
    ("3", 128 / (2 * 2000.0)),      # TPPI
    ("2", 128 / (2 * 2000.0)),      # QSEQ
    ("1", 128 / 2000.0),            # QF
    ("0", 0.0),                     # pseudo-2D: no t1max
])
def test_t1max_follows_fnmode(mode, expected):
    t1max = acq.t1max_seconds({"TD": "128", "SW_h": "2000", "FnMODE": mode})
    assert t1max == pytest.approx(expected)


def test_t1max_without_fnmode_is_taken_as_complex():
    assert acq.t1max_seconds({"TD": "256", "SW_h": "4000"}) == pytest.approx(0.032)


@pytest.mark.parametrize("bad", [{}, {"TD": "0", "SW_h": "1"}, {"TD": "x", "SW_h": "1"},
                                 {"TD": "64", "SW_h": "0"}])
def test_missing_or_bad_values_give_nothing(bad):
    assert acq.aq_seconds(bad) == 0.0 and acq.t1max_seconds(bad) == 0.0


def test_ms_text():
    assert acq.as_ms_text(1.36315) == "1363"
    assert acq.as_ms_text(0.032) == "32.0"
    assert acq.as_ms_text(0.0) == "" and acq.as_ms_text(-1) == ""


# ------------------------------------------------------------ in the explorer


def _timed_expno(sample, number, dim=1, td=32768, sw=12019.2307692308,
                 td1=256, sw1=4000.0, fnmode=5):
    expno = make_expno(sample, number, dim=dim)
    acqus = expno / "acqus"
    acqus.write_text(acqus.read_text().replace(
        "##END=", f"##$TD= {td}\n##$SW_h= {sw}\n##$PULPROG= <zg>\n##END="))
    if dim == 2:
        (expno / "acqu2s").write_text(
            f"##$TD= {td1}\n##$SW_h= {sw1}\n##$FnMODE= {fnmode}\n##END=\n")
    return expno


def _open(qtbot, root):
    b = DatasetBrowser([DataRoot(name="L", path=root)])
    qtbot.addWidget(b)
    node = b.model.root_nodes()[0]
    p = b._populator
    idle = lambda: not p._inflight and not p._bg_queue and not p._bg_active  # noqa: E731
    qtbot.waitUntil(lambda: bool(node.children) and idle(), timeout=10000)
    sample = node.children[0]
    b._request_populate(b.model._index_for(sample))
    qtbot.waitUntil(lambda: bool(sample.children) and idle(), timeout=10000)
    return b, sample


def _cells(b, sample):
    model = b.model
    parent = model._index_for(sample)
    out = {}
    for row, child in enumerate(sample.children):
        out[child.display_name] = (model.data(model.index(row, COL_AQ, parent)),
                                   model.data(model.index(row, COL_T1MAX, parent)))
    return out


def test_explorer_shows_aq_and_t1max(qtbot, tmp_path):
    root = tmp_path / "nmr"
    _timed_expno(root / "S", 1)                                  # 1D
    _timed_expno(root / "S", 10, dim=2, td=2048, sw=12019.23)    # 2D, States-TPPI
    _timed_expno(root / "S", 20, dim=2, td=1024, sw=8000.0, fnmode=0)  # pseudo-2D
    b, sample = _open(qtbot, root)
    assert COLUMNS[COL_AQ] == "AQ (ms)" and COLUMNS[COL_T1MAX] == "t1max (ms)"
    assert _cells(b, sample) == {"1": ("1363", ""), "10": ("85.2", "32.0"),
                                 "20": ("64.0", "")}
    from PySide6.QtCore import Qt

    assert "TD / (2 SW_h)" in b.model.headerData(COL_AQ, Qt.Horizontal, Qt.ToolTipRole)
    b.shutdown()


def test_the_values_are_kept_in_the_index(qtbot, tmp_path):
    root = tmp_path / "nmr"
    _timed_expno(root / "S", 10, dim=2, td=2048, sw=12019.23)
    b, _sample = _open(qtbot, root)
    b.shutdown()
    entry = di.load_index(root).find(str(root / "S")).expnos[0]
    assert entry.meta and entry.aq == pytest.approx(0.0852, abs=1e-4)
    assert entry.t1max == pytest.approx(0.032)
    again = di.ExpnoEntry.from_row(entry.as_row())
    assert (again.aq, again.t1max, again.meta) == (entry.aq, entry.t1max, True)


def test_an_index_from_before_fills_the_columns_in(qtbot, tmp_path):
    """A saved index without the new values: rows still show at once, and
    their details are read again so the columns fill in."""
    root = tmp_path / "nmr"
    _timed_expno(root / "S", 1)
    b, _sample = _open(qtbot, root)
    b.shutdown()
    index = di.load_index(root)
    for sample in index.samples:          # strip the new fields, as 0.9.26 saved
        for e in sample.expnos:
            e.aq = 0.0
    import json
    path = di.cache_path(root)
    data = json.loads(path.read_text())
    for sample in data["samples"]:
        sample["expnos"] = [row[:8] for row in sample["expnos"]]
    path.write_text(json.dumps(data))
    b, sample = _open(qtbot, root)
    assert _cells(b, sample)["1"] == ("1363", "")
    b.shutdown()


def test_carry_meta_carries_the_times():
    old = di.ExpnoEntry("1", meta=True, pulprog="zg", aq=1.2, t1max=0.03)
    new = di.ExpnoEntry("1")
    di.carry_meta(new, old)
    assert (new.meta, new.pulprog, new.aq, new.t1max) == (True, "zg", 1.2, 0.03)
