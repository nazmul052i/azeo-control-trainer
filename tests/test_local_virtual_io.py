"""Configuration-driven Local Virtual-I/O boundary qualification."""
from __future__ import annotations

# Standalone invocation is part of this repository's test contract.
# ruff: noqa: E402

import json
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from azeo_control_trainer import app as trainer_app
from azeo_control_trainer.connectivity.fieldio.dynamic_provider import (
    ProviderConfigurationError,
    ProviderSession,
    load_provider,
)
from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (
    LocalVirtualIoDriver,
    UnavailableLocalVirtualIoDriver,
    load_signal_routes,
)
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.strategy.blocks.io_blocks import (
    AIBlock,
    AOBlock,
    DIBlock,
    DOBlock,
)
from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
from azeo_control_trainer.core.strategy.blocks.signal_blocks import ScalerBlock
from azeo_control_trainer.core.strategy.engine.bridge import DataBridge
from azeo_control_trainer.core.strategy.engine.compiler import compile_strategy
from azeo_control_trainer.core.strategy.engine.runtime import StrategyRuntime
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality
from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy
from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase


class _Session:
    def __init__(self, options):
        self.options = options
        self.started = 0
        self.stopped = 0
        self.claimed = None
        self.released = None
        self.writes = []
        self.fail_writes = set()
        self.defer_writes = False
        self.read_sample_calls = []
        self.samples = {
            "AI-1": (42.5, 1, time.time()),
            "DI-1": {"value": True, "quality": "GOOD",
                     "timestamp": time.time()},
            "AO-1": (64.3263, "GOOD", time.time()),
        }
        if "output_sample" in options:
            self.samples["AO-1"] = options["output_sample"]

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def claim(self, source):
        self.claimed = source
        return True

    def release(self, source):
        self.released = source

    def read_sample(self, tag):
        self.read_sample_calls.append(tag)
        return self.samples[tag]

    def read(self, tag):
        return self.samples[tag][0]

    def write(self, tag, value):
        if tag in self.fail_writes:
            raise RuntimeError(f"cannot write {tag}")
        self.writes.append((tag, value))
        if not self.defer_writes:
            self.samples[tag] = (value, "GOOD", time.time())

    def health(self):
        return {"connected": bool(self.started and not self.stopped)}


@pytest.fixture
def provider_module(monkeypatch):
    module = types.ModuleType("test_vio_provider")
    made = []

    def create_embedded_plant(options):
        session = _Session(options)
        made.append(session)
        return session

    module.create_embedded_plant = create_embedded_plant
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module.__name__, made


def _config(module_name: str) -> dict:
    return {
        "type": "local_virtual_io",
        "name": "Qualification VIO",
        "source": "TEST-CTRL-1",
        "period_ms": 60_000,
        "input_stale_timeout_s": 2.0,
        "claim_outputs": True,
        "provider_manages_claim": False,
        "provider": {
            "factory": f"{module_name}:create_embedded_plant",
            "options": {"open_loop": True},
        },
        "signals": {
            "FIELD.AI": {"signal": "AI-1", "direction": "read"},
            "FIELD.DI": {"signal": "DI-1", "direction": "read"},
            "FIELD.AO": {"signal": "AO-1", "direction": "write"},
        },
    }


def _write_provider(root: Path, module_name: str, marker: str) -> None:
    root.mkdir()
    (root / f"{module_name}.py").write_text(
        "class Session:\n"
        f"    marker = {marker!r}\n"
        "    def read(self, tag): return self.marker\n"
        "    def write(self, tag, value): pass\n"
        "def create(**options): return Session()\n",
        encoding="utf-8",
    )


