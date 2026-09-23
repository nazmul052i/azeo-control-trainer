"""Every procedure comparison claim resolves to executable code or an honest gap."""
from importlib import import_module
from pathlib import Path
import sys

import yaml
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from azeo_control_trainer.core.procedures.capabilities import (
    ParityStatus,
    block_capabilities,
    capability_inventory,
    markdown_inventory,
)
from azeo_control_trainer.core.procedures.documentation import export_sop, format_sop_markdown
from azeo_control_trainer.core.procedures.audit import ProcedureStore
from azeo_control_trainer.core.procedures.model import AdvisoryProcedure, ProcedureDefinition
from azeo_control_trainer.core.procedures.trial import run_isolated_trial
from azeo_control_trainer.core.pa_designer.connectors.advisory_tag_provider import AdvisoryTagProvider
from azeo_control_trainer.core.pa_designer.connectors.read_only_connector import SimulatedReadOnlyConnector
from azeo_control_trainer.core.pa_designer.reports import build_run_report


def _resolve(reference):
    module_name, _, attribute = reference.partition(":")
    target = import_module(module_name)
    if attribute:
        for part in attribute.split("."):
            target = getattr(target, part)
    return target


def test_every_comparison_block_has_one_parity_classification():
    source = yaml.safe_load(
        Path("src/azeo_control_trainer/core/pa_designer/library/definitions/consolidated_catalog.yaml").read_text(encoding="utf-8")
    )
    expected = {"block." + row["block_id"] for row in source["blocks"]}
    actual = {row.key for row in block_capabilities()}
    assert actual == expected
    assert len(actual) == len(block_capabilities())


def test_every_positive_parity_claim_resolves_to_code():
    positive = {ParityStatus.IMPLEMENTED, ParityStatus.COMPOSED, ParityStatus.PARTIAL}
    for row in capability_inventory():
        if row.status in positive:
            assert row.implementation, row.key
            assert _resolve(row.implementation) is not None, row.key
        if row.status in {ParityStatus.MISSING, ParityStatus.DEPARTURE, ParityStatus.PARTIAL}:
            assert row.note, row.key
    rendered = markdown_inventory()
    assert "block.equipment.ramp_up_down" in rendered
    assert "operation.concurrent_runs" in rendered
    assert "legacy.vb_activex" in rendered


def test_human_gap_report_covers_every_nonimplemented_capability():
    report = Path("docs/PROCEDURE_PARITY_GAP_REPORT.md").read_text(encoding="utf-8")
    for row in capability_inventory():
        if row.status is not ParityStatus.IMPLEMENTED:
            assert f"`{row.key}`" in report, row.key
    assert "| Implemented | 38 |" in report
    assert "| Composed equivalent | 0 |" in report
    assert "| Partial | 0 |" in report
    assert "| Missing | 0 |" in report
    assert "| Deliberate departure | 0 |" in report


def _ramp_definition():
    procedure = AdvisoryProcedure.model_validate({
        "procedure_id": "ramp_trial",
        "name": "Ramp trial",
        "mode": "advisory",
        "metadata": {"version": "2.0", "owner": "Operations", "approval_status": "approved"},
        "tags": [{"tag": "LOOP.SP", "access": "read_write", "data_type": "float"}],
        "steps": [
            {"id": "ramp", "type": "ramp_tag", "tag": "LOOP.SP", "start": 0, "end": 3,
             "rate_per_sec": 1, "poll_sec": 1, "require_confirmation": True},
            {"id": "verify", "type": "wait_until", "condition": "LOOP.SP == 3", "poll_sec": 1,
             "timeout_sec": 5},
            {"id": "done", "type": "complete"},
        ],
    })
    return ProcedureDefinition(procedure, {"LOOP.SP": "UNIT/PID/SP"}).validate()


def test_isolated_trial_executes_ramp_without_a_live_write_path(tmp_path):
    definition = _ramp_definition()
    result = run_isolated_trial(definition, {}, tmp_path / "trial.sqlite")
    assert result.execution.status.value == "COMPLETE"
    assert result.final_values["LOOP.SP"] == 3
    assert [row["value"] for row in result.writes] == [0, 1.0, 2.0, 3]
    metadata = build_run_report(
        ProcedureStore(tmp_path / "trial.sqlite"), result.execution.run_id
    )["run"]["metadata_json"]
    assert metadata["execution_environment"] == "isolated_trial"
    assert metadata["live_outputs_enabled"] is False


def test_ramp_schedule_is_bounded_before_a_run_can_start():
    with pytest.raises(ValueError, match="10000 updates"):
        AdvisoryProcedure.model_validate({
            "procedure_id": "unbounded_ramp",
            "name": "Unbounded ramp",
            "mode": "advisory",
            "steps": [{"id": "ramp", "type": "ramp_tag", "tag": "LOOP.SP",
                       "start": 0, "end": 100, "rate_per_sec": 0.001, "poll_sec": 0.001}],
        })


def test_declined_output_confirmation_happens_before_any_proposal(tmp_path):
    procedure = AdvisoryProcedure.model_validate({
        "procedure_id": "confirm_output",
        "name": "Confirm output",
        "mode": "advisory",
        "tags": [{"tag": "LOOP.SP", "access": "read_write", "data_type": "float"}],
        "steps": [{"id": "set", "type": "write_tag", "tag": "LOOP.SP", "value": 5,
                   "require_confirmation": True}],
    })
    provider = AdvisoryTagProvider(SimulatedReadOnlyConnector({"LOOP.SP": 0}))
    store = ProcedureStore(tmp_path / "declined.sqlite")
    from azeo_control_trainer.core.procedures.trial import _TrialEngine
    result = _TrialEngine(provider, store, confirm_fn=lambda _step, _prompt: False).run(procedure)
    assert result.status.value == "FAILED"
    assert provider.proposed_writes == []
    events = store.run_events(result.run_id)
    assert any(row["event_type"] == "PCS_OUTPUT_CONFIRMATION"
               and row["data_json"]["confirmed"] is False for row in events)


def test_self_document_is_derived_from_the_executable_revision(tmp_path):
    definition = _ramp_definition()
    document = format_sop_markdown(definition)
    assert "# Ramp trial" in document
    assert definition.digest in document
    assert "LOOP.SP" in document and "UNIT/PID/SP" in document
    assert "0 → 3" in document
    target = export_sop(definition, tmp_path / "ramp-sop.html")
    assert target.read_text(encoding="utf-8").startswith("<!doctype html>")
