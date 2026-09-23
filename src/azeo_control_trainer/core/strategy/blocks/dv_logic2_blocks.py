"""Azeo logical blocks — BDE, BFI, BFO, DCC.

Second batch of Azeo "Logical Blocks" palette parity, transcribed from
``doc/AZEO_FUNCTION_BLOCKS.md``:

    BDE — Bi-directional Edge Trigger (one-scan pulse on any transition)
    BFI — Boolean Fan Input  (pack up to 16 discretes into an integer/BCD)
    BFO — Boolean Fan Output (unpack an integer back into 16 discretes)
    DCC — Discrete Control Condition (16 interlock / 8 permissive /
          8 force-setpoint conditions feeding an EDC block)

None of these blocks has modes in Azeo, so none exposes a ``MODE``
parameter.

**Signal status.** Azeo carries a status byte on every parameter; this
repo's terminals carry only a value, and a block has a single
``self.status``. The status *rules* from the spec are therefore modelled
with explicit discrete/integer status inputs (``IN_BAD`` on BDE/BFO,
``IN_BAD_MASK`` on BFI, ``CMD_IN_BAD`` on DCC) and reported on
``self.status`` / dedicated ``*_BAD`` outputs. Azeo's *GoodNonCascade*
maps to :attr:`BlockStatus.GOOD` here.
"""
from __future__ import annotations

from typing import Any

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block

# Azeo named state meaning "the block's configured Passive state"
PASSIVE_STATE = 255


# ═══════════════════════════════════════════════════════════════════════
#  BDE — Bi-directional Edge Trigger
# ═══════════════════════════════════════════════════════════════════════

@register_block
class BiDirectionalEdgeBlock(FunctionBlock):
    """Bi-directional Edge Trigger (BDE) — pulse on any state change.

    Each execution compares ``IN_D`` with the value latched on the
    previous execution. If it changed in *either* direction
    (False→True or True→False) ``OUT_D`` is True for exactly one scan;
    otherwise it is False. Used to trigger logic on any state change,
    e.g. equipment startup *or* shutdown.

    The pulse is one scan wide — do not consume it from a module running
    at a different scan rate; lengthen it with an off-delay timer
    (``TIMER_OFF``) or use it to set a latch (``SR_LATCH``).

    Status: ``OUT_D`` status = ``IN_D`` status, modelled by the optional
    ``IN_BAD`` discrete input, which drives ``self.status``.
    """
    block_type = "BDE"
    category = BlockCategory.LOGIC
    display_name = "Bi-dir Edge Trigger (BDE)"
    description = "One-scan True pulse on either a rising or a falling edge of IN_D"

    def __init__(self, instance_name: str = ""):
        self._prev = False
        self._primed = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN_D", DataType.BOOL, False, "Discrete input")
        self.add_input("IN_BAD", DataType.BOOL, False,
                       "IN_D signal status is Bad (status propagation)")
        self.add_output("OUT_D", DataType.BOOL, False,
                        "One-scan pulse on any IN_D transition")

    def get_config_schema(self):
        return {}

    def reset(self):
        super().reset()
        self._prev = False
        self._primed = False

    def execute(self, dt: float):
        cur = bool(self.get_input("IN_D"))
        # First execution latches the input without pulsing — Azeo
        # compares against the previous *execution*, and there is none.
        if not self._primed:
            self._primed = True
            self._prev = cur
            self.set_output("OUT_D", False)
        else:
            self.set_output("OUT_D", cur != self._prev)
            self._prev = cur
        self.status = (BlockStatus.BAD if bool(self.get_input("IN_BAD"))
                       else BlockStatus.GOOD)


# ═══════════════════════════════════════════════════════════════════════
#  BFI — Boolean Fan Input
# ═══════════════════════════════════════════════════════════════════════

