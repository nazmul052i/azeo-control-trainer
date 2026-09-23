"""Real class adoption transactions and mixed-version runtime contracts."""
import base64
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from test_configuration_database import database as database_fixture, encoded, capture
from azeo_control_trainer.core.configuration.documents import Conflict, ConfigurationError, Forbidden, read_project
from azeo_control_trainer.core.configuration.editing import EditingRepository
from azeo_control_trainer.core.configuration.libraries import LibraryRepository, decode
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary

database = database_fixture


@pytest.fixture
def library_pilot(database, tmp_path):
    root = tmp_path / "source"
    graphics = root / "displays/pvm"
    graphics.mkdir(parents=True)
    (root / "_project.json").write_text('{"areas": []}')
    library = UserPvmLibrary(graphics)
    library.add("Shared", [{"id": "shape", "kind": "rect", "x": 0, "y": 0, "w": 80, "h": 40,
                            "fill": "#4472C4"}])
    items = library.instantiate("Shared", 20, 20) + library.instantiate("Shared", 200, 20)
    display = graphics / "Pilot/draft.json"
    display.parent.mkdir()
    display.write_text(json.dumps({"display": "Pilot", "name": "Pilot", "pvms": [], "items": items}))
    repo, token = database
    source = capture(repo, token, read_project(root)["files"])["project_id"]
    editing = EditingRepository(repo)
    project = editing.fork(token, source, "Library pilot", str(uuid4()))["project_id"]
    return repo, token, editing, LibraryRepository(repo), project


def adopt(libraries, token, project, selected, *, pin=False, generation=None):
    review = libraries.preview(token, project, selected, pin=pin, generation=generation)
    return libraries.commit(token, project, review["id"], "Reviewed class adoption", str(uuid4()))


def edit_class(repo, token, editing, project, *, width=120):
    bundle = repo.export(token, project)
    path = "displays/pvm/_library/user_pvms.json"
    document = decode(bundle)[path]
    document["Shared"]["items"][0]["w"] = width
    document["Shared"]["definition_revision"] += 1
    session = str(uuid4())
    editing.lease(token, project, session, [path])
    edit = {**encoded(path, document), "expected_revision": next(o["revision"] for o in bundle["objects"] if o["path"] == path)}
    try:
        return editing.checkin(token, project, session, [edit], "Widen shared class", str(uuid4()))
    finally:
        editing.lease(token, project, session, [], release=True)


def test_adopt_subset_preserves_other_instance_and_supports_reviewed_rollback(library_pilot):
    repo, token, editing, libraries, project = library_pilot
    with pytest.raises(Conflict, match="Pin existing"):
        edit_class(repo, token, editing, project)
    rows = libraries.state(token, project)["instances"]
    adopt(libraries, token, project, [r["id"] for r in rows], pin=True)
    pinned = repo.export(token, project)
    before = decode(pinned)["displays/pvm/Pilot/draft.json"]["items"]
    assert before[0]["class_revision"] == before[1]["class_revision"]
    edit_class(repo, token, editing, project)
    rows = libraries.state(token, project)["instances"]
    assert all(row["status"] == "Update available" for row in rows)
    adopt(libraries, token, project, [rows[0]["id"]])
    after = decode(repo.export(token, project))["displays/pvm/Pilot/draft.json"]["items"]
    assert after[0]["w"] == 120 and after[1] == before[1]
    assert after[0]["id"] == before[0]["id"]
    assert after[0]["class_revision"] != after[1]["class_revision"]
    adopt(libraries, token, project, [rows[0]["id"]], generation=pinned["project"]["generation"])
    restored = decode(repo.export(token, project))["displays/pvm/Pilot/draft.json"]["items"]
    assert restored[0]["w"] == 80
    assert restored[1] == before[1]


def test_stale_review_retry_permissions_and_pin_integrity(library_pilot):
    repo, token, editing, libraries, project = library_pilot
    rows = libraries.state(token, project)["instances"]
    review = libraries.preview(token, project, [row["id"] for row in rows], pin=True)
    command = str(uuid4())
    receipt = libraries.commit(token, project, review["id"], "Initial pins", command)
    another_session = str(uuid4())
    editing.lease(token, project, another_session, ["displays/pvm/Pilot/draft.json"])
    try:
        assert libraries.commit(token, project, review["id"], "Initial pins", command) == receipt
    finally:
        editing.lease(token, project, another_session, [], release=True)
    edit_class(repo, token, editing, project)
    stale = libraries.preview(token, project, [rows[0]["id"]])
    edit_class(repo, token, editing, project, width=150)
    with pytest.raises(Conflict, match="Project changed"):
        libraries.commit(token, project, stale["id"], "Stale review", str(uuid4()))
    other = repo.provision_identity("reader")
    repo.grant("reader", project, "reader")
    with pytest.raises(Forbidden):
        libraries.preview(other, project, [rows[0]["id"]])
    bundle = repo.export(token, project)
    path = next(o["path"] for o in bundle["objects"] if "_class_revisions" in o["path"])
    obj = next(o for o in bundle["objects"] if o["path"] == path)
    with pytest.raises(ConfigurationError, match="immutable"):
        editing.preview(token, project, [{**encoded(path, {}), "expected_revision": obj["revision"]}])
    damaged = deepcopy(bundle)
    next(f for f in damaged["files"] if f["path"] == path)["content"] = base64.b64encode(b"{}").decode()
    from azeo_control_trainer.core.hmi.pvms.class_revisions import verify_files
    with pytest.raises(ValueError):
        verify_files(damaged["files"])


