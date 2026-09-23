"""Device control blocks — DEVCTL (motor), VLVCTL (valve)."""
from __future__ import annotations
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality, LimitStatus


@register_block
class DevctlBlock(FunctionBlock):
    """Device/Motor control block (Honeywell DEVCTL/MOTCTL, Azeo DC §3694).

    Manages start/stop of a motor or discrete device with interlock logic,
    run feedback, and fail-to-start detection.

    States: STOPPED → STARTING → RUNNING → STOPPING → STOPPED

    **Precedence note.** For the pump/motor logic this block serves,
    ``doc/motor_pump_permissive_interlock_design.md`` is the canonical
    reference and wins wherever it and the Azeo manual disagree — the
    trainer's ``MOTOR_INTERLOCK`` feeds ``START_PERMIT`` into ``INTERLOCK``
    here, and that contract (a single active-high permit that both blocks
    a start and drops a running device) is preserved unchanged.  The
    Azeo parameters below are layered on top of it, every one of them
    neutral by default:

    * ``TRIP_TIME`` (0 = today) — how long an Active confirm may be lost
      before the device faults, instead of faulting on a single scan of
      contact bounce.
    * ``PERMISSIVE_D`` (True) / ``SHUTDOWN_D`` (False) — Azeo's start
      permissive and its non-optional shutdown input.
    * ``RESET_REQUIRED`` (False) + ``LOCKED`` — after a shutdown/interlock
      trip the device parks Locked and needs ``RESET`` before it may run
      again, instead of restarting the instant the interlock returns with
      ``START_CMD`` still asserted.
    * ``DELAY_TIME`` / ``RESTART_TIME`` (0 = today) — sequenced group
      starts and the Passive dwell between successive Active states.
    * ``FAIL`` / ``FAIL_ACTIVE`` — Azeo's failure code alongside the
      existing single ``FAULTED`` flag.
    * ``use_azeo_states`` (False) — routes an interlock/shutdown trip to
      a dedicated ``Shutdown/Interlocked`` state (and ``Locked``) instead
      of through STOPPING, where a stop timeout could land it in FAULTED.
    * ``CAS_IN_D`` — a wired cascade setpoint (0 = Passive, 1 = Active)
      drives the device in Cas mode; unconnected, START_CMD/STOP_CMD rule.
    * ``INTERLOCK_OPT`` (Always Use Value = today) — what to do when the
      *quality* of ``INTERLOCK`` / ``PERMISSIVE_D`` / ``SHUTDOWN_D`` is Bad.
      Driven per-signal from the terminal ``status`` the wire carries, not
      from a side-channel "status is bad" pin.

    **Two places the Azeo manual and the project doc disagree** (project
    doc wins, per its precedence over the manual for this block):

    1. *Fail-safe default.* Azeo's ``INTERLOCK_OPT`` default is
       *Always Use Value* — a Bad interlock reading is acted on as if it
       were trustworthy, which is fail-danger. The project doc's whole
       posture (§2.3, §31) is that a permissive/interlock that cannot be
       trusted is not a permit. The schema default here stays
       ``ALWAYS_USE_VALUE`` because defaults must not change behaviour,
       but trainer pump modules should configure ``PASSIVE_IF_BAD``,
       which is the project-doc-conforming choice.
    2. *Restart after a trip.* Azeo: a Locked state from a
       shutdown/interlock "resumes the previous Active state once the
       condition clears and ``RESET_D`` is set True". The project doc
       (§19.1, §31.4) is explicit: "Never allow automatic restart after a
       protective trip." This block therefore leaves ``RESET`` returning
       the device to STOPPED rather than to the former Active state; a
       maintained ``START_CMD`` restarting from there is the *trainer's*
       EM logic to latch, and ``MOTOR_INTERLOCK`` owns that contract.
    """
    block_type = "DEVCTL"
    category = BlockCategory.IO
    display_name = "Device Control"
    description = "Motor start/stop with interlocks, run feedback, fail detection"

    terminal_aliases = {"SP_D": "CAS_IN_D", "OUT_D": "DO_START",
                        "FV_D": "RUN_FB", "PV_D": "RUN_FB",
                        "INTERLOCK_D": "INTERLOCK", "RESET_D": "RESET",
                        "DC_STATE": "STATE"}

    # State constants
    _STOPPED = 0
    _STARTING = 1
    _RUNNING = 2
    _STOPPING = 3
    _FAULTED = 4
    # Azeo special states, used only when use_azeo_states is set
    _INTERLOCKED = 5      # DC_STATE "Shutdown/Interlocked"
    _LOCKED = 6           # DC_STATE "Locked"

    # FAIL codes (Azeo table, §3694 "Failure code")
    _FAIL_CLEAR = 0
    _FAIL_PASS_CFM_TIME = 1
    _FAIL_ACT1_CFM_TIME = 2
    _FAIL_ACT1_CFM_LOST = 5
    _FAIL_TRIPPED = 7
    _FAIL_SHUTDOWN = 8

    config_choices = {
        "mode": ("AUTO", "CAS"),
        "INTERLOCK_OPT": ("ALWAYS_USE_VALUE", "USE_LAST_GOOD", "PASSIVE_IF_BAD"),
        "DEVICE_KIND": ("MOTOR", "VALVE"),
    }
    config_units = {"START_TIMEOUT": "s", "STOP_TIMEOUT": "s", "TRIP_TIME": "s",
                    "DELAY_TIME": "s", "RESTART_TIME": "s"}

    #: Passive (safe) substitute per interlock-family input, used by
    #: INTERLOCK_OPT = PASSIVE_IF_BAD. Azeo: "0 for INTERLOCK_D /
    #: PERMISSIVE_D, 1 for SHUTDOWN_D" — i.e. no permit, shut down.
    _ILK_PASSIVE = {"INTERLOCK": False, "PERMISSIVE_D": False,
                    "SHUTDOWN_D": True}
    #: Neutral last-good seed, so USE_LAST_GOOD before any Good scan is
    #: still a permit rather than an accidental trip.
    _ILK_NEUTRAL = {"INTERLOCK": True, "PERMISSIVE_D": True,
                    "SHUTDOWN_D": False}

    def __init__(self, instance_name=""):
        self._state = 0
        self._timer = 0.0
        self._trip_t = 0.0      # time an Active confirm has been lost
        self._delay_t = 0.0     # DELAY_TIME accumulator on a start request
        self._passive_t = 0.0   # dwell in Passive, for RESTART_TIME
        self._locked = False    # Reset Required lock-out
        self._fail = 0
        # Last value seen on each interlock-family input while its status
        # was not Bad (INTERLOCK_OPT = USE_LAST_GOOD). Azeo notes this is
        # not preserved across a controller switchover — nor across a
        # block reset() here.
        self._ilk_last_good = dict(self._ILK_NEUTRAL)
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("START_CMD", DataType.BOOL, False, "Start command")
        self.add_input("STOP_CMD", DataType.BOOL, False, "Stop command")
        self.add_input("INTERLOCK", DataType.BOOL, True, "Interlock OK (True=permit)")
        self.add_input("PERMISSIVE_D", DataType.BOOL, True,
                       "Start permissive (True=permit); no effect once running")
        self.add_input("SHUTDOWN_D", DataType.BOOL, False,
                       "Shutdown request (True=force Passive)")
        self.add_input("CAS_IN_D", DataType.BOOL, False,
                       "Cascade setpoint (Cas mode): True=Active, False=Passive")
        self.add_input("RUN_FB", DataType.BOOL, False, "Run feedback from field")
        self.add_input("RESET", DataType.BOOL, False, "Reset fault / clear Locked")
        # Hold a transition rather than let it time out. **Level, not a
        # pulse**: a pause that expired on its own would release the device
        # at a moment nobody chose.
        #
        # Deliberately narrow. Pausing a *motor* is not a thing — a running
        # motor is stopped, not paused — so this acts only while the device
        # is part way through a confirmed change, which is the one place the
        # word has an unambiguous meaning: wait, do not fault.
        self.add_input("PAUSE_CMD", DataType.BOOL, False,
                       "Hold a start/stop transition (freezes its timer)")
        self.add_output("DO_START", DataType.BOOL, False, "Start output to field")
        self.add_output("PAUSED", DataType.BOOL, False,
                        "A transition is being held by PAUSE_CMD")
        self.add_output("RUNNING", DataType.BOOL, False, "Device running")
        self.add_output("STOPPED", DataType.BOOL, True, "Device stopped")
        self.add_output("FAULTED", DataType.BOOL, False, "Fault active")
        self.add_output("LOCKED", DataType.BOOL, False,
                        "Locked — reset required before the device may run")
        self.add_output("FAIL", DataType.INT, 0, "Azeo FAIL code (0 = clear)")
        self.add_output("FAIL_ACTIVE", DataType.BOOL, False, "FAIL != 0")
        self.add_output("MODE", DataType.STRING, "AUTO", "Actual mode (AUTO/CAS/LO)")
        self.add_output("STATE", DataType.INT, 0,
                        "State: 0=Stop 1=Starting 2=Run 3=Stopping 4=Fault "
                        "5=Interlocked 6=Locked")
        # The block already times every transition — it is what trips
        # FAIL_TO_START — but kept it to itself, so a faceplate could show
        # the configured limit and never how far through it the device was.
        # Azeo puts "Time Limit" and "Elapsed Time" side by side for
        # exactly this: a motor 8 s into a 10 s start is about to fail, and
        # that is the moment an operator can still do something about it.
        # **Added, never renamed** — an output is additive and no shipped
        # module references it.
        self.add_output("ELAPSED", DataType.FLOAT, 0.0,
                        "Seconds in the current state transition")

    def get_config_schema(self):
        return {
            # What the device *is*, which decides the faceplate's verbs:
            # START/STOP for a motor, OPEN/CLOSE for a valve. Declared, not
            # inferred — the operator surface reads it from the module
            # rather than guessing from the tag name. XV-101 ships with
            # VALVE; absent means MOTOR.
            "DEVICE_KIND": (str, "MOTOR",
                            "MOTOR (START/STOP) or VALVE (OPEN/CLOSE) — "
                            "the faceplate's command verbs"),
            "START_TIMEOUT": (float, 10.0, "Fail-to-start timeout [s]"),
            "STOP_TIMEOUT": (float, 10.0, "Fail-to-stop timeout [s]"),
            "TRIP_TIME": (float, 0.0,
                          "Seconds a run confirm may be lost before the device "
                          "faults (0 = fault on the first scan)"),
            "DELAY_TIME": (float, 0.0, "Delay a Passive->Active start [s]"),
            "RESTART_TIME": (float, 0.0, "Minimum Passive dwell before restarting [s]"),
            "RESET_REQUIRED": (bool, False,
                               "Park in Locked after a shutdown/interlock trip; "
                               "RESET is required before the device may run again"),
            "use_azeo_states": (bool, False,
                                  "Route an interlock/shutdown trip to the "
                                  "Shutdown/Interlocked state instead of STOPPING"),
            "mode": (str, "AUTO", "AUTO = START_CMD/STOP_CMD, CAS = CAS_IN_D"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
            "INTERLOCK_OPT": (str, "ALWAYS_USE_VALUE",
                              "Bad-quality INTERLOCK/PERMISSIVE_D/SHUTDOWN_D: "
                              "ALWAYS_USE_VALUE (use it anyway), USE_LAST_GOOD "
                              "(last value seen with good quality), "
                              "PASSIVE_IF_BAD (no permit / shut down)"),
        }

    # ------------------------------------------------------------- helpers
    def _resolve_interlocks(self) -> tuple[dict[str, bool], Quality]:
        """Effective interlock-family values + the quality they were read at.

        Azeo ``INTERLOCK_OPT`` decides what a *Bad* ``INTERLOCK_D`` /
        ``PERMISSIVE_D`` / ``SHUTDOWN_D`` means.  Terminals now carry a
        ``Quality`` along the wire, so this reads real per-signal status
        instead of one lumped "status is bad" pin: one transmitter can go
        Bad without condemning the other two.

        Only ``Quality.BAD`` engages an option — an Uncertain value is
        still used (and still shows up in the published output quality),
        which is the manual's reading of "when the status is Bad".
        """
        opt = str(self.config.params.get(
            "INTERLOCK_OPT", "ALWAYS_USE_VALUE")).upper()
        eff: dict[str, bool] = {}
        worst = Quality.GOOD
        for name in ("INTERLOCK", "PERMISSIVE_D", "SHUTDOWN_D"):
            raw = bool(self.get_input(name))
            q = self.input_status(name)
            if q is not Quality.BAD:
                self._ilk_last_good[name] = raw
                eff[name] = raw
            elif opt == "USE_LAST_GOOD":
                eff[name] = self._ilk_last_good[name]
                # The substitute is a real, once-trusted value — usable,
                # but no longer the field, so it reads back as Uncertain.
                q = Quality.UNCERTAIN
            elif opt == "PASSIVE_IF_BAD":
                eff[name] = self._ILK_PASSIVE[name]
                q = Quality.UNCERTAIN
            else:  # ALWAYS_USE_VALUE — Azeo default: act on it regardless
                eff[name] = raw
            if q.value > worst.value:
                worst = q
        return eff, worst

    def _publish_quality(self, ilk_q: Quality, *, held: bool) -> None:
        """Stamp output quality: worst of the inputs this scan actually used.

        A device whose run confirm or command is untrustworthy cannot claim
        a trustworthy RUNNING/STOPPED, so the worst input quality travels on
        to every output.  ``DO_START`` additionally reports ``CONSTANT``
        while the device is pinned Passive (interlocked, locked or faulted)
        and no command can move it.
        """
        worst = ilk_q
        for name in ("START_CMD", "STOP_CMD", "CAS_IN_D", "RUN_FB", "RESET"):
            t = self.inputs.get(name)
            if t is not None and t.connected and t.status.value > worst.value:
                worst = t.status
        for name in self.outputs:
            self.set_output_status(name, worst)
        self.set_output_status(
            "DO_START", worst,
            LimitStatus.CONSTANT if held else LimitStatus.NOT_LIMITED)

    def _commands(self) -> tuple[bool, bool]:
        """(start, stop) requests for this scan, honouring Cas mode."""
        start = bool(self.get_input("START_CMD"))
        stop = bool(self.get_input("STOP_CMD"))
        mode = str(self.config.params.get("mode", "AUTO")).upper()
        if mode == "CAS" and self.inputs["CAS_IN_D"].connected:
            sp = bool(self.get_input("CAS_IN_D"))
            return sp, not sp
        return start, stop

    def execute(self, dt: float):
        p = self.config.params
        ilk, ilk_q = self._resolve_interlocks()
        shutdown = ilk["SHUTDOWN_D"]
        interlock = ilk["INTERLOCK"] and not shutdown
        permissive = ilk["PERMISSIVE_D"]
        start, stop = self._commands()
        run_fb = bool(self.get_input("RUN_FB"))
        reset = bool(self.get_input("RESET"))
        paused = bool(self.get_input("PAUSE_CMD"))
        azeo = bool(p.get("use_azeo_states", False))
        reset_required = bool(p.get("RESET_REQUIRED", False))

        if reset:
            if self._state == self._FAULTED:
                self._state = self._STOPPED
                self._timer = 0.0
                self._fail = self._FAIL_CLEAR
            if self._state == self._LOCKED:
                self._state = self._STOPPED
                self._timer = 0.0
                self._fail = self._FAIL_CLEAR
            self._locked = False

        # Interlock / shutdown trip — force stop
        if not interlock and self._state in (self._STARTING, self._RUNNING):
            self._state = self._INTERLOCKED if azeo else self._STOPPING
            self._timer = 0.0
            self._fail = self._FAIL_SHUTDOWN
            if reset_required:
                self._locked = True

        if self._state == self._INTERLOCKED:
            # Azeo: OUT_D Passive, actual mode LO; leaves the state as
            # soon as the condition clears (Locked when a reset is required).
            if interlock:
                self._state = self._LOCKED if (reset_required and self._locked) \
                    else self._STOPPED
                self._timer = 0.0
                if self._state == self._STOPPED:
                    self._fail = self._FAIL_CLEAR

        elif self._state == self._LOCKED:
            pass  # only RESET (handled above) leaves Locked

        elif self._state == self._STOPPED:
            self._passive_t += dt
            can_start = (start and interlock and permissive
                         and not (reset_required and self._locked)
                         and self._passive_t >= float(p.get("RESTART_TIME", 0.0)))
            if can_start:
                delay = float(p.get("DELAY_TIME", 0.0))
                self._delay_t += dt
                if delay <= 0.0 or self._delay_t >= delay:
                    self._state = self._STARTING
                    self._timer = 0.0
                    self._delay_t = 0.0
                    self._fail = self._FAIL_CLEAR
            else:
                self._delay_t = 0.0

        elif self._state == self._STARTING:
            # A held transition does not age. The interlock and shutdown
            # paths above have already run and are untouched by this — a
            # pause may delay a start, never defeat a trip.
            if not paused:
                self._timer += dt
            if run_fb:
                self._state = self._RUNNING
                self._timer = 0.0
                self._fail = self._FAIL_CLEAR
            elif stop:
                self._state = self._STOPPING
                self._timer = 0.0
            elif not paused and self._timer > p.get("START_TIMEOUT", 10.0):
                self._state = self._FAULTED
                self._fail = self._FAIL_ACT1_CFM_TIME

        elif self._state == self._RUNNING:
            if stop or not interlock:
                self._state = self._STOPPING
                self._timer = 0.0
            elif not run_fb:
                # Lost run feedback while supposedly running.  Azeo waits
                # TRIP_TIME before declaring the confirm lost; TRIP_TIME = 0
                # keeps the original fault-on-the-first-scan behaviour.
                self._trip_t += dt
                if self._trip_t >= float(p.get("TRIP_TIME", 0.0)):
                    self._state = self._FAULTED
                    self._fail = self._FAIL_ACT1_CFM_LOST
            else:
                self._trip_t = 0.0

        elif self._state == self._STOPPING:
            if not paused:
                self._timer += dt
            if not run_fb:
                self._state = self._STOPPED
                self._timer = 0.0
                self._passive_t = 0.0
            elif not paused and self._timer > p.get("STOP_TIMEOUT", 10.0):
                self._state = self._FAULTED
                self._fail = self._FAIL_PASS_CFM_TIME

        if self._state != self._RUNNING:
            self._trip_t = 0.0
        if self._state != self._STOPPED:
            self._passive_t = 0.0
        if self._state in (self._STOPPED, self._STARTING, self._RUNNING):
            if self._fail in (self._FAIL_SHUTDOWN, self._FAIL_TRIPPED):
                self._fail = self._FAIL_CLEAR

        running_out = self._state in (self._STARTING, self._RUNNING)
        self.set_output("DO_START", running_out)
        self.set_output("RUNNING", self._state == self._RUNNING)
        self.set_output("STOPPED", self._state == self._STOPPED)
        self.set_output("FAULTED", self._state == self._FAULTED)
        self.set_output("LOCKED", self._state == self._LOCKED
                        or (reset_required and self._locked))
        self.set_output("FAIL", self._fail)
        self.set_output("FAIL_ACTIVE", self._fail != self._FAIL_CLEAR)
        self.set_output("STATE", self._state)
        self.set_output("ELAPSED", round(self._timer, 2))
        # Only a *held transition* is paused. Asserting the pin
        # against a settled device reports nothing, because it
        # does nothing.
        self.set_output("PAUSED", paused and self._state in (
            self._STARTING, self._STOPPING))
        # Actual mode: LO while shut down, interlocked or locked.
        if (not interlock) or self._state in (self._INTERLOCKED, self._LOCKED):
            mode = "LO"
        else:
            mode = str(p.get("mode", "AUTO")).upper()
        self.set_output("MODE", mode)

        self._publish_quality(
            ilk_q,
            held=(not interlock)
            or self._state in (self._INTERLOCKED, self._LOCKED, self._FAULTED)
            or (reset_required and self._locked),
        )

    def reset(self):
        super().reset()
        self._state = self._STOPPED
        self._timer = 0.0
        self._trip_t = 0.0
        self._delay_t = 0.0
        self._passive_t = 0.0
        self._locked = False
        self._fail = self._FAIL_CLEAR
        self._ilk_last_good = dict(self._ILK_NEUTRAL)


@register_block
class VlvctlBlock(FunctionBlock):
    """Valve control block (Honeywell VLVCTL).

    On/off valve control with open/close commands, position feedback,
    stroke timing, and limit switch monitoring.
    """
    block_type = "VLVCTL"
    category = BlockCategory.IO
    display_name = "Valve Control"
    description = "On/off valve with position feedback, stroke timer, limits"

    def __init__(self, instance_name=""):
        self._position = 0.0  # 0=closed, 100=open
        self._target = 0.0
        self._transit = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("OPEN_CMD", DataType.BOOL, False, "Open command")
        self.add_input("CLOSE_CMD", DataType.BOOL, False, "Close command")
        self.add_input("INTERLOCK", DataType.BOOL, True, "Interlock OK")
        self.add_input("POS_FB", default=-1.0, description="Position feedback (-1=simulate)")
        self.add_output("DO_OPEN", DataType.BOOL, False, "Open solenoid output")
        self.add_output("DO_CLOSE", DataType.BOOL, False, "Close solenoid output")
        self.add_output("POSITION", description="Valve position (0-100%)")
        self.add_output("OPEN", DataType.BOOL, False, "Valve fully open")
        self.add_output("CLOSED", DataType.BOOL, True, "Valve fully closed")
        self.add_output("TRANSIT", DataType.BOOL, False, "Valve in transit")

    def get_config_schema(self):
        return {
            "STROKE_TIME": (float, 5.0, "Full stroke time [s]"),
            "FAIL_ACTION": (str, "CLOSE", "Fail action: CLOSE or OPEN"),
        }

    def execute(self, dt: float):
        p = self.config.params
        interlock = self.get_input("INTERLOCK")
        open_cmd = self.get_input("OPEN_CMD")
        close_cmd = self.get_input("CLOSE_CMD")
        pos_fb = self.get_input("POS_FB")
        stroke_time = max(p.get("STROKE_TIME", 5.0), 0.1)

        if not interlock:
            # Fail-safe
            if p.get("FAIL_ACTION", "CLOSE") == "OPEN":
                self._target = 100.0
            else:
                self._target = 0.0
        elif open_cmd and not close_cmd:
            self._target = 100.0
        elif close_cmd:
            self._target = 0.0

        # Simulate position if no feedback
        if pos_fb < 0:
            rate = 100.0 / stroke_time  # %/s
            delta = self._target - self._position
            max_change = rate * dt
            if abs(delta) <= max_change:
                self._position = self._target
            else:
                self._position += max_change if delta > 0 else -max_change
        else:
            self._position = pos_fb

        self._transit = abs(self._position - self._target) > 1.0
        self.set_output("DO_OPEN", self._target > 50.0)
        self.set_output("DO_CLOSE", self._target < 50.0)
        self.set_output("POSITION", self._position)
        self.set_output("OPEN", self._position >= 99.0)
        self.set_output("CLOSED", self._position <= 1.0)
        self.set_output("TRANSIT", self._transit)
        # A commanded position is only as trustworthy as the commands and
        # the feedback behind it (values are unchanged — status only).
        self.propagate_status()
        self.set_output_status(
            "POSITION", self.outputs["POSITION"].status,
            LimitStatus.CONSTANT if not interlock else
            LimitStatus.NOT_LIMITED if self._transit else
            LimitStatus.HIGH_LIMITED if self._position >= 99.0 else
            LimitStatus.LOW_LIMITED if self._position <= 1.0 else
            LimitStatus.NOT_LIMITED)

    def reset(self):
        super().reset()
        self._position = 0.0
        self._target = 0.0
        self._transit = False
