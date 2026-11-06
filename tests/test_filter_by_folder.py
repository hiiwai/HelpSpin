"""0.9.19: finding samples by the folders they sit in.

Reported: "not only NRAS but the ETHZ directory is not discovered". The tree
lists samples directly under their data root, by their own folder name, so a
group folder like ETHZ appears nowhere -- and the filter matched only sample
names and pulse programmes, so typing "ETHZ" found nothing even when every
sample below it had been found.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QRegularExpression, Qt

from helspin.core import dataset_index as di
from helspin.domain.ports import DataRoot
from helspin.ui.browser import DatasetBrowser
from helspin.ui.dataset_filter import DatasetFilterProxy
from helspin.ui.dataset_model import COL_NAME

from .test_browser_speed import make_expno

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def volume(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "Active_RD_NMR600"
    ethz = root / "NMR600_2026" / "data" / "ETHZ" / "Shared" / "NRAS"
    make_expno(ethz / "20260821_NRAS_Hideo_Hari_700", 5)
    make_expno(ethz / "20260901_KRAS_x", 1)
    make_expno(root / "NMR600_2026" / "data" / "UH" / "20260815_other", 1)
    make_expno(root / "top_sample", 1)
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    node = browser.model.root_nodes()[0]
    qtbot.waitUntil(lambda: node.children is not None and len(node.children) == 4,
                    timeout=10000)
    yield browser, root
    browser.shutdown()


def visible_samples(browser) -> list[str]:
    proxy = browser._proxy
    root = proxy.index(0, 0)
    return sorted(proxy.data(proxy.index(r, COL_NAME, root))
                  for r in range(proxy.rowCount(root)))


@pytest.mark.parametrize("text, expected", [
    ("ETHZ", ["20260821_NRAS_Hideo_Hari_700", "20260901_KRAS_x"]),
    ("ethz/shared", ["20260821_NRAS_Hideo_Hari_700", "20260901_KRAS_x"]),
    ("Shared/NRAS", ["20260821_NRAS_Hideo_Hari_700", "20260901_KRAS_x"]),
    ("UH", ["20260815_other"]),
    ("NMR600_2026", ["20260815_other", "20260821_NRAS_Hideo_Hari_700",
                     "20260901_KRAS_x"]),
    ("KRAS", ["20260901_KRAS_x"]),              # by name, as before
])
def test_typing_a_folder_name_finds_the_samples_under_it(volume, text, expected):
    browser, _root = volume
    browser._filter_edit.setText(text)
    assert visible_samples(browser) == expected


def test_the_data_root_itself_is_not_part_of_the_match(volume):
    browser, _root = volume
    browser._filter_edit.setText("Active_RD")         # the root FOLDER's name
    model = browser.model
    root_node = model.root_nodes()[0]
    # Folders are matched from below the data root only: otherwise every
    # sample would match anything in the root's own path.
    assert all("active_rd" not in model.sample_folder(c).lower()
               for c in root_node.children)
    assert browser._proxy.rowCount() == 0


def test_a_matching_folder_shows_the_samples_experiments(volume, qtbot):
    browser, _root = volume
    model = browser.model
    sample = next(c for c in model.root_nodes()[0].children
                  if c.display_name.startswith("20260821"))
    browser._request_populate(model._index_for(sample))
    qtbot.waitUntil(lambda: bool(sample.children), timeout=5000)
    proxy = DatasetFilterProxy()
    proxy.setSourceModel(model)
    proxy.setFilterRegularExpression(QRegularExpression("ETHZ"))
    sample_index = proxy.mapFromSource(model._index_for(sample))
    assert sample_index.isValid() and proxy.rowCount(sample_index) == 1


def test_sample_rows_show_where_they_are_on_hover(volume):
    browser, _root = volume
    model = browser.model
    by_name = {c.display_name: c for c in model.root_nodes()[0].children}
    tip = model.data(model._index_for(by_name["20260821_NRAS_Hideo_Hari_700"]),
                     Qt.ToolTipRole)
    assert tip == ("NMR600_2026/data/ETHZ/Shared/NRAS/"
                   "20260821_NRAS_Hideo_Hari_700")
    assert model.data(model._index_for(by_name["top_sample"]),
                      Qt.ToolTipRole) == "top_sample"
    assert model.sample_folder(model.root_nodes()[0]) == ""


def test_a_sample_outside_its_root_still_has_a_folder(volume):
    browser, _root = volume
    model = browser.model
    node = model.root_nodes()[0].children[0]
    node.folder_text = None
    node.path = Path("/elsewhere/linked/S")
    assert model.sample_folder(node) == "/elsewhere/linked"


# --------------------------------------------- what --why says about the cache


def test_a_finished_search_records_when_and_the_limit(volume, tmp_path):
    browser, root = volume
    browser.shutdown()
    saved = json.loads(di.cache_path(root).read_text())
    assert saved["built_at"] > 0 and saved["limit"] == 0
    assert "an earlier session" not in di.explain_cache(root, root / "missing")


def test_a_cache_served_start_does_not_count_as_a_search(qtbot, tmp_path,
                                                        monkeypatch):
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "nmr"
    make_expno(root / "S", 1)
    index = di.RootIndex(root=str(root), complete=True, built_at=123.0)
    index.add(di.SampleEntry(path=str(root / "S")))
    di.save_index(index)
    browser = DatasetBrowser([DataRoot(name="600", path=root)])
    qtbot.addWidget(browser)
    qtbot.waitUntil(lambda: bool(browser.model.root_nodes()[0].children), timeout=5000)
    node = browser.model.root_nodes()[0]
    assert browser.model.cached_index(node.path).built_at == 123.0
    browser.shutdown()


def test_why_names_the_old_limit_and_an_incomplete_search(tmp_path, monkeypatch):
    monkeypatch.setenv("HELSPIN_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "nmr"
    index = di.RootIndex(root=str(root), complete=False, truncated=True,
                         limit=5000, built_at=0.0)
    di.save_index(index)
    text = di.explain_cache(root, root / "S")
    assert "5,000-sample limit" in text and "now 100,000" in text
    assert "searches this root again by itself" in text
    assert "an earlier session" in text
    index.limit = 0
    di.save_index(index)
    assert "stopped at the limit of the version" in di.explain_cache(root, root / "S")
