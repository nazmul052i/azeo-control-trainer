"""Module-level parameters have one collision-free live address."""
from __future__ import annotations

# Standalone invocation is part of this repository's test contract.
# ruff: noqa: E402

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource
from azeo_control_trainer.core.strategy.blocks.utility_blocks import ConstantBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase


def _graph() -> StrategyGraph:
    graph = StrategyGraph("M-101")
    graph.extra["parameters"] = {
        "REMOTE_SP": {
            "value": 12.5,
            "access": "input",
            "data_type": "FLOAT",
            "units": "bar",
            "description": "Remote pressure demand",
        },
        "CALCULATED": {
            "value": 7,
            "access": "output",
            "data_type": "INT",
        },
        "FIXED_BIAS": {
            "value": True,
            "access": "internal_read",
        },
        "SCRATCH": {
            "value": "ready",
            "access": "internal_write",
        },
    }
    constant = ConstantBlock("K1")
    constant.config.params["value"] = 3.0
    constant._apply_config()
    graph.add_block(constant)
    return graph


def test_tag_database_indexes_module_parameters_at_canonical_path() -> None:
    database = TagDatabase.from_graphs([_graph()])

    public_input = database.lookup("M-101/PARAMETERS/REMOTE_SP")
    public_output = database.lookup("M-101/PARAMETERS/CALCULATED")
    internal_read = database.lookup("M-101/PARAMETERS/FIXED_BIAS")
    internal_write = database.lookup("M-101/PARAMETERS/SCRATCH")

    assert public_input is not None
    assert public_input.kind == EntryKind.PARAMETER
    assert public_input.data_type == "FLOAT"
    assert public_input.unit == "bar"
    assert public_input.description == "Remote pressure demand"
    assert public_input.direction == "input"
    assert public_input.writable
    assert public_output is not None
    assert public_output.direction == "output"
    assert not public_output.writable
    assert internal_read is not None
    assert internal_read.data_type == "BOOL"
    assert internal_read.direction == "internal"
    assert not internal_read.writable
    assert internal_write is not None
    assert internal_write.direction == "internal"
    assert not internal_write.writable


def test_live_source_reads_all_module_parameters_but_writes_only_inputs() -> None:
    graph = _graph()
    graph.module_parameters()["CALCULATED"].update(
        status="UNCERTAIN", limit="HIGH_LIMITED")
    source = LiveGraphSource(lambda: {graph.name: graph})

    result = source.read("M-101/PARAMETERS/REMOTE_SP")
    assert result.value == 12.5
    assert result.quality is Quality.GOOD
    assert result.units == "bar"
    assert result.module_running is True
    calculated = source.read("M-101/PARAMETERS/CALCULATED")
    assert calculated.value == 7
    assert calculated.quality is Quality.UNCERTAIN
    assert source.read("M-101/PARAMETERS/FIXED_BIAS").value is True
    assert source.read("M-101/PARAMETERS/SCRATCH").value == "ready"

    assert source.can_write("M-101/PARAMETERS/REMOTE_SP").success
    assert source.write("M-101/PARAMETERS/REMOTE_SP", "18.75").success
    assert graph.module_parameters()["REMOTE_SP"]["value"] == 18.75
    for name in ("CALCULATED", "FIXED_BIAS", "SCRATCH"):
        refusal = source.can_write(f"M-101/PARAMETERS/{name}")
        assert not refusal.success
        assert "read-only" in refusal.error


def test_parameter_leg_does_not_change_block_terminal_addressing() -> None:
    graph = _graph()
    source = LiveGraphSource(lambda: {graph.name: graph})

    # Three-component block paths remain terminal paths unless the middle
    # component is the reserved PARAMETERS namespace leg.
    assert source.read("M-101/K1/OUT").value == 3.0
    missing = source.read("M-101/PARAMETERS/OUT")
    assert missing.quality is Quality.BAD
    assert missing.value is None
