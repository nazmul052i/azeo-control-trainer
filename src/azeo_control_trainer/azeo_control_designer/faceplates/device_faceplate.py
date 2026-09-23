"""Two-state device faceplate for ``DEVCTL``.

Smaller than the motor faceplate: one permissive, one interlock, one shutdown
rather than twenty channels. It is also the one the shipped example needs —
`MTR-102`, `MTR-203`, `XV-101` and `XV-OPTION` are all built on ``DEVCTL``.

Commanded and confirmed get equal weight and sit adjacent, because the
interesting condition is when they disagree — that is a fail-to-start or a
fail-to-open, and one big state indicator hides it.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from azeo_control_trainer.core.pid.theme.colors import BG_INSET
from azeo_control_trainer.core.pid.widgets.io_faceplates import (
    _Separator, _apply_btn_style, _make_cmd_button, _make_header,
)

from .block_faceplate import BlockFaceplate
from .faceplate_widgets import (
    BAD, BORDER, ConditionRow, FieldRow, IDLE, OK, TAG_COLOR, VALUE_COLOR,
    WARN, band,
)
from .start_blocked import show_start_blocked

log = logging.getLogger("ui.faceplates.device")

#: ``STATE`` → (name, text colour, lamp colour), using the block's numbering.
STATES = {
    0: ("STOPPED", VALUE_COLOR, IDLE),
    1: ("STARTING", WARN, WARN),
    2: ("RUNNING", OK, OK),
    3: ("STOPPING", WARN, WARN),
    4: ("FAULTED", BAD, BAD),
    5: ("INTERLOCKED", BAD, BAD),
    6: ("LOCKED", BAD, BAD),
}

#: The Azeo FAIL codes the block publishes.
FAIL_CODES = {
    0: "clear",
    1: "passive confirm time-out",
    2: "active confirm time-out",
    5: "active confirm lost",
    7: "tripped",
    8: "shutdown",
}

#: Tag prefixes that mean the device is a valve rather than a motor. ``DEVCTL``
#: is generic and has no named-set parameter, so the faceplate takes the verb
#: pair from the tag the module's author already chose. It changes the button
#: captions and nothing else — both write the same terminals.
VALVE_PREFIXES = ("XV", "HV", "FV", "ZV", "SDV", "BV", "MOV", "PV")


def is_valve(module: str, block_name: str, description: str) -> bool:
    stem = (module or block_name).upper().split("/")[-1]
    head = stem.split("-")[0].split("_")[0]
    if head in VALVE_PREFIXES:
        return True
    return "valve" in description.lower()


class DevctlFaceplate(BlockFaceplate):
    """The operator's view of one two-state device."""

    BLOCK_TYPE = "DEVCTL"

    def __init__(self, block, graph=None, module: str = "", store=None,
                 description: str = "", parent=None):
        super().__init__(block, graph, module, store, parent)
        self._description = description
        valve = is_valve(module, block.instance_name, description)
        self._on_label, self._off_label = (
            ("OPEN", "CLOSE") if valve else ("START", "STOP"))
        self._active, self._passive = (
            ("OPEN", "CLOSED") if valve else ("RUNNING", "STOPPED"))

        self.setWindowTitle(f"{module or block.instance_name} — device")
        self.resize(360, 440)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------- build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)
        root.addWidget(_make_header(self.module or self.block.instance_name,
                                    self._description,
                                    pin_btn=self._make_pin()))

        boxes = QGridLayout()
        boxes.setSpacing(4)
        self._cmd_box = self._state_box()
        self._fb_box = self._state_box()
        for col, (cap, box) in enumerate(
                (("commanded", self._cmd_box), ("confirmed", self._fb_box))):
            lbl = QLabel(cap)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"QLabel {{ font-size: 7pt; color: {TAG_COLOR}; }}")
            boxes.addWidget(box, 0, col)
            boxes.addWidget(lbl, 1, col)
        root.addLayout(boxes)
        root.addWidget(_Separator())

        rows = QVBoxLayout()
        rows.setSpacing(1)
        self._row_state = FieldRow("State", lamp=True)
        self._row_mode = FieldRow("Mode")
        self._row_fail = FieldRow("Failure")
        for r in (self._row_state, self._row_mode, self._row_fail):
            rows.addWidget(r)
        root.addLayout(rows)

        cond_band, self._cond_status = band("CONDITIONS")
        root.addWidget(cond_band)
        grid = QGridLayout()
        grid.setSpacing(0)
        self._cond_perm = ConditionRow(1, "Permissive")
        self._cond_ilk = ConditionRow(2, "Interlock")
        self._cond_sd = ConditionRow(3, "No shutdown")
        self._cond_fail = ConditionRow(4, "No failure")
        self._cond_lock = ConditionRow(5, "Not locked")
        grid.addWidget(self._cond_perm, 0, 0)
        grid.addWidget(self._cond_ilk, 0, 1)
        grid.addWidget(self._cond_sd, 1, 0)
        grid.addWidget(self._cond_fail, 1, 1)
        grid.addWidget(self._cond_lock, 2, 0)
        root.addLayout(grid)

        root.addStretch()
        root.addWidget(_Separator())

        cmds = QHBoxLayout()
        cmds.setSpacing(4)
        self._btn_on = _make_cmd_button(self._on_label, OK)
        self._btn_off = _make_cmd_button(self._off_label, BAD)
        self._btn_reset = _make_cmd_button("RESET", "#3574C4")
        for btn, term in ((self._btn_on, "START_CMD"),
                          (self._btn_off, "STOP_CMD"),
                          (self._btn_reset, "RESET")):
            btn.setCheckable(False)
            btn.setToolTip(self.command_tooltip(term))
            btn.setEnabled(self.command_available(term))
            cmds.addWidget(btn)
        self._btn_on.clicked.connect(self._on_activate)
        self._btn_off.clicked.connect(lambda: self.pulse("STOP_CMD"))
        self._btn_reset.clicked.connect(lambda: self.pulse("RESET"))
        root.addLayout(cmds)

        # AUTO and CAS are the two modes the block implements
        # (``config_choices``); offering MAN or OOS here would be a control
        # the block has nowhere to put.
        modes = QHBoxLayout()
        modes.setSpacing(4)
        cap = QLabel("Mode")
        cap.setStyleSheet(f"QLabel {{ font-size: 8pt; color: {TAG_COLOR}; }}")
        modes.addWidget(cap)
        self._mode_btns: dict[str, object] = {}
        for m in ("AUTO", "CAS"):
            btn = _make_cmd_button(m, "#3574C4")
            btn.setCheckable(False)
            btn.setFixedSize(60, 24)
            btn.clicked.connect(lambda _=False, mode=m: self._set_mode(mode))
            self._mode_btns[m] = btn
            modes.addWidget(btn)
        modes.addStretch()
        root.addLayout(modes)

    def _state_box(self) -> QLabel:
        lbl = QLabel("—")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setFixedHeight(38)
        lbl.setStyleSheet(
            f"QLabel {{ font-size: 12pt; font-weight: bold; color: {VALUE_COLOR};"
            f" background-color: {BG_INSET}; border: 1px solid {BORDER};"
            " border-radius: 3px; font-family: Consolas; }")
        return lbl

    # ----------------------------------------------------------- commands
    def _on_activate(self) -> None:
        blockers = self.blockers()
        if blockers:
            show_start_blocked(self.module or self.block.instance_name,
                               blockers, self)
            return
        self.pulse("START_CMD")

    def blockers(self) -> list[tuple[str, str]]:
        """What is standing between this device and its active state."""
        out: list[tuple[str, str]] = []
        if not bool(self._in("PERMISSIVE_D", True)):
            out.append(("PERMISSIVE_D", "the start permissive is not made"))
        if not bool(self._in("INTERLOCK", True)):
            out.append(("INTERLOCK", "an interlock is holding the device off"))
        if bool(self._in("SHUTDOWN_D", False)):
            out.append(("SHUTDOWN_D", "a shutdown request is asserted"))
        if bool(self._out("LOCKED", False)):
            out.append(("LOCKED",
                        "the device is locked out after a trip — RESET first"))
        return out

    def _set_mode(self, mode: str) -> None:
        self.set_param("mode", mode)
        self.refresh()

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        mode = str(self._out("MODE", "") or self._param("mode", "AUTO")).upper()

        # Commanded: in Cas the cascade input is the command; otherwise it is
        # what the block is driving at the field.
        commanded = bool(self._in("CAS_IN_D", False)) if mode == "CAS" \
            else bool(self._out("DO_START", False))
        confirmed = bool(self._in("RUN_FB", False))
        for box, val in ((self._cmd_box, commanded), (self._fb_box, confirmed)):
            box.setText(self._active if val else self._passive)
            box.setStyleSheet(
                f"QLabel {{ font-size: 12pt; font-weight: bold;"
                f" color: {OK if val else IDLE};"
                f" background-color: {BG_INSET}; border: 1px solid {BORDER};"
                " border-radius: 3px; font-family: Consolas; }")

        state = int(self._out("STATE", 0) or 0)
        name, colour, lamp = STATES.get(
            state, (f"STATE {state}", VALUE_COLOR, IDLE))
        self._row_state.set(name, colour, lamp)
        self._row_mode.set(mode)

        fail = int(self._out("FAIL", 0) or 0)
        self._row_fail.set(FAIL_CODES.get(fail, f"code {fail}"),
                           BAD if fail else OK)

        perm = bool(self._in("PERMISSIVE_D", True))
        ilk = bool(self._in("INTERLOCK", True))
        shutdown = bool(self._in("SHUTDOWN_D", False))
        locked = bool(self._out("LOCKED", False))
        self._cond_perm.set_state(perm)
        self._cond_ilk.set_state(ilk)
        self._cond_sd.set_state(not shutdown)
        self._cond_fail.set_state(not bool(self._out("FAIL_ACTIVE", False)))
        self._cond_lock.set_state(not locked)

        healthy = perm and ilk and not shutdown and not locked
        self._cond_status.setText("READY ✓" if healthy else "BLOCKED ✗")
        self._cond_status.setStyleSheet(
            f"QLabel {{ font-size: 7pt; font-weight: bold;"
            f" color: {OK if healthy else BAD}; background: transparent;"
            " border: none; }")

        for m, btn in self._mode_btns.items():
            _apply_btn_style(btn, "#3574C4", m == mode)
        _apply_btn_style(self._btn_on, OK, commanded)
        _apply_btn_style(self._btn_off, BAD, not commanded)
