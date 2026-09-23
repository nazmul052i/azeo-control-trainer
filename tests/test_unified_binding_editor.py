"""Typed, reusable Graphics Designer binding workflow contracts."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QWidget

import azeo_control_trainer.core.strategy.blocks  # noqa: F401
from azeo_control_trainer.core.hmi.pvms.configurator.model import (
    INTERNAL,
    PvmConfiguration,
    PvmProperty,
    Option,
    PropertyGroup,
)
from azeo_control_trainer.azeo_graphics_designer.studio.binding_editor import (
    BindingCatalog,
    BindingKind,
    BindingTarget,
    ExpressionReference,
    UnifiedBindingEditor,
    ValueType,
    make_binding_result,
    normalize_value_type,
    types_compatible,
)
from azeo_control_trainer.core.hmi.binding.result import BindingResult
from azeo_control_trainer.core.hmi.pvms.properties import PropertyResolver
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary
from azeo_control_trainer.core.strategy.model.block_registry import BlockRegistry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph
from azeo_control_trainer.core.strategy.model.terminal import Quality


def _application():
    return QApplication.instance() or QApplication([])


def _graph():
    block = BlockRegistry().create("PID", "PID1")
    assert block is not None
    block._apply_config()
    graph = StrategyGraph(name="UNIT100")
    graph.add_block(block)
    return graph


def _configuration():
    return PvmConfiguration("LoopCard", [
        PropertyGroup("Interface", [
            PvmProperty("ControlTag", "Function Block Reference",
                        title="Control tag", required=True,
                        drop_target=True, accepted_block_types=["PID"]),
            PvmProperty("Caption", "String", title="Caption"),
            PvmProperty(
                "Orientation", "Selection", title="Orientation",
                default="Right", columns=["BodyRot"],
                options=[Option("Right", ["0"]), Option("Up", ["270"])]),
        ]),
        PropertyGroup("Implementation", [
            PvmProperty("NormalizedPV", "Number", scope=INTERNAL),
        ]),
    ])


def _catalog():
    catalog = BindingCatalog()
    catalog.add_configuration(_configuration())
    catalog.add_graphs({"UNIT100": _graph()})
    catalog.add_component_properties([
        {"reference": "Pump01.Speed", "label": "Pump speed",
         "value_type": "Number", "group": "Pump01", "writable": True},
        {"reference": "Pump01.Caption", "label": "Pump caption",
         "value_type": "String", "group": "Pump01"},
    ])
    return catalog


def test_catalog_unifies_control_class_and_component_sources():
    catalog = _catalog()

    pv = catalog.find(BindingKind.DIRECT_TAG, "UNIT100/PID1/PV")
    assert pv is not None
    assert pv.normalized_type is ValueType.NUMBER
    assert pv.group.endswith("Outputs")

    public = catalog.find(BindingKind.CLASS_PROPERTY, "Pvm.ControlTag")
    internal = catalog.find(BindingKind.CLASS_PROPERTY, "Pvm.NormalizedPV")
    column = catalog.find(
        BindingKind.CLASS_PROPERTY, "Pvm.Orientation.BodyRot")
    assert public is not None and public.scope == "Public"
    assert internal is not None and internal.scope == "Internal"
    assert column is not None
    assert {"ControlTag", "Orientation.BodyRot"} \
        <= catalog.indirect_fields()
    assert "Pvm.ControlTag" not in catalog.indirect_fields()

    sibling = catalog.find(
        BindingKind.COMPONENT_PROPERTY, "Pump01.Speed")
    assert sibling is not None and sibling.writable


def test_value_types_refuse_hidden_conversions_but_allow_label_formatting():
    assert normalize_value_type("LREAL") is ValueType.NUMBER
    assert normalize_value_type("BOOL") is ValueType.BOOLEAN
    assert normalize_value_type("Function Block Reference") \
        is ValueType.REFERENCE
    assert types_compatible(ValueType.NUMBER, "REAL")
    assert types_compatible(ValueType.STRING, ValueType.NUMBER)
    assert not types_compatible(ValueType.NUMBER, ValueType.STRING)
    assert not types_compatible(ValueType.COLOR, ValueType.NUMBER)


def test_direct_and_indirect_bindings_are_typed_and_serializable():
    catalog = _catalog()
    number_target = BindingTarget("rotation", ValueType.NUMBER, "Impeller")

    direct = make_binding_result(
        number_target, BindingKind.DIRECT_TAG,
        source="UNIT100/PID1/PV", source_type=ValueType.NUMBER,
        catalog=catalog)
    assert direct.valid
    assert direct.descriptor() == {
        "kind": "animation", "path": "UNIT100/PID1/PV", "type": "value"}

    indirect = make_binding_result(
        number_target, BindingKind.INDIRECT_TAG,
        source="{ControlTag}/PV", source_type=ValueType.NUMBER,
        catalog=catalog)
    assert indirect.valid
    assert indirect.descriptor()["indirect"] is True

    missing = make_binding_result(
        number_target, BindingKind.INDIRECT_TAG,
        source="{MissingTag}/PV", source_type=ValueType.NUMBER,
        catalog=catalog)
    assert not missing.valid
    assert any("unknown class placeholder" in issue.message
               for issue in missing.issues)

    wrong_type = make_binding_result(
        number_target, BindingKind.DIRECT_TAG,
        source="UNIT100/PID1/MODE", source_type=ValueType.STRING,
        catalog=BindingCatalog())
    assert not wrong_type.valid
    assert any(issue.field == "Type" for issue in wrong_type.issues)


def test_property_and_expression_bindings_preserve_structure():
    catalog = _catalog()
    target = BindingTarget(
        "value", ValueType.NUMBER, "Readout", writable=True)

    prop = make_binding_result(
        target, BindingKind.COMPONENT_PROPERTY,
        source="Pump01.Speed", source_type=ValueType.NUMBER,
        bidirectional=True, catalog=catalog)
    assert prop.valid
    assert prop.descriptor() == {
        "kind": "property", "ref": "Pump01.Speed", "bidirectional": True}

    refs = (
        ExpressionReference("pv", "UNIT100/PID1/PV", ValueType.NUMBER),
        ExpressionReference("sp", "UNIT100/PID1/SP", ValueType.NUMBER),
    )
    expression = make_binding_result(
        target, BindingKind.EXPRESSION, expression="pv - sp",
        references=refs, catalog=catalog)
    assert expression.valid
    assert expression.descriptor()["refs"] == {
        "pv": "UNIT100/PID1/PV", "sp": "UNIT100/PID1/SP"}

    unsafe = make_binding_result(
        target, BindingKind.EXPRESSION, expression="pv > sp",
        references=refs, catalog=catalog)
    assert not unsafe.valid
    assert any("display expression language" in issue.message
               for issue in unsafe.issues)


def test_expression_reference_rejects_non_path_unknown_source():
    result = make_binding_result(
        BindingTarget("value", ValueType.NUMBER),
        BindingKind.EXPRESSION,
        expression="pv * 2",
        references=(ExpressionReference("pv", "nonsense"),),
        catalog=BindingCatalog(),
    )

    assert not result.valid
    assert any("MODULE/BLOCK/PARAMETER" in issue.message
               for issue in result.issues)


def test_editor_browses_sources_and_only_accepts_a_valid_result():
    _application()
    catalog = _catalog()
    dialog = UnifiedBindingEditor(
        BindingTarget("value", ValueType.NUMBER, "Readout"), catalog)

    assert dialog.property("authoringDialog") is True
    assert dialog.findChild(QWidget, "authoring_dialog_header") is not None
    assert dialog.buttons.button(
        QDialogButtonBox.Ok).property("primaryAction") is True
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    assert dialog.select_source(
        BindingKind.DIRECT_TAG, "UNIT100/PID1/PV")
    chosen = dialog.current_result()
    assert chosen.valid
    assert chosen.source == "UNIT100/PID1/PV"
    assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()

    dialog._accept_if_valid()
    assert dialog.binding_result() is not None
    assert dialog.binding_result().descriptor()["path"] == "UNIT100/PID1/PV"
    dialog.close()


def test_editor_round_trips_an_expression_result():
    _application()
    catalog = _catalog()
    initial = make_binding_result(
        BindingTarget("value", ValueType.NUMBER, "Deviation"),
        BindingKind.EXPRESSION, expression="pv - sp", references=(
            ExpressionReference("pv", "UNIT100/PID1/PV"),
            ExpressionReference("sp", "UNIT100/PID1/SP"),
        ), catalog=catalog)
    assert initial.valid

    dialog = UnifiedBindingEditor(initial.target, catalog, initial=initial)
    current = dialog.current_result()
    assert current.valid
    assert current.expression == "pv - sp"
    assert [ref.name for ref in current.references] == ["pv", "sp"]
    dialog.close()


def test_structured_expression_descriptor_runs_without_hidden_path_text():
    values = {
        "UNIT100/PID1/PV": BindingResult(72.5, Quality.GOOD),
        "UNIT100/PID1/SP": BindingResult(50.0, Quality.GOOD),
    }
    resolver = PropertyResolver(read=lambda path: values.get(path))
    result = resolver.resolve({
        "kind": "expression",
        "expr": "pv - sp",
        "refs": {
            "pv": "UNIT100/PID1/PV",
            "sp": "UNIT100/PID1/SP",
        },
    })
    assert result.value == 22.5
    assert result.quality is Quality.GOOD

    values["UNIT100/PID1/PV"] = BindingResult(None, Quality.BAD)
    failed = resolver.resolve({
        "kind": "expression", "expr": "pv - sp",
        "refs": {"pv": "UNIT100/PID1/PV", "sp": "UNIT100/PID1/SP"},
    })
    assert not failed.usable
    assert failed.quality is Quality.BAD


def test_indirect_class_path_resolves_from_typed_instance_choice():
    config = _configuration()
    assert UserPvmLibrary._resolve(
        "{ControlTag}/PV", config,
        {"ControlTag": "UNIT100/PID1"}, None) == "UNIT100/PID1/PV"


def test_class_expression_references_lower_when_instance_is_created(tmp_path):
    library = UserPvmLibrary(tmp_path)
    library.add("LoopReadout", [{
        "id": "value", "kind": "text", "x": 0, "y": 0,
        "w": 80, "h": 20, "text": "PV",
        "props": {"text": {
            "kind": "expression", "expr": "pv * 2",
            "refs": {"pv": "{ControlTag}/PV"},
        }},
    }])

    items = library.instantiate(
        "LoopReadout", 10, 20, config=_configuration(),
        choices={"ControlTag": "UNIT100/PID1"})

    assert items[0]["props"]["text"]["refs"] == {
        "pv": "UNIT100/PID1/PV"}
