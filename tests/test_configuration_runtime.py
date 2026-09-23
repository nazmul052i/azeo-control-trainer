"""Real runtimes, publication replay, accepted class pins, and crash-safe recovery bytes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4, uuid5, NAMESPACE_URL

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.configuration.documents import ConfigurationError, prepare_import
from azeo_control_trainer.core.configuration.release_manifest import fingerprint
from azeo_control_trainer.core.configuration.release_validation import implementation_digest
from azeo_control_trainer.core.configuration.runtime_packages import RuntimeAdapter, materialize, read_package
from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore, PvmDisplay
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def release(version=1, module="LOOP", gain=1.0):
    from azeo_control_trainer.core.strategy.blocks.io_blocks import AIBlock
    from azeo_control_trainer.core.strategy.blocks.pid_block import PIDBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    graph = StrategyGraph(module)
    ai, pid = AIBlock("AI1"), PIDBlock("PID1")
    ai.id, pid.id = "ai", "pid"
    ai.config.params["tag"] = module + ".PV"
    pid.config.params["GAIN"] = gain
    graph.add_block(ai)
    graph.add_block(pid)
    wire = graph.add_wire(ai.id, "OUT", pid.id, "IN")
    graph.wires[wire].id = "connection"
    documents = {"_project.json": {"areas": []}, f"control/{module}.json": graph.to_dict(),
                 "displays/pvm/Unit/draft.json": PvmDisplay(name="Unit", description=f"Version {version}").to_dict(),
                 "displays/pvm/_library/asset.txt": f"asset-{version}".encode()}
    files, objects = [], []
    for path, document in documents.items():
        content = document if isinstance(document, bytes) else json.dumps(document).encode()
        files.append({"path": path, "content": base64.b64encode(content).decode()})
        objects.append({"path": path, "digest": hashlib.sha256(content).hexdigest(), "id": str(uuid5(NAMESPACE_URL, path)),
                        "revision": version, "kind": "module" if path.startswith("control/") else "display" if path.endswith("draft.json") else "asset"})
    manifest = {"objects": objects, "selected": [f"control/{module}.json", "displays/pvm/Unit/draft.json"],
                "implementation": implementation_digest(), "findings": []}
    manifest["package_hash"] = fingerprint(manifest)
    return {"id": str(uuid4()), "manifest": manifest, "bundle": {"files": files, "digest": prepare_import(files).digest}}


def adapter(tmp_path):
    store = SharedDataStore()
    display_store = DisplayStore(tmp_path / "station")
    display_store.root.mkdir(parents=True)
    deployment = PvmDeployment(display_store)
    return RuntimeAdapter(store, display_store, deployment)


def test_package_tamper_and_immutable_save_are_refused(tmp_path):
    from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy, save_strategy
    package = release()
    root = materialize(package, tmp_path)
    graph, comments = load_strategy(root / "control/LOOP.json", remember=False)
    with pytest.raises(ValueError, match="immutable"):
        save_strategy(graph, root / "control/LOOP.json", comments=comments)
    assert read_package(root)["id"] == package["id"]
    (root / "displays/pvm/_library/asset.txt").write_text("tampered")
    with pytest.raises(ConfigurationError, match="changed"):
        materialize(package, tmp_path)
    with pytest.raises(ConfigurationError, match="integrity"):
        read_package(root)


def test_controller_replay_does_not_reset_and_partial_download_preserves_other_modules(tmp_path):
    runtime = adapter(tmp_path)
    package = release()
    root = materialize(package, runtime.display_store.root / "_release_packages")
    runtime.install_controller(package, root)
    first = next(iter(runtime.loaded.values()))["runtime"]
    first.execute_scan(0.5)
    runtime.install_controller(package, root)
    assert next(iter(runtime.loaded.values()))["runtime"] is first and first.scan_count == 1
    another = release(module="OTHER")
    another_root = materialize(another, runtime.display_store.root / "_release_packages")
    runtime.install_controller(another, another_root)
    assert len(runtime.store.get_strategy_runtimes()) == 2
    assert first.is_online and first.scan_count == 1
    updated = release(2, gain=3.5)
    updated_root = materialize(updated, runtime.display_store.root / "_release_packages")
    runtime.install_controller(updated, updated_root)
    assert not first.is_online and len(runtime.store.get_strategy_runtimes()) == 2
    assert runtime.graphs()["LOOP"].blocks["pid"].config.params["GAIN"] == 3.5


def test_scanning_defaults_is_not_online_retuning_and_filter_weights_are_observed(tmp_path):
    runtime = adapter(tmp_path)
    package = release()
    root = materialize(package, runtime.display_store.root / "_release_packages")
    runtime.install_controller(package, root)
    entry = next(iter(runtime.loaded.values()))
    loop = entry["runtime"]
    loop.execute_scan(0.5)
    observed = runtime.observation()["objects"][0]
    assert observed["parameters_hash"] == observed["loaded_parameters"]
    pid = loop.compiled.graph.blocks["pid"]
    pid._pid_core.alpha = 0.35
    loop.execute_scan(0.5)
    observed = runtime.observation()["objects"][0]
    assert observed["parameters_hash"] != observed["loaded_parameters"]
    sample = next(p for p in observed["parameters"] if p["block_id"] == "pid")
    assert sample["values"]["alpha"] == 0.35
    # A replacement must compare against its own configuration, never the
    # previous controller's last-published values still in the shared store.
    updated = release(2, gain=3.5)
    updated_root = materialize(updated, runtime.display_store.root / "_release_packages")
    runtime.install_controller(updated, updated_root)
    next(iter(runtime.loaded.values()))["runtime"].execute_scan(0.5)
    observed = runtime.observation()["objects"][0]
    assert observed["parameters_hash"] == observed["loaded_parameters"]


def test_station_replay_pins_assets_until_explicit_refresh_and_keeps_old_documents(tmp_path):
    runtime = adapter(tmp_path)
    first = release()
    first_root = materialize(first, runtime.display_store.root / "_release_packages")
    job = {"id": str(uuid4())}
    runtime.publish_station(first, job)
    runtime.publish_station(first, job)
    assert len(runtime.display_store.history("Unit")) == 1
    assert runtime.deployment.document("Unit")["description"] == "Version 1"
    for version in range(2, 14):
        package = release(version)
        materialize(package, runtime.display_store.root / "_release_packages")
        runtime.publish_station(package, {"id": str(uuid4())})
    assert runtime.deployment.configuration_root("Unit") == first_root / "displays/pvm"
    assert runtime.deployment.document("Unit")["description"] == "Version 1"
    assert runtime.display_store.revision_document("Unit", 1) is not None
    restarted = PvmDeployment(runtime.display_store)
    assert restarted.document("Unit")["description"] == "Version 1"
    assert "Unit" in restarted.refresh()
    assert restarted.document("Unit")["description"] == "Version 13"
    assert (restarted.configuration_root("Unit") / "_library/asset.txt").read_text() == "asset-13"


def test_live_station_uses_accepted_package_after_publish_and_refresh(tmp_path, qapp):
    from azeo_control_trainer.azeo_operator_station.console import LiveStation
    runtime = adapter(tmp_path)
    first = release()
    first_root = materialize(first, runtime.display_store.root / "_release_packages")
    runtime.publish_station(first, {"id": str(uuid4())})
    station = LiveStation(runtime.deployment, runtime.graphs, config_root=runtime.display_store.root,
                          history_path=tmp_path / "history.sqlite3")
    try:
        station.show()
        assert station.show_display("Unit")
        old_view = station.view
        assert old_view.config_root == first_root / "displays/pvm"
        second = release(2)
        second_root = materialize(second, runtime.display_store.root / "_release_packages")
        runtime.publish_station(second, {"id": str(uuid4())})
        assert station.view is old_view
        station.refresh_configuration()
        assert station.view.config_root == second_root / "displays/pvm"
    finally:
        station.close()
        qapp.processEvents()


def test_release_comparison_keeps_stale_reported_objects_searchable(qapp, monkeypatch):
    from azeo_control_trainer.core.presentation.configuration_releases import ReleaseManager
    monkeypatch.setattr(ReleaseManager, "refresh", lambda self: None)
    dialog = ReleaseManager({"id": "project", "name": "Pilot"}, profile={"url": "http://127.0.0.1:1", "token": "test"})
    known = {"id": "loop", "target_id": "target", "target": "Runtime", "path": "control/LOOP.json", "status": "Unknown"}
    unseen = {**known, "id": "other", "path": "control/OTHER.json"}
    dialog.state = {"targets": [{"id": "target", "fresh": False, "report": {"objects": [{"object_id": "loop"}]}}],
                    "comparison": [known, unseen]}
    try:
        dialog.filter_comparison()
        assert dialog.comparison_model.rows == [known]
        dialog.comparison_search.setText("unknown")
        assert dialog.comparison_model.rows == [known]
        dialog.comparison_search.setText("other")
        assert dialog.comparison_model.rows == []
        dialog.reported_only.setChecked(False)
        assert dialog.comparison_model.rows == [unseen]
    finally:
        dialog.timer.stop()
        dialog.close()
        qapp.processEvents()
