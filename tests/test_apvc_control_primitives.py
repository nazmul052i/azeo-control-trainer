"""Focused contracts used by the generated APVC control topology."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.pid.core import (  # noqa: E402
    LimitStatus as CoreLimit,
    Mode,
    PIDBlock as PIDCore,
    ScaleRange,
    SignalStatus,
    Structure,
)
from azeo_control_trainer.core.datastore.shared_data_store import (  # noqa: E402
    SharedDataStore,
)
from azeo_control_trainer.core.strategy.blocks.apc_blocks import (  # noqa: E402
    SPHandoffBlock,
)
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock  # noqa: E402
from azeo_control_trainer.core.strategy.blocks.signal_blocks import (  # noqa: E402
    RemoteAnalogBlock,
)
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge  # noqa: E402
from azeo_control_trainer.core.strategy.engine.compiler import (  # noqa: E402
    compile_strategy,
)
from azeo_control_trainer.core.strategy.engine.runtime import (  # noqa: E402
    StrategyRuntime,
)
from azeo_control_trainer.core.strategy.engine.runtime_context import (  # noqa: E402
    RuntimeContext,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import (  # noqa: E402
    StrategyGraph,
)
from azeo_control_trainer.core.strategy.model.terminal import (  # noqa: E402
    LimitStatus,
    Quality,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    load_strategy,
)


PROJECT = ROOT / "projects" / "AzeoPlantVirtualController"


class _NoSnapshotStore(SharedDataStore):
    """Fail if REMOTE_ANALOG regresses to a whole-store copy per block."""

    def __init__(self):
        super().__init__()
        self.get_calls: list[str] = []

    def get(self, key, default=None):
        self.get_calls.append(key)
        return super().get(key, default)

    def get_all(self):  # pragma: no cover - a regression makes this fail
        raise AssertionError("REMOTE_ANALOG must use O(1) keyed reads")


def test_remote_analog_carries_value_quality_limit_and_holds_missing() -> None:
    store = _NoSnapshotStore()
    store.set_many({
        "ctrl.SLAVE.BKCAL_OUT": 37.5,
        "ctrl.SLAVE.BKCAL_OUT.quality": "UNCERTAIN",
        "ctrl.SLAVE.BKCAL_OUT.limit": "HIGH_LIMITED",
    })
    remote = RemoteAnalogBlock("FROM_SLAVE")
    remote.config.params.update({
        "path": "ctrl.SLAVE.BKCAL_OUT",
        "default": 12.0,
        "missing_is_bad": True,
    })
    remote.runtime_context = RuntimeContext(store=store, plugin_id="test")
    remote.execute(0.2)
    assert remote.get_output("OUT") == pytest.approx(37.5)
    assert remote.outputs["OUT"].status is Quality.UNCERTAIN
    assert remote.outputs["OUT"].limit is LimitStatus.HIGH_LIMITED
    assert store.get_calls == [
        "ctrl.SLAVE.BKCAL_OUT",
        "ctrl.SLAVE.BKCAL_OUT.quality",
        "ctrl.SLAVE.BKCAL_OUT.limit",
    ]

    # Provider loss retains the last numeric sample but marks it unusable.
    missing = _NoSnapshotStore()
    remote.runtime_context = RuntimeContext(store=missing, plugin_id="test")
    remote.execute(0.2)
    assert remote.get_output("OUT") == pytest.approx(37.5)
    assert remote.outputs["OUT"].status is Quality.BAD
    assert remote.outputs["OUT"].limit is LimitStatus.NOT_LIMITED


def test_bad_handoff_target_invalidates_an_owned_remote_demand() -> None:
    graph = StrategyGraph("typed-handoff")
    handoff = SPHandoffBlock("TO_SLAVE")
    handoff.config.params.update({
        "controller_tag": "SLAVE",
        "target_parameter": "CAS_IN",
        "required_modes": "CAS",
        "ramp_rate": 0.0,
    })
    graph.add_block(handoff)
    store = SharedDataStore()
    bridge = DataBridge(store)
    handoff.set_controller_mode("CAS")
    handoff.inputs["ACTIVE"].value = True
    handoff.inputs["DCS_SP"].value = 40.0
    handoff.inputs["DCS_SP"].status = Quality.GOOD
    handoff.inputs["APC_SP"].value = 60.0
    handoff.inputs["APC_SP"].status = Quality.BAD

    handoff.execute(0.2)
    bridge.write_outputs(graph)
    assert not handoff.effective_active
    assert store.drain_writes() == [
        ("ctrl.SLAVE.wb.CAS_IN", 40.0),
        ("ctrl.SLAVE.wb.CAS_IN.quality", "BAD"),
        ("ctrl.SLAVE.wb.CAS_IN.limit", "NOT_LIMITED"),
    ]
    assert handoff.outputs["TARGET_SP"].status is Quality.BAD

    handoff.inputs["APC_SP"].status = Quality.GOOD
    handoff.execute(0.2)
    bridge.write_outputs(graph)
    assert handoff.effective_active
    assert store.drain_writes() == [
        ("ctrl.SLAVE.wb.CAS_IN", 60.0),
        ("ctrl.SLAVE.wb.CAS_IN.quality", "GOOD"),
        ("ctrl.SLAVE.wb.CAS_IN.limit", "NOT_LIMITED"),
    ]

    # Removing the request must revoke the resident Good CAS_IN.  Merely
    # stopping writes leaves the last command reusable on a later CAS entry.
    handoff.inputs["ACTIVE"].value = False
    handoff.execute(0.2)
    bridge.write_outputs(graph)
    assert store.drain_writes() == [
        ("ctrl.SLAVE.wb.CAS_IN", 60.0),
        ("ctrl.SLAVE.wb.CAS_IN.quality", "BAD"),
        ("ctrl.SLAVE.wb.CAS_IN.limit", "NOT_LIMITED"),
    ]
    handoff.execute(0.2)
    bridge.write_outputs(graph)
    assert not store.drain_writes()


@pytest.mark.parametrize("reset_impl,dynamic", [
    ("external", False),
    ("positive_feedback", True),
])
def test_external_reset_removes_feedforward_before_integration(
        reset_impl: str, dynamic: bool) -> None:
    pid = PIDCore("FF_RESET")
    pid.pv_scale = ScaleRange(0.0, 100.0)
    pid.out_scale = ScaleRange(0.0, 100.0)
    pid.gain = 1.0
    pid.reset = 1.0
    pid.structure = Structure.ID_ON_ERROR
    pid.reset_impl = reset_impl
    pid.dynamic_reset_limit = dynamic
    pid.ff_enable = True
    pid.ff_gain = 1.0
    pid.ff_scale = ScaleRange(0.0, 100.0)
    pid.FF_VAL = SignalStatus(value=10.0)
    pid.BKCAL_IN = SignalStatus(value=20.0, limit=CoreLimit.NOT_LIMITED)
    pid.condition_for_output(pv=40.0, sp=50.0, output=20.0)
    pid.set_target_mode(Mode.Auto)

    for _ in range(20):
        pid.IN = SignalStatus(value=40.0)
        pid.BKCAL_IN = SignalStatus(value=20.0, limit=CoreLimit.NOT_LIMITED)
        pid.execute(0.1)
        assert pid.OUT.value == pytest.approx(20.0, abs=1e-9)


def test_cascade_limit_and_slew_apply_to_working_setpoint() -> None:
    pid = PIDCore("CAS_SLEW")
    pid.pv_scale = ScaleRange(0.0, 200.0)
    pid.out_scale = ScaleRange(0.0, 100.0)
    pid.SP = pid.SP_WRK = 100.0
    pid._sp_filt = 100.0
    pid.sp_lo_lim = 68.0
    pid.sp_hi_lim = 120.0
    pid.sp_rate_up = pid.sp_rate_dn = 0.15
    pid.sp_rate_keeps_target = True
    pid.obey_sp_lim_cas_rcas = True
    pid.CAS_IN = SignalStatus(value=180.0)
    pid.BKCAL_IN = SignalStatus(value=100.0, limit=CoreLimit.NOT_LIMITED)
    pid.IN = SignalStatus(value=100.0)
    pid.set_target_mode(Mode.Cas)

    pid.execute(2.0)
    assert pid.actual_mode is Mode.Cas
    assert pid.SP_WRK == pytest.approx(100.3)
    for _ in range(100):
        pid.IN = SignalStatus(value=100.0)
        pid.CAS_IN = SignalStatus(value=180.0)
        pid.execute(2.0)
    assert pid.SP_WRK <= 120.0


def test_bridge_primes_first_cascade_scan_then_consumes_cas_writeback() -> None:
    graph = StrategyGraph("cascade-writeback")
    pid = PIDBlock("FIC-TEST")
    pid.config.params.update({
        "GAIN": 1.0,
        "RESET": 60.0,
        "structure": "pi_error_d_pv",
        "pv_scale_lo": 0.0,
        "pv_scale_hi": 200.0,
        "sp_lo": 0.0,
        "sp_hi": 200.0,
        "sp_init": 50.0,
        "mode": "MAN",
        "normal_mode": "CAS",
        "sp_pv_track_man": False,
    })
    pid._apply_config()
    graph.add_block(pid)
    store = SharedDataStore()
    store.set_many({
        "ctrl.FIC-TEST.wb.Mode": "CAS",
        "ctrl.FIC-TEST.SP_WRK": 50.0,
    })
    runtime = StrategyRuntime()
    runtime.set_context(RuntimeContext(store=store, plugin_id="test"))
    runtime.load(compile_strategy(graph), DataBridge(store))
    assert runtime.go_online()
    try:
        # Production slaves receive this invitation from their AO/BKCAL
        # chain; the focused one-block graph supplies the same status directly.
        pid.pid_core_block.BKCAL_IN.limit = CoreLimit.NOT_LIMITED
        runtime.execute_scan(0.2)
        assert pid.get_output("MODE") == "CAS"
        assert pid.get_output("SP_WRK") == pytest.approx(50.0)

        store.set("ctrl.FIC-TEST.wb.CAS_IN", 150.0)
        runtime.execute_scan(0.2)
        assert pid.get_output("MODE") == "AUTO"
        assert pid.get_output("SP_WRK") == pytest.approx(50.0)

        store.set("ctrl.FIC-TEST.wb.CAS_IN.quality", "GOOD")
        store.set("ctrl.FIC-TEST.wb.CAS_IN.limit", "NOT_LIMITED")
        # A Bad cascade demand deliberately shed the loop to AUTO.  Restoring
        # signal quality does not silently return control ownership.
        store.set("ctrl.FIC-TEST.wb.Mode", "CAS")
        runtime.execute_scan(0.2)
        assert pid.get_output("SP_WRK") == pytest.approx(150.0)
    finally:
        runtime.go_offline()


@pytest.mark.parametrize("module", ["TIC-5002", "TIC-6002"])
def test_column_steam_cap_is_quality_strict(module: str) -> None:
    graph, _comments = load_strategy(PROJECT / "control" / f"{module}.json")
    low = next(
        block for block in graph.blocks.values()
        if block.instance_name == "STEAM_QUALITY_STRICT_LOW_SELECT"
    )
    assert low.block_type == "ACT"
    assert low.config.params["EXPRESSION"] == "OUT1 = min(IN1, IN2, 16.5)"
    assert not any(
        block.instance_name in {"STEAM_HEADER_CAP", "STEAM_LOW_SELECT"}
        for block in graph.blocks.values()
    )

    for demand_quality, cap_quality, expected_quality in (
        (Quality.GOOD, Quality.GOOD, Quality.GOOD),
        (Quality.BAD, Quality.GOOD, Quality.BAD),
        (Quality.GOOD, Quality.BAD, Quality.BAD),
        (Quality.BAD, Quality.BAD, Quality.BAD),
    ):
        low.inputs["IN1"].value = 14.0
        low.inputs["IN1"].status = demand_quality
        low.inputs["IN2"].value = 12.0
        low.inputs["IN2"].status = cap_quality
        low.execute(0.2)
        assert low.get_output("OUT1") == pytest.approx(12.0)
        assert low.outputs["OUT1"].status is expected_quality
