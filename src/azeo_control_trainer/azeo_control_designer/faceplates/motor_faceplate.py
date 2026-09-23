"""Motor / pump faceplate for ``MOTOR_INTERLOCK``.

Eight start permissives, eight run interlocks, four hard trips, first-out and
bypass state — mapped onto what the block actually publishes. Layout follows
``docs/ARCHITECTURE.md``.

Three rules the layout encodes, each from the interlock spec:

* **Trips sit in their own band** and carry no bypass control — they are the
  channels that cannot be bypassed. Grouping them with the permissives would
  imply otherwise.
* **Bypass is annunciated, not hidden.** A bypassed condition reads healthy on
  its own row, so the banner is the only thing telling the operator the
  protection is not there.
* **First-out is a field, not a derivation.** The block latches which cause
  asserted first; display that rather than guessing from what is unhealthy
  now.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

from azeo_control_trainer.core.pid.theme.colors import BG_INSET
from azeo_control_trainer.core.pid.widgets.io_faceplates import (
    _Separator, _apply_btn_style, _make_cmd_button, _make_header,
)

from .block_faceplate import BlockFaceplate, ROUTE_DRIVEN, resolve_command
from .faceplate_widgets import (
    BAD, BODY_BG, BORDER, ConditionRow, FieldRow, IDLE, OK, TAG_COLOR,
    VALUE_COLOR, WARN, band, set_band_status,
)
from .start_blocked import show_start_blocked

log = logging.getLogger("ui.faceplates.motor")

#: State name → (text colour, lamp colour).
STATE_STYLE = {
    "STOPPED":         (VALUE_COLOR, IDLE),
    "STARTING":        (WARN, WARN),
    "RUNNING":         (OK, OK),
    "STOPPING":        (WARN, WARN),
    "TRIPPED":         (BAD, BAD),
    "FAILED_TO_START": (BAD, BAD),
    "MAINTENANCE":     (WARN, WARN),
    "LOCAL":           (WARN, WARN),
    "LOCKOUT":         (BAD, BAD),
}


def channel_label(block, channel: int) -> str:
    """``FIRST_OUT`` numbering: 1..8 perms, 9..16 run interlocks, 17..20 trips."""
    n_p = getattr(block, "_NUM_PERMS", 8)
    n_r = getattr(block, "_NUM_RUNS", 8)
    if 1 <= channel <= n_p:
        return f"PERM_{channel}"
    if channel <= n_p + n_r:
        return f"RUN_{channel - n_p}"
    return f"TRIP_{channel - n_p - n_r}"


class MotorInterlockFaceplate(BlockFaceplate):
    """The operator's view of one motor and everything holding it off."""

    BLOCK_TYPE = "MOTOR_INTERLOCK"

    def __init__(self, block, graph=None, module: str = "", store=None,
                 parent=None):
        super().__init__(block, graph, module, store, parent)
        self._n_perm = getattr(block, "_NUM_PERMS", 8)
        self._n_run = getattr(block, "_NUM_RUNS", 8)
        self._n_trip = getattr(block, "_NUM_TRIPS", 4)
        self._last_cmd = ""
        self._show_all = False
        self._perm_rows: dict[int, ConditionRow] = {}
        self._run_rows: dict[int, ConditionRow] = {}
        self._trip_rows: dict[int, ConditionRow] = {}
        # Show All changes visibility; it does not destroy and reconstruct a
        # native widget tree from inside the checkbox's signal. Stable row
        # identity avoids deferred native ownership churn during interaction.
        self._perm_pool: dict[int, ConditionRow] = {}
        self._run_pool: dict[int, ConditionRow] = {}
        self._trip_pool: dict[int, ConditionRow] = {}

        self.setWindowTitle(f"{block.instance_name} — motor")
        self.resize(430, 580)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------- build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)
        root.addWidget(_make_header(self.block.instance_name, self.module,
                                    pin_btn=self._make_pin()))

        ident = QVBoxLayout()
        ident.setSpacing(1)
        self._row_state = FieldRow("State", lamp=True)
        self._row_mode = FieldRow("Mode")
        self._row_cmd = FieldRow("Command")
        self._row_fb = FieldRow("Feedback")
        self._row_first = FieldRow("First-Out")
        for r in (self._row_state, self._row_mode, self._row_cmd,
                  self._row_fb, self._row_first):
            ident.addWidget(r)
        root.addLayout(ident)
        root.addWidget(_Separator())

        self._perm_band, self._perm_status = band("START PERMISSIVES")
        root.addWidget(self._perm_band)
        self._perm_grid = QGridLayout()
        self._perm_grid.setSpacing(0)
        root.addLayout(self._perm_grid)
        self._perm_empty = self._empty_note()
        root.addWidget(self._perm_empty)

        self._run_band, self._run_status = band("RUN INTERLOCKS")
        root.addWidget(self._run_band)
        self._run_grid = QGridLayout()
        self._run_grid.setSpacing(0)
        root.addLayout(self._run_grid)
        self._run_empty = self._empty_note()
        root.addWidget(self._run_empty)

        self._trip_band, self._trip_status = band("TRIPS")
        root.addWidget(self._trip_band)
        self._trip_grid = QGridLayout()
        self._trip_grid.setSpacing(0)
        root.addLayout(self._trip_grid)
        self._trip_empty = self._empty_note()
        root.addWidget(self._trip_empty)

        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            f"QLabel {{ font-size: 8pt; font-weight: bold; color: #4A3000;"
            f" background-color: {WARN}; border-radius: 3px;"
            " padding: 4px 6px; }")
        self._banner.setVisible(False)
        root.addWidget(self._banner)

        root.addStretch()

        self._chk_all = QCheckBox("Show all channels")
        self._chk_all.setStyleSheet(
            f"QCheckBox {{ font-size: 7pt; color: {TAG_COLOR}; }}")
        self._chk_all.setToolTip(
            "Channels with no description and nothing wired to them are "
            "unassigned, and are hidden by default.")
        self._chk_all.toggled.connect(self._on_show_all)
        root.addWidget(self._chk_all)

        root.addWidget(_Separator())

        cmds = QHBoxLayout()
        cmds.setSpacing(4)
        self._btn_start = _make_cmd_button("START", OK)
        self._btn_stop = _make_cmd_button("STOP", BAD)
        self._btn_reset = _make_cmd_button("RESET", "#3574C4")
        self._btn_maint = _make_cmd_button("MAINT", WARN)
        for btn, term in ((self._btn_start, "START_REQ"),
                          (self._btn_stop, "STOP_REQ"),
                          (self._btn_reset, "RESET"),
                          (self._btn_maint, "MAINT")):
            btn.setCheckable(False)
            btn.setToolTip(self.command_tooltip(term))
            btn.setEnabled(self.command_available(term))
            cmds.addWidget(btn)
        self._btn_maint.setCheckable(True)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_stop.clicked.connect(lambda: self._issue("STOP_REQ", "STOP"))
        self._btn_reset.clicked.connect(self._on_reset)
        self._btn_maint.toggled.connect(self._on_maint)
        root.addLayout(cmds)

        row = QHBoxLayout()
        row.addStretch()
        btn_detail = QPushButton("Detail…")
        btn_detail.setFixedSize(72, 24)
        btn_detail.setAutoDefault(False)
        btn_detail.setStyleSheet(
            f"QPushButton {{ background-color: {BG_INSET}; color: {TAG_COLOR};"
            f" border: 1px solid {BORDER}; border-radius: 3px;"
            " font-size: 8pt; }")
        btn_detail.clicked.connect(self._open_detail)
        row.addWidget(btn_detail)
        root.addLayout(row)

        self._rebuild_rows()

    def _empty_note(self) -> QLabel:
        lbl = QLabel("    none configured")
        lbl.setStyleSheet(f"QLabel {{ font-size: 8pt; color: {IDLE}; }}")
        lbl.setVisible(False)
        return lbl

    # -------------------------------------------------------- channel set
    def _channel_desc(self, prefix: str, n: int) -> str:
        return str(self._param(f"{prefix}_{n}_DESC", "") or "")

    def _in_use(self, prefix: str, n: int) -> bool:
        """A channel is in use when it is described or something drives it.

        An undescribed, unwired channel is a default constant with nothing to
        say, and twenty of those bury the two that matter. Nothing is hidden
        permanently — *Show all channels* brings them back, and the detail
        dialog always lists every one.
        """
        if self._show_all:
            return True
        return bool(self._channel_desc(prefix, n)) or self._wired(f"{prefix}_{n}")

    def _rebuild_rows(self) -> None:
        for grid, rows, pool, prefix, count, empty in (
                (self._perm_grid, self._perm_rows, self._perm_pool,
                 "PERM", self._n_perm, self._perm_empty),
                (self._run_grid, self._run_rows, self._run_pool,
                 "RUN", self._n_run, self._run_empty),
                (self._trip_grid, self._trip_rows, self._trip_pool,
                 "TRIP", self._n_trip, self._trip_empty)):
            if not pool:
                for n in range(1, count + 1):
                    row = ConditionRow(n, self._channel_desc(prefix, n)
                                       or f"{prefix}_{n}")
                    pool[n] = row
                    grid.addWidget(row, (n - 1) // 2, (n - 1) % 2)
            rows.clear()
            for n, row in pool.items():
                row.set_description(self._channel_desc(prefix, n)
                                    or f"{prefix}_{n}")
                visible = self._in_use(prefix, n)
                row.setVisible(visible)
                if visible:
                    rows[n] = row
            empty.setVisible(not rows)

    def _on_show_all(self, checked: bool) -> None:
        self._show_all = checked
        self._rebuild_rows()
        self.refresh()

    # ----------------------------------------------------------- commands
    def _issue(self, terminal: str, label: str) -> None:
        if self.pulse(terminal):
            self._last_cmd = label
            self.refresh()

    def _on_start(self) -> None:
        """START — or an explanation of why not."""
        if not bool(self._out("START_PERMIT", False)):
            show_start_blocked(self.block.instance_name,
                               self.start_blockers(), self)
            return
        self._issue("START_REQ", "START")

    def start_blockers(self) -> list[tuple[str, str]]:
        """Everything currently standing between this motor and a start."""
        out: list[tuple[str, str]] = []
        for n in range(1, self._n_perm + 1):
            if bool(self._in(f"BYPASS_PERM_{n}", False)):
                continue
            if not bool(self._in(f"PERM_{n}", True)):
                out.append((f"PERM_{n}",
                            self._channel_desc("PERM", n) or "permissive"))
        # A state reason blocks a start just as hard as a missing permissive,
        # and an operator shown only the permissive list keeps pressing.
        for terminal, label, text in (
                ("LOCKOUT", "LOCKOUT", "lockout / tagout is active"),
                ("MAINT", "MAINT", "the equipment is in maintenance"),
                ("LOCAL", "LOCAL", "the field switch is in LOCAL")):
            if bool(self._in(terminal, False)):
                out.append((label, text))
        if bool(self._out("TRIPPED", False)):
            first = str(self._out("FIRST_OUT_DESC", "") or "")
            out.append(("TRIPPED",
                        f"a trip is latched — {first}" if first
                        else "a trip is latched; RESET it first"))
        return out

    def _on_reset(self) -> None:
        """RESET asserts ACK with it.

        The block clears a latched trip only on ``RESET and ACK`` with the
        cause clean. Splitting those across two buttons would make RESET look
        broken; one operator action, both terminals.
        """
        self.pulse("ACK")
        if self.pulse("RESET"):
            self._last_cmd = "RESET"
            self.refresh()

    def _on_maint(self, checked: bool) -> None:
        """MAINT is a position, not a press — it holds until switched back."""
        route, _, _ = resolve_command(self.graph, self.block, "MAINT")
        if route == ROUTE_DRIVEN or not self.hold("MAINT", checked):
            self._btn_maint.blockSignals(True)
            self._btn_maint.setChecked(not checked)
            self._btn_maint.blockSignals(False)
            return
        _apply_btn_style(self._btn_maint, WARN, checked)
        self.refresh()

    def _open_detail(self) -> None:
        MotorInterlockDetailDialog(self, parent=self).show()

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        state = str(self._out("STATE_NAME", "") or "UNKNOWN")
        colour, lamp = STATE_STYLE.get(state, (VALUE_COLOR, IDLE))
        self._row_state.set(state, colour, lamp)

        # Mode, in the precedence order the block itself resolves them.
        if bool(self._in("LOCKOUT", False)):
            mode, mcol = "LOCKOUT", BAD
        elif bool(self._in("MAINT", False)):
            mode, mcol = "MAINTENANCE", WARN
        elif bool(self._in("LOCAL", False)):
            mode, mcol = "LOCAL", WARN
        else:
            mode, mcol = "REMOTE", VALUE_COLOR
        self._row_mode.set(mode, mcol)

        if bool(self._in("START_REQ", False)):
            self._row_cmd.set("START", OK)
        elif bool(self._in("STOP_REQ", False)):
            self._row_cmd.set("STOP", BAD)
        else:
            self._row_cmd.set(self._last_cmd or "—", IDLE)

        running = bool(self._in("RUN_FB", False))
        self._row_fb.set("RUNNING" if running else "STOPPED",
                         OK if running else IDLE)

        first = str(self._out("FIRST_OUT_DESC", "") or "")
        if not first:
            channel = int(self._out("FIRST_OUT", 0) or 0)
            first = channel_label(self.block, channel) if channel else ""
        self._row_first.set(first or "NONE", BAD if first else IDLE)

        for n, row in self._perm_rows.items():
            row.set_state(bool(self._in(f"PERM_{n}", True)),
                          bool(self._in(f"BYPASS_PERM_{n}", False)))
        for n, row in self._run_rows.items():
            row.set_state(bool(self._in(f"RUN_{n}", True)),
                          bool(self._in(f"BYPASS_RUN_{n}", False)))
        for n, row in self._trip_rows.items():
            # A trip input is active-high: True means tripped.
            row.set_state(not bool(self._in(f"TRIP_{n}", False)))

        perms_ok = bool(self._out("PERMS_OK", False))
        set_band_status(self._perm_status,
                        "PERMS_OK ✓" if perms_ok else "PERMS_OK ✗", perms_ok)
        run_ok = all(bool(self._in(f"RUN_{n}", True))
                     or bool(self._in(f"BYPASS_RUN_{n}", False))
                     for n in range(1, self._n_run + 1))
        set_band_status(self._run_status, "OK ✓" if run_ok else "FAILED ✗",
                        run_ok)
        trip = bool(self._out("TRIP_ACTIVE", False))
        set_band_status(self._trip_status,
                        "TRIP ACTIVE ✗" if trip else "CLEAR ✓", not trip)

        bypassed = self.bypassed_channels()
        if bypassed:
            self._banner.setText("⚠  BYPASS ACTIVE — " + ", ".join(bypassed))
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)

        _apply_btn_style(self._btn_start, OK,
                         bool(self._out("READY_TO_START", False)))

    def bypassed_channels(self) -> list[str]:
        out = []
        for n in range(1, self._n_perm + 1):
            if bool(self._in(f"BYPASS_PERM_{n}", False)):
                out.append(self._channel_desc("PERM", n) or f"PERM_{n}")
        for n in range(1, self._n_run + 1):
            if bool(self._in(f"BYPASS_RUN_{n}", False)):
                out.append(self._channel_desc("RUN", n) or f"RUN_{n}")
        return out


