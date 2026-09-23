"""``BlockFaceplate`` — a faceplate bound to a live block, and how it commands it.

The analog faceplates follow a *field tag* and read it out of the store. A
device faceplate cannot: state, the first-out cause, which permissive is
missing — none of that reaches the store. It lives on terminals. So these hold
the block itself, straight out of the compiled graph (``compile_strategy``
does not copy it, so the block on the canvas *is* the block being scanned), and
read its terminals directly.

The other half of this module is the command path. An operator button has to
reach an input terminal, and how it does that depends on what drives that
terminal — see :func:`resolve_command`.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QPushButton

from azeo_control_trainer.core.pid.theme.colors import TEXT_ON_DARK

from .faceplate_widgets import BODY_BG

log = logging.getLogger("ui.faceplates.block")

#: How long an operator command is held asserted. It must span at least one
#: module scan or the block never sees it; the executive's default period is
#: 500 ms, so this covers two scans with margin. A momentary press is the
#: right shape here — the blocks latch their own state, and a maintained
#: command would restart a device the instant a trip cleared.
PULSE_MS = 1200

#: A command terminal reaches the block by one of three routes.
ROUTE_TERMINAL = "terminal"   # unwired: write the terminal
ROUTE_FIELD = "field"         # wired from a DI: press the field pushbutton
ROUTE_DRIVEN = "driven"       # driven by module logic: the module owns it


def resolve_command(graph, block, terminal: str) -> tuple[str, str, bool]:
    """How a faceplate button reaches ``terminal``.

    Returns ``(route, target, invert)``:

    ``terminal``
        Nothing is wired to it, so the faceplate writes the terminal directly.
    ``field``
        It is wired from a ``DI``. Writing that ``DI``'s store tag is what
        pressing the real pushbutton does, and it is the only path a running
        module cannot undo — the wire re-asserts the terminal on every scan,
        so writing the terminal instead would last exactly until the next one.
        ``invert`` carries the ``DI``'s own inversion, so the button means what
        its caption says.
    ``driven``
        Something else computes it. The module owns that input and the
        faceplate must not fight it; ``target`` names the source, for the
        tooltip on the disabled button.
    """
    if graph is None:
        return ROUTE_TERMINAL, "", False
    try:
        wires = graph.get_input_wires(block.id)
    except Exception:                                       # noqa: BLE001
        return ROUTE_TERMINAL, "", False
    for w in wires:
        if w.dst_terminal != terminal:
            continue
        src = graph.blocks.get(w.src_block_id)
        if src is None:
            continue
        if src.block_type == "DI":
            tag = str(src.config.params.get("tag", "") or "").strip()
            if tag:
                return (ROUTE_FIELD, tag,
                        bool(src.config.params.get("invert", False)))
        return ROUTE_DRIVEN, f"{src.instance_name}.{w.src_terminal}", False
    return ROUTE_TERMINAL, "", False


class BlockFaceplate(QDialog):
    """Base for a faceplate that follows one live function block."""

    #: ``block_type`` this faceplate serves. Set by each subclass.
    BLOCK_TYPE = ""

    def __init__(self, block, graph=None, module: str = "", store=None,
                 parent=None):
        super().__init__(parent)
        self.block = block
        self.graph = graph
        self.module = module
        self._store = store
        self._pinned = False
        self._pulses: dict[str, QTimer] = {}
        self._pulse_releases = {}

        self.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setStyleSheet(f"QDialog {{ background-color: {BODY_BG}; }}")

    # ---------------------------------------------------------- identity
    @property
    def path(self) -> str:
        """``MODULE/BLOCK`` — the tag database's address for this block."""
        return f"{self.module}/{self.block.instance_name}" if self.module \
            else self.block.instance_name

    # -------------------------------------------------------------- pin
    def _make_pin(self) -> QPushButton:
        btn = QPushButton("Pin")
        btn.setCheckable(True)
        btn.setFixedSize(32, 20)
        btn.setStyleSheet(
            "QPushButton { font-size: 7pt; background: transparent;"
            f" color: {TEXT_ON_DARK}; border: 1px solid {TEXT_ON_DARK};"
            " border-radius: 2px; }"
            " QPushButton:checked { background: #CBD9E2; color: #0E3260;"
            " border: 1px solid #0E3260; }")
        btn.setToolTip("Pin window (keep on top, prevent auto-close)")
        btn.toggled.connect(self._on_pin_toggled)
        return btn

    def _on_pin_toggled(self, checked: bool) -> None:
        self._pinned = checked

    @property
    def pinned(self) -> bool:
        return self._pinned

    # -------------------------------------------------------- block reads
    def _in(self, name: str, default=False):
        t = self.block.inputs.get(name)
        return default if t is None else t.value

    def _out(self, name: str, default=False):
        t = self.block.outputs.get(name)
        return default if t is None else t.value

    def _param(self, name: str, default=None):
        return self.block.config.params.get(name, default)

    def _wired(self, terminal: str) -> bool:
        if self.graph is None:
            return False
        try:
            return any(w.dst_terminal == terminal
                       for w in self.graph.get_input_wires(self.block.id))
        except Exception:                                   # noqa: BLE001
            return False

    # ----------------------------------------------------- command writes
    def command_tooltip(self, terminal: str) -> str:
        route, target, _ = resolve_command(self.graph, self.block, terminal)
        if route == ROUTE_FIELD:
            return f"Pulses the field point {target}  ({terminal})"
        if route == ROUTE_DRIVEN:
            return (f"{terminal} is driven by {target} — the module owns this "
                    f"command, so the faceplate cannot issue it")
        return f"Writes {terminal} directly (nothing is wired to it)"

    def command_available(self, terminal: str) -> bool:
        route, _, _ = resolve_command(self.graph, self.block, terminal)
        return route != ROUTE_DRIVEN

    def pulse(self, terminal: str, hold_ms: int = PULSE_MS) -> bool:
        """Assert a command terminal momentarily. False if the module owns it."""
        route, target, invert = resolve_command(self.graph, self.block, terminal)
        if route == ROUTE_DRIVEN:
            log.info("%s: %s is driven by %s — command refused",
                     self.path, terminal, target)
            return False

        if route == ROUTE_FIELD and self._store is not None:
            on, off = (False, True) if invert else (True, False)
            self._store.set(target, on)
            self._arm_release(terminal,
                              lambda: self._store.set(target, off), hold_ms)
            log.info("%s: %s pulsed via field point %s",
                     self.path, terminal, target)
            return True

        if self.block.inputs.get(terminal) is None:
            return False
        self._set_terminal(terminal, True)
        self._arm_release(terminal,
                          lambda: self._set_terminal(terminal, False), hold_ms)
        log.info("%s: %s pulsed on the terminal", self.path, terminal)
        return True

    def hold(self, terminal: str, value: bool) -> bool:
        """Set a *maintained* input — a mode switch, not a command.

        ``MAINT`` and ``LOCAL`` are positions, not presses: they stay where
        the operator leaves them until they are moved back.
        """
        route, target, invert = resolve_command(self.graph, self.block, terminal)
        if route == ROUTE_DRIVEN:
            return False
        if route == ROUTE_FIELD and self._store is not None:
            self._store.set(target, (not value) if invert else value)
            return True
        if self.block.inputs.get(terminal) is None:
            return False
        self._set_terminal(terminal, value)
        return True

    def _set_terminal(self, terminal: str, value) -> None:
        t = self.block.inputs.get(terminal)
        if t is not None:
            t.value = value

    def _arm_release(self, key: str, release, hold_ms: int) -> None:
        """Drop the command after ``hold_ms``, restarting an in-flight hold."""
        timer = self._pulses.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._pulses[key] = timer
        else:
            timer.stop()
            try:
                timer.timeout.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._pulse_releases[key] = release
        timer.timeout.connect(lambda: self._release_pulse(key))
        timer.start(hold_ms)

    def _release_pulse(self, key: str) -> None:
        self._pulses[key].stop()
        release = self._pulse_releases.pop(key, None)
        if release is not None:
            release()

    def _release_commands(self) -> None:
        # Stopping a timer alone leaves START/STOP/RESET asserted indefinitely.
        for key in list(self._pulse_releases):
            self._release_pulse(key)

    def set_param(self, name: str, value) -> None:
        """Change a block config parameter the way the properties panel does."""
        self.block.config.params[name] = value
        try:
            self.block._apply_config()
        except Exception:                                   # noqa: BLE001
            log.exception("%s: applying %s=%r failed", self.path, name, value)

    # ----------------------------------------------------------- refresh
    def refresh(self) -> None:                              # pragma: no cover
        raise NotImplementedError

    def closeEvent(self, event):                            # noqa: N802
        self._release_commands()
        super().closeEvent(event)

    def done(self, result):
        self._release_commands()
        super().done(result)
