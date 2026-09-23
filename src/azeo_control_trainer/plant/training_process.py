"""The training process — drawings PID01 and PID02.

    PLANT_AREA_A                                    PLANT_AREA_B

    ┌────────┐                                      ┌────────┐
    │ T-101  │──MTR-102──▶ XV-101 ──▶ FV-102 ──FT-102──▶│ T-201  │──MTR-203──▶ LIC-201
    │ LI-101 │             (NC blk)    (FC ctrl)     │ LI-201 │              (FC ctrl)
    └────────┘                                       └────────┘

Interlocks on the drawing, both of which shut down MTR-102 and both of which
the shipped ``MTR-102`` module implements:

    01   LOW LEVEL S/D MTR-102        (T-101 below the CND2 limit)
    02   XV-101 CLOSED S/D MTR-102    (block valve not open)

The tags below are not invented. They are exactly the field points the shipped
modules are wired to — run ``TagDatabase.field_tags()`` and the two lists
match, which :meth:`Plant.verify_against` asserts rather than assumes.

What this plant deliberately does **not** write:

*   ``XV-101.PV_D`` — the ``XV-101`` module's own ``DO_PV_D`` drives it. It is
    the controller's opinion of valve state, which ``MTR-102`` then reads for
    interlock 02. The plant reports the *limit switch* (``XV-101.ZSO``); the
    controller decides what that means.
*   Operator commands — start, stop, reset, bypass, open, close. Those come
    from a person, through a faceplate or a field pushbutton.
"""
from __future__ import annotations

import logging
import random

from .components import ControlValve, FlowMeter, Motor, OnOffValve, Tank
from .process import Plant, registry

log = logging.getLogger("plant.training_process")


