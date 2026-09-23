"""Pinned faceplates refresh their own data without polling hidden displays."""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from azeo_control_trainer.core.hmi.binding import BindingEngine, BindingResult  # noqa: E402
from azeo_control_trainer.core.hmi.binding.source import WriteResult  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.base import registry  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.render import PvmFaceplateWidget  # noqa: E402
from azeo_control_trainer.core.strategy.model.terminal import Quality  # noqa: E402


class Source:
    def __init__(self):
        self.values = {}
        self.quality = Quality.GOOD
        self.reads = []
        self.scopes = self.depth = 0
        self.fail = False

    @contextmanager
    def snapshot(self):
        self.scopes += 1
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def read(self, path):
        self.reads.append(path)
        if self.fail:
            raise RuntimeError("source disconnected")
        return BindingResult(value=self.values.get(path, 1), quality=self.quality,
                             mode_actual="AUTO", mode_normal="AUTO")

    def write(self, path, value):
        self.values[path] = value
        return WriteResult(True)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_requested_expression_refreshes_dependencies_once_and_leaves_other_owners_alone():
    source = Source()
    engine = BindingEngine(source)
    direct = engine.bind("A", name="base")
    middle = engine.bind_expression("x + y", {"x": "base", "y": "B"}, name="middle")
    outer = engine.bind_expression("x * 2", {"x": "middle"})
    unrelated = engine.bind("UNRELATED")
    source.values.update(A=5, B=7, UNRELATED=90)
    source.reads.clear()
    engine.poll([outer, direct, middle, outer])
    assert Counter(source.reads) == Counter({"A": 1, "B": 1})
    assert (direct.result.value, middle.result.value, outer.result.value) == (5, 12, 24)
    assert unrelated.result.value == 1
    assert source.scopes == 1 and source.depth == 0
    engine.poll()
    assert unrelated.result.value == 90


def test_targeted_poll_keeps_quality_last_good_and_change_notifications():
    source = Source()
    engine = BindingEngine(source)
    changes = []
    binding = engine.bind("A", on_change=changes.append)
    source.values["A"] = 42
    assert engine.poll([binding]) == 1
    assert engine.poll([binding]) == 0
    source.quality = Quality.BAD
    assert engine.poll([binding]) == 1
    assert binding.result.quality == Quality.BAD
    assert binding.result.last_good_value == 42
    assert binding.result.last_good_at is not None
    assert len(changes) == 2


def test_empty_or_retired_selection_never_reads_a_replacement_binding():
    source = Source()
    engine = BindingEngine(source)
    retired = engine.bind("A", name="owner")
    engine.unbind(retired)
    replacement = engine.bind("B", name="owner")
    source.values["B"] = 90
    source.reads.clear()
    assert engine.poll([]) == 0
    assert engine.poll([retired]) == 0
    assert source.reads == [] and source.scopes == 0
    assert replacement.result.value == 1
    engine.poll([replacement])
    assert replacement.result.value == 90


def test_source_failure_releases_scope_and_next_poll_reads_current_values():
    source = Source()
    engine = BindingEngine(source)
    binding = engine.bind("A")
    source.fail = True
    with pytest.raises(RuntimeError, match="disconnected"):
        engine.poll([binding])
    assert source.depth == 0
    source.fail = False
    source.values["A"] = 93
    engine.poll([binding])
    assert binding.result.value == 93 and source.depth == 0


def test_checked_write_during_targeted_notification_still_refreshes_all_feedback():
    source = Source()
    engine = BindingEngine(source)
    feedback = engine.bind("OUTPUT")
    triggered = []

    def command(_):
        if not triggered:
            triggered.append(engine.write("OUTPUT", 37))

    trigger = engine.bind("TRIGGER", on_change=command)
    source.values["TRIGGER"] = 2
    engine.poll([trigger])
    assert len(triggered) == 1 and triggered[0].success
    assert feedback.result.value == 37
    assert source.depth == 0


def test_each_faceplate_refresh_leaves_hidden_display_and_other_faceplate_bindings_untouched(app):
    source = Source()
    engine = BindingEngine(source)
    hidden = engine.bind("HIDDEN/DISPLAY/PV")
    windows = []
    try:
        for path in ("A/PID1", "B/PID1"):
            windows.append(PvmFaceplateWidget(registry.get("PID", "faceplate"), {"path": path}, engine, live=False))
        first, second = windows
        source.values.update({"A/PID1/PV": 42, "B/PID1/PV": 75, "HIDDEN/DISPLAY/PV": 99})
        source.reads.clear()
        first.refresh()
        assert source.reads and all(path.startswith("A/PID1/") for path in source.reads)
        assert hidden.result.value == 1
        assert second._primary_result().value == 1
        assert first._primary_result().value == 42
        source.reads.clear()
        second.refresh()
        assert source.reads and all(path.startswith("B/PID1/") for path in source.reads)
        assert second._primary_result().value == 75
        first.close()
        source.values["B/PID1/PV"] = 82
        source.reads.clear()
        second.refresh()
        assert second._primary_result().value == 82
        assert all(path.startswith("B/PID1/") for path in source.reads)
        assert hidden.result.value == 1
    finally:
        for window in windows:
            window.close()
            window.deleteLater()
        app.processEvents()


def test_faceplate_permissions_share_one_inventory_but_recheck_every_decision(app):
    from azeo_control_trainer.core.hmi.binding import LiveGraphSource
    inventories, checks = [], []
    allowed = [True]

    class Permissions(LiveGraphSource):
        def can_write(self, path):
            self._graph_map()
            checks.append(path)
            return WriteResult(allowed[0], "role revoked" if not allowed[0] else "")

    source = Permissions(lambda: inventories.append(True) or {})
    engine = BindingEngine(source)
    window = PvmFaceplateWidget(registry.get("PID", "faceplate"),
                                {"path": "A/PID1"}, engine, live=False)
    window.set_write_handler(engine.write, engine.can_write)
    try:
        inventories.clear()
        checks.clear()
        window.refresh()
        first_checks = list(checks)
        assert len(first_checks) > 1
        assert len(inventories) == 1
        allowed[0] = False
        inventories.clear()
        checks.clear()
        window.refresh()
        assert checks == first_checks
        assert len(inventories) == 1
        assert all(not button.isEnabled() for _, button, _ in window.write_controls.values())
        assert source._snapshot is None
    finally:
        window.close()


def test_display_polls_its_pvms_and_compounds_without_refreshing_faceplate(app):
    from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
    from azeo_control_trainer.core.hmi.pvms.rendering.viewer import PvmDisplayView

    source = Source()
    document = PvmDisplay(name="Owned", items=[
        {"id": "link", "kind": "datalink", "x": 0, "y": 0,
         "w": 100, "h": 30, "path": "DISPLAY/AI/OUT", "data_type": "numeric"}])
    view = PvmDisplayView(document.to_dict(), lambda: {}, source=source, live=False)
    window = PvmFaceplateWidget(registry.get("PID", "faceplate"),
                                {"path": "FACE/PID"}, view.engine, live=False)
    try:
        source.values.update({"DISPLAY/AI/OUT": 23, "FACE/PID/PV": 89})
        source.reads.clear()
        view.refresh()
        assert source.reads == ["DISPLAY/AI/OUT"]
        assert window._primary_result().value == 1
        window.refresh()
        assert window._primary_result().value == 89
        view.close()
        source.values["FACE/PID/PV"] = 93
        window.refresh()
        assert window._primary_result().value == 93
    finally:
        window.close()
        view.close()
