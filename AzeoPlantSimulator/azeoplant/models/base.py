"""Process unit base class.

A unit owns its equipment, its tags and its malfunctions. It reads and writes the
flowsheet ``bus`` for anything that crosses a battery limit, which keeps units
independent enough to test one at a time.

Open loop
---------
No unit contains a controller. Every ``AO`` and ``DO`` tag is an input the unit
obeys, and every ``AI`` and ``DI`` tag is an output it produces. All regulatory
control lives in the DCS. That is what makes this an open loop simulator, and it
is why the models must be well behaved at any operating point: there is no
integral action here to hide a discontinuity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from ..core.tags import Tag, TagDatabase
from .accountability import BalanceReading, discover_parameters

log = logging.getLogger(__name__)


@dataclass
class Malfunction:
    """One instructor-injectable fault."""

    mf_id: str
    target: str
    description: str
    category: str
    param_label: str = ""
    param_min: float = 0.0
    param_max: float = 100.0
    apply: Callable[[bool, float], None] = lambda active, value: None
    active: bool = False
    value: float = 0.0

    def set(self, active: bool, value: float | None = None) -> None:
        self.active = bool(active)
        if value is not None:
            self.value = float(value)
        try:
            self.apply(self.active, self.value)
        except Exception:
            log.exception("Malfunction %s failed to apply", self.mf_id)


class ProcessUnit:
    """Base class for every process unit."""

    code: str = "U000"
    name: str = "Unnamed unit"

    def __init__(self, db: TagDatabase, bus: Dict[str, float],
                 dt: float = 0.1) -> None:
        self.db = db
        self.bus = bus
        self.dt = float(dt)      # engine step, needed to size DeadTime buffers
        self.malfunctions: List[Malfunction] = []
        self.tags: Dict[str, Tag] = {}
        self._balance_readings: Dict[str, BalanceReading] = {}
        self.build()

    # ------------------------------------------------------------- construction
    def build(self) -> None:
        """Create tags and equipment. Subclasses must override."""
        raise NotImplementedError

    def ai(self, name, desc, eu, lo, hi, value=None) -> Tag:
        t = self.db.analog(name, "AI", self.code, desc, eu, lo, hi, value)
        self.tags[name] = t
        return t

    def ao(self, name, desc, eu="%", lo=0.0, hi=100.0, value=0.0) -> Tag:
        t = self.db.analog(name, "AO", self.code, desc, eu, lo, hi, value)
        self.tags[name] = t
        return t

    def di(self, name, desc, state0="Off", state1="On", value=False) -> Tag:
        t = self.db.discrete(name, "DI", self.code, desc, state0, state1, value)
        self.tags[name] = t
        return t

    def do(self, name, desc, state0="Idle", state1="Cmd", value=False) -> Tag:
        t = self.db.discrete(name, "DO", self.code, desc, state0, state1, value)
        self.tags[name] = t
        return t

    def add_malfunction(self, mf_id, target, description, category,
                        param_label="", param_min=0.0, param_max=100.0,
                        apply=None) -> Malfunction:
        mf = Malfunction(mf_id, target, description, category, param_label,
                         param_min, param_max, apply or (lambda a, v: None))
        self.malfunctions.append(mf)
        return mf

    # ------------------------------------------------------------------ running
    def step(self, dt: float) -> None:
        """Advance the unit by ``dt`` seconds. Subclasses must override."""
        raise NotImplementedError

    # --------------------------------------------------------------- persistence
    def save_state(self) -> Dict[str, float]:
        """Plain values not already captured by tags. Override as needed.

        Dynamic elements do not belong here. ``capture`` finds those itself.
        """
        return {}

    def load_state(self, state: Dict[str, float]) -> None:
        return None

    # ---------------------------------------------------------------- snapshots
    def capture(self) -> Dict[str, object]:
        """Everything needed to resume this unit exactly where it stopped.

        The dynamic elements are collected by walking the unit rather than from
        a hand written list. A list goes stale the moment somebody adds a lag
        and says nothing about it, which is how most of the state came to be
        missing from snapshots in the first place. Anything exposing
        ``capture_state`` is picked up automatically and filed under ``_dyn``,
        which cannot collide with the plain values a unit writes itself.
        """
        payload: Dict[str, object] = dict(self.save_state())
        dynamic: Dict[str, object] = {}
        for name, obj in vars(self).items():
            fn = getattr(obj, "capture_state", None)
            if callable(fn):
                try:
                    dynamic[name] = fn()
                except Exception:                      # never lose the snapshot
                    log.exception("Could not capture %s.%s", self.code, name)
        if dynamic:
            payload["_dyn"] = dynamic
        # An injected fault is part of the plant's condition. Dropping it would
        # quietly heal the very thing the exercise is about.
        active = {m.mf_id: m.value for m in self.malfunctions if m.active}
        if active:
            payload["_mf"] = active
        return payload

    def apply(self, state: Dict[str, object]) -> None:
        """Restore what ``capture`` wrote.

        The unit's own ``load_state`` runs *first*. It is the coarse pass, and
        several units reset a lag or an integrator inside it, which would
        otherwise throw away the exact value restored from ``_dyn`` and quietly
        lose things like an integrator's saturation flag. A version 1 snapshot
        carries no ``_dyn`` at all and so still behaves exactly as before.
        """
        self.load_state(state)
        for name, sub in (state.get("_dyn") or {}).items():
            fn = getattr(getattr(self, name, None), "apply_state", None)
            if callable(fn):
                try:
                    fn(sub)
                except Exception:
                    log.exception("Could not restore %s.%s", self.code, name)
        wanted = state.get("_mf") or {}
        for mf in self.malfunctions:
            if mf.mf_id in wanted:
                mf.set(True, float(wanted[mf.mf_id]))
            elif mf.active:
                mf.set(False, mf.value)

    # ------------------------------------------------------------------ helpers
    def clear_malfunctions(self) -> None:
        for mf in self.malfunctions:
            mf.set(False, 0.0)

    # ---------------------------------------------------------- accountability
    def record_balance(self, name: str, inflow: float, outflow: float,
                       accumulation: float, eu: str,
                       tolerance: float = 1e-6,
                       kind: str = "material") -> BalanceReading:
        """Publish a diagnostic conservation identity for the latest step."""
        incoming = float(inflow)
        outgoing = float(outflow)
        stored = float(accumulation)
        reading = BalanceReading(
            unit=self.code,
            name=name,
            inflow=incoming,
            outflow=outgoing,
            accumulation=stored,
            residual=incoming - outgoing - stored,
            tolerance=max(float(tolerance), 0.0),
            eu=eu,
            kind=kind,
        )
        self._balance_readings[name] = reading
        return reading

    def record_inventory_balance(
            self, name: str, inflow: float, outflow: float,
            before: float, after: float, capacity: float, dt: float,
            *, state_span: float = 100.0, time_base_s: float = 3600.0,
            eu: str = "m3/h", tolerance: float = 1e-5) -> BalanceReading:
        """Account for an inventory integrated from a normalized state.

        The helper is the inverse of the model equation::

            d(state)/dt = (in - out) / capacity * state_span / time_base

        A clamp at an inventory boundary therefore appears as a real residual
        instead of silently deleting or creating material.
        """
        dt = max(float(dt), 1e-12)
        accumulation = ((float(after) - float(before)) / dt
                        * float(capacity) * float(time_base_s)
                        / max(abs(float(state_span)), 1e-12))
        return self.record_balance(name, inflow, outflow, accumulation,
                                   eu, tolerance)

    def balance_readings(self) -> List[BalanceReading]:
        return list(self._balance_readings.values())

    def model_parameters(self):
        return discover_parameters(self)

    # ------------------------------------------------------------ instruments
    def transmitters(self) -> Dict[str, object]:
        """The unit's transmitters by attribute name, for the realism layer.

        Found by walking the unit, like the snapshot state: a transmitter
        added to a unit is on the settings page without being listed. The
        native units answer the same call from their state registry.
        """
        from ..core.devices import Transmitter
        return {n: o for n, o in vars(self).items() if isinstance(o, Transmitter)}

    def valves(self) -> Dict[str, object]:
        """The unit's control valves by attribute name, likewise."""
        from ..core.devices import ControlValve
        return {n: o for n, o in vars(self).items() if isinstance(o, ControlValve)}