def test_composite_adoption_preserves_override_and_other_downloadable_graph(database, tmp_path):
    from azeo_control_trainer.core.strategy.composites import CompositeLibrary
    from azeo_control_trainer.core.strategy.blocks.composite_blocks import CompositeBlock
    from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
    from test_linked_composite_instances import _create_definition
    library = CompositeLibrary(tmp_path / "composites")
    definition = _create_definition(library)
    graph = StrategyGraph("Control")
    graph.add_block(CompositeBlock.from_definition(definition, "A", overrides={"Gain": 2.75}))
    graph.add_block(CompositeBlock.from_definition(definition, "B"))
    repo, token = database
    files = [encoded("_project.json", {"areas": []}), encoded("control/Control.json", graph.to_dict()),
             encoded("composites/Shared.json", definition.to_dict())]
    source = capture(repo, token, files)["project_id"]
    editing = EditingRepository(repo)
    project = editing.fork(token, source, "Composite pilot", str(uuid4()))["project_id"]
    libraries = LibraryRepository(repo)
    rows = libraries.state(token, project)["instances"]
    assert rows[0]["properties"][0]["origin"] == "Override"
    updated = deepcopy(definition)
    updated.revision = 2
    updated.public_parameters[1] = type(updated.public_parameters[1])("Bias", "BG1/BIAS", "FLOAT", 9.0)
    session = str(uuid4())
    path = "composites/Shared.json"
    editing.lease(token, project, session, [path])
    editing.checkin(token, project, session, [{**encoded(path, updated.to_dict()), "expected_revision": 1}], "Change inherited bias", str(uuid4()))
    editing.lease(token, project, session, [], release=True)
    adopt(libraries, token, project, [rows[0]["id"]])
    blocks = decode(repo.export(token, project))["control/Control.json"]["blocks"]
    assert blocks[0]["definition_revision"] == 2 and blocks[1]["definition_revision"] == 1
    adopted = CompositeBlock.from_dict(blocks[0])
    assert adopted.public_parameter_overrides == {"Gain": 2.75}
    assert adopted.public_parameter_value("Bias") == 9.0


def test_installed_pvm_pin_uses_previous_typed_contract(tmp_path):
    from azeo_control_trainer.core.configuration.documents import prepare_import, export_project
    from azeo_control_trainer.core.configuration.libraries import adoption
    from azeo_control_trainer.core.hmi.pvms import registry
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration, PropertyGroup, PvmProperty
    from azeo_control_trainer.core.hmi.pvms.rendering.renderer import DisplayRenderer, pvm_from_dict
    from azeo_control_trainer.core.hmi.binding import BindingEngine, LiveGraphSource
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    cls = registry.get("PID", "dynamo_compact")
    cfg = PvmConfiguration(cls.__name__, [PropertyGroup("Data", [PvmProperty("Tag", "String", default="OLD")])])
    cfg_path = f"displays/pvm/_pvmcfg/{cls.__name__}.pvmcfg.json"
    files = [encoded("_project.json", {"areas": []}), encoded(cfg_path, cfg.to_dict()),
             encoded("displays/pvm/Test/draft.json", {"display": "Test", "items": [], "pvms": [
                 {"id": "one", "class": "PID/dynamo_compact", "params": {"path": "LOOP/PID"}},
                 {"id": "two", "class": "PID/dynamo_compact", "params": {"path": "OTHER/PID"}}]})]
    prepared = prepare_import(files)
    bundle = {"project": {"id": str(uuid4()), "generation": 1}, "files": files, "digest": prepared.digest,
              "objects": [{"id": str(uuid4()), "path": d.path, "kind": d.kind, "revision": 1, "digest": d.digest} for d in prepared.documents]}
    from azeo_control_trainer.core.configuration.libraries import inventory
    rows = inventory(bundle)["instances"]
    edits, _ = adoption(bundle, [r["id"] for r in rows], pin=True)
    all_files = {f["path"]: f for f in files}
    all_files.update({e["path"]: {"path": e["path"], "content": e["content"]} for e in edits})
    cfg.groups[0].properties[0].default = "NEW"
    all_files[cfg_path] = encoded(cfg_path, cfg.to_dict())
    prepared = prepare_import(list(all_files.values()))
    root = export_project({"files": list(all_files.values()), "digest": prepared.digest}, tmp_path / "export")
    document = json.loads((root / "displays/pvm/Test/draft.json").read_text())
    renderer = DisplayRenderer(BindingEngine(LiveGraphSource(lambda: {})), {}, root / "displays/pvm")
    pvm = pvm_from_dict(document["pvms"][0])
    assert renderer.pvm_config(cls, pvm.class_revision).value_of("Tag", {}) == "OLD"
    assert renderer.pvm_config(cls).value_of("Tag", {}) == "NEW"
    app.processEvents()


