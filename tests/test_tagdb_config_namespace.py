"""Configuration and terminal names remain independently addressable."""
from __future__ import annotations

# Standalone invocation is part of this repository's test contract.
# ruff: noqa: E402

import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer.core.strategy.blocks.signal_blocks import AlarmBlock
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase


def test_alarm_terminal_and_config_limit_use_distinct_namespaces(caplog) -> None:
    alarm = AlarmBlock("ALM1")
    alarm.config.params["DEV_HI"] = 2.5
    graph = StrategyGraph("FY-0101")
    graph.add_block(alarm)

    with caplog.at_level(logging.WARNING, logger="strategy.tagdb"):
        database = TagDatabase.from_graphs([graph])

    terminal = database.lookup("FY-0101/ALM1/DEV_HI")
    configured_limit = database.lookup("FY-0101/ALM1/CONFIG/DEV_HI")

    assert terminal is not None
    assert terminal.kind == EntryKind.TERMINAL
    assert terminal.data_type == "BOOL"
    assert configured_limit is not None
    assert configured_limit.kind == EntryKind.PARAMETER
    assert configured_limit.data_type == "FLOAT"
    assert configured_limit.value == 2.5
    assert not [record for record in caplog.records
                if "duplicate path" in record.getMessage()]


def test_every_configuration_parameter_has_an_explicit_config_leg() -> None:
    alarm = AlarmBlock("ALM1")
    graph = StrategyGraph("FY-0101")
    graph.add_block(alarm)

    parameters = TagDatabase.from_graphs([graph]).of_kind(EntryKind.PARAMETER)

    assert parameters
    assert all(entry.path.startswith("FY-0101/ALM1/CONFIG/")
               for entry in parameters)