@registry.register
class AzeoTrainingPlant(Plant):
    """Two tanks, two pumps, a block valve and two control valves."""

    key = "training_process"
    display_name = "Azeo training process"
    description = ("Azeo two-area training process: T-101 feeds "
                   "T-201 through MTR-102, XV-101 and FV-102")
    area = "azeo_training"

    #: Design flows, chosen so the workshop moves at a watchable pace: T-101
    #: drains in a few minutes at full flow rather than a few seconds.
    MTR102_RATED_GPM = 120.0
    MTR203_RATED_GPM = 90.0
    #: T-101 has a makeup supply, or the exercise ends the first time the
    #: student runs the pump and never refills.
    T101_MAKEUP_GPM = 45.0

    def __init__(self, seed: int = 7009):
        self._rng = random.Random(seed)
        self.reset()

    # --------------------------------------------------------------- setup
    def reset(self) -> None:
        # Levels start where the shipped interlocks are satisfied: T-101 above
        # the CND2 low-level limit of 50, so a student can start the pump
        # without first having to work out why they cannot.
        self.t101 = Tank("T-101", capacity_gal=1000.0, level_gal=750.0)
        self.t201 = Tank("T-201", capacity_gal=1000.0, level_gal=300.0)

        self.mtr102 = Motor("MTR-102", start_delay_s=1.5, stop_delay_s=1.0)
        self.mtr203 = Motor("MTR-203", start_delay_s=1.5, stop_delay_s=1.0)

        self.xv101 = OnOffValve("XV-101", stroke_time_s=3.0, fail_closed=True)
        self.xv_option = OnOffValve("XV-OPTION", stroke_time_s=2.0,
                                    fail_closed=True)

        self.fv102 = ControlValve("FV-102", stroke_time_s=4.0)
        self.lic201_valve = ControlValve("LIC-201", stroke_time_s=4.0)

        self.ft102 = FlowMeter("FT-102", tau_s=1.2, noise=0.15, _rng=self._rng)

        # Upset state. Reset clears them: an exercise starts from a healthy
        # plant unless the scenario says otherwise.
        self._xv101_stuck = False
        self._li101_failed = False
        self.mtr102.fail_to_start = False
        self.T101_MAKEUP_GPM = type(self).T101_MAKEUP_GPM

        self.flow_a = 0.0        # T-101 → T-201, gpm
        self.flow_b = 0.0        # T-201 → downstream, gpm

    # ------------------------------------------------------- tag contract
    def reads(self) -> list[str]:
        """Controller outputs — contactors, solenoids and valve demands."""
        return [
            "MTR-102.do_start",
            "MTR-203.do_start",
            "XV-101.solenoid",
            "XV-OPTION.solenoid",
            "FV-102.OUT",
            "LIC-201.rsp",
        ]

    def writes(self) -> list[str]:
        """Measurements and feedbacks — what the instruments report."""
        return [
            "LI-101.PV",
            "LI-201.PV",
            "FT-102.PV",
            "MTR-102.run_fb",
            "MTR-203.run_fb",
            "XV-101.ZSO",
            "XV-OPTION.ZSO",
        ]

    def initial_values(self) -> dict:
        """Seed the field before the first scan.

        Includes the operator commands and the permissive at their rest
        state. The plant does not *drive* those afterwards — but leaving them
        absent means a `DI` reads a missing tag and publishes Bad, and a
        student's first sight of the module is a wall of bad quality that has
        nothing to do with the process.
        """
        seeded = self.step(0.0, {t: 0.0 for t in self.reads()})
        seeded.update({
            "MTR-102.cmd_start": False,
            "MTR-102.cmd_stop": False,
            "MTR-102.reset_cmd": False,
            "MTR-102.bypass_1": False,
            "MTR-102.bypass_2": False,
            "MTR-203.cmd_start": False,
            "MTR-203.cmd_stop": False,
            "MTR-203.reset_cmd": False,
            "XV-101.cmd_open": False,
            "XV-101.cmd_close": False,
            "XV-101.permit": True,      # the field permissive is made
            "XV-OPTION.cmd_flush": False,
        })
        return seeded

    # --------------------------------------------------------------- step
    def step(self, dt: float, inputs: dict) -> dict:
        """One pass of the process.

        Order matters: actuators move first, then flows are computed from
        where they ended up, then levels integrate the flows. Doing levels
        first would use last step's valve positions and put the measurement
        one step ahead of its cause.
        """
        # ── actuators ────────────────────────────────────────────────
        run_102 = self.mtr102.step(dt, bool(inputs.get("MTR-102.do_start")))
        run_203 = self.mtr203.step(dt, bool(inputs.get("MTR-203.do_start")))
        # A stuck valve ignores its solenoid: the controller keeps commanding
        # and the position never changes, which is what the operator has to
        # notice from commanded-versus-confirmed disagreeing.
        if not self._xv101_stuck:
            self.xv101.step(dt, bool(inputs.get("XV-101.solenoid")))
        self.xv_option.step(dt, bool(inputs.get("XV-OPTION.solenoid")))
        self.fv102.step(dt, _as_float(inputs.get("FV-102.OUT")))
        self.lic201_valve.step(dt, _as_float(inputs.get("LIC-201.rsp")))

        # ── flow: pump develops it, valves in series restrict it ─────
        # Two valves in series pass the product of their fractions. Crude
        # against a real hydraulic curve, and exactly right for teaching that
        # a shut block valve means no flow no matter what the controller does.
        demand_a = (self.MTR102_RATED_GPM * self.xv101.position
                    * self.fv102.fraction) if run_102 else 0.0
        demand_b = (self.MTR203_RATED_GPM * self.lic201_valve.fraction
                    if run_203 else 0.0)

        # ── levels ───────────────────────────────────────────────────
        # T-101's outflow is limited by what it can actually deliver, and that
        # same limited number is what reaches T-201.
        self.flow_a = self.t101.step(dt, self.T101_MAKEUP_GPM, demand_a)
        self.flow_b = self.t201.step(dt, self.flow_a, demand_b)

        measured = self.ft102.step(dt, self.flow_a)

        # A failed transmitter publishes nothing at all, so the bridge sees a
        # missing tag and the AI reports Bad - the same path a real dead
        # transmitter takes. Publishing a fake "bad" number would teach the
        # student to trust a number that is wrong.
        out = {
            "LI-101.PV": round(self.t101.level_gal, 3),
            "LI-201.PV": round(self.t201.level_gal, 3),
            "FT-102.PV": round(measured, 3),
            "MTR-102.run_fb": run_102,
            "MTR-203.run_fb": run_203,
            "XV-101.ZSO": self.xv101.open_limit,
            "XV-OPTION.ZSO": self.xv_option.open_limit,
        }
        if self._li101_failed:
            out.pop("LI-101.PV", None)
        return out

    # ------------------------------------------------------- exercises
    def set_fail_to_start(self, motor: str, failing: bool) -> bool:
        """Make a motor refuse to start — the fail-to-start exercise.

        The contactor still closes; the feedback never arrives, so the device
        block times out on ``START_TIMEOUT`` and the student has to read the
        faceplate to find out why.
        """
        target = {"MTR-102": self.mtr102, "MTR-203": self.mtr203}.get(motor)
        if target is None:
            return False
        target.fail_to_start = bool(failing)
        return True

    #: Upsets a scenario can inject. Each is a real process event with a real
    #: consequence, not a synthetic alarm: the alarm is what the *controller*
    #: makes of it, which is the only way an exercise trains anything.
    UPSETS = {
        "low-level": "T-101 drains toward the low-level interlock",
        "high-level": "T-201 fills toward its high alarm",
        "pump-fails": "MTR-102 will not start - contactor closes, no feedback",
        "valve-sticks": "XV-101 stops responding to its solenoid",
        "transmitter-fails": "LI-101 fails, so the level reads Bad",
    }

    def inject(self, upset: str, active: bool = True) -> bool:
        """Start or clear a process upset. Returns False for an unknown one."""
        if upset not in self.UPSETS:
            return False
        if upset == "low-level":
            self.T101_MAKEUP_GPM = 0.0 if active else 45.0
            if active:
                self.t101.level_gal = min(self.t101.level_gal, 120.0)
        elif upset == "high-level":
            if active:
                self.t201.level_gal = max(self.t201.level_gal, 880.0)
        elif upset == "pump-fails":
            self.mtr102.fail_to_start = active
        elif upset == "valve-sticks":
            self._xv101_stuck = active
        elif upset == "transmitter-fails":
            self._li101_failed = active
        log.info("Upset %s: %s", "raised" if active else "cleared", upset)
        return True

    def clear_upsets(self) -> None:
        for upset in self.UPSETS:
            self.inject(upset, False)

    def state(self) -> dict:
        """A snapshot for a test or a status panel to assert on."""
        return {
            "T-101_gal": round(self.t101.level_gal, 2),
            "T-201_gal": round(self.t201.level_gal, 2),
            "flow_gpm": round(self.flow_a, 2),
            "MTR-102": "running" if self.mtr102.running else "stopped",
            "MTR-203": "running" if self.mtr203.running else "stopped",
            "XV-101": f"{self.xv101.position * 100:.0f}%",
            "FV-102": f"{self.fv102.position:.1f}%",
        }


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
