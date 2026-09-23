from __future__ import annotations

from copy import deepcopy
import base64
import json
from uuid import uuid4

import pytest

from azeo_control_trainer.core.strategy.composites import PublicParameter
from azeo_control_trainer.core.strategy.module_classes import (
    ModuleClassDefinition,
    ModuleClassLibrary,
    ModuleClassRevisionConflict,
    analyze_instance,
    apply_update,
    clear_public_parameter_override,
    create_linked_instance,
    definition_state,
    parameter_candidates,
    plan_update,
    public_parameter_value,
    set_public_parameter_override,
    unlink_instance,
)
from azeo_control_trainer.core.strategy.serialization.strategy_io import (
    graph_from_document,
)
from azeo_control_trainer.core.strategy.serialization import strategy_io


def _document(name="LOOP-101"):
    return {
        "name": name,
        "description": "Reusable loop",
        "blocks": [{
            "id": "constant",
            "block_type": "CONSTANT",
            "instance_name": "K-101",
            "x": 20.0,
            "y": 40.0,
            "config": {"value": 1.0, "label": "Reference"},
        }],
        "wires": [],
    }


def _class(library):
    return library.create(
        "ANALOG_LOOP", _document(),
        description="Standard analog loop",
        public_parameters=[PublicParameter(
            "Setpoint", "K-101/CONFIG/value", "FLOAT", 1.0,
            "Instance reference")],
    )


