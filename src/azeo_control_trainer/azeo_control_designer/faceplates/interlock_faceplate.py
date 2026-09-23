"""Interlock summary faceplate — what a student opens to understand WHY.

The `INTERLOCK` block latches a shutdown; the reasons live upstream in
`CND` blocks. This faceplate walks the module's wires from the block's
TRIP and PERM inputs back to every CND feeding them (through logic
gates, which pass through) and shows each one the way the wireframe
asks (docs/ARCHITECTURE.md): expression, delay, current verdict — and
the **elapsed-time bar**, ET against TIME_TRUE. A condition with a
four-second duration that has been true for two seconds is *about to*
trip, and nothing else on any screen says so.

Honesty rules carried from the rest of the layer:

- "Tripped by" reports what is asserted NOW; when the latch is in and
  no condition is still asserted it says so — *condition cleared —
  trip is latched* — which is the latching lesson itself.
- Bypass writes the CND's own DISABLE through the same three-route
  command resolution every faceplate uses: unwired → the terminal,
  wired from a DI → that DI's store tag, driven by logic → refused
  with the source named. A button that looks live and does nothing is
  the worse failure.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .block_faceplate import (
    ROUTE_DRIVEN, ROUTE_FIELD, BlockFaceplate, resolve_command,
)
from .device_faceplate import _Separator, _make_header
from .faceplate_widgets import FieldRow, band, set_band_status

_TRACK = "#DBDDDF"
_AMBER = "#E0A020"
_RED = "#C0392B"
_TEXT = "#2B3238"


class ElapsedBar(QWidget):
    """ET against TIME_TRUE: amber while counting, red when made."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.elapsed = 0.0
        self.total = 0.0
        self.made = False
        self.setFixedHeight(14)
        self.setMinimumWidth(140)

    def set_values(self, elapsed: float, total: float,
                   made: bool) -> None:
        self.elapsed = max(0.0, float(elapsed or 0.0))
        self.total = max(0.0, float(total or 0.0))
        self.made = bool(made)
        self.update()

    def paintEvent(self, _event) -> None:           # noqa: N802
        painter = QPainter(self)
        rect = QRectF(0, 2, self.width() - 58, 10)
        painter.setPen(QColor("#9AA0A6"))
        painter.setBrush(QColor(_TRACK))
        painter.drawRect(rect)
        fraction = 1.0 if self.made else (
            min(1.0, self.elapsed / self.total)
            if self.total > 0 else 0.0)
        if fraction > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(_RED if self.made else _AMBER))
            painter.drawRect(QRectF(rect.x() + 1, rect.y() + 1,
                                    (rect.width() - 2) * fraction,
                                    rect.height() - 2))
        painter.setPen(QColor(_TEXT))
        font = painter.font()
        font.setPointSizeF(7.0)
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.right() + 4, 0, 54, 14),
            Qt.AlignVCenter | Qt.AlignLeft,
            f"{self.elapsed:.1f}/{self.total:.1f} s")
        painter.end()


class _ConditionPanel(QWidget):
    """One upstream CND: name, expression · delay, verdict, ET bar,
    and the Bypass toggle routed like every other command."""

    def __init__(self, faceplate, cnd, parent=None):
        super().__init__(parent)
        self.faceplate = faceplate
        self.cnd = cnd
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 4)
        lay.setSpacing(1)

        top = QHBoxLayout()
        self.name_label = QLabel(cnd.instance_name)
        self.name_label.setStyleSheet(
            f"font-weight: 600; font-size: 8pt; color: {_TEXT};")
        top.addWidget(self.name_label)
        self.verdict_label = QLabel("")
        self.verdict_label.setStyleSheet("font-size: 8pt;")
        top.addStretch(1)
        top.addWidget(self.verdict_label)
        self.bypass = QPushButton("Bypass")
        self.bypass.setCheckable(True)
        self.bypass.setFixedHeight(18)
        self.bypass.setStyleSheet("font-size: 7pt; padding: 1px 8px;")
        self.bypass.toggled.connect(self._on_bypass)
        top.addWidget(self.bypass)
        lay.addLayout(top)

        self.expr_label = QLabel("")
        self.expr_label.setStyleSheet(
            "font-family: Consolas; font-size: 7.5pt;"
            "color: #5A6168;")
        lay.addWidget(self.expr_label)
        self.et_bar = ElapsedBar()
        lay.addWidget(self.et_bar)
        self._sync_bypass_route()

    def _sync_bypass_route(self) -> None:
        route, source, _writable = resolve_command(
            self.faceplate.graph, self.cnd, "DISABLE")
        if route == ROUTE_DRIVEN:
            self.bypass.setEnabled(False)
            self.bypass.setToolTip(
                f"DISABLE is driven by {source} — bypass from the "
                "logic that owns it.")
        else:
            self.bypass.setToolTip(
                "Hold this condition bypassed (writes DISABLE — a "
                "position, not a press).")

    def _on_bypass(self, on: bool) -> None:
        route, source, _w = resolve_command(
            self.faceplate.graph, self.cnd, "DISABLE")
        if route == ROUTE_DRIVEN:
            return
        if route == ROUTE_FIELD and self.faceplate._store is not None:
            self.faceplate._store.set(source, bool(on))
            return
        terminal = self.cnd.inputs.get("DISABLE")
        if terminal is not None:
            terminal.value = bool(on)

    def refresh(self) -> None:
        params = self.cnd.config.params
        expression = str(params.get("EXPRESSION", ""))
        delay = float(params.get("TIME_TRUE", 0.0) or 0.0)
        self.expr_label.setText(
            f"expr  {expression}     delay  {delay:g} s")
        made = bool(self.cnd.outputs["OUT_D"].value)
        raw = bool(self.cnd.outputs["RAW"].value)
        bypassed = bool(self.cnd.get_input("DISABLE"))
        elapsed = float(self.cnd.outputs["ET"].value or 0.0)
        self.et_bar.set_values(elapsed, delay, made)
        if bypassed:
            text, colour = "BYPASSED", _AMBER
        elif made:
            text, colour = "✗ ASSERTED", _RED
        elif raw:
            text, colour = "counting…", _AMBER
        else:
            text, colour = "✓ healthy", "#3C7A3C"
        self.verdict_label.setText(text)
        self.verdict_label.setStyleSheet(
            f"font-size: 8pt; font-weight: 600; color: {colour};")
        if self.bypass.isChecked() != bypassed \
                and not self.bypass.isDown():
            self.bypass.blockSignals(True)
            self.bypass.setChecked(bypassed)
            self.bypass.blockSignals(False)


