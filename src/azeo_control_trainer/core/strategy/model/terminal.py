"""Function block terminal (input/output port) model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


class TerminalDirection(Enum):
    INPUT = "IN"
    OUTPUT = "OUT"


class DataType(Enum):
    FLOAT = "FLOAT"
    BOOL = "BOOL"
    INT = "INT"
    STRING = "STRING"
    ENUM = "ENUM"


class Quality(Enum):
    """Signal quality carried alongside a terminal's value.

    In Azeo every parameter is a value *plus* a status, and that status is
    what drives real behaviour: a PID sheds to Manual on a Bad PV, STATUS_OPTS
    decides whether Uncertain counts as Good, a Cause & Effect matrix reacts to
    a bad cause. Carrying only a float made all of that unimplementable, so
    blocks faked it with side-channel ``*_BAD`` pins.

    Ordered worst-last so ``max()`` gives the worst of a set of inputs.
    """
    GOOD = 0
    UNCERTAIN = 1
    BAD = 2

    @property
    def is_usable(self) -> bool:
        """True for Good — the default notion of "safe to act on"."""
        return self is Quality.GOOD


class LimitStatus(Enum):
    """Whether a value is being held at a limit, and which way.

    A downstream block that is limited reports it back up the BKCAL chain so
    the upstream controller can stop winding reset into the limit.
    """
    NOT_LIMITED = "NOT_LIMITED"
    LOW_LIMITED = "LOW_LIMITED"
    HIGH_LIMITED = "HIGH_LIMITED"
    CONSTANT = "CONSTANT"        # cannot move at all (e.g. block in Man)


@dataclass
class Terminal:
    """A single input or output port on a function block."""

    name: str
    direction: TerminalDirection
    data_type: DataType = DataType.FLOAT
    default_value: float | bool | int | str = 0.0
    description: str = ""
    is_bkcal: bool = False  # Back-calculation terminal (feedback edge)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # Runtime state
    value: float | bool | int | str = 0.0
    # Signal quality + limit state travelling with ``value`` along wires.
    # Defaults are Good / Not limited, so a block that never touches them
    # behaves exactly as before this existed.
    status: Quality = Quality.GOOD
    limit: LimitStatus = LimitStatus.NOT_LIMITED
    # Engineering units and range (HMI proposal §7.1). Both default inert
    # (I4) and are runtime metadata populated by the block where it already
    # knows them (AI scale, AO clamps, PID output span) — never persisted,
    # never invented. ``None`` means "unknown"; downstream must fall back
    # explicitly rather than assume 0–100.
    units: str = ""
    eu_range: tuple[float, float] | None = None
    connected: bool = False
    hidden: bool = False  # Hidden from UI (pin not shown on block)
    # Force / pulse (DCS-standard): when ``forced`` is True the runtime
    # overrides this terminal's value with ``forced_value`` after every
    # block execution and wire propagation pass. Used for debugging at
    # design time and operator-driven overrides at runtime.
    forced: bool = False
    forced_value: float | bool | int | str = 0.0

    def reset(self):
        """Reset runtime value to default. Preserves `connected` flag
        since that reflects graph topology (set by add_wire), not runtime state.
        Also preserves force state — releasing a force is an explicit user action."""
        self.value = self.default_value
        self.status = Quality.GOOD
        self.limit = LimitStatus.NOT_LIMITED
