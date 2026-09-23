"""Base class for all ISA-style function blocks."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import logging
import uuid

from .terminal import (
    Terminal, TerminalDirection, DataType, Quality, LimitStatus,
)

_log = logging.getLogger("strategy.block")


class BlockCategory(Enum):
    IO = "I/O"
    CONTROL = "Control"
    APC = "APC"
    MATH = "Math"
    SIGNAL = "Signal"
    LOGIC = "Logic"
    SAFETY = "Safety"
    SFC = "SFC"
    COMPOSITE = "Composite"


class BlockStatus(Enum):
    GOOD = "GOOD"
    BAD = "BAD"
    UNCERTAIN = "UNCERTAIN"
    OOS = "OOS"  # Out of Service


@dataclass
class BlockConfig:
    """Persistent configuration for a block (saved with strategy)."""

    params: dict[str, Any] = field(default_factory=dict)


class FunctionBlock:
    """Base class for all function blocks in the strategy graph.

    Subclasses must override:
        - block_type (class attribute): unique type name e.g. "PID", "AI"
        - category (class attribute): BlockCategory for palette grouping
        - _define_terminals(): create input/output terminals
        - execute(dt): perform one scan of block logic
    """

    block_type: str = "BASE"
    category: BlockCategory = BlockCategory.CONTROL
    display_name: str = "Block"
    description: str = ""

    # Legacy/alternate spellings accepted when loading saved strategies.
    # ``terminal_aliases`` maps an alias terminal name to the real one and is
    # resolved by StrategyGraph.add_wire; ``config_aliases`` maps an alias
    # config key to the real one and is applied by from_dict(). Both exist
    # because strategy JSON authored against IEC-style names (IN1/OUT/Q,
    # lead_time) would otherwise be silently discarded at load time.
    terminal_aliases: dict[str, str] = {}
    config_aliases: dict[str, str] = {}

    # Config keys accepted on any block as documentation metadata.
    _METADATA_KEYS = frozenset({"description", "label", "desc", "comment"})

    # Enumerated ("named set") config parameters: {param: (value, value, …)}.
    # The properties panel renders these as a drop-down instead of a free-text
    # box, so an operator picks a valid Azeo value rather than typing
    # "FLOW_COMP_SQUARE_ROOT" exactly right and silently getting the default.
    config_choices: dict[str, tuple[str, ...]] = {}

    # Engineering unit per config parameter: {param: "s"} — shown in the
    # properties panel's Unit column, mirroring Azeo's parameter view.
    config_units: dict[str, str] = {}

    @classmethod
    def choices_for(cls, param: str) -> tuple[str, ...]:
        """Enumerated values for a config parameter, case-insensitively."""
        if param in cls.config_choices:
            return tuple(cls.config_choices[param])
        low = param.lower()
        for key, vals in cls.config_choices.items():
            if key.lower() == low:
                return tuple(vals)
        return ()

    @classmethod
    def unit_for(cls, param: str) -> str:
        """Engineering unit for a config parameter, case-insensitively."""
        if param in cls.config_units:
            return cls.config_units[param]
        low = param.lower()
        for key, val in cls.config_units.items():
            if key.lower() == low:
                return val
        return ""

    def _default_hidden(self) -> set:
        """Terminal names a pristine block of this type + config hides.

        The reference block is built the way the loader builds one —
        construct, apply this block's config — so anything IT hides is
        derived state, not an engineer's choice, and stays out of the file.
        """
        try:
            reference = type(self)("__default_hidden__")
            reference.config.params = dict(self.config.params)
            try:
                reference._apply_config()
            except Exception:                           # noqa: BLE001
                pass
            return {n for n, t in reference.inputs.items() if t.hidden} \
                | {n for n, t in reference.outputs.items() if t.hidden}
        except Exception:                               # noqa: BLE001
            return set()

    def set_terminal_eu(self, name: str, units: str = "",
                        eu_range: tuple[float, float] | None = None) -> bool:
        """Stamp engineering units / range onto a terminal (HMI §7.1).

        Mirrors the `unit_for()` / `choices_for()` pattern: the block calls
        this from `_apply_config()` where it already knows the answer, and
        the type catalog / UA projection read it back. Returns False when
        the terminal does not exist — callers stamping optional terminals
        need not guard first.
        """
        terminal = self.outputs.get(name) or self.inputs.get(name)
        if terminal is None:
            return False
        terminal.units = units
        terminal.eu_range = (float(eu_range[0]), float(eu_range[1])) \
            if eu_range is not None else None
        return True

    def __init__(self, instance_name: str = ""):
        self.id: str = uuid.uuid4().hex[:12]
        self.instance_name: str = instance_name or f"{self.block_type}_{self.id[:4]}"
        self.inputs: dict[str, Terminal] = {}
        self.outputs: dict[str, Terminal] = {}
        self.config: BlockConfig = BlockConfig()
        self.status: BlockStatus = BlockStatus.GOOD

        # Canvas position (for serialization)
        self.x: float = 0.0
        self.y: float = 0.0

        # Execution order (set by compiler)
        self._exec_order: int = -1

        # Bypass: when True, runtime skips execute() and passes inputs through
        self._bypassed: bool = False

        # Block scan rate (Azeo "Block Scan Rate", right-click on a block).
        # "The block scan rate indicates the number of times the module's
        # algorithm is executed compared to the block. If the block scan rate
        # is one, there is a 1:1 ratio. If the block scan rate is 3, there is
        # a 1:3 ratio and the block is executed every third scan." A rate of 1
        # is the default and is neither displayed on the block nor persisted.
        # This is what lets a cascade's outer loop run slower than its inner
        # loop inside a single module.
        self._scan_rate: int = 1
        self._scan_tick: int = 0

        # Optional per-scan environment set by the strategy runtime before
        # each execute() call. Blocks that need cross-cutting facilities
        # (tag-store read/write from inside an ACT script, future alarm
        # raise, etc.) read this; most blocks ignore it.
        self.runtime_context = None

        # Per-block scan-time stats (microseconds, populated by the runtime).
        # Used by DiagnosticsDialog and the watch panel; never read in
        # hot paths. ``_exec_count`` counts successful executions; the
        # average is reconstructed as ``_exec_total_us / _exec_count``.
        self._last_exec_us: float = 0.0
        self._max_exec_us: float = 0.0
        self._exec_total_us: float = 0.0
        self._exec_count: int = 0

        self._define_terminals()

    @property
    def avg_exec_us(self) -> float:
        """Mean execute() duration in microseconds, 0 if never run."""
        return self._exec_total_us / self._exec_count if self._exec_count else 0.0

    def _define_terminals(self):
        """Override to define block terminals."""
        pass

    def add_input(
        self,
        name: str,
        data_type: DataType = DataType.FLOAT,
        default=0.0,
        description: str = "",
        is_bkcal: bool = False,
    ) -> Terminal:
        t = Terminal(
            name=name,
            direction=TerminalDirection.INPUT,
            data_type=data_type,
            default_value=default,
            description=description,
            is_bkcal=is_bkcal,
        )
        t.value = default
        self.inputs[name] = t
        return t

    def add_output(
        self,
        name: str,
        data_type: DataType = DataType.FLOAT,
        default=0.0,
        description: str = "",
        is_bkcal: bool = False,
    ) -> Terminal:
        t = Terminal(
            name=name,
            direction=TerminalDirection.OUTPUT,
            data_type=data_type,
            default_value=default,
            description=description,
            is_bkcal=is_bkcal,
        )
        t.value = default
        self.outputs[name] = t
        return t

    def resolve_terminal(self, name: str, *, output: bool) -> str:
        """Map an alias terminal name to the real one (identity if unknown)."""
        pool = self.outputs if output else self.inputs
        if name in pool:
            return name
        return self.terminal_aliases.get(name, name)

    def normalize_config(self) -> list[str]:
        """Rewrite alias config keys to their canonical names in place.

        Returns the keys that are neither canonical, an alias, nor metadata —
        i.e. values that would be silently ignored at runtime.
        """
        params = self.config.params
        for alias, real in self.config_aliases.items():
            if alias in params and real not in params:
                params[real] = params.pop(alias)
            else:
                params.pop(alias, None)
        try:
            known = set(self.get_config_schema())
        except Exception:
            return []
        return [k for k in params
                if k not in known and k not in self._METADATA_KEYS]

    # ── Signal quality ────────────────────────────────────────────────
    # Terminals carry a Quality and a LimitStatus alongside their value (see
    # model/terminal.py). Defaults are Good / Not limited, so a block that
    # ignores these behaves exactly as it did before they existed.

    def input_status(self, name: str) -> "Quality":
        t = self.inputs.get(name)
        return t.status if t else Quality.GOOD

    def input_limit(self, name: str) -> "LimitStatus":
        t = self.inputs.get(name)
        return t.limit if t else LimitStatus.NOT_LIMITED

    def set_output_status(self, name: str, status: "Quality",
                          limit: "LimitStatus | None" = None) -> None:
        t = self.outputs.get(name)
        if t is None:
            return
        t.status = status
        if limit is not None:
            t.limit = limit

    def worst_input_status(self, *names: str) -> "Quality":
        """Worst quality across the named inputs (all connected ones if none).

        This is the Azeo default for a computation block: the result is only
        as trustworthy as its least trustworthy input.
        """
        if names:
            terms = [self.inputs[n] for n in names if n in self.inputs]
        else:
            terms = [t for t in self.inputs.values() if t.connected]
        if not terms:
            return Quality.GOOD
        return max((t.status for t in terms), key=lambda q: q.value)

    def propagate_status(self, *, inputs: tuple[str, ...] = (),
                         outputs: tuple[str, ...] = ()) -> "Quality":
        """Stamp the worst input quality onto the named outputs (all if none).

        Returns the quality applied, so a caller can branch on it.
        """
        worst = self.worst_input_status(*inputs)
        targets = outputs or tuple(self.outputs)
        for n in targets:
            t = self.outputs.get(n)
            if t is not None:
                t.status = worst
        return worst

    def get_input(self, name: str) -> float | bool:
        t = self.inputs.get(name)
        return t.value if t else 0.0

    def get_output(self, name: str) -> float | bool:
        t = self.outputs.get(name)
        return t.value if t else 0.0

    def set_output(self, name: str, value):
        t = self.outputs.get(name)
        if t:
            t.value = value

    @property
    def bypassed(self) -> bool:
        """When True, runtime skips execute() for this block."""
        return self._bypassed

    @bypassed.setter
    def bypassed(self, value: bool):
        self._bypassed = bool(value)

    @property
    def scan_rate(self) -> int:
        """Execute once every N module scans (Azeo Block Scan Rate)."""
        return self._scan_rate

    @scan_rate.setter
    def scan_rate(self, value: int):
        try:
            n = int(value)
        except (TypeError, ValueError):
            n = 1
        self._scan_rate = max(1, n)
        self._scan_tick = 0

    def due_this_scan(self) -> bool:
        """Advance the scan divider and report whether this scan executes.

        Rate 1 always returns True, so the common case costs one comparison
        and nothing else changes. Higher rates execute on the *first* scan
        and then every Nth after it — a block must not sit idle through the
        download scan that seeds its outputs.
        """
        if self._scan_rate <= 1:
            return True
        due = (self._scan_tick % self._scan_rate) == 0
        self._scan_tick += 1
        return due

    def execute(self, dt: float):
        """Execute one scan. Override in subclasses."""
        pass

    def reset(self):
        """Reset block to initial state."""
        for t in self.inputs.values():
            t.reset()
        for t in self.outputs.values():
            t.reset()
        # Restart the block-scan-rate divider, so a block that goes back on
        # scan executes on the first scan rather than wherever its previous
        # run happened to leave the counter.
        self._scan_tick = 0

    # ─── Force / pulse — DCS-standard parameter override ────────────
    def force_terminal(self, name: str, value, *, release_after_s: float | None = None) -> bool:
        """Force an input terminal to a value (overrides its wire value).

        Azeo exposes forcing at block inputs only. Refusing output names is
        important: an output force hides the algorithm's actual result and can
        make an online test appear healthy while the block itself is failing.
        Returns True if the input exists and the force was applied.

        ``release_after_s`` — optional auto-release timer in seconds.
        Tracked by the runtime; on expiry the force is cleared and the
        terminal returns to its computed/wire-driven value.
        """
        t = self.inputs.get(name)
        if t is None:
            return False
        t.forced = True
        t.forced_value = value
        # The runtime tracks auto-release timers in a per-block dict so
        # they survive serialisation round-trips without modelling them
        # on the Terminal itself.
        if release_after_s is not None and release_after_s > 0:
            if not hasattr(self, "_force_timers"):
                self._force_timers = {}
            self._force_timers[name] = float(release_after_s)
        else:
            if hasattr(self, "_force_timers"):
                self._force_timers.pop(name, None)
        return True

    def release_terminal(self, name: str) -> bool:
        """Clear a force on a single input terminal. Returns True if released."""
        t = self.inputs.get(name)
        if t is None or not t.forced:
            return False
        t.forced = False
        if hasattr(self, "_force_timers"):
            self._force_timers.pop(name, None)
        return True

    def release_all_forces(self) -> int:
        """Clear every supported input force on this block.

        Output flags are also scrubbed defensively so a legacy file or caller
        that mutated a terminal directly cannot leave an unsupported force
        active after the engineer requests *Release all*.
        """
        n = 0
        for t in self.inputs.values():
            if t.forced:
                t.forced = False
                n += 1
        for t in self.outputs.values():
            if t.forced:
                t.forced = False
                n += 1
        if hasattr(self, "_force_timers"):
            self._force_timers.clear()
        return n

    def is_forced(self, name: str) -> bool:
        t = self.inputs.get(name)
        return bool(t and t.forced)

    def any_forced(self) -> bool:
        return any(t.forced for t in self.inputs.values())

    def forced_terminals(self) -> list[str]:
        return [n for n, t in self.inputs.items() if t.forced]

    def apply_forces(self) -> None:
        """Snap every forced input back to its forced value.

        Called by the runtime after wire propagation and before block
        execution, so an input force wins against its upstream wire."""
        for t in self.inputs.values():
            if t.forced:
                t.value = t.forced_value

    def tick_force_timers(self, dt: float) -> list[str]:
        """Decrement auto-release timers; release expired ones. Returns
        names of terminals released this tick."""
        released: list[str] = []
        timers = getattr(self, "_force_timers", None)
        if not timers:
            return released
        for name in list(timers.keys()):
            timers[name] -= dt
            if timers[name] <= 0:
                if self.release_terminal(name):
                    released.append(name)
        return released

    def get_config_schema(self) -> dict:
        """Return dict of configurable parameters {name: (type, default, description)}.
        Override in subclasses for properties panel."""
        return {}

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "block_type": self.block_type,
            "instance_name": self.instance_name,
            "x": self.x,
            "y": self.y,
            "config": self.config.params,
        }
        if self._bypassed:
            d["bypassed"] = True
        # Persist custom UI size if set
        if getattr(self, '_ui_width', None) is not None:
            d["ui_width"] = self._ui_width
        if getattr(self, '_ui_height', None) is not None:
            d["ui_height"] = self._ui_height
        # Persist hidden terminals — but only when they differ from what a
        # fresh block with this config hides on its own (absent when
        # default, I1). Blocks like OR auto-hide IN_D4..16 for the default
        # NOF_INPUTS; writing that set back persisted a derived default and
        # churned shipped modules on a plain load→save.
        hidden = [n for n, t in self.inputs.items() if t.hidden]
        hidden += [n for n, t in self.outputs.items() if t.hidden]
        if hidden and set(hidden) != self._default_hidden():
            d["hidden_terminals"] = hidden
        # Forced input terminals. The canvas can create these (block menu → Force),
        # and a force that silently disappears on reload is dangerous: the
        # engineer believes a value is still pinned when it is not.
        forced = {n: t.forced_value for n, t in self.inputs.items() if t.forced}
        if forced:
            d["forced_terminals"] = forced
        # Block scan rate. Azeo: "If the block scan rate is one, it is not
        # displayed on the block" — likewise it is not written to the file,
        # so existing modules stay byte-identical.
        if self.scan_rate != 1:
            d["scan_rate"] = self.scan_rate
        return d

    @classmethod
    def from_dict(cls, data: dict) -> FunctionBlock:
        block = cls(instance_name=data.get("instance_name", ""))
        block.id = data["id"]
        block.x = data.get("x", 0)
        block.y = data.get("y", 0)
        block.config.params = data.get("config", {})
        # Restore custom UI size
        if "ui_width" in data:
            block._ui_width = data["ui_width"]
        if "ui_height" in data:
            block._ui_height = data["ui_height"]
        # Restore hidden terminals
        for name in data.get("hidden_terminals", []):
            if name in block.inputs:
                block.inputs[name].hidden = True
            elif name in block.outputs:
                block.outputs[name].hidden = True
        # Restore forced terminals (auto-release timers are deliberately not
        # persisted — a timed force must not survive a reload).
        for name, value in (data.get("forced_terminals") or {}).items():
            block.force_terminal(name, value)
        block._bypassed = data.get("bypassed", False)
        block.scan_rate = data.get("scan_rate", 1)
        ignored = block.normalize_config()
        if ignored:
            _log.warning(
                "%s '%s': config keys have no effect: %s",
                block.block_type, block.instance_name, ", ".join(sorted(ignored)),
            )
        block._apply_config()
        return block

    def _apply_config(self):
        """Apply loaded config params to block state. Override in subclasses."""
        pass

    def __repr__(self):
        return f"<{self.block_type} '{self.instance_name}'>"
