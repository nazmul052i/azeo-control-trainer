"""Navigation icons must resolve a target before becoming clickable."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import azeo_control_trainer.core.hmi.pvms  # noqa: E402,F401
from azeo_control_trainer.core.hmi.pvms.base import Pvm, registry  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.faceplate_actions import (  # noqa: E402
    resolve_associated_dcc,
    resolve_module_faceplate,
)
from azeo_control_trainer.core.hmi.pvms.render import (  # noqa: E402
    PvmFaceplateWidget,
)
from azeo_control_trainer.azeo_operator_station.console import (  # noqa: E402
    LiveStation,
)
from azeo_control_trainer.azeo_graphics_designer.studio.assembler import (  # noqa: E402
    PvmStudio,
)


def _pvm(block_type: str, path: str = "M/FB", **params) -> Pvm:
    return Pvm(
        id="source", pvm_class="Source", block_type=block_type,
        role="dynamo_compact", params=params or {"path": path},
    )


def _block(block_type: str, name: str):
    return SimpleNamespace(block_type=block_type, instance_name=name)


def _engine(*blocks):
    graph = SimpleNamespace(blocks={
        str(index): block for index, block in enumerate(blocks)
    })
    source = SimpleNamespace(_graphs=lambda: {"M": graph})
    return SimpleNamespace(_source=source)


def test_module_faceplate_resolves_only_one_real_module_shell() -> None:
    source = _pvm("AT")
    engine = _engine(_block("AT", "FB"), _block("PID", "PID1"))
    target = resolve_module_faceplate(source, engine)

    assert target is not None
    assert target.block_type == "PID"
    assert target.params == {"path": "M/PID1"}

    ambiguous = _engine(
        _block("AT", "FB"), _block("PID", "PID1"),
        _block("AI", "AI1"),
    )
    assert resolve_module_faceplate(source, ambiguous) is None


def test_composite_params_never_inherit_first_block_navigation() -> None:
    composite = _pvm(
        "PID", master="M/PID1", slave="M/PID2")
    engine = _engine(_block("PID", "PID1"), _block("PID", "PID2"))

    assert resolve_module_faceplate(composite, engine) is None
    pvm_class = registry.get("PID", "faceplate", "cascade_pair")
    widget_stub = SimpleNamespace(pvm=pvm_class())
    assert PvmFaceplateWidget.serviceable_actions(widget_stub) == set()


def test_dcc_resolver_requires_exactly_one_conditions_faceplate() -> None:
    source = _pvm("PID", "M/PID1")
    one = _engine(
        _block("PID", "PID1"),
        _block("MOTOR_INTERLOCK", "DCC1"),
    )
    target = resolve_associated_dcc(source, one)
    assert target is not None
    assert target.block_type == "MOTOR_INTERLOCK"
    assert target.params == {"path": "M/DCC1"}

    two = _engine(
        _block("PID", "PID1"),
        _block("MOTOR_INTERLOCK", "DCC1"),
        _block("MOTOR_INTERLOCK", "DCC2"),
    )
    assert resolve_associated_dcc(source, two) is None
    assert resolve_associated_dcc(source, _engine(
        _block("PID", "PID1"))) is None


def test_station_and_studio_route_resolved_targets_not_original_pvm() -> None:
    source = _pvm("AT")
    engine = _engine(_block("AT", "FB"), _block("PID", "PID1"))

    station_opened = []
    station = SimpleNamespace(open_faceplate=lambda pvm, engine=None:
                              station_opened.append((pvm, engine)))
    LiveStation.faceplate_action(
        station, "faceplate", source, engine, origin_kind="faceplate")
    assert station_opened[0][0].params == {"path": "M/PID1"}
    assert station_opened[0][1] is engine

    studio_opened = []
    studio = SimpleNamespace(
        engine=engine,
        open_faceplate=studio_opened.append,
    )
    PvmStudio.faceplate_action(
        studio, "faceplate", source, origin_kind="faceplate")
    assert studio_opened[0].params == {"path": "M/PID1"}

    # A detail display's return button is different: its target is the
    # original module faceplate, so it intentionally bypasses FB resolution.
    studio_opened.clear()
    PvmStudio.faceplate_action(
        studio, "faceplate", source, origin_kind="detail")
    assert studio_opened == [source]


def test_station_and_studio_route_only_unambiguous_dcc() -> None:
    source = _pvm("PID", "M/PID1")
    engine = _engine(
        _block("PID", "PID1"),
        _block("MOTOR_INTERLOCK", "DCC1"),
    )

    station_opened = []
    station = SimpleNamespace(open_faceplate=lambda pvm, engine=None:
                              station_opened.append(pvm))
    LiveStation.faceplate_action(station, "dcc", source, engine)
    assert station_opened[0].params == {"path": "M/DCC1"}

    studio_opened = []
    studio = SimpleNamespace(engine=engine, open_faceplate=studio_opened.append)
    PvmStudio.faceplate_action(studio, "dcc", source)
    assert studio_opened[0].params == {"path": "M/DCC1"}


def test_studio_history_routes_to_process_history_view() -> None:
    """The History icon must not masquerade a tuning popup as PHV."""
    source = _pvm("PID", "M/PID1")
    opened = []
    studio = SimpleNamespace(
        _module_for=PvmStudio._module_for,
        open_process_history=opened.append,
        open_tuning_trend=lambda _pvm: (_ for _ in ()).throw(
            AssertionError("History routed to the tuning trend")),
    )

    PvmStudio.faceplate_action(studio, "history", source)

    assert opened == ["M"]
