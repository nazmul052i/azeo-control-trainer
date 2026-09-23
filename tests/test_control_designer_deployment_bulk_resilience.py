from __future__ import annotations

import copy
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.bulk_engineering import (
    EditRow, GenerationRow, parse_edit_csv, parse_generation_csv,
    prepare_edits, prepare_generation,
)
from azeo_control_trainer.core.strategy.deployment_analysis import (
    LAYOUT, PARAMETER, STRUCTURE, analyze_deployment,
)
from azeo_control_trainer.core.strategy.engine.redundancy import (
    ACTIVE, FAILED, NOT_SYNCHRONIZED, RedundancySimulator,
)


@pytest.fixture(scope="module", autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def _constant_document(name="LOOP-101", block_id="constant"):
    return {
        "name": name,
        "blocks": [{
            "id": block_id,
            "block_type": "CONSTANT",
            "instance_name": "K-101",
            "x": 20.0,
            "y": 40.0,
            "config": {"value": 1.0, "label": "{{PREFIX}} reference"},
        }],
        "wires": [],
    }


def test_deployment_diff_separates_layout_parameter_and_structure():
    baseline = _constant_document()
    candidate = copy.deepcopy(baseline)
    candidate["blocks"][0]["x"] = 99.0
    candidate["blocks"][0]["config"]["value"] = 2.5
    candidate["blocks"].append({
        "id": "downstream", "block_type": "ABS", "instance_name": "ABS-101",
        "x": 200.0, "y": 40.0, "config": {},
    })
    candidate["blocks"].append({
        "id": "consumer", "block_type": "ABS", "instance_name": "ABS-102",
        "x": 380.0, "y": 40.0, "config": {},
    })
    candidate["wires"].append({
        "id": "wire", "src_block_id": "constant", "src_terminal": "OUT",
        "dst_block_id": "downstream", "dst_terminal": "IN",
    })
    candidate["wires"].append({
        "id": "wire2", "src_block_id": "downstream", "src_terminal": "OUT",
        "dst_block_id": "consumer", "dst_terminal": "IN",
    })

    report = analyze_deployment(baseline, candidate)

    assert {change.scope for change in report.changes} >= {
        LAYOUT, PARAMETER, STRUCTURE}
    assert report.restart_required
    assert report.risk == "HIGH"
    assert any(impact.object_id == "consumer"
               for impact in report.impacts)


def test_deployment_diff_finds_cross_module_shared_tag_impact():
    baseline = {
        "name": "WRITE", "blocks": [{
            "id": "ao", "block_type": "AO", "instance_name": "AO-1",
            "x": 0, "y": 0, "config": {"tag": "plant.valve"},
        }], "wires": []}
    candidate = copy.deepcopy(baseline)
    candidate["blocks"][0]["config"]["tag"] = "plant.valve.new"
    reader = {
        "name": "READ", "blocks": [{
            "id": "ai", "block_type": "AI", "instance_name": "AI-1",
            "x": 0, "y": 0, "config": {"tag": "plant.valve"},
        }], "wires": []}

    report = analyze_deployment(
        baseline, candidate, project_documents=[reader])

    assert any(impact.external and impact.module == "READ"
               and "plant.valve" in impact.reason for impact in report.impacts)


def test_deployment_diff_treats_composite_interior_as_controller_structure():
    baseline = _constant_document()
    baseline["blocks"][0].update({
        "block_type": "COMPOSITE",
        "inner_graph": _constant_document("INNER", "inner-k"),
    })
    candidate = copy.deepcopy(baseline)
    candidate["blocks"][0]["inner_graph"]["blocks"][0]["config"][
        "value"] = 8.0

    report = analyze_deployment(baseline, candidate)

    assert report.restart_required
    assert any(change.scope == STRUCTURE
               and "inner graph" in change.detail.lower()
               for change in report.changes)


def test_generation_preview_substitutes_tokens_remaps_ids_and_compiles(tmp_path):
    template = _constant_document(name="TEMPLATE")
    operation = prepare_generation(
        template, tmp_path, [GenerationRow("LOOP-201", "F201")])

    target = tmp_path / "LOOP-201.json"
    generated = operation.documents[target]
    assert generated["name"] == "LOOP-201"
    assert generated["blocks"][0]["id"] != "constant"
    assert generated["blocks"][0]["config"]["label"] == "F201 reference"
    assert not target.exists()

    assert operation.apply() == [target]
    assert json.loads(target.read_text(encoding="utf-8"))["name"] == "LOOP-201"
    with pytest.raises(FileExistsError):
        operation.apply()


def test_bulk_generation_refuses_to_corrupt_module_class_link(tmp_path):
    template = _constant_document(name="CLASS_INSTANCE")
    template["module_class"] = {"definition_id": "class-id"}

    with pytest.raises(ValueError, match="Create Linked Instance"):
        prepare_generation(
            template, tmp_path, [GenerationRow("LOOP-201", "F201")])


def test_controlled_bulk_edit_coerces_schema_and_refuses_unknown_fields(tmp_path):
    path = tmp_path / "LOOP-101.json"
    source = _constant_document()
    path.write_text(json.dumps(source), encoding="utf-8")

    operation = prepare_edits(
        {path: source}, [EditRow("LOOP-101", "K-101", "value", "2.75")])
    assert operation.changes[0].before == 1.0
    assert operation.changes[0].after == 2.75
    operation.apply()
    assert json.loads(path.read_text(encoding="utf-8"))[
        "blocks"][0]["config"]["value"] == 2.75
    assert operation.rollback() == [path]
    assert path.read_bytes() == json.dumps(source).encode("utf-8")

    with pytest.raises(ValueError, match="no configuration parameter"):
        prepare_edits(
            {path: source}, [EditRow("LOOP-101", "K-101", "TYPO", "3")])


def test_bulk_csv_contracts_are_explicit():
    assert parse_generation_csv(
        "module,prefix\nLOOP-201,F201\n") == [
            GenerationRow("LOOP-201", "F201")]
    assert parse_edit_csv(
        "module,block,parameter,value\nLOOP-101,K-101,value,4\n") == [
            EditRow("LOOP-101", "K-101", "value", "4")]


def test_generated_module_remaps_embedded_composite_graph_ids():
    from azeo_control_trainer.core.strategy.bulk_engineering import (
        _remap_block_ids,
    )

    document = {
        "name": "OUTER",
        "blocks": [{
            "id": "composite", "block_type": "COMPOSITE",
            "instance_name": "C-1", "config": {},
            "inner_graph": {
                "name": "INNER",
                "blocks": [
                    {"id": "a", "block_type": "CONSTANT",
                     "instance_name": "A", "config": {}},
                    {"id": "b", "block_type": "ABS",
                     "instance_name": "B", "config": {}},
                ],
                "wires": [{
                    "id": "inner-wire", "src_block_id": "a",
                    "src_terminal": "OUT", "dst_block_id": "b",
                    "dst_terminal": "IN",
                }],
            },
        }],
        "wires": [],
    }

    _remap_block_ids(document, "OUTER-201")

    inner = document["blocks"][0]["inner_graph"]
    ids = {block["id"] for block in inner["blocks"]}
    assert ids.isdisjoint({"a", "b"})
    assert inner["wires"][0]["src_block_id"] in ids
    assert inner["wires"][0]["dst_block_id"] in ids


def test_communication_failure_holds_bad_input_and_drops_outputs():
    store = SharedDataStore()
    store.set_sample("field.pv", 10.0, quality="GOOD", timestamp=100.0)
    input_failure = store.inject_communication_failure(
        "field.pv", direction="input")[0]
    store.set_sample("field.pv", 20.0, quality="GOOD", timestamp=200.0)

    sample = store.get_sample("field.pv")
    assert store.get_all()["field.pv"] == 10.0
    assert sample.value == 10.0 and sample.quality == "BAD" and sample.stale

    output_failure = store.inject_communication_failure(
        "field.out", direction="output")[0]
    assert store.queue_write("field.out", 55.0) is False
    assert store.drain_writes() == []
    failures = {item["failure_id"]: item for item in store.communication_failures()}
    assert failures[output_failure]["dropped_writes"] == 1

    assert store.clear_communication_failure(input_failure) == 1
    assert store.get_all()["field.pv"] == 20.0
    assert store.clear_communication_failure() == 1


def test_wildcard_failure_latches_first_late_discovered_sample():
    store = SharedDataStore()
    store.inject_communication_failure("*", direction="input")
    store.set_sample("late.pv", 11.0, timestamp=101.0)
    store.set_sample("late.pv", 22.0, timestamp=202.0)

    sample = store.get_sample("late.pv")
    assert sample is not None
    assert sample.value == 11.0
    assert sample.timestamp == 101.0
    assert sample.quality == "BAD" and sample.stale
    assert store.get_all()["late.pv"] == 11.0


class _Runtime:
    is_online = True


class _Executive:
    def __init__(self):
        self.is_running = True
        self._runtimes = [_Runtime()]

    def online_runtimes(self):
        return list(self._runtimes)

    def stop(self):
        self.is_running = False

    def start(self):
        self.is_running = True


def test_redundancy_pair_inhibits_unsynchronized_failover_and_promotes_standby():
    store = SharedDataStore()
    executive = _Executive()
    pair = RedundancySimulator(store, executive)
    assert pair.status().failover_ready

    pair.lose_peer_link()
    assert pair.status().standby_state == NOT_SYNCHRONIZED
    assert not pair.fail_primary()
    pair.restore_peer_link()
    assert pair.status().failover_ready

    old_primary = pair.primary
    assert pair.fail_primary()
    assert not executive.is_running
    assert pair.takeover()
    assert executive.is_running
    assert pair.primary != old_primary
    assert pair.primary_state == ACTIVE
    assert pair.standby_state == FAILED


def test_new_dialogs_construct_with_shared_engineering_style():
    from types import SimpleNamespace

    from azeo_control_trainer.azeo_control_designer.dialogs.deployment_impact import (
        DeploymentImpactDialog,
    )
    from azeo_control_trainer.azeo_control_designer.dialogs.redundancy_simulator import (
        RedundancySimulatorDialog,
    )

    graph = SimpleNamespace(name="LOOP-101", to_dict=lambda: _constant_document())
    canvas = SimpleNamespace(
        scene=SimpleNamespace(graph=graph), download_snapshot=_constant_document())
    impact = DeploymentImpactDialog([canvas])
    assert impact.windowTitle() == "Deployment Diff and Impact"
    assert impact.reports[0].risk == "NONE"

    store = SharedDataStore()
    redundancy = RedundancySimulatorDialog(store, _Executive())
    assert "Redundancy" in redundancy.windowTitle()
    assert "not certified" in redundancy.findChildren(QLabel)[1].text().lower()

    impact.close()
    redundancy.close()


def test_closing_redundancy_dialog_during_outage_restores_scanning():
    from azeo_control_trainer.azeo_control_designer.dialogs.redundancy_simulator import (
        RedundancySimulatorDialog,
    )

    executive = _Executive()
    dialog = RedundancySimulatorDialog(SharedDataStore(), executive)
    assert dialog.model.fail_primary()
    assert not executive.is_running

    dialog.close()

    assert executive.is_running
    assert dialog.model.active_drill is None
