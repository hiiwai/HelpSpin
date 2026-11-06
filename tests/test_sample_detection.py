"""0.9.18: a folder is a sample only if it holds numbered EXPERIMENTS.

Reported: no data at all found under the data root NMR600_2026, for a path
like NMR600_2026/data/ETHZ/Shared/NRAS/<sample>/5/pdata/1. Any folder with a
numbered subfolder counted as a sample, and the search never looks inside a
sample: one "2026" (or "600", or "1") folder next to "ETHZ" turned "data"
into a "sample" and hid everything below it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from helspin.core import dataset_index as di
from helspin.domain import paths as dp
from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser

from .test_browser_speed import OpCounter, make_expno

pytestmark = pytest.mark.usefixtures("qapp")

SAMPLE = "data/ETHZ/Shared/NRAS/20260821_NRAS_Hideo_Hari_700"


def reported_layout(tmp_path, decoy="2026") -> tuple[Path, Path]:
    root = tmp_path / "NMR600_2026"
    sample = root / SAMPLE
    make_expno(sample, 5)
    (root / "data" / decoy / "notes").mkdir(parents=True)   # not an experiment
    return root, sample


@pytest.mark.parametrize("decoy", ["2026", "600", "1"])
def test_a_numbered_non_experiment_folder_no_longer_hides_the_data(tmp_path, decoy):
    root, sample = reported_layout(tmp_path, decoy)
    found, truncated = di.discover_samples(root, workers=8)
    assert found == [str(sample)] and not truncated


def test_year_folders_holding_samples_are_searched(tmp_path):
    root = tmp_path / "nmr"
    for year in ("2025", "2026"):
        make_expno(root / "NRAS" / year / f"S_{year}", 1)
    found, _ = di.discover_samples(root, workers=4)
    assert found == [str(root / "NRAS" / y / f"S_{y}") for y in ("2025", "2026")]


def test_a_sample_whose_first_experiment_is_empty_is_still_a_sample(tmp_path):
    sample = tmp_path / "nmr" / "S"
    (sample / "1").mkdir(parents=True)          # aborted: nothing in it
    make_expno(sample, 2)
    assert dp.holds_experiments(str(sample))
    assert di.discover_samples(tmp_path / "nmr")[0] == [str(sample)]


@pytest.mark.parametrize("marker", sorted(dp.EXPERIMENT_MARKERS))
def test_any_experiment_file_marks_an_experiment(tmp_path, marker):
    expno = tmp_path / "S" / "3"
    expno.mkdir(parents=True)
    target = expno / marker
    target.mkdir() if marker == "pdata" else target.write_text("x")
    assert dp.holds_experiments(str(tmp_path / "S"))


def test_unreadable_or_missing_folders_are_not_samples(tmp_path):
    assert not dp.holds_experiments(str(tmp_path / "missing"))
    assert dp.numbered_subfolders(str(tmp_path / "missing")) == []
    assert not dp.looks_like_experiment(str(tmp_path / "missing"))


def test_the_root_itself_can_still_be_a_sample(tmp_path):
    sample = tmp_path / "S"
    make_expno(sample, 1)
    assert di.discover_samples(sample, workers=8) == ([str(sample)], False)


def test_finding_samples_still_costs_per_folder_not_per_experiment(tmp_path,
                                                                  monkeypatch):
    root = tmp_path / "nmr"
    for s in range(10):
        for e in range(1, 21):
            make_expno(root / f"S{s}", e)
    with OpCounter(monkeypatch) as ops:
        found, _ = di.discover_samples(root)
    assert len(found) == 10
    # The root twice (is it a sample itself? its children), then one listing
    # per sample plus one experiment check: never anything per experiment
    # (200 of them here).
    assert ops.counts["scandir"] <= 2 + 10 * 2


# ------------------------------------------------------------ --why


def test_why_traces_the_search_to_the_sample(tmp_path):
    root, sample = reported_layout(tmp_path)
    lines = di.explain_path(root, sample / "5" / "pdata" / "1")
    text = "\n".join(lines)
    assert "data: folder, searched" in text and "hold no experiment files" in text
    assert f"{SAMPLE}: SAMPLE -- found (experiments 5)" in text


def test_why_names_the_folder_that_hides_the_data(tmp_path):
    root = tmp_path / "nmr"
    make_expno(root / "data", 1)                 # "data" really holds an expno
    make_expno(root / "data" / "ETHZ" / "S", 1)
    lines = di.explain_path(root, root / "data" / "ETHZ" / "S")
    assert "data: SAMPLE" in lines[1]
    assert "'ETHZ' below it is never reached" in lines[2]


@pytest.mark.parametrize("case, expected", [
    ("outside", "is not inside the data root"),
    ("unreachable", "is not reachable"),
    ("pdata", "a pdata folder; never searched"),
    ("missing", "not a folder, or not readable"),
    ("not_a_sample", "is not a sample"),
])
def test_why_explains_the_other_outcomes(tmp_path, case, expected):
    root = tmp_path / "nmr"
    (root / "plain").mkdir(parents=True)
    (root / "x" / "pdata").mkdir(parents=True)
    target = {
        "outside": tmp_path / "elsewhere",
        "unreachable": tmp_path / "gone" / "S",
        "pdata": root / "x" / "pdata",
        "missing": root / "nope",
        "not_a_sample": root / "plain",
    }[case]
    use_root = tmp_path / "gone" if case == "unreachable" else root
    assert expected in "\n".join(di.explain_path(use_root, target))


def test_why_explains_too_deep_and_links_back(tmp_path, monkeypatch):
    root = tmp_path / "nmr"
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    monkeypatch.setattr(di, "MAX_DEPTH", 1)
    assert "deeper than 1 folder levels" in "\n".join(di.explain_path(root, deep))
    monkeypatch.setattr(di, "MAX_DEPTH", 8)
    os.symlink(root / "a", root / "link")
    assert "a link back inside the data root" in "\n".join(
        di.explain_path(root, root / "link" / "b"))


def test_why_when_the_root_is_a_sample(tmp_path):
    make_expno(tmp_path / "S", 1)
    assert "The data root itself is a sample" in "\n".join(
        di.explain_path(tmp_path / "S", tmp_path / "S" / "1"))


def test_why_reports_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root, sample = reported_layout(tmp_path)
    assert "No cached index" in di.explain_cache(root, sample)
    index = di.RootIndex(root=str(root), built_at=1.0, complete=True)
    index.add(di.SampleEntry(path=str(root / "old")))
    di.save_index(index)
    text = di.explain_cache(root, sample)
    assert "NO" in text and "press Refresh" in text
    index.add(di.SampleEntry(path=str(sample)))
    index.truncated = True
    di.save_index(index)
    assert "yes" in di.explain_cache(root, sample / "5")


def test_why_command_line(tmp_path, monkeypatch, capsys):
    import helspin.__main__ as main_module

    root, sample = reported_layout(tmp_path)
    monkeypatch.setattr(main_module, "load_data_roots",
                        lambda: [DataRoot(name="600", path=root)])
    monkeypatch.setattr(main_module.sys, "argv",
                        ["helspin", "--why", str(sample / "5" / "pdata" / "1")])
    assert main_module.main() == 0
    out = capsys.readouterr().out
    assert "SAMPLE -- found" in out and "cached index" in out.lower()

    monkeypatch.setattr(main_module.sys, "argv",
                        ["helspin", "--why", str(tmp_path / "x"), "--root", str(root)])
    assert main_module.main() == 0
    assert "is not inside the data root" in capsys.readouterr().out

    monkeypatch.setattr(main_module.sys, "argv", ["helspin", "--why", "/nowhere"])
    assert main_module.main() == 2
    assert "not inside any data root" in capsys.readouterr().out


# ---------------------------------------------- caches from the old rule


def test_an_old_rule_cache_is_searched_again_and_corrected(qtbot, tmp_path,
                                                           monkeypatch):
    root, sample = reported_layout(tmp_path)
    old_rule = di.RootIndex(root=str(root), complete=True, built_at=1.0)
    old_rule.add(di.SampleEntry(path=str(root / "data")))     # the wrong "sample"
    payload = old_rule.to_dict()
    payload.pop("rule")                                         # as 0.9.17 wrote it
    di.save_payload(root, payload)
    assert di.load_index(root).complete is False

    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    p = browser._populator
    qtbot.waitUntil(lambda: node.children is not None and not p._inflight
                    and not p._bg_queue and not p._bg_active, timeout=10000)
    assert [str(c.path) for c in node.children] == [str(sample)]
    browser.shutdown()
    saved = json.loads(di.cache_path(root).read_text())
    assert saved["rule"] == di.DISCOVERY_RULE and saved["complete"] is True
    assert [s["path"] for s in saved["samples"]] == [str(sample)]


def test_a_truncated_walk_never_drops_rows(qtbot, tmp_path, monkeypatch):
    root = tmp_path / "nmr"
    for i in range(4):
        make_expno(root / f"S{i}", 1)
    cached = di.RootIndex(root=str(root), complete=False)
    for i in range(4):
        cached.add(di.SampleEntry(path=str(root / f"S{i}")))
    di.save_index(cached)
    monkeypatch.setattr(di, "SAMPLE_LIMIT", 2)
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    qtbot.waitUntil(lambda: node.truncated, timeout=10000)
    assert len(node.children) == 4
    browser.shutdown()
