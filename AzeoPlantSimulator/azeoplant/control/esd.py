# -*- coding: utf-8 -*-
"""The ESD cause and effect matrix, implemented as the DCS-side SIS modules.

The plant model (u800_u900) deliberately supplies only the initiators and
obeys the group trip commands ``XY-9001`` to ``XY-9005``; the matrix itself
belongs to the safety system, which on this branch is this module. The 24
causes of the schedule map onto the four shutdown modules of the
Control_Modules sheet:

* ``ESD-9000`` - level 0, total plant shutdown (``XY-9001``)
* ``ESD-9200`` - reaction section, U200 / U300 / U400 (``XY-9002``)
* ``ESD-9500`` - fractionation section, U500 / U600 (``XY-9003``)
* ``ESD-9700`` - boiler (``XY-9004``)

plus reactor depressuring on ``XY-9005``. Effects finer than the model's
group interface (a heater-only fuel trip, say) land on the smallest group
that contains them, which errs on the safe side.

Causes latch, the first cause to arrive is held as the first-out, and the
lamp output ``XY-9006`` goes healthy again only when a field reset arrives
with every cause clear.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from ..core.tags import TagDatabase

log = logging.getLogger(__name__)

# cause id, initiator tag, description, groups tripped.
# Groups: total / reaction / fractionation / boiler / depressure.
CAUSES: List[Tuple[str, str, str, Tuple[str, ...]]] = [
    ("C01", "HS-9002", "Control room ESD pushbutton (level 0)",
     ("total",)),
    ("C02", "HS-9001", "Field ESD pushbutton (level 1)",
     ("reaction", "fractionation", "boiler")),
    ("C03", "GD-9001", "Gas detected reactor area",
     ("reaction", "depressure")),
    ("C04", "GD-9002", "Gas detected compressor house",
     ("reaction", "depressure")),
    ("C05", "FD-9001", "Flame detected zone 1",
     ("total", "depressure")),
    ("C06", "PSLL-0101", "Fuel gas header pressure low low",
     ("reaction", "boiler")),
    ("C07", "LSLL-1001", "D1 feed surge drum level low low",
     ("reaction",)),
    ("C08", "VSHH-2001", "C1 vibration high high",
     ("reaction",)),
    ("C09", "LSHH-2001", "V-201 knockout drum level high high",
     ("reaction",)),
    ("C10", "PSL-2001", "C1 lube oil pressure low",
     ("reaction",)),
    ("C11", "PSLL-3001", "H1 fuel gas pressure low low",
     ("reaction",)),
    ("C12", "BS-3001", "H1 main flame failure",
     ("reaction",)),
    ("C13", "TT-3005", "H1 tube skin temperature high high",
     ("reaction",)),
    ("C14", "TSHH-4001", "R1 catalyst bed temperature high high",
     ("reaction", "depressure")),
    ("C15", "PSHH-4001", "R1 pressure high high",
     ("reaction", "depressure")),
    ("C16", "LSHH-4001", "D3 separator level high high",
     ("reaction",)),
    ("C17", "PSHH-5001", "T1 overhead pressure high high",
     ("fractionation",)),
    ("C18", "LSLL-5001", "V-501 reflux drum level low low",
     ("fractionation",)),
    ("C19", "LSLL-5002", "T1 column bottom level low low",
     ("fractionation",)),
    ("C20", "LSLL-6001", "V-601 reflux drum level low low",
     ("fractionation",)),
    ("C21", "LSLL-6002", "T2 column bottom level low low",
     ("fractionation",)),
    ("C22", "LSLL-7001", "B1 steam drum level low low",
     ("boiler",)),
    ("C23", "PSHH-7001", "B1 steam drum pressure high high",
     ("boiler",)),
    ("C24", "BS-7001", "B1 flame failure",
     ("boiler",)),
]

# The design basis behind each cause: its initiators, the vote, the trip
# setting as the plant model computes it, the final elements the trip
# drives, and what protects the plant if the trip does not act. Read by
# the SIS window's second tab and quoted on the help pages; the numbers
# are the models' own (the switch thresholds in azeoplant/models).
_REACTION = "XV-2001 closes, C1 trips; XV-3001 closes, H1 burners out; XV-4001 and XV-4002 close"
_FRACT = "XV-5001 and XV-6001 close (column feeds)"
_BOILER = "XV-7001 closes, B1 burner out, FD fan permissive lost"
_TOTAL = "XV-1001 closes (feed isolated), and every section"
_DEPRESS = "BDV-4001 de-energised, fails open to flare"
VOTING = [
    {"cause": "C01", "sensors": ("HS-9002",), "vote": "1oo1", "setting": "pushbutton", "elements": _TOTAL, "backup": "Field pushbutton C02; the operator's own actions at the console"},
    {"cause": "C02", "sensors": ("HS-9001",), "vote": "1oo1", "setting": "pushbutton", "elements": "Reaction, fractionation and boiler groups", "backup": "Control room pushbutton C01"},
    {"cause": "C03", "sensors": ("GD-9001",), "vote": "1oo1", "setting": "20 % LEL, reactor area", "elements": _REACTION + "; " + _DEPRESS, "backup": "Flame detector C05 (total trip); fire water"},
    {"cause": "C04", "sensors": ("GD-9002",), "vote": "1oo1", "setting": "20 % LEL, compressor house", "elements": _REACTION + "; " + _DEPRESS, "backup": "Flame detector C05 (total trip)"},
    {"cause": "C05", "sensors": ("FD-9001",), "vote": "1oo1", "setting": "flame detected, zone 1", "elements": _TOTAL + "; " + _DEPRESS, "backup": "Fire water, relief and flare"},
    {"cause": "C06", "sensors": ("PSLL-0101",), "vote": "1oo1", "setting": "header < 5.0 barg", "elements": _REACTION + "; " + _BOILER, "backup": "Burner flame failures C12 and C24 as the header starves"},
    {"cause": "C07", "sensors": ("LSLL-1001",), "vote": "1oo1", "setting": "D1 level < 8 %", "elements": _REACTION, "backup": "FIC-1002 minimum-flow protection; P-101 cavitation alarms"},
    {"cause": "C08", "sensors": ("VSHH-2001",), "vote": "1oo1", "setting": "vibration > 110 micron", "elements": _REACTION, "backup": "VT-2001 HI alarm; anti-surge controller"},
    {"cause": "C09", "sensors": ("LSHH-2001",), "vote": "1oo1", "setting": "V-201 level > 85 %", "elements": _REACTION, "backup": "LIC-2001 drain; LT-2001 HI alarm"},
    {"cause": "C10", "sensors": ("PSL-2001",), "vote": "1oo1", "setting": "lube oil < 1.8 barg", "elements": _REACTION, "backup": "Auxiliary lube pump auto-start"},
    {"cause": "C11", "sensors": ("PSLL-3001",), "vote": "1oo1", "setting": "burner pressure < 1.5 barg", "elements": _REACTION, "backup": "PIC-3002 supply cutback; flame failure C12"},
    {"cause": "C12", "sensors": ("BS-3001",), "vote": "1oo1", "setting": "flame not present", "elements": _REACTION, "backup": "None: unburnt fuel to a hot box is what this prevents"},
    {"cause": "C13", "sensors": ("TT-3005",), "vote": "1oo1", "setting": "tube skin >= 640 degC", "elements": _REACTION, "backup": "TIC-3004 skin constraint at 560 degC low-selects fuel"},
    {"cause": "C14", "sensors": ("TSHH-4001A", "TSHH-4001B"), "vote": "1oo2", "setting": "either bed > 470 degC", "elements": _REACTION + "; " + _DEPRESS, "backup": "TIC-4001 quench; TT-4002 HI HI alarm"},
    {"cause": "C15", "sensors": ("PSHH-4001",), "vote": "1oo1", "setting": "R1 > 54 barg", "elements": _REACTION + "; " + _DEPRESS, "backup": "PCV-4001 vent; relief"},
    {"cause": "C16", "sensors": ("LSHH-4001",), "vote": "1oo1", "setting": "D3 level > 88 %", "elements": _REACTION, "backup": "LIC-4001; LT-4001 HI HI alarm"},
    {"cause": "C17", "sensors": ("PSHH-5001",), "vote": "1oo1", "setting": "T1 > 12.5 barg", "elements": _FRACT, "backup": "PIC-5001 split range vent; PCV-5001 relief"},
    {"cause": "C18", "sensors": ("LSLL-5001",), "vote": "1oo1", "setting": "V-501 level < 10 %", "elements": _FRACT, "backup": "Reflux cut back below 30 % drum level (FIC-5001 selector)"},
    {"cause": "C19", "sensors": ("LSLL-5002",), "vote": "1oo1", "setting": "T1 sump < 10 %", "elements": _FRACT, "backup": "Reboiler low-level override on FIC-5002"},
    {"cause": "C20", "sensors": ("LSLL-6001",), "vote": "1oo1", "setting": "V-601 level < 10 %", "elements": _FRACT, "backup": "Reflux cut back below 30 % drum level (FIC-6001 selector)"},
    {"cause": "C21", "sensors": ("LSLL-6002",), "vote": "1oo1", "setting": "T2 sump < 10 %", "elements": _FRACT, "backup": "Reboiler low-level override on FIC-6002"},
    {"cause": "C22", "sensors": ("LSLL-7001",), "vote": "1oo1", "setting": "drum level < -200 mm", "elements": _BOILER, "backup": "LIC-7001 three-element; LT-7001 LO LO alarm"},
    {"cause": "C23", "sensors": ("PSHH-7001",), "vote": "1oo1", "setting": "drum > 52 barg", "elements": _BOILER, "backup": "The 30 t/h reboiler steam cap; PCV-7001 letdown; relief"},
    {"cause": "C24", "sensors": ("BS-7001",), "vote": "1oo1", "setting": "flame not present", "elements": _BOILER, "backup": "None: unburnt fuel to a hot furnace is what this prevents"},
]

# TT-3005 is the one analogue initiator: its trip point is the schedule's
# HI HI alarm on tube skin.
_SKIN_TRIP = 640.0

# Burner switches read flame PRESENT when healthy: the cause is the flame
# going away, so these two activate on False.
_HEALTHY_TRUE = {"BS-3001", "BS-7001"}

# Every cause holds for this long before latching, so a restore transient
# or a single noisy scan cannot shut the plant down.
_CONFIRM_S = 2.0

GROUP_OUT = {"total": "XY-9001", "reaction": "XY-9002",
             "fractionation": "XY-9003", "boiler": "XY-9004",
             "depressure": "XY-9005"}


#: k-out-of-n voting for the causes with more than one initiator: cause
#: id -> (the initiator tags, how many of them must be tripped). A cause
#: not listed here votes its one initiator from CAUSES, 1oo1. The
#: reactor bed temperature is 1oo2 across the beds: either bed running
#: away is enough. A 2oo3 cause would be listed the same way with 2.
VOTES: Dict[str, Tuple[Tuple[str, ...], int]] = {
    "C14": (("TSHH-4001A", "TSHH-4001B"), 1),
}


class EsdLogic:
    """Latched cause evaluation, group trip outputs, first-out record."""

    def __init__(self, db: TagDatabase, bus=None) -> None:
        self.db = db
        self.bus = bus
        self.latched: Dict[str, bool] = {c[0]: False for c in CAUSES}
        self.first_out: Optional[str] = None
        self._timers: Dict[str, float] = {c[0]: 0.0 for c in CAUSES}

    # ------------------------------------------------------------- scanning
    def _cause_active(self, tag: str) -> bool:
        t = self.db.get(tag)
        if t is None:
            return False
        if tag == "TT-3005":
            return float(t.value) >= _SKIN_TRIP
        if tag in _HEALTHY_TRUE:
            return not bool(t.value)
        return bool(t.value)

    def _cause_tripped(self, cid: str, tag: str) -> bool:
        """The cause after its vote: k of its n initiators tripped."""
        vote = VOTES.get(cid)
        if vote is None:
            return self._cause_active(tag)
        sensors, k = vote
        if any(s not in self.db for s in sensors):
            # a database without the voted sensors (an older core) falls
            # back to the cause's own initiator rather than never tripping
            return self._cause_active(tag)
        return sum(1 for s in sensors if self._cause_active(s)) >= k

    def step(self, dt: float = 0.1) -> None:
        db = self.db
        for cid, tag, desc, _groups in CAUSES:
            if self._cause_tripped(cid, tag):
                self._timers[cid] += dt
            else:
                self._timers[cid] = 0.0
            if self._timers[cid] >= _CONFIRM_S and not self.latched[cid]:
                self.latched[cid] = True
                if self.first_out is None:
                    self.first_out = cid
                    log.warning("ESD first out: %s %s (%s)", cid, desc, tag)

        # A field reset clears every latch whose cause has gone away; the
        # first-out survives until everything is clear, the way a first-out
        # display holds its lamp through the restart discussion.
        if "XS-9002" in db and bool(db["XS-9002"].value):
            for cid, tag, _d, _g in CAUSES:
                if self.latched[cid] and not self._cause_tripped(cid, tag):
                    self.latched[cid] = False
            if not any(self.latched.values()):
                self.first_out = None

        tripped = {g: False for g in GROUP_OUT}
        for cid, _tag, _d, groups in CAUSES:
            if self.latched[cid]:
                for g in groups:
                    tripped[g] = True
        # A total trip implies every section trip.
        if tripped["total"]:
            for g in ("reaction", "fractionation", "boiler"):
                tripped[g] = True

        def write(tag: str, value: bool) -> None:
            if tag not in db:
                return
            if self.bus is not None:
                self.bus.write_from_dcs(tag, value, source="sis")
            else:
                db[tag].value = value

        for g, out in GROUP_OUT.items():
            write(out, tripped[g])
        write("XY-9006", not any(self.latched.values()))

    # --------------------------------------------------------- persistence
    def capture_state(self) -> dict:
        return {"latched": dict(self.latched), "first_out": self.first_out}

    def apply_state(self, s: dict) -> None:
        for cid, v in (s or {}).get("latched", {}).items():
            if cid in self.latched:
                self.latched[cid] = bool(v)
        self.first_out = (s or {}).get("first_out")