@register_block
class BooleanFanInputBlock(FunctionBlock):
    """Boolean Fan Input (BFI) — pack 1..16 discretes into one integer.

    Outputs
        ``OUT_INT``   binary-weighted sum of the used inputs: IN_D1
                      weight 1, IN_D2 weight 2, IN_D3 weight 4, ...
        ``OUT_D``     logical OR of the used inputs.
        ``BCD``       binary-coded-decimal view for thumbwheel
                      interfaces (see below).
        ``FIRST_OUT`` snapshot of OUT_INT taken when OUT_INT goes from
                      zero to non-zero while ``ARM_TRAP`` is set —
                      first-out failure diagnosis.

    ``NUM_INPUTS`` stands in for the Azeo extensible input count
    (default 2, max 16); inputs above it are ignored.

    **BCD rule.** Inputs 1–4 form the ones digit (input 1 = least
    significant bit of the nibble), inputs 5–8 the tens digit, 9–12 the
    hundreds, 13–16 the thousands; any nibble greater than nine is
    limited to nine. (The spec also names a ten-thousands digit, which
    16 inputs cannot reach — noted in ``doc/AZEO_FUNCTION_BLOCKS.md``;
    the digit is simply never produced here.) The spec's own numeric
    worked example was a figure lost in the HTML→Markdown conversion, so
    the encoding of the ``BCD`` parameter itself is ambiguous:
    ``BCD_FORMAT`` selects ``DECIMAL`` (default — the decimal number the
    thumbwheel digits spell, e.g. digits 3 and 5 → 53) or ``PACKED``
    (the classic packed-BCD word, one digit per nibble, e.g. 0x53 = 83).

    ``RESET_IN`` non-zero clears ``FIRST_OUT``; the block returns the
    input to zero at end of scan. A reset does not re-arm the trap —
    since the trap only fires on a zero→non-zero transition of OUT_INT,
    FIRST_OUT necessarily stays zero until every input has gone to zero
    and one is set again.

    Status: ``OUT_D`` is Good when at least one used input is True with
    non-Bad status, else Bad if any used input is Bad (per
    ``IN_BAD_MASK``). ``OUT_INT``'s status is the worst input status and
    is published on ``OUT_INT_BAD`` (wire it to a BFO's ``IN_BAD``);
    ``FIRST_OUT``'s status is trapped with the value and published on
    ``FIRST_OUT_BAD``.
    """
    block_type = "BFI"
    category = BlockCategory.LOGIC
    display_name = "Boolean Fan Input (BFI)"
    description = "Packs up to 16 discretes into a binary-weighted integer + BCD + first-out"

    MAX_INPUTS = 16
    _BCD_FORMATS = ("DECIMAL", "PACKED")

    # BFI has no modes. BCD_FORMAT is not a Azeo named set — the manual's
    # worked example for the BCD parameter was a lost figure, so the two
    # readings are offered explicitly (see the class docstring).
    config_choices = {
        "BCD_FORMAT": _BCD_FORMATS,
    }

    def __init__(self, instance_name: str = ""):
        self._prev_nonzero = False
        self._first_out = 0
        self._first_out_bad = False
        super().__init__(instance_name)

    def _define_terminals(self):
        for n in range(1, self.MAX_INPUTS + 1):
            self.add_input(f"IN_D{n}", DataType.BOOL, False,
                           f"Discrete input {n} (weight {1 << (n - 1)})")
        self.add_input("ARM_TRAP", DataType.BOOL, True,
                       "Non-zero enables the first-out trap")
        self.add_input("RESET_IN", DataType.BOOL, False,
                       "Non-zero clears FIRST_OUT; cleared at end of scan")
        self.add_input("IN_BAD_MASK", DataType.INT, 0,
                       "Bit n-1 set = IN_Dn signal status is Bad")
        self.add_output("OUT_INT", DataType.INT, 0,
                        "Binary-weighted value of the input combination")
        self.add_output("OUT_D", DataType.BOOL, False,
                        "Logical OR of the discrete inputs")
        self.add_output("BCD", DataType.INT, 0,
                        "Binary-coded-decimal view of the inputs")
        self.add_output("FIRST_OUT", DataType.INT, 0,
                        "OUT_INT trapped on the zero -> non-zero transition")
        self.add_output("OUT_INT_BAD", DataType.BOOL, False,
                        "OUT_INT status is Bad (worst input status)")
        self.add_output("FIRST_OUT_BAD", DataType.BOOL, False,
                        "FIRST_OUT status trapped with the value")

    def get_config_schema(self):
        return {
            "NUM_INPUTS": (int, 2,
                "Number of discrete inputs used (Azeo extensible count, 1-16)"),
            "BCD_FORMAT": (str, "DECIMAL",
                "BCD encoding: DECIMAL (digit value, e.g. 53) or PACKED (nibbles, 0x53)"),
        }

    def reset(self):
        super().reset()
        self._prev_nonzero = False
        self._first_out = 0
        self._first_out_bad = False

    def _bcd(self, bits: list[bool]) -> int:
        """Digits of four inputs each, every nibble limited to nine."""
        fmt = str(self.config.params.get("BCD_FORMAT", "DECIMAL")).upper()
        value = 0
        for d in range(4):          # ones, tens, hundreds, thousands
            nibble = 0
            for k in range(4):
                idx = 4 * d + k
                if idx < len(bits) and bits[idx]:
                    nibble |= 1 << k
            digit = min(9, nibble)
            value += digit * (16 ** d if fmt == "PACKED" else 10 ** d)
        return value

    def execute(self, dt: float):
        p = self.config.params
        count = max(1, min(self.MAX_INPUTS, int(p.get("NUM_INPUTS", 2))))
        bad_mask = int(self.get_input("IN_BAD_MASK") or 0)

        bits = [bool(self.get_input(f"IN_D{n}")) for n in range(1, count + 1)]
        out_int = 0
        for i, b in enumerate(bits):
            if b:
                out_int |= 1 << i
        out_d = any(bits)

        used_mask = (1 << count) - 1
        any_bad = bool(bad_mask & used_mask)
        good_true = any(b and not (bad_mask & (1 << i)) for i, b in enumerate(bits))

        # First-out trap: fires on the zero -> non-zero transition of
        # OUT_INT while ARM_TRAP is set. RESET_IN clears the value but
        # leaves _prev_nonzero alone, so a reset does not re-arm.
        if bool(self.get_input("ARM_TRAP")) and out_int and not self._prev_nonzero:
            self._first_out = out_int
            self._first_out_bad = any_bad
        self._prev_nonzero = out_int != 0

        if self.get_input("RESET_IN"):
            self._first_out = 0
            self._first_out_bad = False
            self.inputs["RESET_IN"].value = False   # block clears it at end of scan

        self.set_output("OUT_INT", out_int)
        self.set_output("OUT_D", out_d)
        self.set_output("BCD", self._bcd(bits))
        self.set_output("FIRST_OUT", self._first_out)
        self.set_output("OUT_INT_BAD", any_bad)
        self.set_output("FIRST_OUT_BAD", self._first_out_bad)

        # OUT_D is GoodNonCascade whenever a non-Bad input is True.
        self.status = (BlockStatus.GOOD if good_true
                       else BlockStatus.BAD if any_bad
                       else BlockStatus.GOOD)


