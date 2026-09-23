"""Azeo Enhanced Device Control block.

Layout:
    EDC — Enhanced Device Control (six-state discrete device controller)

The block follows the Azeo *Enhanced Device Control function block (EDC)*
specification (see ``doc/AZEO_FUNCTION_BLOCKS.md``): command / confirm
comparison over up to six device states, per-state travel and crack timing,
trip on lost confirmation, interlock to a configurable state, permissives,
setpoint forcing / tracking, pulsed outputs, run-time and start counters,
and the Sentinel / Write-Alarm failure summary.
"""
from __future__ import annotations

import json

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


# ═══════════════════════════════════════════════════════════════════════
#  Named-set encodings ($edc_pv_states / $edc_operation_states /
#  $edc_failure_states) — see the EDC spec tables.
# ═══════════════════════════════════════════════════════════════════════

UNDEFINED = 255          # FV_D / PV_D "no input mask matched"
PASSIVE_REF = 255        # INTERLOCK_STATE / FORCE_SP_VAL "use PASSIVE_STATE"

MAX_STATES = 6
N_IO = 4                 # 4 command outputs + 4 confirm inputs

# OPERATION_STATE ($edc_operation_states)
OP_NORMAL = 0
OP_INTERLOCKED = 1
OP_TRIPPED = 2
OP_SHUTDOWN = 3
OP_LOCKED = 4
OP_TRACKING = 5

# Mode strings. Azeo EDC supports Auto and Cas only; OOS/MAN are added
# here because every block in this repo exposes the standard mode set.
MODES = ("OOS", "MAN", "AUTO", "CAS")


def _pv_confirmed(n: int) -> int:
    """PV_STATE index for "Confirmed State n" (0-5)."""
    return n


def _pv_transient(n: int) -> int:
    """PV_STATE index for "Going to State 0" (6) / "Expecting State n" (7-11)."""
    return 6 + n


def _fail_crack(n: int) -> int:
    """FAILURE_STATE index for "State n crack time exceeded" (1-6)."""
    return 1 + n


def _fail_travel(n: int) -> int:
    """FAILURE_STATE index for "State n travel time exceeded" (7-12)."""
    return 7 + n


def _fail_cfm_lost(n: int) -> int:
    """FAILURE_STATE index for "State n confirm lost" (13-18)."""
    return 13 + n


# ═══════════════════════════════════════════════════════════════════════
#  EDC — Enhanced Device Control
# ═══════════════════════════════════════════════════════════════════════