def test_mapping_factory_is_selected_without_double_call(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    session = load_provider(_config(module_name), tmp_path)
    assert len(made) == 1
    assert made[0].options == {"open_loop": True}
    session.start()
    session.start()
    session.stop()
    assert made[0].started == 1
    assert made[0].stopped == 1


def test_factory_typeerror_is_not_retried_as_another_call_style(
        tmp_path, monkeypatch) -> None:
    module = types.ModuleType("broken_vio_provider")
    calls = []

    def create(**options):
        calls.append(options)
        raise TypeError("failure inside provider")

    module.create = create
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(TypeError, match="failure inside provider"):
        load_provider({"provider": {
            "factory": "broken_vio_provider:create",
            "options": {"answer": 42},
        }}, tmp_path)
    assert calls == [{"answer": 42}]


def test_provider_stop_unwinds_every_component_and_search_path(tmp_path) -> None:
    stopped = []

    class Adapter:
        def start(self):
            pass

        def stop(self):
            stopped.append("adapter")

    class Runtime:
        def start(self):
            pass

        def stop(self):
            stopped.append("runtime")
            raise RuntimeError("engine did not stop")

    inserted = str(tmp_path.resolve())
    sys.path.insert(0, inserted)
    runtime = Runtime()
    session = ProviderSession(
        product=runtime,
        transport=Adapter(),
        lifecycle=[runtime],
        search_paths=[inserted],
    )
    session.start()
    with pytest.raises(RuntimeError, match="failed to stop"):
        session.stop()
    assert stopped == ["runtime", "adapter"]
    assert inserted not in sys.path
    # All ownership state was unwound despite the first failure.
    session.stop()


def test_unset_environment_search_path_uses_valid_fallback(
        tmp_path, monkeypatch) -> None:
    module_path = tmp_path / "fallback_vio_provider.py"
    module_path.write_text(
        "class Session:\n"
        "    def read(self, tag): return 1\n"
        "    def write(self, tag, value): pass\n"
        "def create(**options): return Session()\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("NEVER_SET_AZEO_PROVIDER", raising=False)
    session = load_provider({"provider": {
        "factory": "fallback_vio_provider:create",
        "search_paths": ["${NEVER_SET_AZEO_PROVIDER}", str(tmp_path)],
    }}, tmp_path)
    assert session.transport.read("anything") == 1
    session.stop()
    sys.modules.pop("fallback_vio_provider", None)


def test_provider_path_is_leased_by_concurrent_sessions(tmp_path) -> None:
    """One session cannot remove a search path another still requires."""
    module_name = "leased_vio_provider"
    root = tmp_path / "shared-provider"
    _write_provider(root, module_name, "SHARED")
    resolved = str(root.resolve())
    config = {"provider": {
        "factory": f"{module_name}:create",
        "search_paths": [resolved],
    }}
    sys.modules.pop(module_name, None)
    sessions = []
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            sessions = list(pool.map(
                lambda _index: load_provider(config, tmp_path),
                range(2),
            ))
        assert sys.path.count(resolved) == 1
        sessions[0].stop()
        assert resolved in sys.path
        assert sessions[1].transport.read("PV") == "SHARED"
        sessions[1].stop()
        assert resolved not in sys.path
    finally:
        for session in sessions:
            session.stop()
        sys.modules.pop(module_name, None)


def test_same_provider_module_cannot_cross_configured_roots(tmp_path) -> None:
    """A cached module from controller A is never reused for controller B."""
    module_name = "conflicting_vio_provider"
    root_a = tmp_path / "provider-a"
    root_b = tmp_path / "provider-b"
    _write_provider(root_a, module_name, "A")
    _write_provider(root_b, module_name, "B")
    path_a = str(root_a.resolve())
    path_b = str(root_b.resolve())
    sys.modules.pop(module_name, None)
    session_a = None
    try:
        session_a = load_provider({"provider": {
            "factory": f"{module_name}:create",
            "search_paths": [path_a],
        }}, tmp_path)
        assert session_a.transport.read("PV") == "A"

        with pytest.raises(
                ProviderConfigurationError,
                match="already-imported provider module.*outside configured"):
            load_provider({"provider": {
                "factory": f"{module_name}:create",
                "search_paths": [path_b],
            }}, tmp_path)

        assert path_a in sys.path
        assert path_b not in sys.path
        assert session_a.transport.read("PV") == "A"
    finally:
        if session_a is not None:
            session_a.stop()
        sys.modules.pop(module_name, None)
    assert path_a not in sys.path


def test_installed_workspace_resolves_provider_from_immutable_bundle(
        tmp_path, monkeypatch) -> None:
    """A seeded project keeps its relative path while provider code stays installed."""
    from azeo_control_trainer.config import paths
    from azeo_control_trainer.connectivity.fieldio import dynamic_provider

    bundle = tmp_path / "application" / "current"
    workspace = tmp_path / "user" / "workspace"
    project = workspace / "projects" / "Demo"
    provider_root = bundle / "provider-root"
    project.mkdir(parents=True)
    bundle.mkdir(parents=True)
    (bundle / "installation.ini").write_text(
        "[Install]\nVersion=0.4.0\nComponents=runtime,help\n",
        encoding="utf-8",
    )
    module_name = "installed_layout_provider"
    _write_provider(provider_root, module_name, "INSTALLED")
    monkeypatch.setenv("AZEO_WORKSPACE_DIR", str(workspace))
    monkeypatch.setattr(paths, "project_root", lambda: bundle)
    monkeypatch.setattr(dynamic_provider, "project_root", lambda: bundle)
    monkeypatch.setattr(dynamic_provider, "workspace_root", lambda: workspace)

    escaped = dynamic_provider._search_path_candidates(
        Path("../../../outside-provider"), project)
    assert (bundle.parent / "outside-provider").resolve() not in escaped

    session = load_provider({"provider": {
        "factory": f"{module_name}:create",
        "search_paths": ["../../provider-root"],
    }}, project)
    try:
        assert session.product.marker == "INSTALLED"
        assert session.search_paths == [str(provider_root.resolve())]
    finally:
        session.stop()
        sys.modules.pop(module_name, None)


def test_catalog_drives_routes_without_compiled_signal_names(tmp_path) -> None:
    catalog = tmp_path / "signals.json"
    catalog.write_text(json.dumps({"tags": [
        {"name": "MEAS", "kind": "AI", "direction": "SIM_TO_DCS",
         "stale_timeout_sec": 2.5},
        {"name": "COMMAND", "kind": "DO", "direction": "DCS_TO_SIM"},
    ]}), encoding="utf-8")
    routes = load_signal_routes({
        "signal_catalog": {
            "path": "${PROJECT_DIR}/signals.json",
            "store_tag_template": "FIELD.{name}",
        },
    }, tmp_path)
    assert routes["FIELD.MEAS"].direction == "read"
    assert routes["FIELD.MEAS"].stale_timeout_s == 2.5
    assert routes["FIELD.COMMAND"].direction == "write"


def test_explicit_opc_metadata_does_not_become_local_signal_address(
        tmp_path) -> None:
    routes = load_signal_routes({"signals": {
        "FT-1001": {
            "node": "ns=2;s=FT-1001",
            "direction": "SIM_TO_DCS",
            "kind": "AI",
        },
    }}, tmp_path)
    assert routes["FT-1001"].signal == "FT-1001"


def test_explicit_routes_inherit_stale_timeout_from_catalog_alias(
        tmp_path) -> None:
    (tmp_path / "catalog.json").write_text(json.dumps({"tags": [{
        "name": "FT-1001", "kind": "AI", "direction": "SIM_TO_DCS",
        "stale_timeout_sec": 2.0,
    }]}), encoding="utf-8")
    routes = load_signal_routes({
        "catalog": "catalog.json",
        "signals": {
            "FT-1001": {"node": "ns=2;s=FT-1001", "kind": "AI",
                        "direction": "SIM_TO_DCS"},
        },
    }, tmp_path)
    assert routes["FT-1001"].signal == "FT-1001"
    assert routes["FT-1001"].stale_timeout_s == 2.0


def test_driver_preserves_metadata_and_enqueues_each_output_once(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    # This is the ordering app.py guarantees before start.
    store.field_io_driver = driver
    assert driver.start()
    session = made[0]

    ai = store.get_sample("FIELD.AI")
    assert ai is not None
    assert (ai.value, ai.quality, ai.stale) == (42.5, 1, False)
    assert store.get("FIELD.DI") is True
    assert store.get("FIELD.AO") == pytest.approx(64.3263)
    assert driver.outputs_seeded == 1
    assert session.claimed == "TEST-CTRL-1"

    store.queue_write("FIELD.AO", 55.0)
    store.queue_write("ctrl.LOOP.wb.SP", 60.0)
    driver.scan_once()
    driver.scan_once()
    assert session.writes == [("AO-1", 55.0)]
    assert store.get("FIELD.AO") == 55.0
    assert driver.writes_enqueued == 1
    assert driver.output_readbacks_published >= 2
    assert store.get("ctrl.LOOP.wb.SP") == 60.0
    assert driver.health()["provider"]["connected"] is True

    driver.stop()
    driver.stop()
    assert session.released == "TEST-CTRL-1"
    assert session.stopped == 1
    assert store.field_io_driver is driver
    stopped_sample = store.get_sample("FIELD.AI")
    assert stopped_sample is not None
    assert stopped_sample.stale and stopped_sample.quality == "BAD"


def test_stopped_driver_remains_owner_and_can_start_again(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver

    assert driver.start()
    first_session = made[0]
    driver.stop()
    assert store.field_io_driver is driver
    assert not driver.running
    assert first_session.released == "TEST-CTRL-1"

    driver.last_error = "fault from the previous provider session"
    assert driver.start()
    assert driver.running
    assert driver.last_error == ""
    assert len(made) == 2
    assert made[1] is not first_session
    assert made[1].claimed == "TEST-CTRL-1"
    restarted_sample = store.get_sample("FIELD.AI")
    assert restarted_sample is not None and not restarted_sample.stale

    driver.stop()
    assert store.field_io_driver is driver


def test_unavailable_driver_remains_declared_queue_owner(tmp_path) -> None:
    store = SharedDataStore()
    config = _config("provider_that_is_never_loaded")
    driver = UnavailableLocalVirtualIoDriver(
        store, config, RuntimeError("invalid provider"), project_dir=tmp_path)
    store.field_io_driver = driver

    driver.stop()

    assert store.field_io_driver is driver


def test_requested_output_is_not_acknowledged_until_actual_readback(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()
    session = made[0]
    session.defer_writes = True

    store.queue_write("FIELD.AO", 125.0)
    driver.scan_once()
    assert session.writes == [("AO-1", 125.0)]
    assert driver.writes_enqueued == 1
    # Transport acceptance only means queued.  It is not proof that a plant
    # step applied the request, and therefore must not overwrite readback.
    assert store.get("FIELD.AO") == pytest.approx(64.3263)

    session.samples["AO-1"] = (100.0, "GOOD", time.time())
    driver.scan_once()
    assert store.get("FIELD.AO") == pytest.approx(100.0)
    assert session.writes == [("AO-1", 125.0)]
    driver.stop()


def test_batch_read_acquires_inputs_and_output_readbacks_once(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()
    session = made[0]
    session.read_sample_calls.clear()
    batch_calls = []

    def read_samples(signals):
        batch_calls.append(list(signals))
        return {signal: session.samples[signal] for signal in signals}

    session.read_samples = read_samples
    driver.scan_once()
    assert batch_calls == [["AI-1", "DI-1", "AO-1"]]
    assert session.read_sample_calls == []
    assert store.get("FIELD.AI") == pytest.approx(42.5)
    assert store.get("FIELD.AO") == pytest.approx(64.3263)
    driver.stop()


def test_seeded_output_is_adopted_without_a_first_scan_bump(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()

    graph = StrategyGraph("bumpless-output-adoption")
    ao = AOBlock("AO1")
    ao.config.params.update({
        "tag": "FIELD.AO", "out_lo": 0.0, "out_hi": 100.0,
    })
    ao._apply_config()
    graph.add_block(ao)
    bridge = DataBridge(store)
    bridge.initialize_outputs(graph)
    assert ao.get_output("OUT") == pytest.approx(64.3263)

    ao.execute(0.2)
    bridge.write_outputs(graph)
    driver.scan_once()
    assert made[0].writes == [("AO-1", pytest.approx(64.3263))]
    driver.stop()


@pytest.mark.parametrize("action", ["reverse", "direct"])
def test_pid_chain_remains_bumpless_for_three_scans(
        tmp_path, provider_module, action) -> None:
    """The live AO readback conditions P, I and D for either action.

    A non-zero error is deliberate: the old ``OUT / gain`` reset appeared
    correct only at SP == PV, then added the entire proportional contribution
    on the first automatic scan.
    """
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()

    graph = StrategyGraph(f"bumpless-{action}-pid-chain")
    graph.scan_ms = 200
    ai = AIBlock("AI1")
    ai.config.params.update({"tag": "FIELD.AI", "scale_lo": 0.0,
                             "scale_hi": 100.0})
    ai._apply_config()
    pid = PIDBlock("PID1")
    pid.config.params.update({
        "GAIN": 2.0,
        "RESET": 120.0,
        "RATE": 4.0,
        "alpha": 0.1,
        "deriv_filter_mode": "azeo",
        "sp_init": 50.0,
        "action": action,
        "form": "standard",
        "structure": "pi_error_d_pv",
        "pv_scale_lo": 0.0,
        "pv_scale_hi": 100.0,
        "mode": "AUTO",
    })
    pid._apply_config()
    scaler = ScalerBlock("SCALE1")
    scaler.config.params.update({
        "in_lo": 0.0, "in_hi": 100.0,
        "out_lo": 0.0, "out_hi": 100.0,
    })
    ao = AOBlock("AO1")
    ao.config.params.update({
        "tag": "FIELD.AO", "out_lo": 0.0, "out_hi": 100.0,
        "mode": "CAS", "normal_mode": "CAS",
    })
    ao._apply_config()
    for block in (ai, pid, scaler, ao):
        graph.add_block(block)
    assert graph.add_wire(ai.id, "OUT", pid.id, "IN")
    assert graph.add_wire(pid.id, "OUT", scaler.id, "IN")
    assert graph.add_wire(scaler.id, "OUT", ao.id, "CAS_IN")
    assert graph.add_wire(ao.id, "BKCAL_OUT", scaler.id, "BKCAL_IN", True)
    assert graph.add_wire(scaler.id, "BKCAL_OUT", pid.id, "BKCAL_IN", True)

    runtime = StrategyRuntime()
    runtime.load(compile_strategy(graph), DataBridge(store))
    assert runtime.go_online()
    target = float(store.get("FIELD.AO"))
    try:
        for _ in range(3):
            runtime.execute_scan(0.2)
            driver.scan_once()
            assert float(pid.get_output("OUT")) == pytest.approx(target, abs=0.25)
            assert float(ao.get_output("OUT")) == pytest.approx(target, abs=0.25)
        # RATE was non-zero; zero derivative proves its state was seated on
        # the actual action-aware PV rather than the default zero reference.
        assert pid.pid_core_block.d_term == pytest.approx(0.0, abs=1e-12)
        assert len(made[0].writes) == 3
    finally:
        runtime.go_offline()
        driver.stop()


def test_real_fic_1001_stays_bumpless_when_provider_is_present(
        ) -> None:
    project = REPO / "projects" / "AzeoPlantVirtualController"
    if not (project / "_project.json").is_file():
        pytest.skip("generated virtual-controller project is not present")
    config = trainer_app._field_io_config(project)
    paths = (config.get("provider") or {}).get("search_paths") or ()
    available = any(
        "$" not in str(raw) and "%" not in str(raw)
        and (project / str(raw)).resolve().is_dir()
        for raw in paths
    )
    if not available:
        pytest.skip("configured embedded plant provider is not present")

    store = SharedDataStore()
    driver = trainer_app._attach_field_io(store, project)
    try:
        # The production project is now manual-start; this qualification asks
        # explicitly for a live provider because it measures bumpless adoption
        # rather than the Explorer startup policy.
        if not driver.running:
            assert driver.start(), driver.status()
        assert driver.running, driver.status()
        seed = store.get_sample("FCV-1001")
        assert seed is not None
        assert float(seed.value) != 0.0
        assert not seed.stale

        # Freeze a real provider snapshot for the algorithm check.  The
        # embedded plant continues evolving in its own thread; allowing a PV
        # change here would test normal controller response, not startup
        # conditioning in the absence of a process/operator change.
        pv_seed = store.get_sample("FT-1001")
        assert pv_seed is not None and not pv_seed.stale
        control_store = SharedDataStore()
        for tag, sample in (("FT-1001", pv_seed), ("FCV-1001", seed)):
            control_store.set_sample(
                tag, sample.value, quality=sample.quality,
                timestamp=sample.timestamp, stale=sample.stale,
            )

        graph, _comments = load_strategy(project / "control" / "FIC-1001.json")
        pid = next(block for block in graph.blocks.values()
                   if block.instance_name == "FIC-1001")
        ao = next(block for block in graph.blocks.values()
                  if block.instance_name == "FCV-1001")
        bridge = DataBridge(control_store)
        runtime = StrategyRuntime()
        runtime.load(compile_strategy(graph), bridge)
        assert runtime.go_online()
        target = float(seed.value)
        assert float(ao.get_output("OUT")) == pytest.approx(target)
        runtime.execute_scan(0.2)
        assert float(pid.get_output("OUT")) == pytest.approx(
            target, abs=1e-9,
        ), (
            pid.get_output("PV"), pid.get_output("SP"),
            pid.get_output("P_TERM"), pid.get_output("I_TERM"),
            pid.get_output("D_TERM"),
        )
        assert float(ao.get_output("OUT")) == pytest.approx(target, abs=1e-9)
        runtime.go_offline()
        assert driver.outputs_seeded == len(driver.write_routes) == 173
    finally:
        driver.stop()


def test_discrete_output_adopts_seed_with_inversion() -> None:
    store = SharedDataStore()
    store.set_sample("FIELD.DO", True, quality="GOOD")
    graph = StrategyGraph("bumpless-discrete-output")
    output = DOBlock("DO1")
    output.config.params.update({"tag": "FIELD.DO", "invert": True})
    output._apply_config()
    graph.add_block(output)

    bridge = DataBridge(store)
    bridge.initialize_outputs(graph)
    output.execute(0.2)
    bridge.write_outputs(graph)
    assert store.drain_writes() == [("FIELD.DO", True)]


def test_stale_provider_timestamp_publishes_bad_sample(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    config = _config(module_name)
    config["signals"]["FIELD.AI"]["stale_timeout_s"] = 0.1
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, config, tmp_path)
    store.field_io_driver = driver
    assert driver.start()
    made[0].samples["AI-1"] = (99.0, "GOOD", time.time() - 2.0)
    driver.scan_once()
    sample = store.get_sample("FIELD.AI")
    assert sample is not None
    assert sample.quality == "BAD" and sample.stale
    driver.stop()


@pytest.mark.parametrize("bad_timestamp", [
    float("nan"), float("inf"), "not-a-time",
])
def test_invalid_input_timestamp_marks_last_value_bad(
        tmp_path, provider_module, bad_timestamp) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()
    made[0].samples["AI-1"] = (99.0, "GOOD", bad_timestamp)
    driver.scan_once()
    sample = store.get_sample("FIELD.AI")
    assert sample is not None
    assert sample.stale and sample.quality == "BAD"
    assert driver.read_failures == 1
    driver.stop()


def test_future_input_timestamp_marks_last_value_bad(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()
    made[0].samples["AI-1"] = (99.0, "GOOD", time.time() + 30.0)
    driver.scan_once()
    sample = store.get_sample("FIELD.AI")
    assert sample is not None and sample.stale and sample.quality == "BAD"
    driver.stop()


def test_provider_timestamp_is_validated_after_blocking_read(
        tmp_path, provider_module) -> None:
    """A current source sample must survive waiting for the provider lock."""
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()

    original_read = made[0].read_sample

    def delayed_read(tag):
        if tag == "AI-1":
            # Longer than the future-skew allowance: the pre-fix driver used
            # the time from before this wait and rejected the valid epoch.
            time.sleep(1.05)
            return (99.0, "GOOD", time.time())
        return original_read(tag)

    made[0].read_sample = delayed_read
    driver.scan_once()
    sample = store.get_sample("FIELD.AI")
    assert sample is not None
    assert sample.value == pytest.approx(99.0)
    assert sample.quality == "GOOD" and not sample.stale
    assert driver.read_failures == 0
    driver.stop()


@pytest.mark.parametrize("value,quality", [
    (float("nan"), "GOOD"),
    (float("inf"), "GOOD"),
    (50.0, "BAD"),
])
def test_start_rejects_invalid_output_seed(
        tmp_path, provider_module, value, quality) -> None:
    module_name, _made = provider_module
    config = _config(module_name)
    config["provider"]["options"]["output_sample"] = (
        value, quality, time.time())
    driver = LocalVirtualIoDriver(SharedDataStore(), config, tmp_path)
    assert not driver.start()
    assert driver.outputs_seeded == 0
    assert "startup readback" in driver.last_error


def test_failed_field_write_does_not_swallow_later_local_write(
        tmp_path, provider_module) -> None:
    module_name, made = provider_module
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _config(module_name), tmp_path)
    store.field_io_driver = driver
    assert driver.start()
    made[0].fail_writes.add("AO-1")
    store.queue_write("FIELD.AO", 25.0)
    store.queue_write("ctrl.LOOP.wb.SP", 67.0)

    driver.scan_once()
    assert driver.write_failures == 1
    assert driver.writes_enqueued == 0
    assert store.get("FIELD.AO") == pytest.approx(64.3263)
    assert store.get("ctrl.LOOP.wb.SP") == 67.0
    driver.stop()


def test_data_bridge_consumes_ai_and_di_sample_quality() -> None:
    store = SharedDataStore()
    store.set_sample("AI.VALUE", 12.5, quality="UNCERTAIN")
    store.set_sample("DI.VALUE", True, quality=2)
    graph = StrategyGraph("quality")
    ai = AIBlock("AI1")
    ai.config.params["tag"] = "AI.VALUE"
    di = DIBlock("DI1")
    di.config.params["tag"] = "DI.VALUE"
    graph.add_block(ai)
    graph.add_block(di)

    DataBridge(store).read_inputs(graph)
    ai.execute(0.1)
    di.execute(0.1)

    assert ai.get_output("OUT") == 12.5
    assert ai.outputs["OUT"].status is Quality.UNCERTAIN
    assert di.get_output("OUT") is True
    assert di.outputs["OUT"].status is Quality.BAD


@pytest.mark.parametrize(("startup_mode", "expected_starts"), [
    (None, 1),
    ("manual", 0),
])
def test_app_attaches_queue_owner_before_optional_start(
        tmp_path, monkeypatch, startup_mode, expected_starts) -> None:
    area = tmp_path / "area"
    area.mkdir()
    virtual_io = {
            "type": "local_virtual_io",
            "source": "CTRL",
            "provider": {"factory": "unused:create"},
            "signals": {"PV": {"signal": "PV", "direction": "read"}},
    }
    if startup_mode is not None:
        virtual_io["startup_mode"] = startup_mode
    (area / "_project.json").write_text(json.dumps({"areas": [{
        "virtual_io": virtual_io,
    }]}), encoding="utf-8")
    observed = []

    class FakeDriver:
        def __init__(self, store, config, project_dir):
            self.store = store
            self.config = config
            self.project_dir = project_dir
            self.startup_mode = str(
                config.get("startup_mode") or "automatic").strip().lower()

        def start(self):
            observed.append(self.store.field_io_driver is self)
            return True

        def status(self):
            return "ready"

    import azeo_control_trainer.connectivity.fieldio.local_virtual_io as local_module

    monkeypatch.setattr(local_module, "LocalVirtualIoDriver", FakeDriver)
    store = SharedDataStore()
    driver = trainer_app._attach_field_io(store, area)
    assert driver is store.field_io_driver
    assert observed == [True] * expected_starts


def test_tag_database_indexes_distinct_virtual_io_node(tmp_path) -> None:
    (tmp_path / "_project.json").write_text(json.dumps({"areas": [{
        "virtual_io": {
            "type": "local_virtual_io",
            "signals": {
                "AI-1": {"node": "ns=2;s=AI-1", "kind": "AI",
                         "direction": "SIM_TO_DCS"},
                "AO-1": {"node": "ns=2;s=AO-1", "kind": "AO",
                         "direction": "DCS_TO_SIM"},
            },
        },
    }]}), encoding="utf-8")
    database = TagDatabase.from_area(tmp_path)
    fields = {item.io_tag: item for item
              in database.of_kind(EntryKind.FIELD)}
    assert fields["AI-1"].direction == "input"
    assert fields["AO-1"].direction == "output"