# ═══════════════════════════════════════════════════════════════════════
#  BFO — Boolean Fan Output
# ═══════════════════════════════════════════════════════════════════════

@register_block
class BooleanFanOutputBlock(FunctionBlock):
    """Boolean Fan Output (BFO) — unpack an integer into 1..16 discretes.

    ``OUT_D1`` is the least significant bit of ``IN_INT``, ``OUT_D2`` the
    next, and so on; e.g. ``IN_INT = 5153`` sets OUT_D1, OUT_D6, OUT_D11
    and OUT_D13. ``NUM_OUTPUTS`` stands in for the Azeo extensible
    output count (default 2, max 16); outputs above it stay False.

    Typical use: the receiving end of a BFI→BFO pair that compresses
    discrete traffic between controllers.

    Status: every ``OUT_Dn`` takes the ``IN_INT`` status, modelled by the
    ``IN_BAD`` input (wire a BFI's ``OUT_INT_BAD`` here).
    """
    block_type = "BFO"
    category = BlockCategory.LOGIC
    display_name = "Boolean Fan Output (BFO)"
    description = "Decodes a binary-weighted integer into up to 16 discrete outputs"

    MAX_OUTPUTS = 16

    def _define_terminals(self):
        self.add_input("IN_INT", DataType.INT, 0,
                       "Binary-weighted input value")
        self.add_input("IN_BAD", DataType.BOOL, False,
                       "IN_INT signal status is Bad (status propagation)")
        for n in range(1, self.MAX_OUTPUTS + 1):
            self.add_output(f"OUT_D{n}", DataType.BOOL, False,
                            f"Bit {n - 1} of IN_INT (weight {1 << (n - 1)})")

    def get_config_schema(self):
        return {
            "NUM_OUTPUTS": (int, 2,
                "Number of discrete outputs used (Azeo extensible count, 1-16)"),
        }

    def execute(self, dt: float):
        count = max(1, min(self.MAX_OUTPUTS,
                           int(self.config.params.get("NUM_OUTPUTS", 2))))
        raw = self.get_input("IN_INT")
        # IN_INT is an unsigned 32-bit value in Azeo; a float wire is
        # rounded, and negatives wrap through the 32-bit mask.
        value = int(round(float(raw or 0))) & 0xFFFFFFFF

        for n in range(1, self.MAX_OUTPUTS + 1):
            on = n <= count and bool(value & (1 << (n - 1)))
            self.set_output(f"OUT_D{n}", on)

        self.status = (BlockStatus.BAD if bool(self.get_input("IN_BAD"))
                       else BlockStatus.GOOD)


# ═══════════════════════════════════════════════════════════════════════
#  DCC — Discrete Control Condition
# ═══════════════════════════════════════════════════════════════════════

class _CondTimer:
    """On/off-delay debounce for one DCC condition (a_PRE_OUT_Dn).

    ``t_on`` is the elapsed time since the expression went True,
    ``t_off`` the elapsed time since it went False; Azeo's
    ``a_TIMERn`` reports whichever the configured Delay Off selects.
    """
    __slots__ = ("pre", "t_on", "t_off")

    def __init__(self):
        self.pre = False
        self.t_on = 0.0
        self.t_off = 0.0

    def step(self, raw: bool, dt: float, delay_on: float, delay_off: float) -> bool:
        if delay_off <= 0.0:
            # Timer = elapsed time since the condition went True; the
            # pre-out drops the moment the condition clears.
            if raw:
                self.t_on += dt
                self.pre = self.t_on >= delay_on
            else:
                self.t_on = 0.0
                self.pre = False
            self.t_off = 0.0
        else:
            if raw:
                self.t_off = 0.0            # re-occurrence resets the off timer
                if not self.pre:
                    self.t_on += dt
                    if self.t_on >= delay_on:
                        self.pre = True
                        self.t_on = 0.0
            else:
                self.t_on = 0.0
                if self.pre:
                    self.t_off += dt
                    if self.t_off >= delay_off:
                        self.pre = False
                        self.t_off = 0.0
        return self.pre

    def timer(self, delay_off: float) -> float:
        return self.t_on if delay_off <= 0.0 else self.t_off