@register_block
class EnhancedDeviceControlBlock(FunctionBlock):
    """Enhanced Device Control (EDC) — multistate discrete device controller.

    Setpoint control for motors, pumps and block valves with up to **six**
    device states (State 0 … State 5, States 2-5 optional). One state is
    designated Passive (the power-failure / safe state) via
    ``PASSIVE_STATE``; the others are Active states. ``SP_D`` requests a
    state, ``OUT_D`` commands it, the four ``IO_IN_n`` confirm contacts are
    matched against per-state input masks to produce ``FV_D``/``PV_D``, and
    the block times the transition, alarms on failure, and can trip, lock,
    interlock or shed the device.

    **State masks (`STATE_MASKS`).** JSON object keyed by state number,
    each state carrying an ``in`` (confirm) and ``out`` (command) mask of
    up to four entries. ``1``/``true`` = bit must be True, ``0``/``false``
    = bit must be False, ``null``/``"x"``/``-1`` = not used (don't care;
    a don't-care *output* bit is always driven False, per Azeo)::

        {"0": {"in": [1, 0, null, null], "out": [0, 0, 0, 0]},
         "1": {"in": [0, 1, null, null], "out": [1, 0, 0, 0]}}

    Missing entries pad with don't-care (``in``) / False (``out``). Leave
    ``STATE_MASKS`` blank for the default binary encoding: state *n*'s
    masks are the 4-bit little-endian binary representation of *n*
    (state 0 = 0000, state 1 = 1000, state 2 = 0100, state 3 = 1100 …),
    which gives the usual single-bit start/run wiring for a 2-state motor.
    Malformed JSON, an out-of-range state key, or a bad mask entry sets
    ``BlockStatus.BAD`` and **holds** every output at its last value
    rather than raising.

    Per the EDC spec, a state whose ``in`` mask is *entirely* don't-care is
    confirmed immediately when commanded, and is skipped when classifying
    ``FV_D`` — the confirm inputs may then still report another state's
    pattern in ``FV_D`` without raising a mismatch failure (motor-operated
    valve with a STOP state).

    **DEVICE_OPTS.** Azeo packs the device options into a 16-bit string.
    This repo's properties panel has no bitfield editor, so each option is
    a separate ``bool`` config parameter named for the option, with the
    Azeo bit number in its description (bit 8 is unused in Azeo and is
    therefore not exposed).

    **Modes.** ``MODE`` is the target mode. ``AUTO`` takes ``SP_D`` from
    the config parameter (or the wired ``SP_IN_D``); ``CAS`` takes it from
    ``CAS_IN_D``. The actual mode (``MODE_ACT``) drops to ``LO`` when
    ``TRK_IN_D`` is True, ``SHUTDOWN_D`` is True, the interlock is lost, the
    permissive blocks the current confirmed state, or ``OPERATION_STATE``
    is Locked. ``OOS`` holds all outputs and reports ``BlockStatus.OOS``;
    ``MAN`` drives ``OUT_D`` straight from ``SP_D`` (still overridden by
    tracking / shutdown / interlock / trip). OOS and MAN are repo
    extensions — Azeo's EDC has Auto and Cas only.

    **Terminology vs. `doc/motor_pump_permissive_interlock_design.md`.**
    That document is this project's canonical motor/pump reference and
    agrees with the EDC on *permissive* (a condition that must be True to
    let the device start, and that never stops a running device). The two
    differ on *interlock* and *trip*, and the Azeo spec wins here:

    * In the project doc an interlock **is** a running trip that latches
      the device off. In the EDC, ``INTERLOCK_D`` is a *maintained*
      condition — while it is False the block continuously drives
      ``OUT_D`` to ``INTERLOCK_STATE`` (which need not be Passive) and
      releases the device again when the condition returns, unless
      *Reset Required* latches it in Locked.
    * In the project doc a trip is any protective process shutdown. In the
      EDC a *trip* is specifically the loss of an established **active**
      confirmation for longer than ``TRIP_TIME``; process shutdowns are
      wired to ``SHUTDOWN_D`` instead. Loss of a *passive* confirm only
      raises a failure and never moves ``OUT_D``.
    * The project doc's "first-out" is approximated by ``FAILURE_STATE``
      plus ``PREV_FAIL_STATE`` (the previous failure, latched until
      ``CLEAR_PREV_FAIL``).

    **Omitted hardware bindings.** ``IO_IN_n``/``IO_OUT_n`` are terminals
    rather than DST channel references, and this repo's terminals carry no
    per-signal status, so ``F_IN_Dn``/``F_OUT_Dn`` readback, the Readback
    Failed / Output Failure block errors, ``BAD_MASK``/``ABNORM_ACTIVE``
    and the event chronicle are not implemented. ``INTERLOCK_OPT`` is
    retained and driven by the explicit ``ILK_STS_BAD`` input, which a
    strategy wires from whatever supplies the bad-status indication for
    ``INTERLOCK_D``/``PERMISSIVE_D``/``SHUTDOWN_D``.
    """

    block_type = "EDC"
    category = BlockCategory.IO
    display_name = "Enhanced Device Control (EDC)"
    description = "Six-state device control: confirms, travel/crack timing, trip, interlock, permissive"

    # ── named sets / units ──────────────────────────────────────────
    # MODE: the Azeo EDC documents Auto and Cas only; OOS and MAN are
    # this repo's standard additions (see the module docstring and
    # ``MODES``), and execute() implements all four, so all four are
    # offered. INTERLOCK_OPT is the doc's "Always Use Value (default) /
    # Use Last Good Value / Passive if Bad" named set.
    config_choices = {
        "MODE": MODES,
        "INTERLOCK_OPT": ("ALWAYS_USE_VALUE", "USE_LAST_GOOD", "PASSIVE_IF_BAD"),
    }
    config_units = {
        **{f"CFM_STATE{n}_TIME": "s" for n in range(MAX_STATES)},
        **{f"IO_OUT_DURATION{i}": "s" for i in range(1, N_IO + 1)},
        "CRACK_TIME": "s",
        "DELAY_TIME": "s",
        "RESTART_TIME": "s",
        "TRIP_TIME": "s",
        "S_TIME_DURATION": "s",
    }

    # ── construction ────────────────────────────────────────────────
    def __init__(self, instance_name: str = ""):
        # Parsed-config cache (keyed by the raw config strings)
        self._cfg_key: tuple | None = None
        self._masks: list[dict] | None = None
        self._num_states = 2
        self._passive = 0
        self._cfg_err = ""

        # Setpoint / command chain
        self._sp = 0                # effective SP_D
        self._sp_accepted = 0       # SP_D after DELAY_TIME / RESTART_TIME
        self._pending_sp: int | None = None
        self._cmd_state = 0         # state request after permissive gating
        self._out = 0               # OUT_D
        self._out_prev = 0
        self._restart_hold = False

        # Feedback
        self._fv = UNDEFINED
        self._pv = UNDEFINED
        self._pv_state = 0
        self._established: int | None = None   # last confirmed state for the standing command
        self._pwc_state: int | None = None     # Passive-when-Confirmed latch
        # State PV_D held when the command last changed; the crack timer
        # runs until PV_D leaves it.
        self._prev_confirm_ref = UNDEFINED

        # Operation / failure
        self._op_state = OP_NORMAL
        self._fail = 0
        self._prev_fail = 0
        self._tripped = False
        self._locked = False
        self._timeout_passive = False
        self._ilk_shutd = False
        self._werr = False
        self._sentinel = False

        # Timers
        self._travel_t = 0.0
        self._crack_t = 0.0
        self._delay_t = 0.0
        self._cfm_loss_t = 0.0
        self._s_elapsed = 0.0

        # Counters
        self._act_count = 0
        self._act_time = 0.0
        self._cur_act_time = 0.0

        # Edge / latch memories
        self._force_edge = False
        self._clear_fail_edge = False
        self._reset_cnt_edge = False
        self._ignore_latch = False
        self._cas_prev = 0
        self._sp_prev_written = 0
        self._sent_cond_prev = False
        self._primed = False

        # Pulsed outputs
        self._io_out = [False] * N_IO
        self._io_raw_prev = [False] * N_IO
        self._pulse_t = [0.0] * N_IO

        # Last-good values for INTERLOCK_OPT = USE_LAST_GOOD
        self._last_good = {"SHUTDOWN_D": False, "INTERLOCK_D": True,
                           "PERMISSIVE_D": True}

        self._mode_act = "AUTO"
        self._block_err = ""

        super().__init__(instance_name)

    def _define_terminals(self):
        # Setpoint sources
        self.add_input("CAS_IN_D", DataType.INT, 0,
                       "Cascade discrete setpoint (state number 0-5)")
        self.add_input("SP_IN_D", DataType.INT, 0,
                       "Externally wired SP_D; leave unwired to use the SP_D parameter")
        self.add_input("FORCE_SP_D", DataType.BOOL, False,
                       "False->True forces SP_D / CAS_IN_D to FORCE_SP_VAL")
        # Condition inputs
        self.add_input("TRK_IN_D", DataType.BOOL, False,
                       "Tracking: OUT_D follows FV_D, actual mode LO")
        self.add_input("SHUTDOWN_D", DataType.BOOL, False,
                       "True forces and holds OUT_D in the Passive state")
        self.add_input("INTERLOCK_D", DataType.BOOL, True,
                       "Must be True (Interlock option) or OUT_D goes to INTERLOCK_STATE")
        self.add_input("PERMISSIVE_D", DataType.BOOL, True,
                       "Permit for a commanded state change (Permissive options)")
        self.add_input("RESET_D", DataType.BOOL, False,
                       "Unlocks the block when Reset Required is selected")
        self.add_input("ILK_STS_BAD", DataType.BOOL, False,
                       "Bad-status indication for SHUTDOWN_D/INTERLOCK_D/PERMISSIVE_D (INTERLOCK_OPT)")
        # Feedback
        for i in range(1, N_IO + 1):
            self.add_input(f"IO_IN_{i}", DataType.BOOL, False,
                           f"Confirm contact {i}")
        self.add_input("SIMULATE_IN_D", DataType.INT, UNDEFINED,
                       "Simulated feedback state used when SIMULATE_D is enabled")
        self.add_input("IGNORE_PV", DataType.BOOL, False,
                       "True forces PV_D to accept OUT_D as confirmed (failed confirm hardware)")
        # Output enable / counters
        self.add_input("DISABLE_IO_OUT", DataType.BOOL, False,
                       "True forces every IO_OUT_n to zero regardless of OUT_D")
        self.add_input("COUNT_NOF_AS", DataType.BOOL, False,
                       "True counts OUT_D Passive->Active transitions")
        self.add_input("COUNT_AS_TIMER", DataType.BOOL, False,
                       "True accumulates active-state run time")
        self.add_input("RESET_ACT_SP", DataType.BOOL, False,
                       "One-shot reset of ACT_STATE_COUNT")
        self.add_input("RESET_ACT_TIME", DataType.BOOL, False,
                       "Resets ACT_TIME and CUR_ACT_TIME")
        self.add_input("CLEAR_PREV_FAIL", DataType.BOOL, False,
                       "One-shot reset of PREV_FAIL_STATE")

        # Commands / feedback
        self.add_output("OUT_D", DataType.INT, 0, "Commanded device state (0-5)")
        self.add_output("CMD_D", DataType.INT, 10,
                        "OUT_D when active; OUT_D + 10 when Passive (for DCC CMD_IN_D)")
        self.add_output("SP_D", DataType.INT, 0, "Effective device setpoint")
        self.add_output("PV_D", DataType.INT, UNDEFINED,
                        "Process state used in execution (FV_D, OUT_D or simulate)")
        self.add_output("FV_D", DataType.INT, UNDEFINED,
                        "Feedback state from the input masks (255 = Undefined)")
        for i in range(1, N_IO + 1):
            self.add_output(f"IO_OUT_{i}", DataType.BOOL, False,
                            f"Command output {i}")
        # State
        self.add_output("PV_STATE", DataType.INT, 0,
                        "0-5 Confirmed State n, 6 Going to State 0, 7-11 Expecting State 1-5")
        self.add_output("OPERATION_STATE", DataType.INT, OP_NORMAL,
                        "0 Normal 1 Interlocked 2 Tripped 3 Shutdown 4 Locked 5 Tracking")
        self.add_output("FAILURE_STATE", DataType.INT, 0,
                        "0 Clear, 1-6 crack time, 7-12 travel time, 13-18 confirm lost")
        self.add_output("PREV_FAIL_STATE", DataType.INT, 0,
                        "Previous FAILURE_STATE, held until CLEAR_PREV_FAIL")
        self.add_output("FAIL_ACTIVE", DataType.BOOL, False,
                        "FAILURE_STATE is not Clear")
        self.add_output("ILK_SHUTD_ACTIVE", DataType.BOOL, False,
                        "An interlock or shutdown changed the command")
        self.add_output("WRITE_ERR_ACTIVE", DataType.BOOL, False,
                        "Write Alarm — the block cannot honour a new setpoint")
        self.add_output("SENTINEL", DataType.BOOL, False,
                        "FAIL_ACTIVE, ILK_SHUTD_ACTIVE or WRITE_ERR_ACTIVE")
        self.add_output("IGNORE_PV_ACT", DataType.BOOL, False, "IGNORE_PV is True")
        # Timers / counters
        self.add_output("TRAVEL_TIMER", description="Elapsed travel time [s]")
        self.add_output("CRACK_TIMER", description="Elapsed crack time [s]")
        self.add_output("DELAY_TIMER", description="DELAY_TIME/RESTART_TIME countdown [s]")
        self.add_output("CFM_LOSS_TIMER", description="Elapsed confirm-loss time vs TRIP_TIME [s]")
        self.add_output("S_ELAPSED_TIMER", description="Elapsed time since Sentinel conditions cleared [s]")
        self.add_output("ACT_STATE_COUNT", DataType.INT, 0,
                        "OUT_D Passive->Active transitions since reset")
        self.add_output("ACT_TIME", description="Total active time of PV_D since reset [s]")
        self.add_output("CUR_ACT_TIME", description="Active time since the last Passive->Active [s]")
        # Diagnostics
        self.add_output("MODE_ACT", DataType.STRING, "AUTO",
                        "Actual mode: OOS | MAN | AUTO | CAS | LO")
        self.add_output("BLOCK_ERR", DataType.STRING, "",
                        "Active block errors (comma separated)")

    def get_config_schema(self):
        schema = {
            "MODE": (str, "AUTO", "Target mode: OOS | MAN | AUTO | CAS"),
            "NUM_STATES": (int, 2, "Number of device states used (2-6; State 0 and 1 mandatory)"),
            "PASSIVE_STATE": (int, 0, "State number designated Passive (power-failure/safe state)"),
            "SP_D": (int, 0, "Device setpoint 0-5 (operator entry; used when SP_IN_D is unwired)"),
            "INTERLOCK_STATE": (int, 255, "Interlock target state 0-5; 255 = the Passive state"),
            "FORCE_SP_VAL": (int, 255, "Forced setpoint 0-5; 255 = the Passive state"),
            "STATE_MASKS": (str, "", "Per-state I/O masks as JSON (see docstring); blank = binary default"),
            "SIMULATE_D": (bool, False, "Enable feedback simulation"),
            "SIM_VAL": (int, 255, "Simulated FV_D when SIMULATE_IN_D is unwired (255 = Undefined)"),
            "INTERLOCK_OPT": (str, "ALWAYS_USE_VALUE",
                              "Bad status handling: ALWAYS_USE_VALUE | USE_LAST_GOOD | PASSIVE_IF_BAD"),
        }
        for n in range(MAX_STATES):
            schema[f"CFM_STATE{n}_TIME"] = (
                float, 0.0, f"Max time allowed to transition to State {n} [s]; 0 = no limit")
        schema.update({
            "CRACK_TIME": (float, 0.0, "Max time allowed to drop the previous state's confirm [s]; 0 = off"),
            "DELAY_TIME": (float, 0.0, "Delay of a Passive->Active setpoint change [s]"),
            "RESTART_TIME": (float, 0.0, "Passive dwell of OUT_D on an Active->Active change [s]"),
            "TRIP_TIME": (float, 0.0, "Time a confirm may be lost before it counts as lost [s]"),
            "S_TIME_DURATION": (float, 0.0, "Sentinel off-delay [s]"),
        })
        for i in range(1, N_IO + 1):
            schema[f"IO_OUT_DURATION{i}"] = (
                float, 0.0, f"Non-zero pulses IO_OUT_{i} for that many seconds; 0 = sustained")
        # DEVICE_OPTS — one bool per Azeo bit (bit 8 is unused).
        schema.update({
            "OPT_PASSIVE_ON_ACTIVE_TIMEOUT": (
                bool, False, "DEVICE_OPTS bit 0 — OUT_D goes Passive if an active state "
                             "is not confirmed in time"),
            "OPT_SP_TRACK_ON_TRIP": (
                bool, False, "DEVICE_OPTS bit 1 — on a trip, SP_D and CAS_IN_D are set Passive"),
            "OPT_CAS_SP_TRACK": (
                bool, False, "DEVICE_OPTS bit 2 — in Auto/LO, CAS_IN_D copies SP_D (bumpless AUTO->CAS)"),
            "OPT_FORCE_SP_IN_AUTO": (
                bool, False, "DEVICE_OPTS bit 3 — in Auto, FORCE_SP_D copies FORCE_SP_VAL to SP_D"),
            "OPT_FORCE_SP_IN_CAS": (
                bool, False, "DEVICE_OPTS bit 4 — in Cas, FORCE_SP_D copies FORCE_SP_VAL to CAS_IN_D"),
            "OPT_LOCK_RECOVERY": (
                bool, False, "DEVICE_OPTS bit 5 — Locked clears on a new CAS_IN_D write "
                             "when target mode is CAS"),
            "OPT_SP_TRACK_PASSIVE_ON_CFM_TIMEOUT": (
                bool, False, "DEVICE_OPTS bit 6 — with bit 0, a confirm timeout sets SP_D/CAS_IN_D Passive"),
            "OPT_SP_TRACK_ON_TRACKING": (
                bool, False, "DEVICE_OPTS bit 7 — with TRK_IN_D True, SP_D tracks OUT_D"),
            "OPT_PASSIVE_WHEN_CONFIRMED": (
                bool, False, "DEVICE_OPTS bit 9 — OUT_D goes Passive once the active state is confirmed"),
            "OPT_PERMISSIVE_TO_ANY_STATE": (
                bool, False, "DEVICE_OPTS bit 10 — PERMISSIVE_D gates every state change, Passive included"),
            "OPT_TRIP": (
                bool, False, "DEVICE_OPTS bit 11 — active confirm lost > TRIP_TIME sets OUT_D Passive"),
            "OPT_RESET_REQUIRED": (
                bool, False, "DEVICE_OPTS bit 12 — a cleared shutdown/interlock/trip goes "
                             "Locked until RESET_D"),
            "OPT_PERMISSIVE": (
                bool, False, "DEVICE_OPTS bit 13 — PERMISSIVE_D gates transitions to an Active state"),
            "OPT_SP_TRACK_ON_SHUTDOWN_ILK": (
                bool, False, "DEVICE_OPTS bit 14 — SP_D tracks OUT_D while Shutdown or Interlocked"),
            "OPT_INTERLOCK": (
                bool, False, "DEVICE_OPTS bit 15 — INTERLOCK_D False drives OUT_D to INTERLOCK_STATE"),
        })
        return schema

    def _apply_config(self):
        self._cfg_key = None       # force a re-parse of STATE_MASKS
        self._primed = False

    # ── configuration parsing ───────────────────────────────────────
    @staticmethod
    def _mask_entry(v, *, is_out: bool):
        """Normalise one mask entry to True / False / None (don't care)."""
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("x", "-", "", "dc", "none", "null"):
                return None
            if s in ("1", "true", "t", "y"):
                return True
            if s in ("0", "false", "f", "n"):
                return False
            raise ValueError(f"bad mask entry {v!r}")
        if isinstance(v, (int, float)):
            iv = int(v)
            if iv < 0:
                return None
            if iv in (0, 1):
                return bool(iv)
        raise ValueError(f"bad mask entry {v!r}")

    def _parse(self, p) -> tuple[list[dict], int, int]:
        """Parse and cache NUM_STATES / PASSIVE_STATE / STATE_MASKS.

        Raises ValueError on malformed configuration; the caller converts
        that into BlockStatus.BAD + hold.
        """
        raw = str(p.get("STATE_MASKS", "") or "")
        num = int(p.get("NUM_STATES", 2))
        passive = int(p.get("PASSIVE_STATE", 0))
        key = (raw, num, passive)
        if key == self._cfg_key and self._masks is not None:
            return self._masks, self._num_states, self._passive

        if not 2 <= num <= MAX_STATES:
            raise ValueError(f"NUM_STATES {num} outside 2..{MAX_STATES}")
        if not 0 <= passive < num:
            raise ValueError(f"PASSIVE_STATE {passive} not an enabled state")

        # Default: 4-bit little-endian binary encoding of the state number.
        masks = []
        for n in range(num):
            bits = [bool((n >> b) & 1) for b in range(N_IO)]
            masks.append({"in": list(bits), "out": list(bits)})

        if raw.strip():
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("STATE_MASKS must be a JSON object keyed by state number")
            for k, entry in data.items():
                n = int(k)
                if not 0 <= n < num:
                    raise ValueError(f"STATE_MASKS state {n} outside 0..{num - 1}")
                if not isinstance(entry, dict):
                    raise ValueError(f"STATE_MASKS state {n} must be an object")
                m_in = list(entry.get("in", []))[:N_IO]
                m_out = list(entry.get("out", []))[:N_IO]
                m_in = [self._mask_entry(v, is_out=False) for v in m_in]
                m_out = [self._mask_entry(v, is_out=True) for v in m_out]
                m_in += [None] * (N_IO - len(m_in))
                # A don't-care output bit is always driven False (Azeo).
                m_out = [bool(v) for v in m_out] + [False] * (N_IO - len(m_out))
                masks[n] = {"in": m_in, "out": m_out}

        self._cfg_key = key
        self._masks = masks
        self._num_states = num
        self._passive = passive
        self._cfg_err = ""
        return masks, num, passive

    # ── helpers ─────────────────────────────────────────────────────
    def _resolve_state(self, value: int, passive: int, num: int) -> int:
        """Map a configured state reference (255 = Passive) to a state number."""
        v = int(value)
        if v == PASSIVE_REF:
            return passive
        return v if 0 <= v < num else passive

    def _match_fv(self, masks, num) -> int:
        """Classify the IO_IN pattern against the input masks."""
        bits = [bool(self.get_input(f"IO_IN_{i}")) for i in range(1, N_IO + 1)]
        for n in range(num):
            m = masks[n]["in"]
            if all(e is None for e in m):
                continue   # all-don't-care states never take part in matching
            if all(e is None or e == b for e, b in zip(m, bits)):
                return n
        return UNDEFINED

    def _auto_confirm(self, masks, n: int) -> bool:
        """True when state n's input mask is entirely don't-care."""
        return 0 <= n < len(masks) and all(e is None for e in masks[n]["in"])

    def _cfm_time(self, p, n: int) -> float:
        return max(0.0, float(p.get(f"CFM_STATE{n}_TIME", 0.0)))

    # ── execution ───────────────────────────────────────────────────
    def execute(self, dt: float):
        p = self.config.params
        try:
            masks, num, passive = self._parse(p)
        except Exception as exc:      # malformed config: hold, do not raise
            self._cfg_err = str(exc)
            self.status = BlockStatus.BAD
            self._block_err = f"ConfigError: {self._cfg_err}"
            self._publish()
            return

        if not self._primed:
            self._out = self._out_prev = passive
            self._sp = self._sp_accepted = self._cmd_state = passive
            self._sp_prev_written = passive
            self._cas_prev = int(self.get_input("CAS_IN_D"))
            self._primed = True

        opt = lambda name: bool(p.get(name, False))   # noqa: E731 — option shorthand

        # --- 1. condition inputs (INTERLOCK_OPT bad-status handling) ---
        sts_bad = bool(self.get_input("ILK_STS_BAD"))
        ilk_opt = str(p.get("INTERLOCK_OPT", "ALWAYS_USE_VALUE")).upper()
        raw_cond = {
            "SHUTDOWN_D": bool(self.get_input("SHUTDOWN_D")),
            "INTERLOCK_D": bool(self.get_input("INTERLOCK_D")),
            "PERMISSIVE_D": bool(self.get_input("PERMISSIVE_D")),
        }
        if sts_bad and ilk_opt == "USE_LAST_GOOD":
            cond = dict(self._last_good)
        elif sts_bad and ilk_opt == "PASSIVE_IF_BAD":
            # Passive value: 0 for INTERLOCK_D/PERMISSIVE_D, 1 for SHUTDOWN_D.
            cond = {"SHUTDOWN_D": True, "INTERLOCK_D": False, "PERMISSIVE_D": False}
        else:
            cond = dict(raw_cond)
        if not sts_bad:
            self._last_good = dict(raw_cond)
        shutdown, interlock, permissive = (
            cond["SHUTDOWN_D"], cond["INTERLOCK_D"], cond["PERMISSIVE_D"])
        tracking = bool(self.get_input("TRK_IN_D"))

        # --- 2. mode ---
        target = str(p.get("MODE", "AUTO")).upper()
        if target not in MODES:
            target = "AUTO"
        if target == "OOS":
            self._mode_act = "OOS"
            self.status = BlockStatus.OOS
            self._block_err = "OutOfService"
            self._publish()
            return

        # --- 3. feedback (FV_D) and PV_D ---
        simulate = bool(p.get("SIMULATE_D", False))
        if simulate:
            sim_t = self.inputs["SIMULATE_IN_D"]
            self._fv = int(sim_t.value) if sim_t.connected else int(p.get("SIM_VAL", UNDEFINED))
        else:
            self._fv = self._match_fv(masks, num)

        ignore = bool(self.get_input("IGNORE_PV"))
        self._ignore_latch = ignore          # IGNORE_PV is latched by the operator
        pv_prev = self._pv
        self._pv = self._out if ignore else self._fv

        # --- 4. transition / confirm timing against the standing command ---
        standing = self._out
        confirmed = (
            self._pv == standing
            or self._auto_confirm(masks, standing)
            or (self._pwc_state is not None and self._pv == self._pwc_state)
        )
        trip_time = max(0.0, float(p.get("TRIP_TIME", 0.0)))

        if confirmed:
            # TRAVEL_TIMER holds its value once the state is confirmed.
            self._cfm_loss_t = 0.0
            self._established = standing
            # The failure clears only when the device actually reached what
            # was asked; while Passive-on-Active-Timeout or a trip holds
            # OUT_D away from the request the alarm stays up.
            if self._fail and standing == self._cmd_state:
                self._prev_fail = self._fail
                self._fail = 0
        elif self._established == standing:
            # An established confirm was lost without a new command.
            self._cfm_loss_t += dt
            if self._cfm_loss_t > trip_time:
                self._set_fail(_fail_cfm_lost(standing))
                # Only loss of an *active* confirm drives OUT_D (to Passive);
                # loss of the passive confirm is a failure only (EDC §4).
                if opt("OPT_TRIP") and standing != passive:
                    self._tripped = True
        else:
            # Travelling towards the standing command. TRAVEL_TIMER counts
            # up until the confirm arrives or the travel time is exceeded,
            # then holds until the next transition (EDC/DC spec).
            if self._fail != _fail_travel(standing):
                self._travel_t += dt
            crack_time = max(0.0, float(p.get("CRACK_TIME", 0.0)))
            if crack_time > 0.0 and self._pv == self._prev_confirm_ref:
                self._crack_t += dt
                if self._crack_t > crack_time:
                    self._set_fail(_fail_crack(standing))
            cfm_t = self._cfm_time(p, standing)
            if cfm_t > 0.0 and self._travel_t > cfm_t:
                self._set_fail(_fail_travel(standing))
                if opt("OPT_PASSIVE_ON_ACTIVE_TIMEOUT") and standing != passive:
                    self._timeout_passive = True

        failed = self._fail in (
            _fail_travel(standing), _fail_crack(standing)) and not confirmed

        # --- 5. cascade setpoint (CAS_IN_D) ---
        cas_t = self.inputs["CAS_IN_D"]
        cas = int(cas_t.value)
        cas_wired = cas_t.connected
        force_val = self._resolve_state(p.get("FORCE_SP_VAL", PASSIVE_REF), passive, num)
        force_edge = bool(self.get_input("FORCE_SP_D")) and not self._force_edge
        permit_force = (not shutdown) and interlock and permissive
        cas_new = cas
        werr = False

        # Actual mode is needed by the CAS SP Track option; the LO
        # conditions below do not depend on the setpoint chain.
        lo = (tracking or shutdown
              or (opt("OPT_INTERLOCK") and not interlock)
              or self._locked)
        if not lo and (opt("OPT_PERMISSIVE") or opt("OPT_PERMISSIVE_TO_ANY_STATE")) \
                and not permissive:
            if opt("OPT_PERMISSIVE_TO_ANY_STATE"):
                lo = self._pv_state < MAX_STATES        # any Confirmed State n
            else:
                lo = self._pv_state == _pv_confirmed(passive)
        mode_act = "LO" if lo else target

        sp_in_t = self.inputs["SP_IN_D"]
        sp_wired = sp_in_t.connected
        sp = int(sp_in_t.value) if sp_wired else int(p.get("SP_D", 0))
        if mode_act == "CAS":
            sp = cas

        if opt("OPT_CAS_SP_TRACK") and mode_act in ("AUTO", "LO") and cas_new != sp:
            cas_new = sp
        if opt("OPT_FORCE_SP_IN_CAS") and mode_act == "CAS" and force_edge and permit_force:
            cas_new = force_val
            if cas_wired and force_val != cas:
                werr = True
            sp = cas_new
        if opt("OPT_SP_TRACK_ON_TRIP") and self._tripped:
            cas_new = passive
            sp = passive
        if opt("OPT_SP_TRACK_PASSIVE_ON_CFM_TIMEOUT") and \
                opt("OPT_PASSIVE_ON_ACTIVE_TIMEOUT") and failed:
            cas_new = passive
            sp = passive
        if cas_new != cas:
            if cas_wired:
                werr = True       # cannot rewrite an externally wired CAS_IN_D
            else:
                cas_t.value = cas_new
                cas = cas_new

        # --- 6. command setpoint (SP_D) ---
        if opt("OPT_FORCE_SP_IN_AUTO") and mode_act == "AUTO" and force_edge and permit_force:
            if sp_wired and force_val != sp:
                werr = True
            sp = force_val
        if opt("OPT_SP_TRACK_ON_SHUTDOWN_ILK") and self._op_state in (OP_SHUTDOWN, OP_INTERLOCKED):
            sp = self._out
        if tracking and opt("OPT_SP_TRACK_ON_TRACKING"):
            sp = self._out
        sp = sp if 0 <= sp < num else passive
        if sp_wired and sp != int(sp_in_t.value):
            werr = True           # a tracking option wants to move a wired SP_D
        if mode_act == "LO" and sp != self._sp_prev_written:
            werr = True           # new setpoint entered while in LO
        if not sp_wired:
            p["SP_D"] = sp
        self._sp = sp
        self._sp_prev_written = sp
        self._force_edge = bool(self.get_input("FORCE_SP_D"))

        # --- 7. setpoint delays (DELAY_TIME / RESTART_TIME) ---
        delay_time = max(0.0, float(p.get("DELAY_TIME", 0.0)))
        restart_time = max(0.0, float(p.get("RESTART_TIME", 0.0)))
        if sp != self._sp_accepted and sp != self._pending_sp:
            if self._sp_accepted == passive and sp != passive and delay_time > 0.0:
                self._pending_sp, self._delay_t, self._restart_hold = sp, delay_time, False
            elif self._sp_accepted != passive and sp != passive and restart_time > 0.0:
                self._pending_sp, self._delay_t, self._restart_hold = sp, restart_time, True
            else:
                self._sp_accepted = sp
                self._pending_sp, self._delay_t, self._restart_hold = None, 0.0, False
        if self._pending_sp is not None:
            self._delay_t = max(0.0, self._delay_t - dt)
            if self._delay_t <= 0.0:
                self._sp_accepted = self._pending_sp
                self._pending_sp, self._restart_hold = None, False

        # --- 8. permissive gating of the accepted setpoint ---
        want = self._sp_accepted
        if want != self._cmd_state:
            if opt("OPT_PERMISSIVE_TO_ANY_STATE"):
                allowed = permissive          # every change needs the permit
            elif opt("OPT_PERMISSIVE"):
                allowed = permissive or want == passive
            else:
                allowed = True
            if allowed:
                self._cmd_state = want
                # A re-entered setpoint clears the Passive-on-Active-Timeout
                # latch (Azeo requires SP_D to be entered again to retry;
                # a same-value rewrite of a config parameter is not
                # observable here, so a *change* of the request is used).
                self._timeout_passive = False

        # --- 9. operation state (Tracking > Shutdown > Interlocked >
        #        Tripped > Locked > Normal) ---
        ilk_active = opt("OPT_INTERLOCK") and not interlock and not shutdown
        if self._tripped and self._sp == passive:
            self._tripped = False             # trip clears on a Passive setpoint
        prev_op = self._op_state

        if opt("OPT_RESET_REQUIRED"):
            was_held = prev_op in (OP_SHUTDOWN, OP_INTERLOCKED, OP_TRIPPED)
            if was_held and not shutdown and not ilk_active and not self._tripped:
                self._locked = True
        if self._locked:
            if bool(self.get_input("RESET_D")):
                self._locked = False
            elif opt("OPT_LOCK_RECOVERY") and target == "CAS" and cas != self._cas_prev \
                    and permissive:
                self._locked = False
        self._cas_prev = cas

        if tracking:
            self._op_state = OP_TRACKING
        elif shutdown:
            self._op_state = OP_SHUTDOWN
        elif ilk_active:
            self._op_state = OP_INTERLOCKED
        elif self._tripped:
            self._op_state = OP_TRIPPED
        elif self._locked:
            self._op_state = OP_LOCKED
        else:
            self._op_state = OP_NORMAL

        if self._op_state == OP_LOCKED:
            mode_act = "LO"

        # --- 10. OUT_D ---
        ilk_state = self._resolve_state(p.get("INTERLOCK_STATE", PASSIVE_REF), passive, num)
        if self._op_state == OP_TRACKING:
            out = self._fv if self._fv != UNDEFINED else self._out
        elif self._op_state == OP_SHUTDOWN:
            out = passive
        elif self._op_state == OP_INTERLOCKED:
            out = ilk_state
        elif self._op_state in (OP_TRIPPED, OP_LOCKED):
            out = passive
        elif mode_act == "MAN":
            out = self._sp
        elif self._restart_hold or self._timeout_passive:
            out = passive
        else:
            out = self._cmd_state

        # Passive when Confirmed (bit 9): drop the drive signal once the
        # requested active state is reached (motor-operated valve).
        if opt("OPT_PASSIVE_WHEN_CONFIRMED") and self._op_state == OP_NORMAL:
            if out != passive and self._pv == out:
                self._pwc_state = out
            if self._pwc_state is not None:
                if self._pv == self._pwc_state and self._cmd_state == self._pwc_state:
                    out = passive
                else:
                    self._pwc_state = None
        else:
            self._pwc_state = None

        out = out if 0 <= out < num else passive
        self._out_prev = self._out
        if out != self._out:
            # New command: restart the travel/crack timers and remember the
            # state that must "crack" before the device is under way.
            self._prev_confirm_ref = self._pv
            self._travel_t = 0.0
            self._crack_t = 0.0
            self._cfm_loss_t = 0.0
            self._established = None
        self._out = out

        # --- 11. PV_STATE ---
        if self._pv == out or self._auto_confirm(masks, out) or \
                (self._pwc_state is not None and self._pv == self._pwc_state):
            self._pv_state = _pv_confirmed(out)
        else:
            self._pv_state = _pv_transient(out)

        # --- 12. IO_OUT decode + pulsed outputs ---
        disable = bool(self.get_input("DISABLE_IO_OUT"))
        out_mask = masks[out]["out"]
        for i in range(N_IO):
            raw = bool(out_mask[i])
            dur = max(0.0, float(p.get(f"IO_OUT_DURATION{i + 1}", 0.0)))
            if dur > 0.0:
                if raw and not self._io_raw_prev[i]:
                    self._pulse_t[i] = dur
                elif not raw:
                    self._pulse_t[i] = 0.0
                else:
                    self._pulse_t[i] = max(0.0, self._pulse_t[i] - dt)
                val = raw and self._pulse_t[i] > 0.0
            else:
                val = raw
            self._io_raw_prev[i] = raw
            self._io_out[i] = False if disable else val

        # --- 13. counters / run-time timers ---
        if bool(self.get_input("RESET_ACT_SP")) and not self._reset_cnt_edge:
            self._act_count = 0
        self._reset_cnt_edge = bool(self.get_input("RESET_ACT_SP"))
        if bool(self.get_input("COUNT_NOF_AS")) and \
                self._out_prev == passive and out != passive:
            self._act_count += 1

        if bool(self.get_input("RESET_ACT_TIME")):
            self._act_time = 0.0
            self._cur_act_time = 0.0
        pv_active = self._pv != passive and self._pv != UNDEFINED
        pv_was_passive = pv_prev == passive or pv_prev == UNDEFINED
        if bool(self.get_input("COUNT_AS_TIMER")) and pv_active:
            if pv_was_passive:
                self._cur_act_time = 0.0     # new Passive->Active transition
            self._cur_act_time += dt
            self._act_time += dt

        # --- 14. failure summary / Sentinel ---
        if bool(self.get_input("CLEAR_PREV_FAIL")) and not self._clear_fail_edge:
            self._prev_fail = 0
        self._clear_fail_edge = bool(self.get_input("CLEAR_PREV_FAIL"))

        fail_active = self._fail != 0
        self._ilk_shutd = (self._op_state in (OP_SHUTDOWN, OP_INTERLOCKED)
                           and out != self._out_prev)
        self._werr = werr

        cond_any = fail_active or self._ilk_shutd or self._werr
        s_dur = max(0.0, float(p.get("S_TIME_DURATION", 0.0)))
        if fail_active:
            self._sentinel = True
            self._s_elapsed = 0.0
        elif cond_any and not self._sent_cond_prev:
            # Momentary condition: SENTINEL latches, then times out after
            # S_TIME_DURATION even if the condition is still present.
            self._sentinel = True
            self._s_elapsed = 0.0
        else:
            self._s_elapsed += dt
            if self._sentinel and self._s_elapsed >= s_dur:
                self._sentinel = False
        self._sent_cond_prev = cond_any

        # --- 15. block errors / status ---
        errs = []
        if simulate:
            errs.append("SimulateActive")
        if mode_act == "LO":
            errs.append("LocalOverride")
        if self._ignore_latch:
            errs.append("IgnorePV")
        if self._fv == UNDEFINED and not simulate and self._pv_state < MAX_STATES \
                and not self._auto_confirm(masks, out):
            errs.append("InputFailure/BadPV")
        self._block_err = ", ".join(errs)
        self._mode_act = mode_act

        if fail_active:
            self.status = BlockStatus.BAD
        elif simulate or self._ignore_latch or mode_act == "LO":
            self.status = BlockStatus.UNCERTAIN
        else:
            self.status = BlockStatus.GOOD

        self._publish()

    def _set_fail(self, code: int):
        if self._fail != code:
            if self._fail:
                self._prev_fail = self._fail
            self._fail = code

    def _publish(self):
        """Write every output from the internal state (also used to hold)."""
        passive = self._passive
        self.set_output("OUT_D", self._out)
        self.set_output("CMD_D", self._out if self._out != passive else self._out + 10)
        self.set_output("SP_D", self._sp)
        self.set_output("PV_D", self._pv)
        self.set_output("FV_D", self._fv)
        for i in range(N_IO):
            self.set_output(f"IO_OUT_{i + 1}", self._io_out[i])
        self.set_output("PV_STATE", self._pv_state)
        self.set_output("OPERATION_STATE", self._op_state)
        self.set_output("FAILURE_STATE", self._fail)
        self.set_output("PREV_FAIL_STATE", self._prev_fail)
        self.set_output("FAIL_ACTIVE", self._fail != 0)
        self.set_output("ILK_SHUTD_ACTIVE", self._ilk_shutd)
        self.set_output("WRITE_ERR_ACTIVE", self._werr)
        self.set_output("SENTINEL", self._sentinel)
        self.set_output("IGNORE_PV_ACT", self._ignore_latch)
        self.set_output("TRAVEL_TIMER", self._travel_t)
        self.set_output("CRACK_TIMER", self._crack_t)
        self.set_output("DELAY_TIMER", self._delay_t)
        self.set_output("CFM_LOSS_TIMER", self._cfm_loss_t)
        self.set_output("S_ELAPSED_TIMER", self._s_elapsed)
        self.set_output("ACT_STATE_COUNT", self._act_count)
        self.set_output("ACT_TIME", self._act_time)
        self.set_output("CUR_ACT_TIME", self._cur_act_time)
        self.set_output("MODE_ACT", self._mode_act)
        self.set_output("BLOCK_ERR", self._block_err)

    def reset(self):
        super().reset()
        self._sp = self._sp_accepted = self._cmd_state = 0
        self._pending_sp = None
        self._out = self._out_prev = 0
        self._restart_hold = False
        self._fv = self._pv = UNDEFINED
        self._pv_state = 0
        self._established = None
        self._pwc_state = None
        self._prev_confirm_ref = UNDEFINED
        self._op_state = OP_NORMAL
        self._fail = self._prev_fail = 0
        self._tripped = self._locked = self._timeout_passive = False
        self._ilk_shutd = self._werr = self._sentinel = False
        self._travel_t = self._crack_t = self._delay_t = 0.0
        self._cfm_loss_t = self._s_elapsed = 0.0
        self._act_count = 0
        self._act_time = self._cur_act_time = 0.0
        self._force_edge = False
        self._clear_fail_edge = self._reset_cnt_edge = False
        self._ignore_latch = self._sent_cond_prev = False
        self._cas_prev = self._sp_prev_written = 0
        self._io_out = [False] * N_IO
        self._io_raw_prev = [False] * N_IO
        self._pulse_t = [0.0] * N_IO
        self._last_good = {"SHUTDOWN_D": False, "INTERLOCK_D": True,
                           "PERMISSIVE_D": True}
        self._mode_act = "AUTO"
        self._block_err = ""
        self._cfg_key = None
        self._primed = False
