#!/usr/bin/env python3
"""Generate the control module schedule and stability narrative.

Walks the live ControlSystem and writes ``docs/Control_Modules.md``: every
module with its wiring and tuning, followed by the narrative of what
actually makes this plant stable - the parts an external DCS engineer has
to reproduce to close the loops against the openloop branch.

    python tools/make_control_narrative.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from azeoplant.models.flowsheet import Flowsheet, TagDatabase  # noqa: E402
from azeoplant.control.strategy import ControlSystem            # noqa: E402

NARRATIVE = """
## What makes this plant stable

The module table above is necessary but not sufficient: the schemes below
are what turned the same tuning from a runaway into an hour-long steady
hold with a 20 percent feed cut rejected. An external DCS closing these
loops must reproduce them.

### Cascades, external reset and feedforward

Every master-slave pair runs external reset: the slave passes its PV as
BKCAL and the master integrates against that, so a railed valve stops the
master's reset without special cases. Feedforward must be taken OUT of the
reset feedback before it is integrated - the achieved value already carries
the FF contribution, and integrating it again ramps the output by the full
FF term once per reset time. This one detail is the difference between the
heater feedforward rejecting a feed cut and the heater running away.

### Fired equipment

H1 and B1 both run fuel-air cross limits computed every scan: air demand is
the HIGHER of firing demand and measured fuel, fuel demand the LOWER of
what is asked and what measured air can burn at the trimmed excess. The
oxygen trim adjusts the excess-air target (1.05 to 1.25), never a damper
directly. Firing is also cut back as fuel supply pressure sags (below
2.0 barg at H1, 3.0 barg at B1), and low-selected under the tube-skin
override at H1 and the drum-pressure margin at B1: the reboiler steam
setpoints are capped at 16.5 t/h so a quality master winding against a
separation limit cannot walk the boiler drum into PSHH-7001.

### Columns

Both towers run dual composition in the LV configuration: drum level takes
the distillate, the analysers trim pressure-compensated tray and bottoms
temperature targets (7.5 degC per bar on the working setpoint), and the
temperature masters cascade to reflux and steam flow slaves with a feed
feedforward (deliberately undercompensated at 0.6) and a small static
decoupler (0.12). Three protections matter:

* **Analyser steps.** The composition analysers update discontinuously.
  The quality masters are near-integral-only, and the temperature targets
  are rate limited (0.15 degC/s) and clamped to a band around the adopted
  operating point, so an analyser step becomes a bounded excursion instead
  of a fifty-degree slam through the cascade.
* **Drum inventory.** Reflux demand is cut back with drum level below 30
  percent: reflux may not outrun condensate, or an upset drains the drum
  and loses reflux entirely.
* **Flooding.** PDIC low-selects steam away on column differential
  pressure before the trays flood.

Sump levels are averaging (low gain, long reset): the T2 bottoms is the
recycle back to D1, and a tight sump loop is exactly how the snowball gets
pumped around the plant instead of riding out in the vessel.

### Overrides

Constraint controllers ride the released side of their selectors (100 for
low-selects, 0 for high-selects) and keep their engineering setpoints
across a restore - seeding an override's SP from its PV arms every
constraint at whatever the plant happened to be doing.

### Adoption of a running plant

Closing loops on a live plant is bumpless only if the strategy adopts the
as-found state: SP = PV on masterless loops, master outputs initialised to
reproduce the slave's PV, O2 trims seeded at the as-found excess-air
ratio, condenser cooling water given 35 percent headroom over the
as-found duty, and split-range masters seeded from whichever valve is off
its rest position. Operators then walk setpoints to targets at their own
pace.

### Scan rate

Modules execute on a 200 ms period against the simulator's 100 ms
integration step - the Azeo-typical rate, and the plant is verified
stable at it.

### Safety layer

The 24-cause ESD matrix (see ``azeoplant/control/esd.py``) latches with a
2 s confirmation time, records the first-out, and refuses reset while any
cause stands. The plant model applies the group trips; the matrix itself
is DCS scope.
"""


def main() -> int:
    db = TagDatabase()
    Flowsheet(db)
    cs = ControlSystem(db)

    role = {}
    for m in cs._constraints:
        role[m] = "override, parked at %g" % cs._constraints[m]
    for m in cs._pv_select:
        role[m] = (role.get(m, "") + "; " if m in role else "") + \
            "PV = high select of " + " / ".join(cs._pv_select[m])
    for m in cs._pct_cas:
        role[m] = "pressure-compensated SP on " + cs._pct_cas[m][0]

    out = ROOT / "docs" / "Control_Modules.md"
    with open(out, "w", encoding="utf-8") as f:
        f.write("# AzeoPlant control modules\n\n")
        f.write("Generated from the closeloop strategy by "
                "`tools/make_control_narrative.py`. Gains are normalised "
                "percent-of-output per percent-of-PV-span; reset in "
                "seconds.\n\n")
        f.write(f"**{len(cs.loops)} modules.**\n\n")
        f.write("| Module | PV | Output | Acting | Mode | Master | Gain |"
                " Reset | Role |\n|---|---|---|---|---|---|---|---|---|\n")
        for m, lp in sorted(cs.loops.items(),
                            key=lambda kv: (db[kv[1].pv_tag].unit, kv[0])):
            pid = lp.pid
            outdesc = (lp.out_tag or
                       ("split: " + "/".join(s[0] for s in lp.split)
                        if lp.split else "cascade SP"))
            f.write(f"| {m} | {lp.pv_tag} | {outdesc} |"
                    f" {'direct' if pid.direct_acting else 'reverse'} |"
                    f" {pid.target_mode.value} | {lp.master or '-'} |"
                    f" {pid.gain:.2f} | {pid.reset:g} |"
                    f" {role.get(m, lp.description)} |\n")
        f.write(NARRATIVE)
    print(f"{len(cs.loops)} modules -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