def test_library_versions_validated_module_class_and_preserves_history(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    first = _class(library)
    changed = deepcopy(first.graph)
    changed["blocks"][0]["config"]["label"] = "Updated"

    second = library.update(
        first.id, expected_revision=1, graph=changed,
        note="Reviewed label revision")

    assert second.revision == 2 and second.digest != first.digest
    assert library.get_revision(first.id, 1).digest == first.digest
    assert library.revisions(first.id) == [1, 2]
    with pytest.raises(ModuleClassRevisionConflict):
        library.update(first.id, expected_revision=1, graph=changed)


def test_linked_instance_embeds_revision_and_applies_typed_override(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    definition = _class(library)

    document = create_linked_instance(
        definition, "FIC-201", overrides={"Setpoint": "2.75"})
    graph, _comments = graph_from_document(document, strict=True)

    assert graph.name == "FIC-201"
    assert graph.blocks["constant"].config.params["value"] == 2.75
    assert graph.extra["module_class"]["definition_id"] == definition.id
    assert public_parameter_value(document, "setpoint") == 2.75
    status = analyze_instance(document, library)
    assert status.state == "current"
    assert status.overrides == 1
    assert not status.deviations


def test_linked_instance_can_publish_a_unique_block_identity(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    definition = library.create(
        "DEVICE_CLASS", _document(),
        public_parameters=[PublicParameter(
            "Published block", "constant/IDENTITY/INSTANCE_NAME", "STRING",
            "DEVICE", "Unique controller-store identity")],
    )

    first = create_linked_instance(
        definition, "MC-A", overrides={"Published block": "MC-A"})
    second = create_linked_instance(
        definition, "MC-B", overrides={"Published block": "MC-B"})

    assert first["blocks"][0]["instance_name"] == "MC-A"
    assert second["blocks"][0]["instance_name"] == "MC-B"
    assert not analyze_instance(first, library).deviations
    assert not analyze_instance(second, library).deviations


def test_public_block_identity_rejects_empty_or_duplicate_names(tmp_path):
    source = _document()
    source["blocks"].append({
        "id": "second", "block_type": "CONSTANT",
        "instance_name": "OTHER", "x": 20.0, "y": 140.0,
        "config": {"value": 2.0, "label": "Other"},
    })
    library = ModuleClassLibrary(tmp_path / "classes")
    definition = library.create(
        "IDENTITY_CLASS", source,
        public_parameters=[PublicParameter(
            "Published block", "constant/IDENTITY/INSTANCE_NAME", "STRING",
            "DEVICE")],
    )

    with pytest.raises(ValueError, match="non-empty"):
        create_linked_instance(
            definition, "MC-A", overrides={"Published block": ""})
    with pytest.raises(ValueError, match="duplicates"):
        create_linked_instance(
            definition, "MC-A", overrides={"Published block": "OTHER"})


def test_module_class_governs_diagram_comments_as_documentation(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    source = _document()
    source["comments"] = [{
        "id": "note", "text": "Validated design basis", "x": 10, "y": 10,
    }]
    definition = library.create("DOCUMENTED_LOOP", source)
    instance = create_linked_instance(definition, "FIC-301")
    instance["comments"][0]["text"] = "Local note"

    status = analyze_instance(instance, library)

    assert definition.graph["comments"][0]["text"] == "Validated design basis"
    assert any(change.detail == "Module comments changed"
               for change in status.deviations)


def test_override_edit_preserves_unrelated_instance_deviation(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    definition = _class(library)
    document = create_linked_instance(definition, "FIC-201")
    document["blocks"][0]["x"] = 175.0

    overridden = set_public_parameter_override(document, "Setpoint", 4.5)
    cleared = clear_public_parameter_override(overridden, "Setpoint")

    assert overridden["blocks"][0]["x"] == 175.0
    assert public_parameter_value(overridden, "Setpoint") == 4.5
    assert cleared["blocks"][0]["x"] == 175.0
    assert public_parameter_value(cleared, "Setpoint") == 1.0
    assert not cleared["module_class"]["public_parameter_overrides"]


def test_nonconflicting_class_update_can_preserve_deviation(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    first = _class(library)
    instance = create_linked_instance(first, "FIC-201")
    instance["blocks"][0]["x"] = 175.0
    changed = deepcopy(first.graph)
    changed["blocks"][0]["config"]["label"] = "Class update"
    second = library.update(first.id, expected_revision=1, graph=changed)

    plan = plan_update(instance, second)
    refreshed = apply_update(
        instance, second, preserve_deviations=True)

    assert not plan.has_conflicts
    assert plan.instance_deviations
    assert plan.class_changes
    assert refreshed["blocks"][0]["x"] == 175.0
    assert refreshed["blocks"][0]["config"]["label"] == "Class update"
    assert refreshed["module_class"]["definition_revision"] == 2
    assert definition_state(refreshed, library) == "current"


def test_same_property_update_requires_explicit_conflict_resolution(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    first = _class(library)
    instance = create_linked_instance(first, "FIC-201")
    instance["blocks"][0]["x"] = 175.0
    changed = deepcopy(first.graph)
    changed["blocks"][0]["x"] = 90.0
    second = library.update(first.id, expected_revision=1, graph=changed)

    plan = plan_update(instance, second)

    assert plan.has_conflicts
    assert any(path.endswith("constant/x") for path in plan.conflict_paths)
    with pytest.raises(ValueError, match="conflicts"):
        apply_update(instance, second, preserve_deviations=True)
    adopted = apply_update(instance, second, preserve_deviations=False)
    assert adopted["blocks"][0]["x"] == 90.0


def test_unlink_retains_effective_module_and_candidate_discovery(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    definition = _class(library)
    linked = create_linked_instance(
        definition, "FIC-201", overrides={"Setpoint": 3.0})

    candidates = parameter_candidates(linked)
    independent = unlink_instance(linked)

    assert any(candidate.path == "K-101/CONFIG/value"
               for candidate in candidates)
    assert "module_class" not in independent
    assert independent["blocks"][0]["config"]["value"] == 3.0
    assert analyze_instance(independent, library).state == "independent"


def test_definition_digest_rejects_tampered_class_record(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")
    definition = _class(library)
    record = definition.to_dict()
    record["graph"]["blocks"][0]["config"]["value"] = 99.0

    with pytest.raises(ValueError, match="digest"):
        ModuleClassDefinition.from_dict(record)

    instance = create_linked_instance(definition, "FIC-201")
    instance["module_class"]["definition_digest"] = "tampered"
    with pytest.raises(ValueError, match="does not match"):
        graph_from_document(instance, strict=True)


def test_module_class_identity_cannot_escape_library_root(tmp_path):
    library = ModuleClassLibrary(tmp_path / "classes")

    with pytest.raises(ValueError, match="UUID"):
        library.create(
            "BAD", _document(), definition_id="../../outside")
    with pytest.raises(ValueError, match="UUID"):
        library.get("../../outside")
    assert not (tmp_path / "outside.json").exists()


def test_configuration_adoption_indexes_and_updates_whole_module_instance(
    tmp_path,
):
    from azeo_control_trainer.core.configuration.documents import prepare_import
    from azeo_control_trainer.core.configuration.libraries import (
        adoption,
        inventory,
    )

    library = ModuleClassLibrary(tmp_path / "classes")
    first = _class(library)
    instance = create_linked_instance(first, "FIC-201")
    changed = deepcopy(first.graph)
    changed["blocks"][0]["config"]["label"] = "Updated by class"
    second = library.update(first.id, expected_revision=1, graph=changed)

    def encoded(path, document):
        content = json.dumps(document).encode("utf-8")
        return {
            "path": path,
            "content": base64.b64encode(content).decode("ascii"),
        }

    project = {"areas": [{
        "name": "Area", "area_id": "area",
        "strategies": ["control/FIC-201.json"],
    }]}

    def bundle(definition):
        files = [
            encoded("_project.json", project),
            encoded("control/FIC-201.json", instance),
            encoded(
                f"_module_classes/{definition.id}.json",
                definition.to_dict()),
        ]
        prepared = prepare_import(files)
        return {
            "project": {"id": str(uuid4()), "generation": 1},
            "files": files,
            "digest": prepared.digest,
            "objects": [{
                "id": str(uuid4()), "path": item.path,
                "kind": item.kind, "revision": 1,
                "digest": item.digest,
            } for item in prepared.documents],
        }

    source = bundle(first)
    target = bundle(second)
    state = inventory(source)
    row = next(item for item in state["instances"]
               if item["kind"] == "module_class")
    cls = next(item for item in state["classes"]
               if item["kind"] == "control module class")
    assert row["class_id"] == cls["id"]
    assert row["status"] == "Current"

    edits, review = adoption(source, [row["id"]], target=target)
    module_edit = next(item for item in edits
                       if item["path"] == "control/FIC-201.json")
    adopted = json.loads(base64.b64decode(module_edit["content"]))
    assert adopted["module_class"]["definition_revision"] == 2
    assert adopted["blocks"][0]["config"]["label"] == "Updated by class"
    assert review[0]["before"] == 1 and review[0]["after"] == 2


def test_internal_class_libraries_are_not_listed_as_runtime_modules(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(strategy_io, "STRATEGY_DIR", tmp_path)
    control = tmp_path / "control"
    classes = tmp_path / "_module_classes"
    composites = tmp_path / "_composites"
    for folder in (control, classes, composites):
        folder.mkdir()
    (control / "LOOP.json").write_text(json.dumps(_document()))
    (classes / "class.json").write_text("{}")
    (composites / "composite.json").write_text("{}")

    listed = strategy_io.list_strategy_folders()

    assert list(listed) == ["control"]
    assert listed["control"] == [control / "LOOP.json"]
