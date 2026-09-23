"""All comparison blocks are first-class, executable Azeo capabilities."""
from pathlib import Path
import sys
import time

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.hmi.binding import WriteResult
from azeo_control_trainer.core.procedures.audit import ProcedureStore
from azeo_control_trainer.core.procedures.adapters import (
    ProcedureAdapter,
    execute_adapter,
    register_adapter,
)
from azeo_control_trainer.core.procedures.capabilities import ParityStatus, block_capabilities
from azeo_control_trainer.core.procedures.library import (
    CATALOG_BLOCK_NAMESPACE,
    block_for_source,
    comparison_block_library,
)
from azeo_control_trainer.core.procedures.model import AdvisoryProcedure, AdvisoryStep, ProcedureDefinition
from azeo_control_trainer.core.procedures.runtime import (
    CapturedTagProvider,
    ObservationConnector,
    ProcedureRun,
    SimulationControl,
    TrainerProcedureEngine,
)
from azeo_control_trainer.core.procedures.trial import run_isolated_trial
from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue


def test_every_catalog_block_is_a_first_class_implemented_azeo_block():
    source = yaml.safe_load(
        Path("src/azeo_control_trainer/core/pa_designer/library/definitions/consolidated_catalog.yaml").read_text(
            encoding="utf-8"
        )
    )
    expected = {row["block_id"] for row in source["blocks"]}
    blocks = comparison_block_library()
    assert len(blocks) == 38
    assert {block.source_id for block in blocks} == expected
    assert len({block.block_id for block in blocks}) == 38
    assert all(block.block_id.startswith(CATALOG_BLOCK_NAMESPACE) for block in blocks)
    assert all(row.status is ParityStatus.IMPLEMENTED for row in block_capabilities())
    for block in blocks:
        step = AdvisoryStep.model_validate(block.instantiate("catalog_step"))
        assert step.library_block_id == block.block_id
        assert step.type == block.step_type


def test_prior_catalog_namespace_is_normalized_when_a_document_is_loaded():
    block = block_for_source("equipment.pump_start")
    document = block.instantiate("legacy_catalog_step")
    document["library_block_id"] = (
        "azeo.procedure." + "exa" + "pilot." + block.source_id
    )

    step = AdvisoryStep.model_validate(document)

    assert step.library_block_id == block.block_id


def _catalog_step(source_id, step_id, **updates):
    return block_for_source(source_id).instantiate(step_id) | updates


def test_integration_event_monitor_pause_and_hmi_blocks_execute_in_isolated_trial(tmp_path):
    procedure = AdvisoryProcedure.model_validate(
        {
            "procedure_id": "all_special_blocks",
            "name": "All special catalog blocks",
            "mode": "advisory",
            "tags": [{"tag": "TAG.PV", "access": "read", "data_type": "float"}],
            "variables": [
                {"name": "source_value", "value": 3.0, "data_type": "float"},
                {"name": "script_result", "value": 0.0, "data_type": "float"},
                {"name": "adapter_result", "value": 0.0, "data_type": "float"},
                {"name": "connector_value", "value": 0.0, "data_type": "float"},
            ],
            "steps": [
                _catalog_step(
                    "integration.legacy_script",
                    "safe_script",
                    expression="source_value * 2",
                    variable="script_result",
                ),
                _catalog_step(
                    "integration.user_application",
                    "adapter",
                    adapter_inputs={"answer": "=source_value + 1"},
                    adapter_results={"answer": "adapter_result"},
                ),
                _catalog_step(
                    "integration.activex_opc_com",
                    "connector",
                    integration_operation="read",
                    tag="TAG.PV",
                    variable="connector_value",
                ),
                _catalog_step("core.user_event", "send_event", event_name="ready"),
                _catalog_step(
                    "flow.user_event_wait",
                    "receive_event",
                    event_name="ready",
                    timeout_sec=2,
                    poll_sec=0.1,
                ),
                _catalog_step(
                    "monitor.process_value",
                    "monitor",
                    condition="TAG.PV == 5",
                    monitor_duration_sec=0.2,
                    poll_sec=0.1,
                ),
                _catalog_step("flow.pause", "pause"),
                _catalog_step("message.hmi_window", "window", hmi_target="Overview"),
                _catalog_step("message.hmi_alarm", "alarm"),
                {"id": "done", "type": "complete", "description": "Complete"},
            ],
        }
    )
    definition = ProcedureDefinition(procedure, {"TAG.PV": "AREA/PID/PV"}).validate()
    history = tmp_path / "trial.sqlite"
    result = run_isolated_trial(definition, {"TAG.PV": 5.0}, history)
    assert result.execution.status.value == "COMPLETE"
    events = ProcedureStore(history).run_events(result.execution.run_id)
    by_type = {row["event_type"]: row["data_json"] for row in events}
    assert by_type["SAFE_SCRIPT"]["value"] == 6.0
    assert by_type["APPLICATION_ADAPTER"]["published"] == {"adapter_result": 4.0}
    assert by_type["PCS_INPUT"]["value"] == 5.0
    assert {"USER_EVENT_RECEIVED", "MONITOR_COMPLETE", "PROCEDURE_PAUSED",
            "PROCEDURE_RESUMED", "HMI_WINDOW_REQUEST", "ALARM"} <= set(by_type)