def test_new_unrelated_class_does_not_require_pinning_other_classes(library_pilot):
    repo, token, editing, libraries, project = library_pilot
    path = "displays/pvm/_library/user_pvms.json"
    document = decode(repo.export(token, project))[path]
    document["Independent"] = deepcopy(document["Shared"])
    document["Independent"]["definition_id"] = str(uuid4())
    session = str(uuid4())
    editing.lease(token, project, session, [path])
    try:
        editing.checkin(token, project, session, [{**encoded(path, document), "expected_revision": 1}],
                        "Add independent class", str(uuid4()))
    finally:
        editing.lease(token, project, session, [], release=True)
    assert all(row["status"] == "Unpinned" for row in libraries.state(token, project)["instances"])


def test_parent_adoption_keeps_explicitly_detached_nested_geometry(tmp_path):
    from azeo_control_trainer.core.configuration.libraries import _adopt_authored
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration
    library = UserPvmLibrary(tmp_path)
    library.add("Child", [{"id": "child", "kind": "rect", "x": 0, "y": 0, "w": 20, "h": 20}])
    library.add("Parent", [{"id": "parent", "kind": "rect", "x": 0, "y": 0, "w": 100, "h": 50}])
    library.add_nested("Parent", "Child", x=10, y=60)
    items = library.instantiate("Parent", 100, 80, unlink_nested=True)
    original_child = next(item for item in items if item.get("user_pvm") == "Child")
    assert original_child["instance_detached_nested"]
    updated = deepcopy(library.entries)
    updated["Child"]["items"][0]["w"] = 999
    updated["Parent"]["items"][0]["w"] = 150
    document = {"items": deepcopy(items)}
    _adopt_authored(document, {"location": items[0]["instance_id"]}, {"root": "displays/pvm", "name": "Parent"},
                    {"displays/pvm/_library/user_pvms.json": updated}, "a" * 64,
                    pin=False, before=PvmConfiguration("Parent"))
    child = next(item for item in document["items"] if item.get("user_pvm") == "Child")
    assert child["w"] == original_child["w"]
    assert child["id"] == original_child["id"]
    assert next(item for item in document["items"] if item.get("user_pvm") == "Parent")["w"] == 150


def test_legacy_override_and_external_pipe_follow_member_identity_after_reorder(tmp_path):
    from azeo_control_trainer.core.configuration.libraries import _adopt_authored
    from azeo_control_trainer.core.hmi.pvms.configurator.model import PvmConfiguration
    library = UserPvmLibrary(tmp_path)
    library.add("Pair", [
        {"id": "first", "kind": "rect", "x": 0, "y": 0, "w": 20, "h": 20, "fill": "#111111"},
        {"id": "second", "kind": "rect", "x": 40, "y": 0, "w": 20, "h": 20, "fill": "#222222"},
    ])
    items = library.instantiate("Pair", 100, 80)
    target = deepcopy(items[1])
    for member in items:
        member["instance_overrides"] = {"1.fill": "#C04040"}
    updated = deepcopy(library.entries)
    updated["Pair"]["items"].reverse()
    pipe = {"id": "external-pipe", "kind": "pipe", "a": target["id"], "b": "outside"}
    document = {"items": deepcopy(items) + [deepcopy(pipe)]}
    _adopt_authored(document, {"location": items[0]["instance_id"]}, {"root": "displays/pvm", "name": "Pair"},
                    {"displays/pvm/_library/user_pvms.json": updated}, "a" * 64,
                    pin=False, before=PvmConfiguration("Pair"))
    adopted = next(item for item in document["items"] if item.get("source_element_id") == target["source_element_id"])
    assert adopted["fill"] == "#C04040"
    assert adopted["id"] == target["id"]
    assert document["items"][-1] == pipe
    updated["Pair"]["items"] = [item for item in updated["Pair"]["items"] if item["source_element_id"] != target["source_element_id"]]
    with pytest.raises(ConfigurationError, match="externally connected"):
        _adopt_authored(document, {"location": items[0]["instance_id"]}, {"root": "displays/pvm", "name": "Pair"},
                        {"displays/pvm/_library/user_pvms.json": updated}, "b" * 64,
                        pin=False, before=PvmConfiguration("Pair"))