class MotorInterlockDetailDialog(QDialog):
    """Every channel, its delay and its raw input — the unassigned included.

    The faceplate hides channels nobody configured; this is where an engineer
    checks that judgement, and where the per-channel delays live.
    """

    def __init__(self, faceplate: MotorInterlockFaceplate, parent=None):
        super().__init__(parent)
        blk = faceplate.block
        self.setWindowTitle(f"{blk.instance_name} — interlock detail")
        self.setStyleSheet(f"QDialog {{ background-color: {BODY_BG}; }}")
        self.resize(540, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)
        root.addWidget(_make_header(blk.instance_name, "Interlock detail"))

        grid = QGridLayout()
        grid.setSpacing(2)
        for c, text in enumerate(
                ("Ch", "Channel", "Description", "Delay", "Input", "Bypass")):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"QLabel {{ font-size: 7pt; font-weight: bold;"
                f" color: {TAG_COLOR}; }}")
            grid.addWidget(lbl, 0, c)

        r = 1
        for prefix, count, delay_key in (
                ("PERM", faceplate._n_perm, "PERM_{}_DELAY"),
                ("RUN", faceplate._n_run, "RUN_{}_DELAY_OFF"),
                ("TRIP", faceplate._n_trip, "")):
            for n in range(1, count + 1):
                name = f"{prefix}_{n}"
                desc = str(blk.config.params.get(f"{name}_DESC", "") or "")
                if delay_key:
                    delay = "%g s" % float(
                        blk.config.params.get(delay_key.format(n), 0.0) or 0.0)
                else:
                    delay = "—"
                raw = blk.inputs.get(name)
                byp = blk.inputs.get(f"BYPASS_{name}")
                for c, text in enumerate((
                        str(r), name, desc or "(unassigned)", delay,
                        "—" if raw is None else str(bool(raw.value)),
                        "—" if byp is None else str(bool(byp.value)))):
                    lbl = QLabel(text)
                    lbl.setStyleSheet(
                        f"QLabel {{ font-size: 8pt; color: "
                        f"{VALUE_COLOR if desc else IDLE}; }}")
                    grid.addWidget(lbl, r, c)
                r += 1
        root.addLayout(grid)

        root.addWidget(_Separator())
        cfg = QLabel("    ".join(
            f"{k} = {blk.config.params.get(k)}"
            for k in ("STARTUP_TIMEOUT", "STOP_TIMEOUT", "LATCH_TRIPS",
                      "AUTO_RESET")
            if k in blk.config.params))
        cfg.setWordWrap(True)
        cfg.setStyleSheet(f"QLabel {{ font-size: 8pt; color: {TAG_COLOR}; }}")
        root.addWidget(cfg)
        root.addStretch()

        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(72, 26)
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)
