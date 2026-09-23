from copy import deepcopy

import pytest

from azeo_control_trainer.core.strategy.blocks.composite_blocks import (
    CompositeBlock,
    InportBlock,
    OutportBlock,
)
from azeo_control_trainer.core.strategy.blocks.signal_blocks import BiasBlock
from azeo_control_trainer.core.strategy.composites import (
    CompositeLibrary,
    CompositeRevisionConflict,
    PublicParameter,
)
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


def _definition_graph(*, gain: float = 1.0, bias: float = 0.0) -> StrategyGraph:
    graph = StrategyGraph("ReusableBias")
    graph.description = "Reusable bias/gain composite"
    graph.set_module_parameter(
        "TARGET", 5.0, access="internal_write", description="Test target")

    source = InportBlock("IN")
    source.config.params["port_name"] = "IN"
    source._apply_config()
    bias_block = BiasBlock("BG1")
    bias_block.config.params.update({"GAIN": gain, "BIAS": bias})
    bias_block._apply_config()
    sink = OutportBlock("OUT")
    sink.config.params["port_name"] = "OUT"
    sink._apply_config()
    for block in (source, bias_block, sink):
        graph.add_block(block)
    assert graph.add_wire(source.id, "OUT", bias_block.id, "IN")
    assert graph.add_wire(bias_block.id, "OUT", sink.id, "IN")
    return graph


def _parameters(*, gain_default=1.5, bias_default=0.5,
                target_default=5.0):
    return [
        PublicParameter("Gain", "BG1/CONFIG/GAIN", "FLOAT", gain_default),
        PublicParameter("Bias", "BG1/BIAS", "FLOAT", bias_default),
        PublicParameter(
            "Target", "MODULE/PARAMETERS/TARGET", "FLOAT", target_default),
    ]


def _create_definition(library: CompositeLibrary, **defaults):
    return library.create(
        "Reusable Bias",
        _definition_graph().to_dict(),
        public_parameters=_parameters(**defaults),
    )


def _bias(block: CompositeBlock) -> BiasBlock:
    return next(inner for inner in block.inner_graph.blocks.values()
                if inner.instance_name == "BG1")


def test_linked_instance_round_trip_keeps_identity_snapshot_and_overrides(
        tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    definition = _create_definition(library)
    block = CompositeBlock.from_definition(
        definition, "BG-A", overrides={"gain": "2.75"})

    restored = CompositeBlock.from_dict(block.to_dict())

    assert restored.is_linked
    assert restored.definition_id == definition.id
    assert restored.definition_revision == 1
    assert restored.definition_digest == definition.digest
    assert restored.definition_snapshot["digest"] == definition.digest
    assert restored.public_parameter_overrides == {"Gain": 2.75}
    assert _bias(restored).config.params["GAIN"] == 2.75
    assert _bias(restored).config.params["BIAS"] == 0.5
    assert restored.inner_graph.module_parameters()["TARGET"]["value"] == 5.0
    assert restored.inner_graph.description == "Reusable bias/gain composite"
    assert restored.inner_graph.module_parameters()["TARGET"]["access"] \
        == "internal_write"


def test_public_parameter_defaults_and_explicit_override_lifecycle(tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    block = CompositeBlock.from_definition(_create_definition(library))

    assert block.public_parameter_value("gain") == 1.5
    assert _bias(block).config.params["GAIN"] == 1.5
    block.set_public_parameter_override("GAIN", "3.25")
    assert block.public_parameter_value("Gain") == 3.25
    assert _bias(block).config.params["GAIN"] == 3.25
    assert block.clear_public_parameter_override("gain") is True
    assert block.clear_public_parameter_override("Gain") is False
    assert _bias(block).config.params["GAIN"] == 1.5
    with pytest.raises(ValueError, match="unknown public parameter"):
        CompositeBlock.from_definition(
            _create_definition(
                CompositeLibrary(tmp_path / "other-composites")),
            overrides={"Typo": 7},
        )


def test_stale_detection_and_refresh_preserve_instance_overrides(tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    original = _create_definition(library)
    block = CompositeBlock.from_definition(
        original, overrides={"Gain": 2.5})
    assert block.definition_state(library) == "current"

    successor = library.update(
        original.id,
        expected_revision=1,
        public_parameters=_parameters(
            gain_default=9.0, bias_default=7.0, target_default=12.0),
    )
    assert successor.revision == 2
    assert block.definition_state(library) == "stale"
    assert block.is_definition_stale(library)

    assert block.refresh_from_library(library, expected_revision=1) is True
    assert block.definition_state(library) == "current"
    assert block.definition_revision == 2
    assert block.public_parameter_overrides == {"Gain": 2.5}
    assert _bias(block).config.params["GAIN"] == 2.5
    assert _bias(block).config.params["BIAS"] == 7.0
    assert block.inner_graph.module_parameters()["TARGET"]["value"] == 12.0


def test_missing_library_uses_executable_last_known_snapshot(tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    definition = _create_definition(library)
    block = CompositeBlock.from_definition(
        definition, overrides={"Gain": 4.0})
    restored = CompositeBlock.from_dict(block.to_dict())
    effective_before = deepcopy(restored.inner_graph.to_dict())

    library.delete(definition.id)

    assert restored.definition_state(library) == "missing"
    assert restored.refresh_from_library(library) is False
    assert restored.inner_graph.to_dict() == effective_before
    assert _bias(restored).config.params["GAIN"] == 4.0


def test_unlink_keeps_effective_graph_and_returns_to_legacy_embedding(tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    block = CompositeBlock.from_definition(
        _create_definition(library), overrides={"Bias": 6.0})
    effective_before = deepcopy(block.inner_graph.to_dict())

    assert block.unlink_to_embedded() is True
    assert block.unlink_to_embedded() is False
    payload = block.to_dict()

    assert block.definition_state(library) == "embedded"
    assert block.inner_graph.to_dict() == effective_before
    assert not ({
        "definition_id",
        "definition_revision",
        "definition_digest",
        "definition_snapshot",
        "public_parameter_overrides",
    } & payload.keys())
    restored = CompositeBlock.from_dict(payload)
    assert not restored.is_linked
    assert _bias(restored).config.params["BIAS"] == 6.0


def test_library_and_instance_updates_use_optimistic_revisions(tmp_path):
    library = CompositeLibrary(tmp_path / "composites")
    original = _create_definition(library)
    block = CompositeBlock.from_definition(original)
    library.update(original.id, expected_revision=1, description="revision 2")

    with pytest.raises(CompositeRevisionConflict):
        library.update(original.id, expected_revision=1, description="lost edit")
    with pytest.raises(CompositeRevisionConflict):
        block.refresh_from_library(library, expected_revision=99)
    assert block.definition_revision == 1
    assert block.definition_state(library) == "stale"


def test_existing_embedded_composite_remains_backward_compatible():
    block = CompositeBlock("Legacy Embedded")
    block.inner_graph = _definition_graph(gain=3.0, bias=2.0)
    payload = block.to_dict()

    restored = CompositeBlock.from_dict(payload)

    assert restored.definition_state() == "embedded"
    assert restored.definition_snapshot is None
    assert _bias(restored).config.params["GAIN"] == 3.0
    assert _bias(restored).config.params["BIAS"] == 2.0