def _dcc_delay_units() -> dict[str, str]:
    """Seconds units for every per-condition delay (doc §DCC parameters)."""
    units: dict[str, str] = {}
    for n in range(1, 17):
        units[f"I_DELAY_ON{n}"] = "s"
        units[f"I_DELAY_OFF{n}"] = "s"
    for n in range(1, 9):
        units[f"P_DELAY_ON{n}"] = "s"
        units[f"P_DELAY_OFF{n}"] = "s"
        units[f"F_DELAY_ON{n}"] = "s"
    return units


@register_block
class DiscreteControlConditionBlock(FunctionBlock):
    """Discrete Control Condition (DCC) — interlock / permissive / force
    conditions for an Enhanced Device Control (EDC) block.

    Evaluates up to **16 interlock**, **8 permissive** and **8 force
    setpoint** conditions. Each condition is an expression with a
    Delay On (interlocks and permissives also have a Delay Off) and a
    disable switch; interlocks additionally have Higher Managed
    (blocks online disabling), Reset Required (latching) and a named
    interlock state.

    Grouped outputs, wired straight into an EDC:
        ``I_OUT_D``     logical **NOR** of the per-condition interlock
                        bits — False while any interlock is active or
                        latched (the EDC interlock input).
        ``I_OUT``       ``I_STATEn`` of the lowest-numbered active or
                        latched condition; ``255`` (Passive) when none.
        ``I_OUT_INT``   binary-weighted combination of the interlock bits.
        ``I_FIRST_OUT`` bit value of the highest-priority (lowest
                        numbered) active condition, trapped while
                        ``ARM_TRAP`` is set.
        ``P_OUT_D``     ``I_OUT_D`` AND the NOR of the permissive bits.
        ``P_OUT_INT``   binary-weighted combination of the permissive bits.
        ``F_OUT_D``     one-shot pulse on the positive edge of any force
                        bit; ``F_OUT`` the ``F_STATEn`` that went active.
        ``F_OUT_INT``   binary-weighted combination of the force bits.

    Expressions are evaluated with the ACT block's safe-AST evaluator
    over the analog inputs ``IN1``..``IN8`` (plus boolean views
    ``b1``..``b8`` and the script-function namespace), which stands in
    for Azeo's cross-module parameter references. An empty expression
    is inactive. A name that cannot be resolved is a *configuration
    error* (Bad status, condition never True); any other evaluation
    failure is treated as a *read error* and follows
    ``OPT_ABORT_ON_READ_ERRORS`` / ``a_ERROR_OPT``.

    Per-condition values (``I_OUT_D_n``, ``I_L_OUT_Dn``, ``I_PRE_OUT_Dn``,
    ``I_TIMERn`` and the P_/F_ equivalents) are not terminals — 48 extra
    pins would be unusable on the canvas — but are readable as the
    Python lists ``i_out_d``, ``i_l_out_d``, ``i_pre_out_d``,
    ``i_timer``, ``p_out_d``, ``p_pre_out_d``, ``f_out_d``,
    ``f_pre_out_d``; the packed ``*_OUT_INT`` outputs carry the same
    bits on the wire.

    Omitted vs. Azeo: the event chronicle entries and the alarm
    subsystem (``DISABLE_ACT`` is published as a discrete output rather
    than raising a bypass alarm), and the module-level BAD_MASK /
    MERROR_MASK / MSTATUS_MASK diagnostics, which need controller
    infrastructure this repo does not model.

    Two errata in the source manual (see doc/AZEO_FUNCTION_BLOCKS.md):
    the spec page describes ``P_OUT_INT`` / ``F_OUT_INT`` as sums of the
    *interlock* bits — the parameter table's ``P_OUT_D_n`` / ``F_OUT_D_n``
    definitions are used here — and the parameter table prints
    ``I_DISABLEn`` as "(n = 1 through 8)" although interlock conditions
    run 1–16; all 16 disables are implemented.
    """
    block_type = "DCC"
    category = BlockCategory.LOGIC
    display_name = "Discrete Control Cond (DCC)"
    description = "16 interlock / 8 permissive / 8 force-setpoint conditions for an EDC block"

    NUM_INTERLOCK = 16
    NUM_PERMISSIVE = 8
    NUM_FORCE = 8
    NUM_EXPR_INPUTS = 8
    _ERROR_OPTS = ("FALSE", "TRUE", "HOLD")

    # DCC has no modes (doc: "does not support modes"). a_ERROR_OPT is the
    # doc's "False (default), True, or hold last value" read-error set.
    config_choices = {
        "I_ERROR_OPT": _ERROR_OPTS,
        "P_ERROR_OPT": _ERROR_OPTS,
        "F_ERROR_OPT": _ERROR_OPTS,
    }
    config_units = _dcc_delay_units()

    def __init__(self, instance_name: str = ""):
        self._i_t = [_CondTimer() for _ in range(self.NUM_INTERLOCK)]
        self._p_t = [_CondTimer() for _ in range(self.NUM_PERMISSIVE)]
        self._f_t = [_CondTimer() for _ in range(self.NUM_FORCE)]
        self._i_raw_last = [False] * self.NUM_INTERLOCK
        self._p_raw_last = [False] * self.NUM_PERMISSIVE
        self._f_raw_last = [False] * self.NUM_FORCE
        self._f_prev_bits = [False] * self.NUM_FORCE
        self._i_latch = [False] * self.NUM_INTERLOCK
        self._i_first_out = 0
        self._i_first_n = 0
        self._prev_i_int = 0
        self._f_out_state = PASSIVE_STATE
        # Public per-condition views (see class docstring)
        self.i_pre_out_d = [False] * self.NUM_INTERLOCK
        self.i_out_d = [False] * self.NUM_INTERLOCK
        self.i_l_out_d = [False] * self.NUM_INTERLOCK
        self.i_timer = [0.0] * self.NUM_INTERLOCK
        self.p_pre_out_d = [False] * self.NUM_PERMISSIVE
        self.p_out_d = [False] * self.NUM_PERMISSIVE
        self.p_timer = [0.0] * self.NUM_PERMISSIVE
        self.f_pre_out_d = [False] * self.NUM_FORCE
        self.f_out_d = [False] * self.NUM_FORCE
        self.f_timer = [0.0] * self.NUM_FORCE
        super().__init__(instance_name)

    # ── terminals ──────────────────────────────────────────────────
    def _define_terminals(self):
        for n in range(1, self.NUM_EXPR_INPUTS + 1):
            self.add_input(f"IN{n}", description=f"Expression operand {n}")
        self.add_input("CMD_IN_D", DataType.INT, PASSIVE_STATE,
                       "Command state fed back from the downstream EDC block")
        self.add_input("CMD_IN_BAD", DataType.BOOL, False,
                       "CMD_IN_D status is Bad (traps the first interlock immediately)")
        self.add_input("RESET_D", DataType.BOOL, False,
                       "Zero->non-zero clears I_FIRST_OUT and unlatches cleared interlocks")
        self.add_input("ARM_TRAP", DataType.BOOL, True,
                       "Non-zero enables the interlock first-out trap")

        self.add_output("I_OUT_D", DataType.BOOL, True,
                        "NOR of the interlock bits (EDC interlock input)")
        self.add_output("I_OUT", DataType.INT, PASSIVE_STATE,
                        "Named state of the lowest-numbered active interlock (255 = Passive)")
        self.add_output("I_OUT_INT", DataType.INT, 0,
                        "Binary-weighted combination of the interlock bits")
        self.add_output("I_FIRST_OUT", DataType.INT, 0,
                        "Bit value of the highest-priority active interlock condition")
        self.add_output("I_DESC", DataType.STRING, "",
                        "Description of the first trapped interlock condition")
        self.add_output("I_RESET_REQD", DataType.BOOL, False,
                        "No interlock active but a reset is still required")
        self.add_output("P_OUT_D", DataType.BOOL, True,
                        "I_OUT_D AND the NOR of the permissive bits (EDC permissive input)")
        self.add_output("P_OUT_INT", DataType.INT, 0,
                        "Binary-weighted combination of the permissive bits")
        self.add_output("F_OUT_D", DataType.BOOL, False,
                        "Pulse on the positive edge of any force-setpoint bit")
        self.add_output("F_OUT", DataType.INT, PASSIVE_STATE,
                        "Named state of the newly active force condition (holds last value)")
        self.add_output("F_OUT_INT", DataType.INT, 0,
                        "Binary-weighted combination of the force-setpoint bits")
        self.add_output("DISABLE_ACT", DataType.BOOL, False,
                        "Bypass alarm — OR of I_/P_/F_DISABLE_ACT")
        self.add_output("I_DISABLE_ACT", DataType.BOOL, False,
                        "OR of the effective I_DISABLEn flags")
        self.add_output("P_DISABLE_ACT", DataType.BOOL, False,
                        "OR of the P_DISABLEn flags")
        self.add_output("F_DISABLE_ACT", DataType.BOOL, False,
                        "OR of the F_DISABLEn flags")
        self.add_output("BLOCK_ERR", DataType.STRING, "",
                        "Active block error ('Configuration Error' or empty)")

    # ── config ─────────────────────────────────────────────────────
    def get_config_schema(self):
        s: dict[str, tuple] = {
            "I_USED_CND": (int, 0, "Number of interlock conditions evaluated (0-16)"),
            "P_USED_CND": (int, 0, "Number of permissive conditions evaluated (0-8)"),
            "F_USED_CND": (int, 0, "Number of force-setpoint conditions evaluated (0-8)"),
            "OPT_ABORT_ON_READ_ERRORS": (bool, False,
                "ALGO_OPTS AbortOnReadErrors: on a read error hold every "
                "a_PRE_OUT_Dn / a_OUT_D_n value and status"),
            "OPT_PERMIT": (bool, False,
                "ALGO_OPTS Permit: permissive expressions mean permit when True "
                "(unselected: True means prevent)"),
            "I_ERROR_OPT": (str, "FALSE",
                "Read-error value for I_PRE_OUT_Dn: FALSE / TRUE / HOLD"),
            "P_ERROR_OPT": (str, "FALSE",
                "Read-error value for P_PRE_OUT_Dn: FALSE / TRUE / HOLD"),
            "F_ERROR_OPT": (str, "FALSE",
                "Read-error value for F_PRE_OUT_Dn: FALSE / TRUE / HOLD"),
        }
        for n in range(1, self.NUM_INTERLOCK + 1):
            s[f"I_EXP{n}"] = (str, "", f"Interlock {n} expression (True = interlock)")
            s[f"I_DESC_{n}"] = (str, "", f"Interlock {n} description")
            s[f"I_DELAY_ON{n}"] = (float, 0.0, f"Interlock {n} delay on (s)")
            s[f"I_DELAY_OFF{n}"] = (float, 0.0, f"Interlock {n} delay off (s)")
            # (1) The source table prints I_DISABLEn as n = 1 through 8
            # while interlock conditions run 1-16; all 16 are provided.
            s[f"I_DISABLE{n}"] = (bool, False, f"Disable interlock {n}")
            s[f"I_HIGHER_MNG{n}"] = (bool, False,
                f"Interlock {n} higher managed — I_DISABLE{n} has no effect")
            s[f"I_RESET_REQD_{n}"] = (bool, False,
                f"Interlock {n} latches until RESET_D")
            s[f"I_STATE{n}"] = (int, PASSIVE_STATE,
                f"Interlock {n} named state (0-5 = EDC states, 255 = Passive)")
        for n in range(1, self.NUM_PERMISSIVE + 1):
            s[f"P_EXP{n}"] = (str, "", f"Permissive {n} expression")
            s[f"P_DESC{n}"] = (str, "", f"Permissive {n} description")
            s[f"P_DELAY_ON{n}"] = (float, 0.0, f"Permissive {n} delay on (s)")
            s[f"P_DELAY_OFF{n}"] = (float, 0.0, f"Permissive {n} delay off (s)")
            s[f"P_DISABLE{n}"] = (bool, False, f"Disable permissive {n}")
        for n in range(1, self.NUM_FORCE + 1):
            s[f"F_EXP{n}"] = (str, "", f"Force setpoint {n} expression")
            s[f"F_DESC{n}"] = (str, "", f"Force setpoint {n} description")
            s[f"F_DELAY_ON{n}"] = (float, 0.0, f"Force setpoint {n} delay on (s)")
            s[f"F_DISABLE{n}"] = (bool, False, f"Disable force setpoint {n}")
            s[f"F_STATE{n}"] = (int, PASSIVE_STATE,
                f"Force setpoint {n} named state (0-5 = EDC setpoints, 255 = Passive)")
        return s

    def reset(self):
        super().reset()
        for t in self._i_t + self._p_t + self._f_t:
            t.pre = False
            t.t_on = 0.0
            t.t_off = 0.0
        self._i_latch = [False] * self.NUM_INTERLOCK
        self._f_prev_bits = [False] * self.NUM_FORCE
        self._i_raw_last = [False] * self.NUM_INTERLOCK
        self._p_raw_last = [False] * self.NUM_PERMISSIVE
        self._f_raw_last = [False] * self.NUM_FORCE
        self._i_first_out = 0
        self._i_first_n = 0
        self._prev_i_int = 0
        self._f_out_state = PASSIVE_STATE

    # ── expression evaluation ──────────────────────────────────────
    def _namespace(self, dt: float) -> dict[str, Any]:
        from .action_block import SAFE_MATH_FUNCTIONS
        from .script_functions import get_script_functions
        ns: dict[str, Any] = dict(SAFE_MATH_FUNCTIONS)
        ns.update(get_script_functions())
        for n in range(1, self.NUM_EXPR_INPUTS + 1):
            v = self.get_input(f"IN{n}")
            ns[f"IN{n}"] = v
            ns[f"b{n}"] = bool(v)
        ns["x"] = ns["IN1"]
        ns["y"] = ns["IN2"]
        ns["z"] = ns["IN3"]
        ns["CMD_IN_D"] = self.get_input("CMD_IN_D")
        ns["dt"] = dt
        return ns

    def _eval(self, expr: str, ns: dict, last: bool,
              error_opt: str, abort: bool) -> tuple[bool, str]:
        """Return (raw, err) where err is '', 'CONFIG', 'READ' or 'HOLD'."""
        expr = str(expr or "").strip()
        if not expr:
            return False, ""
        from .action_block import _safe_eval
        try:
            return bool(_safe_eval(expr, ns)), ""
        except (SyntaxError, ValueError) as exc:
            # Unresolved names/functions and malformed text are Azeo's
            # "Bad: Configuration Error" — the expression is never True.
            if isinstance(exc, SyntaxError) or str(exc).startswith("Unknown"):
                return False, "CONFIG"
            return self._read_error(last, error_opt, abort)
        except Exception:
            return self._read_error(last, error_opt, abort)

    @staticmethod
    def _read_error(last: bool, error_opt: str, abort: bool) -> tuple[bool, str]:
        if abort:
            # AbortOnReadErrors: values and statuses stay unchanged.
            return last, "HOLD"
        opt = str(error_opt or "FALSE").upper()
        if opt == "TRUE":
            return True, "READ"
        if opt == "HOLD":
            return last, "READ"
        return False, "READ"

    # ── execution ──────────────────────────────────────────────────
    def execute(self, dt: float):
        p = self.config.params
        ns = self._namespace(dt)
        abort = bool(p.get("OPT_ABORT_ON_READ_ERRORS", False))
        permit = bool(p.get("OPT_PERMIT", False))
        n_i = max(0, min(self.NUM_INTERLOCK, int(p.get("I_USED_CND", 0))))
        n_p = max(0, min(self.NUM_PERMISSIVE, int(p.get("P_USED_CND", 0))))
        n_f = max(0, min(self.NUM_FORCE, int(p.get("F_USED_CND", 0))))
        config_err = False
        read_err = False

        # ── interlock conditions ───────────────────────────────────
        i_disable_act = False
        for k in range(self.NUM_INTERLOCK):
            n = k + 1
            if k >= n_i:
                self._i_t[k].pre = False
                self.i_pre_out_d[k] = False
                self.i_out_d[k] = False
                self.i_timer[k] = 0.0
                continue
            raw, err = self._eval(p.get(f"I_EXP{n}", ""), ns, self._i_raw_last[k],
                                  p.get("I_ERROR_OPT", "FALSE"), abort)
            config_err |= err == "CONFIG"
            read_err |= err == "READ"
            self._i_raw_last[k] = raw
            d_off = float(p.get(f"I_DELAY_OFF{n}", 0.0))
            if err != "HOLD":
                self._i_t[k].step(raw, dt, float(p.get(f"I_DELAY_ON{n}", 0.0)), d_off)
            pre = self._i_t[k].pre
            # An interlock is disabled only when I_DISABLEn is True AND
            # I_HIGHER_MNGn is False.
            disabled = (bool(p.get(f"I_DISABLE{n}", False))
                        and not bool(p.get(f"I_HIGHER_MNG{n}", False)))
            i_disable_act |= disabled
            self.i_pre_out_d[k] = pre
            self.i_out_d[k] = pre and not disabled
            self.i_timer[k] = self._i_t[k].timer(d_off)

        # Latching: with I_RESET_REQD_n set, I_L_OUT_Dn latches
        # I_OUT_D_n until RESET_D; otherwise it simply copies it.
        # The manual qualifies the latch with "CMD_IN_D differing from
        # I_OUT while CMD_IN_D status is not Bad" but never states the
        # comparison precisely, so the latch is taken unconditionally —
        # the fail-safe reading (an interlock cannot be lost).
        for k in range(self.NUM_INTERLOCK):
            if bool(p.get(f"I_RESET_REQD_{k + 1}", False)):
                if self.i_out_d[k]:
                    self._i_latch[k] = True
                self.i_l_out_d[k] = self._i_latch[k]
            else:
                self._i_latch[k] = False
                self.i_l_out_d[k] = self.i_out_d[k]

        i_int = 0
        for k in range(self.NUM_INTERLOCK):
            if self.i_l_out_d[k]:
                i_int |= 1 << k
        i_out_d = i_int == 0                       # logical NOR
        lowest = next((k + 1 for k in range(self.NUM_INTERLOCK)
                       if self.i_l_out_d[k]), 0)
        i_out = int(p.get(f"I_STATE{lowest}", PASSIVE_STATE)) if lowest else PASSIVE_STATE

        # First-out trap. Fires on the zero -> non-zero transition of
        # I_OUT_INT (BFI-style) while ARM_TRAP is set; a newly active
        # higher-priority condition with a different interlock state
        # supersedes the trapped one. Azeo delays the trap until the
        # EDC acknowledges via CMD_IN_D unless CMD_IN_D status is Bad,
        # in which case it traps immediately; the CMD handshake is not
        # modelled here, so the trap is always immediate.
        if bool(self.get_input("ARM_TRAP")) and i_int:
            if self._prev_i_int == 0:
                self._i_first_n = lowest
                self._i_first_out = 1 << (lowest - 1)
            elif lowest and lowest < self._i_first_n and \
                    int(p.get(f"I_STATE{lowest}", PASSIVE_STATE)) != \
                    int(p.get(f"I_STATE{self._i_first_n}", PASSIVE_STATE)):
                self._i_first_n = lowest
                self._i_first_out = 1 << (lowest - 1)
        self._prev_i_int = i_int

        # RESET_D: clears the trap and unlatches conditions that are
        # latched True with I_OUT_D_n now False. Returned to zero by the
        # block at end of scan; it does not re-arm the trap.
        if self.get_input("RESET_D"):
            self._i_first_out = 0
            self._i_first_n = 0
            for k in range(self.NUM_INTERLOCK):
                if self._i_latch[k] and not self.i_out_d[k] \
                        and bool(p.get(f"I_RESET_REQD_{k + 1}", False)):
                    self._i_latch[k] = False
                    self.i_l_out_d[k] = False
            self.inputs["RESET_D"].value = False
            i_int = 0
            for k in range(self.NUM_INTERLOCK):
                if self.i_l_out_d[k]:
                    i_int |= 1 << k
            i_out_d = i_int == 0
            lowest = next((k + 1 for k in range(self.NUM_INTERLOCK)
                           if self.i_l_out_d[k]), 0)
            i_out = (int(p.get(f"I_STATE{lowest}", PASSIVE_STATE))
                     if lowest else PASSIVE_STATE)
            self._prev_i_int = i_int

        reset_reqd = (not any(self.i_out_d[:n_i])) and any(self.i_l_out_d[:n_i])

        # ── permissive conditions ──────────────────────────────────
        p_disable_act = False
        p_int = 0
        p_block = False
        for k in range(self.NUM_PERMISSIVE):
            n = k + 1
            if k >= n_p:
                self._p_t[k].pre = False
                self.p_pre_out_d[k] = False
                self.p_out_d[k] = False
                self.p_timer[k] = 0.0
                continue
            raw, err = self._eval(p.get(f"P_EXP{n}", ""), ns, self._p_raw_last[k],
                                  p.get("P_ERROR_OPT", "FALSE"), abort)
            config_err |= err == "CONFIG"
            read_err |= err == "READ"
            self._p_raw_last[k] = raw
            d_off = float(p.get(f"P_DELAY_OFF{n}", 0.0))
            if err != "HOLD":
                self._p_t[k].step(raw, dt, float(p.get(f"P_DELAY_ON{n}", 0.0)), d_off)
            pre = self._p_t[k].pre
            disabled = bool(p.get(f"P_DISABLE{n}", False))
            p_disable_act |= disabled
            self.p_pre_out_d[k] = pre
            self.p_out_d[k] = pre and not disabled
            self.p_timer[k] = self._p_t[k].timer(d_off)
            if self.p_out_d[k]:
                p_int |= 1 << k
            # Without the Permit option a True condition prevents; with
            # it, a True condition permits, so an enabled condition that
            # is not True blocks. Disabled conditions never block.
            if permit:
                if not disabled and not pre:
                    p_block = True
            elif self.p_out_d[k]:
                p_block = True

        p_out_d = i_out_d and not p_block

        # ── force setpoint conditions ──────────────────────────────
        f_disable_act = False
        f_int = 0
        f_pulse = False
        f_new_lowest = 0
        for k in range(self.NUM_FORCE):
            n = k + 1
            if k >= n_f:
                self._f_t[k].pre = False
                self.f_pre_out_d[k] = False
                self.f_out_d[k] = False
                self.f_timer[k] = 0.0
                self._f_prev_bits[k] = False
                continue
            raw, err = self._eval(p.get(f"F_EXP{n}", ""), ns, self._f_raw_last[k],
                                  p.get("F_ERROR_OPT", "FALSE"), abort)
            config_err |= err == "CONFIG"
            read_err |= err == "READ"
            self._f_raw_last[k] = raw
            if err != "HOLD":
                # Force conditions have a delay on only (no delay off).
                self._f_t[k].step(raw, dt, float(p.get(f"F_DELAY_ON{n}", 0.0)), 0.0)
            pre = self._f_t[k].pre
            disabled = bool(p.get(f"F_DISABLE{n}", False))
            f_disable_act |= disabled
            self.f_pre_out_d[k] = pre
            self.f_out_d[k] = pre and not disabled
            self.f_timer[k] = self._f_t[k].timer(0.0)
            if self.f_out_d[k]:
                f_int |= 1 << k
            if self.f_out_d[k] and not self._f_prev_bits[k]:
                f_pulse = True
                if not f_new_lowest:
                    f_new_lowest = n
            self._f_prev_bits[k] = self.f_out_d[k]

        if f_new_lowest:
            # F_OUT updates only when a force condition becomes active;
            # otherwise it holds its last value.
            self._f_out_state = int(p.get(f"F_STATE{f_new_lowest}", PASSIVE_STATE))

        # ── publish ────────────────────────────────────────────────
        self.set_output("I_OUT_D", i_out_d)
        self.set_output("I_OUT", i_out)
        self.set_output("I_OUT_INT", i_int)
        self.set_output("I_FIRST_OUT", self._i_first_out)
        self.set_output("I_DESC", str(p.get(f"I_DESC_{self._i_first_n}", ""))
                        if self._i_first_n else "")
        self.set_output("I_RESET_REQD", reset_reqd)
        self.set_output("P_OUT_D", p_out_d)
        self.set_output("P_OUT_INT", p_int)
        self.set_output("F_OUT_D", f_pulse)
        self.set_output("F_OUT", self._f_out_state)
        self.set_output("F_OUT_INT", f_int)
        self.set_output("I_DISABLE_ACT", i_disable_act)
        self.set_output("P_DISABLE_ACT", p_disable_act)
        self.set_output("F_DISABLE_ACT", f_disable_act)
        self.set_output("DISABLE_ACT", i_disable_act or p_disable_act or f_disable_act)
        self.set_output("BLOCK_ERR", "Configuration Error" if config_err else "")

        self.status = (BlockStatus.BAD if (config_err or read_err)
                       else BlockStatus.GOOD)
