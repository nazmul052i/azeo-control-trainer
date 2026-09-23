"""The engineering tag browser stays bounded for plant-scale namespaces."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.strategy.tagdb import (  # noqa: E402
    EntryKind,
    TagDatabase,
    TagEntry,
)
from azeo_control_trainer.core.presentation.tagdb_browser import (  # noqa: E402
    _SEARCH_RESULT_LIMIT,
    TagDatabaseBrowser,
)


def _large_database(point_count: int = 6_000) -> TagDatabase:
    database = TagDatabase("large-area")
    database._put(TagEntry(  # noqa: SLF001 - construct the derived test model
        path="M-1", kind=EntryKind.MODULE, module="M-1", name="M-1"))
    for index in range(point_count):
        block = f"B-{index // 100:03d}"
        name = f"P-{index:05d}"
        database._put(TagEntry(  # noqa: SLF001
            path=f"M-1/{block}/{name}",
            kind=EntryKind.PARAMETER,
            module="M-1",
            block=block,
            name=name,
            value=index,
        ))
    return database


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_large_database_materialises_only_expanded_branches() -> None:
    app = _application()
    database = _large_database()
    browser = TagDatabaseBrowser(
        store=SimpleNamespace(tagdb=database), area_name=database.area_name)

    module = browser._tree.topLevelItem(0)  # noqa: SLF001
    assert browser._tree.topLevelItemCount() == 1  # noqa: SLF001
    assert module.childCount() == 1  # lazy placeholder, not 6,000 rows
    assert len(browser._rows) == 1  # noqa: SLF001
    assert "expand a module" in browser._title.text()  # noqa: SLF001

    module.setExpanded(True)
    app.processEvents()
    assert module.childCount() == 60
    assert len(browser._rows) == 61  # module plus its block rows  # noqa: SLF001

    first_block = module.child(0)
    first_block.setExpanded(True)
    app.processEvents()
    assert first_block.childCount() == 100
    assert len(browser._rows) == 161  # one branch, not the entire database  # noqa: SLF001

    browser.close()
    browser.deleteLater()
    app.processEvents()


def test_broad_search_is_bounded_and_reports_truncation() -> None:
    app = _application()
    database = _large_database()
    browser = TagDatabaseBrowser(
        store=SimpleNamespace(tagdb=database), area_name=database.area_name)

    browser._search.setText("P-")  # noqa: SLF001
    browser._populate()  # noqa: SLF001 - bypass the debounce in the test
    assert sum(1 for path in browser._rows if path.count("/") == 2) \
        == _SEARCH_RESULT_LIMIT
    assert "showing 2000 of 6000 matches" in browser._title.text()  # noqa: SLF001

    browser.close()
    browser.deleteLater()
    app.processEvents()


def test_area_scan_does_not_deserialise_display_json(
        tmp_path: Path, monkeypatch) -> None:
    for relative in (
        "root-module.json",
        "control/U100/loop.json",
        "sequence/startup.json",
        "equipment/pump.json",
        "displays/pvm/_layouts.json",
        "displays/pvm/L1/draft.json",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (tmp_path / "_project.json").write_text('{"areas": []}', encoding="utf-8")

    loaded: list[Path] = []

    def fake_load(path: str, *, remember: bool = True):
        assert not remember
        loaded.append(Path(path))
        return SimpleNamespace(blocks={}), []

    from azeo_control_trainer.core.strategy.serialization import strategy_io

    monkeypatch.setattr(strategy_io, "load_strategy", fake_load)
    TagDatabase.from_area(tmp_path)

    assert {path.relative_to(tmp_path).as_posix() for path in loaded} == {
        "root-module.json",
        "control/U100/loop.json",
        "sequence/startup.json",
        "equipment/pump.json",
    }