class InterlockFaceplate(BlockFaceplate):
    """The interlock summary — result, why, and how close."""

    BLOCK_TYPE = "INTERLOCK"

    #: Inputs whose upstream CNDs are trip causes vs reset permissives.
    _TRIP_INPUTS = ("TRIP",)
    _PERM_INPUTS = ("PERM1", "PERM2")

    def __init__(self, block, graph=None, module: str = "",
                 store=None, description: str = "", parent=None):
        super().__init__(block, graph, module, store, parent)
        self._description = description
        self.setWindowTitle(
            f"{module or block.instance_name} — interlock summary")
        self.resize(380, 460)
        self._build_ui()
        self.refresh()

    # -------------------------------------------------- wire walking
    def _upstream_cnds(self, input_names) -> list:
        """Every CND feeding these inputs, through pass-through logic
        (a transition routed through an AND is still that CND's
        doing). Deterministic order, visited-guarded."""
        if self.graph is None:
            return []
        found: dict = {}
        frontier = [(self.block.id, name) for name in input_names]
        visited = set()
        for _depth in range(12):
            next_frontier = []
            for block_id, terminal in frontier:
                for wire in self.graph.wires.values():
                    if wire.dst_block_id != block_id \
                            or wire.dst_terminal != terminal \
                            or wire.is_bkcal:
                        continue
                    source = self.graph.blocks.get(wire.src_block_id)
                    if source is None or source.id in visited:
                        continue
                    visited.add(source.id)
                    if source.block_type == "CND":
                        found[source.instance_name] = source
                    else:
                        next_frontier.extend(
                            (source.id, name)
                            for name in source.inputs)
            if not next_frontier:
                break
            frontier = next_frontier
        return [found[name] for name in sorted(found)]

    # ------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)
        root.addWidget(_make_header(
            self.module or self.block.instance_name,
            self._description or "Interlock summary",
            pin_btn=self._make_pin()))

        self._row_result = FieldRow("Result", lamp=True)
        self._row_ready = FieldRow("Ready to reset", lamp=True)
        self._row_first = FieldRow("Tripped by")
        for row in (self._row_result, self._row_ready,
                    self._row_first):
            root.addWidget(row)
        root.addWidget(_Separator())

        self._trip_panels: list = []
        self._perm_panels: list = []
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(3)
        for title, inputs, bucket in (
                ("TRIP CONDITIONS", self._TRIP_INPUTS,
                 self._trip_panels),
                ("RESET PERMISSIVES", self._PERM_INPUTS,
                 self._perm_panels)):
            head, status = band(title)
            body_lay.addWidget(head)
            if title == "TRIP CONDITIONS":
                self._trip_status = status
            cnds = self._upstream_cnds(inputs)
            if not cnds:
                empty = QLabel("(no conditions wired)")
                empty.setStyleSheet(
                    "font-size: 7.5pt; color: #8B8D8F;"
                    "padding-left: 6px;")
                body_lay.addWidget(empty)
            for cnd in cnds:
                panel = _ConditionPanel(self, cnd)
                bucket.append(panel)
                body_lay.addWidget(panel)
        body_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none;")
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        root.addWidget(_Separator())

        buttons = QGridLayout()
        self._reset_btn = QPushButton("RESET")
        self._reset_btn.setToolTip(self.command_tooltip("RESET"))
        self._reset_btn.setEnabled(self.command_available("RESET"))
        self._reset_btn.clicked.connect(
            lambda: self.pulse("RESET"))
        buttons.addWidget(self._reset_btn, 0, 0)
        root.addLayout(buttons)

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        tripped = bool(self._out("TRIPPED"))
        ready = bool(self._out("READY"))
        self._row_result.set("TRIPPED" if tripped else "CLEAR",
                             _RED if tripped else "#3C7A3C",
                             lamp_colour=_RED if tripped
                             else "#3C7A3C")
        self._row_ready.set("YES" if ready else "no",
                            "#3C7A3C" if ready else "#8B8D8F",
                            lamp_colour="#3C7A3C" if ready
                            else "#8B8D8F")
        asserted = [panel.cnd.instance_name
                    for panel in self._trip_panels
                    if bool(panel.cnd.outputs["OUT_D"].value)]
        if asserted:
            self._row_first.set(", ".join(asserted), _RED)
        elif tripped:
            # The latching lesson: what tripped it has cleared, and
            # the interlock is still in.
            self._row_first.set("condition cleared — trip is latched",
                                _AMBER)
        else:
            self._row_first.set("—")
        if hasattr(self, "_trip_status"):
            set_band_status(self._trip_status,
                            "ASSERTED" if asserted else "clear",
                            ok=not asserted)
        for panel in (*self._trip_panels, *self._perm_panels):
            panel.refresh()
        self._reset_btn.setEnabled(self.command_available("RESET")
                                   and ready)
