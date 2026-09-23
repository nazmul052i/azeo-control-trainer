"""Renames preserve recorded identity; reused names do not borrow other points."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.hmi.history import ContinuousHistorian
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def graph(name="LOOP", object_id="module", revision=1):
    result = StrategyGraph(name)
    block = PIDBlock("PID")
    block.id = "block"
    result.add_block(block)
    result._configuration_identity = {"project_id": "pilot", "object_id": object_id,
                                      "revision": revision, "release_id": f"release-{revision}"}
    return result


class Source:
    def __init__(self):
        self.values = {}

    def get_all(self):
        return dict(self.values)


def test_recorded_rename_preserves_buffers_archive_identity_and_release_context(tmp_path):
    source = Source()
    historian = ContinuousHistorian(source, period_s=0, archive_path=tmp_path / "history.sqlite3")
    old, new = "LOOP/PID/PV", "RENAMED/PID/PV"
    try:
        historian.configure_from(None, [graph()])
        source.values[old] = 20
        historian.collect(now=1)
        identity = historian.TAGS[old].point_id
        historian.save_chart_state("Operator chart", {"pens": [old]})
        historian.configure_from(None, [graph("RENAMED", revision=2)])
        source.values[new] = 30
        historian.collect(now=2)
        assert historian.TAGS[new].point_id == identity
        assert list(historian.TAGS[new].values) == [20, 30]
        assert historian.TAGS[old] is historian.TAGS[new]
        historian.archive.flush()
        result = historian.archive.read([new], 0, 3, max_points=0)
        assert [r[1] for r in result["samples"][new]] == [20, 30]
        assert [row["configuration"]["revision"] for row in result["provenance"][new]] == [1, 2]
    finally:
        historian.close()
    restarted = ContinuousHistorian(source, period_s=0, archive_path=tmp_path / "history.sqlite3")
    try:
        assert restarted.TAGS[old].point_id == restarted.TAGS[new].point_id == identity
        result = restarted.query_async([old], 0, 3 / 60, raw=True).result(timeout=10)
        assert [r[1] for r in result["samples"][old]] == [20, 30]
        assert restarted.chart_state("Operator chart")["pens"] == [new]
    finally:
        restarted.close()


def test_reused_address_and_separate_project_do_not_merge_history(tmp_path):
    source = Source()
    historian = ContinuousHistorian(source, period_s=0, archive_path=tmp_path / "history.sqlite3")
    path = "LOOP/PID/PV"
    try:
        historian.configure_from(None, [graph()])
        source.values[path] = 11
        historian.collect(now=1)
        previous = historian.TAGS[path].point_id
        historian.save_chart_state("Old loop", {"pens": [path]})
        historian.configure_from(None, [graph(object_id="replacement")])
        source.values[path] = 99
        historian.collect(now=2)
        assert historian.TAGS[path].point_id != previous
        historian.archive.flush()
        result = historian.archive.read([path], 0, 3, max_points=0)
        assert [r[1] for r in result["samples"][path]] == [99]
        old_key = historian.chart_state("Old loop")["pens"][0]
        assert historian.TAGS[old_key].point_id == previous
        assert historian.archive.read([old_key], 0, 3, max_points=0)["samples"][old_key][0][1] == 11
        separate = graph(object_id="replacement")
        separate._configuration_identity["project_id"] = "another-pilot"
        current = historian.TAGS[path].point_id
        historian.configure_from(None, [separate])
        assert historian.TAGS[path].point_id != current
    finally:
        historian.close()


def test_open_chart_tracks_identity_and_empty_release_stops_collection():
    from PySide6.QtWidgets import QApplication
    from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView, PointBrowserDialog
    app = QApplication.instance() or QApplication([])
    source = Source()
    historian = ContinuousHistorian(source, period_s=0)
    historian.configure_from(None, [graph()])
    source.values["LOOP/PID/PV"] = 12
    historian.collect(now=1)
    assert historian.statistics("LOOP/PID/PV")["current"] == 12
    view = ProcessHistoryView(historian)
    view.set_pens(["LOOP/PID/PV"])
    historian.configure_from(None, [graph("RENAMED", revision=2)])
    view._refresh()
    assert view._pens == ["RENAMED/PID/PV"]
    historian.configure_from(None, [graph("RENAMED", object_id="another")])
    source.values["RENAMED/PID/PV"] = 77
    historian.collect(now=2)
    view._refresh()
    assert view._pens[0].startswith("@history:")
    assert historian.TAGS[view._pens[0]].active is False
    browser = PointBrowserDialog(historian, 10)
    assert browser._list.count() == 6
    assert historian.default_pens_for("RENAMED") == ["RENAMED/PID/PV", "RENAMED/PID/SP", "RENAMED/PID/OUT"]
    historian.configure_from(None, [])
    assert historian.collect(now=3) == 0
    view.close()
    browser.close()
    app.processEvents()


def test_archive_export_retains_recorded_units_and_never_relabels_old_samples(tmp_path):
    import csv
    import json
    import math
    from azeo_control_trainer.core.hmi.history.export import export_history
    source = Source()
    historian = ContinuousHistorian(source, period_s=0, archive_path=tmp_path / "history.sqlite3")
    old, new = "LOOP/PID/PV", "RENAMED/PID/PV"
    try:
        first = graph()
        first.blocks["block"].config.params["pv_unit"] = "bar"
        historian.configure_from(None, [first])
        source.values[old] = 2
        historian.collect(now=1)
        second = graph("RENAMED", revision=2)
        second.blocks["block"].config.params["pv_unit"] = "kPa"
        historian.configure_from(None, [second])
        source.values[new] = 200
        historian.collect(now=2)
        historian.archive.flush()
        target = tmp_path / "recorded.csv"
        export_history(target, paths=[new], metadata={new: historian.point_metadata(historian.TAGS[new])},
                       origin=historian.origin, start=0, end=3, archive=historian.archive)
        rows = list(csv.DictReader(target.open(encoding="utf-8-sig")))
        assert [row["unit"] for row in rows] == ["bar", "kPa"]
        assert [row["recorded_path"] for row in rows] == [old, new]
        assert len({row["point_id"] for row in rows}) == 1
        assert [row["configuration_revision"] for row in rows] == ["1", "2"]
        metadata = json.loads((tmp_path / "recorded.metadata.json").read_text())
        assert metadata["statistics"][new]["average"] is None
        mixed = historian.query_async([new], 0, .05, as_snapshot=True).result(timeout=10)
        assert mixed.TAGS[new].metadata_conflict
        assert math.isnan(mixed.statistics(new)["average"])
        original = historian.query_async([new], 0, 1.5 / 60, as_snapshot=True).result(timeout=10)
        assert original.TAGS[new].unit == "bar"
        assert original.statistics(new)["average"] == 2
        from PySide6.QtWidgets import QApplication
        from azeo_control_trainer.core.hmi.history.view import ProcessHistoryView
        app = QApplication.instance() or QApplication([])
        view = ProcessHistoryView(historian)
        view.set_pens([new])
        view._source = original
        view._update_table()
        try:
            assert view._table.item(0, 9).text() == "bar"
            assert "kPa" in view._table.item(0, 4).text()
        finally:
            view.close()
            app.processEvents()
    finally:
        historian.close()


def test_custom_numeric_parameter_keeps_identity_on_reconfigure():
    from azeo_control_trainer.core.configuration.identities import context_for_path
    original = graph()
    historian = ContinuousHistorian(Source())
    path = "LOOP/PID/CONFIG/GAIN"
    context = context_for_path([original], path)
    assert context["point_id"]
    historian.add_point(path, **context)
    historian.configure_from(None, [graph("RENAMED", revision=2)])
    new = "RENAMED/PID/CONFIG/GAIN"
    assert historian.TAGS[new].point_id == context["point_id"]
    assert historian.TAGS[new].active


def test_legacy_archive_remains_queryable_without_invented_repository_provenance(tmp_path):
    path = "LOOP/PID/PV"
    source = Source()
    historian = ContinuousHistorian(source, period_s=0, archive_path=tmp_path / "legacy.sqlite3")
    try:
        historian.add_point(path, unit="bar")
        source.values[path] = 5
        historian.collect(now=1)
        historian.save_chart_state("Legacy chart", {"pens": [path]})
        historian.configure_from(None, [graph()])
        source.values[path] = 12
        historian.collect(now=2)
        historian.archive.flush()
        alias = historian.chart_state("Legacy chart")["pens"][0]
        assert alias == "@legacy:" + path
        old = historian.query_async([alias], 0, .1, raw=True).result(timeout=10)
        current = historian.query_async([path], 0, .1, raw=True).result(timeout=10)
        assert [row[1] for row in old["samples"][alias]] == [5]
        assert old["provenance"][alias] == []
        assert [row[1] for row in current["samples"][path]] == [12]
    finally:
        historian.close()
