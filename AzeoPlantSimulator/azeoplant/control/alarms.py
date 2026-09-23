# -*- coding: utf-8 -*-
"""The plant-wide alarm system: one scanner, ISA-18.2 states, a journal.

Everything annunciated comes from the configured schedule in
:mod:`azeoplant.control.alarm_table` (the Alarms sheet of the design
workbook), scanned here against the live tag database - independent of which
faceplates happen to be open. Each configured point runs the ISA-18.2 cycle:

    NORMAL --condition + on-delay--> UNACK  --ack--> ACKED --clear--> NORMAL
                                       \\--clear--> RTN_UNACK --ack--> NORMAL

with the sheet's deadband as clearing hysteresis and its delay as an
on-delay in simulation time. A shelved alarm keeps being evaluated but is
withheld from annunciation until the shelf expires. Every transition is
written to the :class:`~azeoplant.core.events.EventJournal`.

The SIS is annunciated too: each of the 24 trip causes appears as a
CRITICAL alarm while its latch stands, with the first-out cause marked.
Acknowledging is annunciation bookkeeping only - clearing the latch still
takes the field reset, exactly as before.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..core.tags import Quality, TagDatabase
from .alarm_table import ALARMS

log = logging.getLogger(__name__)

# states
NORMAL, UNACK, ACKED, RTN_UNACK = "NORMAL", "UNACK", "ACKED", "RTN_UNACK"

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "ADVISORY": 2}
_PRI_MAP = {"Critical": "CRITICAL", "High": "HIGH", "Advisory": "ADVISORY"}


@dataclass
class AlarmPoint:
    """One configured alarm: a tag, a type and its annunciation state."""

    tag: str
    atype: str                     # HI_HI, HI, LO, LO_LO, DEV, DISCRETE,
    #                                PV_BAD, TRIP
    setpoint: float
    priority: str                  # CRITICAL / HIGH / ADVISORY
    deadband: float
    delay_s: float
    desc: str = ""
    eu: str = ""
    unit: str = ""
    condition: Optional[Callable[[], bool]] = None
    # ------------------------------------------------------- live state
    active: bool = False
    acked: bool = True
    enabled: bool = True           # False = removed from service
    since: float = 0.0             # wall time of the last activation
    shelved_until: float = 0.0
    first_out: bool = False        # SIS causes only
    value_text: str = ""           # value at annunciation, for the summary
    _timer: float = field(default=0.0, repr=False)   # on-delay accumulator
    # ---- inlined evaluation (the scan runs inside the engine step, so the
    # common threshold types are evaluated without a closure call)
    _tag_ref: object = field(default=None, repr=False)
    _op: str = field(default="", repr=False)     # hi / lo / disc / bad / ""
    _disc: bool = field(default=False, repr=False)

    @property
    def key(self) -> str:
        return f"{self.tag}.{self.atype}"

    @property
    def state(self) -> str:
        if self.active:
            return ACKED if self.acked else UNACK
        return NORMAL if self.acked else RTN_UNACK

    @property
    def shelved(self) -> bool:
        return time.time() < self.shelved_until

    @property
    def standing(self) -> bool:
        """Belongs on the alarm summary: in alarm, or cleared but unacked."""
        return self.active or not self.acked

    def sort_key(self) -> tuple:
        return (PRIORITY_ORDER.get(self.priority, 3), -self.since)


class AlarmSystem:
    """Scan the schedule, keep the states, feed the journal."""

    SCAN_PERIOD = 0.2

    def __init__(self, db: TagDatabase, journal=None, controller=None) -> None:
        self.db = db
        self.journal = journal
        self.controller = controller
        self.points: List[AlarmPoint] = []
        self._esd_cause_of: Dict[str, str] = {}
        self._accum = 0.0
        self._annunciations = 0    # unsilenced horn-worthy activations
        self.horn_priorities = {"CRITICAL", "HIGH"}
        self._build()
        log.info("Alarm system scanning %d configured points", len(self.points))

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        loops_by_pv = {}
        if self.controller is not None:
            loops_by_pv = {l.pv_tag: l for l in self.controller.loops.values()}

        bad_tags = set()
        for tag_name, entries in ALARMS.items():
            t = self.db.get(tag_name)
            if t is None:
                log.debug("Alarm schedule names %s, not in the database; "
                          "skipped", tag_name)
                continue
            for atype, sp, pri, dead, delay in entries:
                p = AlarmPoint(tag=tag_name, atype=atype, setpoint=float(sp),
                               priority=_PRI_MAP.get(pri, "HIGH"),
                               deadband=float(dead), delay_s=float(delay),
                               desc=t.desc, eu=t.eu, unit=t.unit)
                cond = self._condition_for(p, t, loops_by_pv)
                if cond is None:
                    continue
                if callable(cond):
                    p.condition = cond
                self.points.append(p)
            if t.kind.analogue:
                bad_tags.add(tag_name)

        # a failed transmitter on a scheduled point is itself an alarm
        for tag_name in sorted(bad_tags):
            t = self.db[tag_name]
            p = AlarmPoint(tag=tag_name, atype="PV_BAD", setpoint=0.0,
                           priority="HIGH", deadband=0.0, delay_s=2.0,
                           desc=t.desc, eu=t.eu, unit=t.unit)
            p._tag_ref, p._op = t, "bad"
            self.points.append(p)

        # the SIS latches, one alarm per cause
        esd = getattr(self.controller, "esd", None)
        if esd is not None:
            from .esd import CAUSES
            for cid, init_tag, desc, _groups in CAUSES:
                p = AlarmPoint(tag=init_tag, atype="TRIP", setpoint=1.0,
                               priority="CRITICAL", deadband=0.0, delay_s=0.0,
                               desc=f"SIS {cid}: {desc}", unit="U900")
                p.condition = (lambda e=esd, c=cid: bool(e.latched.get(c)))
                self.points.append(p)
                self._esd_cause_of[p.key] = cid

    def _condition_for(self, p: AlarmPoint, t, loops_by_pv: Dict):
        """Set up evaluation for one point.

        Threshold, discrete and quality conditions are marked for inline
        evaluation in :meth:`step` (returns ``"inline"``); DEV needs its
        loop's setpoint and mode and comes back as a closure. ``None``
        drops the point.
        """
        sp, dead = p.setpoint, p.deadband
        if p.atype in ("HI", "HI_HI"):
            p._tag_ref, p._op = t, "hi"
            return "inline"
        if p.atype in ("LO", "LO_LO"):
            p._tag_ref, p._op = t, "lo"
            return "inline"
        if p.atype == "DISCRETE":
            p._tag_ref, p._op, p._disc = t, "disc", bool(sp)
            return "inline"
        if p.atype == "DEV":
            loop = loops_by_pv.get(p.tag)
            if loop is None:
                log.debug("DEV alarm on %s has no loop; skipped", p.tag)
                return None
            from .pid import Mode
            pid = loop.pid

            def dev() -> bool:
                if (self.controller is None or not self.controller.enabled
                        or pid.actual_mode is Mode.MAN):
                    return False
                e = abs(float(t.value) - float(pid.sp))
                return e >= sp if not p.active else e > sp - dead
            return dev
        log.debug("Unknown alarm type %s on %s; skipped", p.atype, p.tag)
        return None

    # ------------------------------------------------------------------- scan
    def step(self, dt: float) -> None:
        self._accum += dt
        if self._accum < self.SCAN_PERIOD:
            return
        scan_dt, self._accum = self._accum, 0.0

        esd = getattr(self.controller, "esd", None)
        first_out = getattr(esd, "first_out", None)
        for p in self.points:
            if not p.enabled:
                if p.active:
                    self._clear(p)
                    p.acked = True
                p._timer = 0.0
                continue
            try:
                op = p._op
                if op == "hi":
                    v = p._tag_ref.value
                    raised = (v > p.setpoint - p.deadband if p.active
                              else v >= p.setpoint)
                elif op == "lo":
                    v = p._tag_ref.value
                    raised = (v < p.setpoint + p.deadband if p.active
                              else v <= p.setpoint)
                elif op == "disc":
                    raised = bool(p._tag_ref.value) == p._disc
                elif op == "bad":
                    raised = p._tag_ref.quality is Quality.BAD
                else:
                    raised = bool(p.condition())
            except Exception:
                log.exception("Alarm condition %s failed; point disabled",
                              p.key)
                p._op = ""
                p.condition = lambda: False
                continue
            if p.atype == "TRIP":
                p.first_out = (first_out is not None and
                               self._esd_cause_of.get(p.key) == first_out)
            if raised and not p.active:
                p._timer += scan_dt
                if p._timer >= p.delay_s:
                    self._activate(p)
            elif not raised:
                p._timer = 0.0
                if p.active:
                    self._clear(p)
            if p.shelved_until and not p.shelved and p.shelved_until > 0:
                p.shelved_until = 0.0
                self._journal("UNSHELVE", p, "shelf expired", source="system")

    def _activate(self, p: AlarmPoint) -> None:
        p.active = True
        p.acked = False
        p.since = time.time()
        t = self.db.get(p.tag)
        p.value_text = t.format() if t is not None else ""
        self._journal("TRIP" if p.atype == "TRIP" else "ALARM", p,
                      f"{p.atype} @ {p.value_text}")
        if not p.shelved and p.priority in self.horn_priorities:
            self._annunciations += 1

    def _clear(self, p: AlarmPoint) -> None:
        p.active = False
        p._timer = 0.0
        self._journal("TRIP_RESET" if p.atype == "TRIP" else "RTN", p,
                      "returned to normal")

    # ------------------------------------------------------------ annunciation
    def take_annunciations(self) -> int:
        """New audible-worthy activations since the last call; resets."""
        n, self._annunciations = self._annunciations, 0
        return n

    def silence(self) -> None:
        self._annunciations = 0

    # ------------------------------------------------------------------- lists
    def standing(self, include_shelved: bool = False) -> List[AlarmPoint]:
        """Summary rows: active or awaiting ack, worst first."""
        rows = [p for p in self.points
                if p.standing and (include_shelved or not p.shelved)]
        return sorted(rows, key=AlarmPoint.sort_key)

    def shelved_points(self) -> List[AlarmPoint]:
        return sorted((p for p in self.points if p.shelved),
                      key=AlarmPoint.sort_key)

    def banner(self, n: int = 5) -> List[AlarmPoint]:
        """The n most important unacknowledged alarms."""
        rows = [p for p in self.points if not p.acked and not p.shelved]
        return sorted(rows, key=AlarmPoint.sort_key)[:n]

    def unacked_count(self) -> int:
        return sum(1 for p in self.points if not p.acked and not p.shelved)

    def counts(self) -> Dict[str, int]:
        c = {"CRITICAL": 0, "HIGH": 0, "ADVISORY": 0}
        for p in self.points:
            if p.active and not p.shelved:
                c[p.priority] = c.get(p.priority, 0) + 1
        return c

    # --------------------------------------------------------------- operator
    def ack(self, p: AlarmPoint, source: str = "operator") -> None:
        if p.acked:
            return
        p.acked = True
        self._journal("ACK", p, "acknowledged", source=source)

    def ack_all(self, points: Optional[List[AlarmPoint]] = None,
                source: str = "operator") -> int:
        n = 0
        for p in (points if points is not None else self.points):
            if not p.acked:
                self.ack(p, source)
                n += 1
        return n

    def ack_tag(self, tag: str, source: str = "operator") -> int:
        return self.ack_all([p for p in self.points if p.tag == tag], source)

    def shelve(self, p: AlarmPoint, minutes: float,
               source: str = "operator") -> None:
        p.shelved_until = time.time() + minutes * 60.0
        self._journal("SHELVE", p, f"shelved for {minutes:g} min",
                      source=source)

    def unshelve(self, p: AlarmPoint, source: str = "operator") -> None:
        if p.shelved:
            p.shelved_until = 0.0
            self._journal("UNSHELVE", p, "unshelved", source=source)

    def remove_from_service(self, p: AlarmPoint,
                            source: str = "operator") -> None:
        if p.enabled:
            p.enabled = False
            self._journal("OOS", p, "removed from service", source=source)

    def return_to_service(self, p: AlarmPoint,
                          source: str = "operator") -> None:
        if not p.enabled:
            p.enabled = True
            self._journal("RTS", p, "returned to service", source=source)

    def oos_points(self) -> List[AlarmPoint]:
        return sorted((p for p in self.points if not p.enabled),
                      key=AlarmPoint.sort_key)

    # ---------------------------------------------------------------- journal
    def _journal(self, category: str, p: AlarmPoint, note: str,
                 source: str = "alarm") -> None:
        if self.journal is None:
            return
        first = "FIRST OUT - " if (p.first_out and category == "TRIP") else ""
        self.journal.log(category, f"{first}{p.desc}: {note}", source=source,
                         tag=p.tag, priority=p.priority, value=p.value_text)
