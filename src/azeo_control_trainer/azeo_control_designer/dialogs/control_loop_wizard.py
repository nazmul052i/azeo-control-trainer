"""Guided generation of validated single, cascade, and override loop patterns."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.authoring_dialog import (
    apply_authoring_dialog,
    style_dialog_buttons,
)
from azeo_control_trainer.core.strategy.blocks.composite_templates import (
    cascade_pid_loop,
    single_pid_loop,
)
from azeo_control_trainer.core.strategy.model.block_registry import registry
from azeo_control_trainer.core.strategy.model.strategy_graph import StrategyGraph


@dataclass(frozen=True)
class LoopWizardSettings:
    pattern: str
    name: str
    primary_pv_tag: str
    secondary_pv_tag: str
    output_tag: str
    low: float = 0.0
    high: float = 100.0
    selector: str = "MIN_SELECT"


def _configured(block_type: str, name: str, **config):
    block = registry.create(block_type, name)
    block.config.params.update(config)
    try:
        block._apply_config()
    except (KeyError, TypeError, ValueError):
        pass
    return block


def _override_graph(settings: LoopWizardSettings) -> StrategyGraph:
    graph = StrategyGraph(settings.name)
    blocks = [
        _configured("AI", f"AI_{settings.name}_1", tag=settings.primary_pv_tag,
                    scale_lo=settings.low, scale_hi=settings.high),
        _configured("AI", f"AI_{settings.name}_2", tag=settings.secondary_pv_tag,
                    scale_lo=settings.low, scale_hi=settings.high),
        _configured("PID", f"{settings.name}_1", out_lo=0.0, out_hi=1.0,
                    pv_scale_lo=settings.low, pv_scale_hi=settings.high,
                    sp_lo=settings.low, sp_hi=settings.high, mode="auto"),
        _configured("PID", f"{settings.name}_2", out_lo=0.0, out_hi=1.0,
                    pv_scale_lo=settings.low, pv_scale_hi=settings.high,
                    sp_lo=settings.low, sp_hi=settings.high, mode="auto"),
        _configured(settings.selector, f"{settings.name}_SELECT"),
        _configured("SCALER", f"{settings.name}_SCALE", in_lo=0.0, in_hi=1.0,
                    out_lo=0.0, out_hi=100.0),
        _configured("AO", f"AO_{settings.name}", tag=settings.output_tag,
                    out_lo=0.0, out_hi=100.0),
    ]
    positions = [(0, 0), (0, 300), (240, 0), (240, 300),
                 (500, 140), (720, 140), (940, 140)]
    for block, (x, y) in zip(blocks, positions):
        block.x, block.y = x, y
        graph.add_block(block)
    ai1, ai2, pid1, pid2, selector, scaler, ao = blocks
    connections = [
        (ai1, "OUT", pid1, "IN", False),
        (ai2, "OUT", pid2, "IN", False),
        (pid1, "OUT", selector, "IN1", False),
        (pid2, "OUT", selector, "IN2", False),
        (selector, "OUT", scaler, "IN", False),
        (scaler, "OUT", ao, "CAS_IN", False),
        (ao, "BKCAL_OUT", scaler, "BKCAL_IN", True),
        (scaler, "BKCAL_OUT", pid1, "BKCAL_IN", True),
        (scaler, "BKCAL_OUT", pid2, "BKCAL_IN", True),
    ]
    for src, src_pin, dst, dst_pin, feedback in connections:
        graph.add_wire(src.id, src_pin, dst.id, dst_pin, is_bkcal=feedback)
    return graph


def build_loop_graph(settings: LoopWizardSettings) -> StrategyGraph:
    """Build the chosen pattern without mutating the current module."""
    if settings.pattern == "single":
        return single_pid_loop(
            instance_name=settings.name,
            pv_tag=settings.primary_pv_tag,
            mv_tag=settings.output_tag,
            pid_name=settings.name,
            pv_lo=settings.low,
            pv_hi=settings.high,
        ).inner_graph
    if settings.pattern == "cascade":
        return cascade_pid_loop(
            instance_name=settings.name,
            outer_pv_tag=settings.primary_pv_tag,
            inner_pv_tag=settings.secondary_pv_tag,
            mv_tag=settings.output_tag,
            outer_pid_name=f"{settings.name}_OUTER",
            inner_pid_name=f"{settings.name}_INNER",
            outer_pv_lo=settings.low,
            outer_pv_hi=settings.high,
            inner_pv_lo=settings.low,
            inner_pv_hi=settings.high,
        ).inner_graph
    return _override_graph(settings)


def generate_control_loop(scene, settings: LoopWizardSettings,
                          origin: QPointF) -> list[str]:
    """Place a complete expanded loop; one Undo removes the whole pattern."""
    if scene.structure_locked:
        scene._refuse_structural_edit("generate a control loop")
        return []
    source = build_loop_graph(settings)
    wires = list(source.wires.values())
    added: list[str] = []
    scene.undo_stack.beginMacro(f"Generate {settings.pattern.title()} Control Loop")
    try:
        for block in source.blocks.values():
            item = scene.add_block(
                block,
                QPointF(origin.x() + block.x, origin.y() + block.y),
            )
            if item is not None:
                added.append(block.id)
        for wire in wires:
            scene.add_wire(
                wire.src_block_id, wire.src_terminal,
                wire.dst_block_id, wire.dst_terminal,
                is_bkcal=wire.is_bkcal,
            )
    finally:
        scene.undo_stack.endMacro()
    scene.clearSelection()
    for block_id in added:
        item = scene.get_block_item(block_id)
        if item is not None:
            item.setSelected(True)
    return added


class ControlLoopWizard(QDialog):
    def __init__(self, scene, origin: QPointF, parent=None):
        super().__init__(parent)
        self._scene = scene
        self._origin = QPointF(origin)
        self.generated_ids: list[str] = []
        self.setWindowTitle("Guided Control Loop Wizard")
        apply_authoring_dialog(self)
        self.resize(520, 420)
        layout = QVBoxLayout(self)
        title = QLabel("GUIDED CONTROL LOOP")
        title.setStyleSheet(f"font-weight: bold; color: {UI.blue};")
        layout.addWidget(title)
        form = QFormLayout()
        self.pattern = QComboBox()
        self.pattern.addItem("Single PID", "single")
        self.pattern.addItem("Cascade PID", "cascade")
        self.pattern.addItem("Override / Constraint", "override")
        self.name = QLineEdit("PIC_LOOP")
        self.primary = QLineEdit()
        self.secondary = QLineEdit()
        self.output = QLineEdit()
        self.low = QDoubleSpinBox()
        self.low.setRange(-1e9, 1e9)
        self.high = QDoubleSpinBox()
        self.high.setRange(-1e9, 1e9)
        self.high.setValue(100.0)
        self.selector = QComboBox()
        self.selector.addItem("Low select", "MIN_SELECT")
        self.selector.addItem("High select", "MAX_SELECT")
        form.addRow("Pattern", self.pattern)
        form.addRow("Loop name", self.name)
        form.addRow("Primary PV tag", self.primary)
        form.addRow("Secondary PV tag", self.secondary)
        form.addRow("Output tag", self.output)
        form.addRow("PV scale low", self.low)
        form.addRow("PV scale high", self.high)
        form.addRow("Override selector", self.selector)
        layout.addLayout(form)
        hint = QLabel(
            "The wizard creates ordinary editable blocks and a complete BKCAL chain. "
            "The entire pattern is one undo operation."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Generate")
        style_dialog_buttons(buttons, QDialogButtonBox.Ok)
        buttons.accepted.connect(self._generate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.pattern.currentIndexChanged.connect(self._update_fields)
        self._update_fields()

    def _update_fields(self):
        pattern = self.pattern.currentData()
        self.secondary.setEnabled(pattern in {"cascade", "override"})
        self.selector.setEnabled(pattern == "override")

    def _generate(self):
        required = [self.name, self.primary, self.output]
        if self.pattern.currentData() in {"cascade", "override"}:
            required.append(self.secondary)
        missing = [field for field in required if not field.text().strip()]
        for field in required:
            field.setStyleSheet("border: 1px solid #C62828;" if field in missing else "")
        if missing or self.low.value() >= self.high.value():
            return
        settings = LoopWizardSettings(
            pattern=self.pattern.currentData(),
            name=self.name.text().strip(),
            primary_pv_tag=self.primary.text().strip(),
            secondary_pv_tag=self.secondary.text().strip(),
            output_tag=self.output.text().strip(),
            low=self.low.value(),
            high=self.high.value(),
            selector=self.selector.currentData(),
        )
        self.generated_ids = generate_control_loop(self._scene, settings, self._origin)
        if self.generated_ids:
            self.accept()