def _live_engine(tmp_path):
    control = SimulationControl()
    control.sim_time = 0.0
    control.updated = time.monotonic()
    connector = ObservationConnector(control)
    connector.update({"TAG.PV": TagValue("TAG.PV", 5.0)})
    provider = CapturedTagProvider(connector)
    store = ProcedureStore(tmp_path / "live.sqlite")
    store.start_run("run", "procedure", "Procedure", "RUNNING")
    engine = TrainerProcedureEngine(
        provider,
        store,
        runtime_control=control,
        auto_confirm=True,
        operator_identity="operator",
    )
    return engine, store


def test_live_equipment_output_uses_authorization_checked_write_and_audit(tmp_path):
    engine, store = _live_engine(tmp_path)
    authorized = []
    applied = []
    engine.output_bindings = {"PUMP.CMD": "AREA/PUMP/CMD"}
    engine.output_authorize_fn = lambda _step, _prompt, payload: authorized.append(payload) or True

    def apply(_step, path, value):
        applied.append((path, value))
        return WriteResult(True)

    engine.output_apply_fn = apply
    step = AdvisoryStep.model_validate(
        _catalog_step("equipment.pump_start", "start", tag="PUMP.CMD", value=1)
    )
    assert engine._step_write_tag("run", step) == "passed"
    assert len(authorized) == 1
    assert applied == [("AREA/PUMP/CMD", 1)]
    event_types = {row["event_type"] for row in store.run_events("run")}
    assert {"PCS_OUTPUT_CONFIRMATION", "PCS_OUTPUT_APPLIED"} <= event_types
    assert "ADVISORY_WRITE_PROPOSAL" not in event_types


def test_live_pause_hmi_window_and_hmi_alarm_have_real_host_callbacks(tmp_path):
    engine, store = _live_engine(tmp_path)
    opened = []
    alarms = []
    engine.hmi_request_fn = lambda _step, target: opened.append(target) or True
    engine.hmi_alarm_fn = lambda _step, alarm: alarms.append(alarm)
    pause = AdvisoryStep.model_validate(_catalog_step("flow.pause", "pause"))
    window = AdvisoryStep.model_validate(
        _catalog_step("message.hmi_window", "window", hmi_target="Overview")
    )
    alarm = AdvisoryStep.model_validate(_catalog_step("message.hmi_alarm", "alarm"))
    assert engine._step_hold("run", pause) == "passed"
    assert engine._step_instruction("run", window) == "passed"
    assert engine._step_alarm("run", alarm) == "alarm"
    assert opened == ["Overview"]
    assert alarms[0]["step_id"] == "alarm"
    event_types = {row["event_type"] for row in store.run_events("run")}
    assert {"PROCEDURE_PAUSED", "PROCEDURE_RESUMED", "HMI_WINDOW_REQUEST",
            "HMI_ALARM_RAISED"} <= event_types


def test_comparison_confirmation_timeout_uses_simulation_time(tmp_path):
    procedure = AdvisoryProcedure.model_validate({
        "procedure_id": "timed_confirmation",
        "name": "Timed confirmation",
        "mode": "advisory",
        "steps": [
            _catalog_step("message.confirmation", "confirm", timeout_sec=0.2),
        ],
    })
    run = ProcedureRun(tmp_path / "confirmation.sqlite")
    try:
        run.observe(0.0, {})
        run.start(ProcedureDefinition(procedure, {}).validate(), actor="operator")
        sim_time = 0.0
        deadline = time.monotonic() + 2
        while run.active and time.monotonic() < deadline:
            sim_time += 0.1
            run.observe(sim_time, {})
            time.sleep(0.01)
        assert not run.active
        assert run.result.status.value == "FAILED"
        assert "Timed out waiting for operator response" in run.result.message
    finally:
        assert run.close()


def test_application_adapters_are_versioned_bounded_and_timed():
    register_adapter(
        ProcedureAdapter(
            "test.slow",
            "1.0.0",
            lambda _inputs, _context: (time.sleep(0.3), {"done": True})[1],
        ),
        replace=True,
    )
    with pytest.raises(ValueError, match="requires version"):
        execute_adapter(
            "test.slow", "2.0.0", {}, run_id="run", step_id="step",
            actor="operator", variables={}, timeout_sec=0.1,
        )
    invalid = AdvisoryProcedure.model_validate({
        "procedure_id": "unknown_adapter_version",
        "name": "Unknown adapter version",
        "mode": "advisory",
        "steps": [
            _catalog_step(
                "integration.user_application",
                "adapter",
                adapter_id="azeo.identity",
                adapter_version="9.0.0",
            )
        ],
    })
    with pytest.raises(ValueError, match="requires version"):
        ProcedureDefinition(invalid, {}).validate()
    with pytest.raises(ValueError, match="non-finite"):
        execute_adapter(
            "test.slow", "1.0.0", {"bad": float("nan")}, run_id="run",
            step_id="step", actor="operator", variables={}, timeout_sec=0.1,
        )
    with pytest.raises(TimeoutError, match="timed out"):
        execute_adapter(
            "test.slow", "1.0.0", {}, run_id="run", step_id="step",
            actor="operator", variables={}, timeout_sec=0.1,
        )
