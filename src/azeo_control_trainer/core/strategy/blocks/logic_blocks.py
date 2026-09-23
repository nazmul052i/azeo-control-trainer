"""Logic function blocks — AND, OR, NOT, XOR, NAND, NOR, timers, counters, etc.

Signal quality
--------------
Terminals carry a :class:`~azeo_control_trainer.core.strategy.model.terminal.Quality` next to
their value, so the status rules the Azeo manual states for these blocks are
implementable rather than approximated:

* gates (§3193/§3214) — ``OUT_D`` status is the worst input status **unless** a
  *deciding* input (False for AND/NAND, True for OR/NOR) is non-Bad, in which
  case the result is decided regardless of the other inputs and the status is
  GoodNonCascade (our :data:`Quality.GOOD`);
* ``NOT``, the edge triggers and the timers (§3235, §3279, §3303, §2778,
  §2804) — output status = input status;
* the flip-flops (§3327, §3358) — output status = worst of SET / RESET_IN;
* ``CTR`` (§2702) — ``OUT_D`` status = ``IN_D`` status; ``RESET_IN`` status
  does not affect it;
* ``TP`` (§2858) — ``OUT_D`` is always GoodNonCascade: input status does *not*
  propagate through a pulse;
* ``CMP`` (§2502) — worst of the inputs feeding each output, with Uncertain
  treated as Good, so only Bad degrades a result.

Statuses default to Good everywhere, so none of this changes a value.
"""
from __future__ import annotations
import time as _time
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality


#: Azeo extensible gates accept 2–16 inputs (spec §3193/§3214).
_MAX_GATE_INPUTS = 16

#: Alias map shared by every gate: strategies authored with IEC-style pin
#: names (IN1/IN2/OUT) predate the Azeo-style IN_D*/OUT_D naming, and
#: ``add_wire`` would drop those wires silently without these.
_GATE_ALIASES = {"OUT": "OUT_D"}
for _i in range(1, _MAX_GATE_INPUTS + 1):
    _GATE_ALIASES[f"IN{_i}"] = f"IN_D{_i}"
    _GATE_ALIASES[f"IN_{_i}"] = f"IN_D{_i}"
del _i


class _ExtensibleGate(FunctionBlock):
    """Shared machinery for the extensible boolean gates.

    Azeo gates are *extensible*: 2 to 16 discrete inputs, and an input
    that is not used simply does not exist.  This block declares all 16
    up front with the gate's neutral value (True for AND/NAND, False for
    OR/NOR) and evaluates

        inputs 1..NOF_INPUTS  ∪  every wired input

    so an unused extra input can never change the result, a wired one
    always participates, and ``NOF_INPUTS`` (default 3, the historical
    fan-in) only controls how many pins are drawn.  Evaluating the three
    original inputs of an untouched block gives exactly the previous
    answer.
    """

    category = BlockCategory.LOGIC
    terminal_aliases = _GATE_ALIASES
    _NEUTRAL = True          # value of an unused input for this gate
    _OUT_DEFAULT = False

    def _define_terminals(self):
        for i in range(1, _MAX_GATE_INPUTS + 1):
            t = self.add_input(f"IN_D{i}", DataType.BOOL, self._NEUTRAL,
                               f"Input {i}")
            if i > 3:
                # Keep the canvas footprint of the historical 3-input gate;
                # raise NOF_INPUTS (or unhide the pin) to use more.
                t.hidden = True
        self.add_output("OUT_D", DataType.BOOL, self._OUT_DEFAULT, "Gate result")

    def get_config_schema(self):
        return {
            "NOF_INPUTS": (int, 3,
                           "Number of inputs shown/evaluated (2–16); wired "
                           "inputs beyond this always participate"),
        }

    def _apply_config(self):
        self._sync_visible_inputs()

    def _nof_inputs(self) -> int:
        try:
            n = int(self.config.params.get("NOF_INPUTS", 3))
        except (TypeError, ValueError):
            n = 3
        return max(2, min(n, _MAX_GATE_INPUTS))

    def _sync_visible_inputs(self):
        """Show inputs 1..NOF_INPUTS plus every wired one.

        Only re-applied when the pin count or the wiring actually changes,
        so an engineer who unhides a spare pin in Control Designer does not
        have it hidden again on the next scan.
        """
        n = self._nof_inputs()
        sig = (n, tuple(self.inputs[f"IN_D{i}"].connected
                        for i in range(1, _MAX_GATE_INPUTS + 1)))
        if sig == getattr(self, "_vis_sig", None):
            return
        self._vis_sig = sig
        for i in range(1, _MAX_GATE_INPUTS + 1):
            t = self.inputs.get(f"IN_D{i}")
            if t is not None:
                t.hidden = i > n and not t.connected

    def _active_terminals(self) -> list:
        """The evaluated input terminals (declared count ∪ wired)."""
        n = self._nof_inputs()
        terms = []
        for i in range(1, _MAX_GATE_INPUTS + 1):
            t = self.inputs.get(f"IN_D{i}")
            if t is None:
                continue
            if i <= n or t.connected:
                terms.append(t)
        return terms

    def _active_values(self) -> list:
        """Values of the evaluated inputs (declared count ∪ wired)."""
        return [t.value for t in self._active_terminals()]

    def _propagate_gate_status(self, deciding: bool):
        """Azeo gate status rule (spec §3193 / §3214).

        ``OUT_D`` status is the worst status among the evaluated inputs —
        *unless* at least one input carries the **deciding** value (False for
        AND/NAND, True for OR/NOR) with a non-Bad status.  That single input
        settles the result on its own, so the answer is trustworthy however
        bad the others are, and Azeo reports GoodNonCascade (Good here).

        An unused input rests at the gate's neutral value, which is never the
        deciding one, so spare pins can never fake a good answer.
        """
        terms = self._active_terminals()
        if any(bool(t.value) is deciding and t.status is not Quality.BAD
               for t in terms):
            self.set_output_status("OUT_D", Quality.GOOD)
            return Quality.GOOD
        worst = max((t.status for t in terms), key=lambda q: q.value) \
            if terms else Quality.GOOD
        self.set_output_status("OUT_D", worst)
        return worst


@register_block
class ANDBlock(_ExtensibleGate):
    block_type = "AND"
    display_name = "AND"
    description = "OUT_D = AND of every used input (extensible, 2–16)"
    _NEUTRAL = True

    def execute(self, dt: float):
        self._sync_visible_inputs()
        self.set_output("OUT_D", all(bool(v) for v in self._active_values()))
        self._propagate_gate_status(False)      # a good False decides an AND


@register_block
class ORBlock(_ExtensibleGate):
    block_type = "OR"
    display_name = "OR"
    description = "OUT_D = OR of every used input (extensible, 2–16)"
    _NEUTRAL = False

    def execute(self, dt: float):
        self._sync_visible_inputs()
        self.set_output("OUT_D", any(bool(v) for v in self._active_values()))
        self._propagate_gate_status(True)       # a good True decides an OR


@register_block
class NOTBlock(FunctionBlock):
    block_type = "NOT"
    category = BlockCategory.LOGIC
    display_name = "NOT"
    description = "OUT_D = NOT IN_D"

    def _define_terminals(self):
        self.add_input("IN_D", DataType.BOOL, False, "Input")
        self.add_output("OUT_D", DataType.BOOL, True, "Inverted output")

    def execute(self, dt: float):
        self.set_output("OUT_D", not bool(self.get_input("IN_D")))
        # Spec §3235: output status = input status.
        self.set_output_status("OUT_D", self.input_status("IN_D"))


@register_block
class TimerOnBlock(FunctionBlock):
    """On-delay timer — Azeo OND (spec §2804).

    A False input passes straight through; a True input reaches the output
    only once it has been held for ``PT`` seconds.

    ``ET`` keeps running past ``PT`` while the input stays True — that is the
    spec's ``ELAPSED_TIMER``, "elapsed time since the True discrete input
    value became active", which OND never describes as freezing at
    ``TIME_DURATION``. It is zeroed when the input goes False, as OND
    requires.
    """
    block_type = "TIMER_ON"
    category = BlockCategory.LOGIC
    display_name = "Timer On-Delay"
    description = "Output turns on after input held true for preset time"
    terminal_aliases = {"Q": "OUT", "IN_D": "IN", "ET_OUT": "ET"}
    config_aliases = {"preset": "PT", "PRESET": "PT", "pt": "PT"}

    def __init__(self, instance_name=""):
        self._elapsed = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", DataType.BOOL, False, "Input signal")
        self.add_output("OUT", DataType.BOOL, False, "Delayed output")
        self.add_output("ET", description="Elapsed time [s]")

    def get_config_schema(self):
        return {"PT": (float, 5.0, "Preset time [s]")}

    def execute(self, dt: float):
        pt = self.config.params.get("PT", 5.0)
        if self.get_input("IN"):
            self._elapsed += dt
            if self._elapsed >= pt:
                self.set_output("OUT", True)
        else:
            self._elapsed = 0.0
            self.set_output("OUT", False)
        self.set_output("ET", self._elapsed)
        # Spec §2804: output status = input status.
        self.set_output_status("OUT", self.input_status("IN"))

    def reset(self):
        super().reset()
        self._elapsed = 0.0


@register_block
class TimerOffBlock(FunctionBlock):
    block_type = "TIMER_OFF"
    category = BlockCategory.LOGIC
    display_name = "Timer Off-Delay"
    description = "Output stays on for preset time after input goes false"
    terminal_aliases = {"Q": "OUT", "IN_D": "IN", "ET_OUT": "ET"}
    config_aliases = {"preset": "PT", "PRESET": "PT", "pt": "PT"}

    def __init__(self, instance_name=""):
        self._elapsed = 0.0
        self._triggered = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", DataType.BOOL, False, "Input signal")
        self.add_output("OUT", DataType.BOOL, False, "Delayed output")
        self.add_output("ET", description="Elapsed time [s]")

    def get_config_schema(self):
        return {"PT": (float, 5.0, "Preset time [s]")}

    def execute(self, dt: float):
        pt = self.config.params.get("PT", 5.0)
        if self.get_input("IN"):
            self._elapsed = 0.0
            self._triggered = True
            self.set_output("OUT", True)
        elif self._triggered:
            self._elapsed += dt
            if self._elapsed >= pt:
                self.set_output("OUT", False)
                self._triggered = False
            else:
                self.set_output("OUT", True)
        self.set_output("ET", self._elapsed)
        # Spec §2778: output status = input status.
        self.set_output_status("OUT", self.input_status("IN"))

    def reset(self):
        super().reset()
        self._elapsed = 0.0
        self._triggered = False


@register_block
class ComparatorBlock(FunctionBlock):
    """Comparator — Azeo CMP (spec §2490).

    Azeo compares ``DISC_VAL`` against ``COMP_VAL1`` and publishes **four
    results simultaneously** (``LT`` / ``GT`` / ``EQ`` / ``NEQ``), plus
    ``IN_RANGE`` for the ``COMP_VAL1``–``COMP_VAL2`` band — and either bound
    may be the larger, so ``DISC_VAL`` 2.25 with bounds 15.0 / 1.0 is in
    range.  This block only ever emitted one ``OUT``, selected by the ``OP``
    config, so a strategy needing two comparisons had to instantiate two
    blocks (C-1) and had no range test at all (C-2).

    The five Azeo outputs are now computed unconditionally every scan,
    *alongside* the original ``OUT``/``OP``/``HYST`` behaviour, which is
    unchanged and still what existing wires see.

    ``HYST`` is a platform extra with no Azeo counterpart: it is a
    hysteresis band on the latched ``OUT`` only.  It previously applied to
    ``GT``/``LT``/``EQ`` but was silently dropped for ``GE``/``LE`` (C-3) —
    those two now use the same on/off thresholds, which with the default
    ``HYST = 0.0`` is exactly the old bare comparison.  The Azeo outputs
    are always raw comparisons, as the spec defines them.

    ``LT``/``GT``/``EQ``/``NEQ`` status is the worst of ``DISC_VAL`` and
    ``COMP_VAL1``, ``IN_RANGE`` also takes in ``COMP_VAL2``, and Uncertain
    counts as Good — only Bad degrades an output (spec §2502).
    """
    block_type = "COMPARATOR"
    category = BlockCategory.LOGIC
    display_name = "Comparator"
    description = "Azeo CMP: LT/GT/EQ/NEQ/IN_RANGE, plus OUT selected by OP"
    # Azeo pin names for the two comparison inputs (C-5). ``IN1``/``IN2``
    # stay the real terminals — nothing is renamed.
    terminal_aliases = {"DISC_VAL": "IN1", "COMP_VAL1": "IN2"}
    config_choices = {"OP": ("GT", "LT", "GE", "LE", "EQ")}

    def __init__(self, instance_name=""):
        self._state = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN1", description="Input 1 (Azeo DISC_VAL)")
        self.add_input("IN2", description="Input 2 / threshold (Azeo COMP_VAL1)")
        self.add_input("COMP_VAL2",
                       description="Second range bound for IN_RANGE (unused while 0)")
        self.add_output("OUT", DataType.BOOL, False,
                        "Comparison result selected by OP (with HYST)")
        self.add_output("LT", DataType.BOOL, False, "DISC_VAL < COMP_VAL1")
        self.add_output("GT", DataType.BOOL, False, "DISC_VAL > COMP_VAL1")
        self.add_output("EQ", DataType.BOOL, False, "DISC_VAL = COMP_VAL1")
        self.add_output("NEQ", DataType.BOOL, False, "DISC_VAL <> COMP_VAL1")
        self.add_output("IN_RANGE", DataType.BOOL, False,
                        "DISC_VAL within COMP_VAL1..COMP_VAL2 (either order)")

    def get_config_schema(self):
        return {
            "HYST": (float, 0.0, "Hysteresis deadband (applies to OUT only)"),
            "OP": (str, "GT", "Operator for OUT: GT, LT, GE, LE, EQ"),
        }

    def _range_active(self) -> bool:
        """Is COMP_VAL2 in play?

        ``IN_RANGE`` needs a second bound, and an unwired ``COMP_VAL2`` sitting
        at its 0.0 default would otherwise make every block report the band
        ``COMP_VAL1..0``.  So the range test engages only once the pin is
        actually wired (or forced/driven to a non-default value); until then
        ``IN_RANGE`` reads False.
        """
        t = self.inputs.get("COMP_VAL2")
        return bool(t is not None and (t.connected or t.value != t.default_value))

    def execute(self, dt: float):
        a, b = self.get_input("IN1"), self.get_input("IN2")
        hyst = self.config.params.get("HYST", 0.0)
        op = self.config.params.get("OP", "GT")

        if op == "GT":
            on_thresh, off_thresh = b, b - hyst
            self._state = a > on_thresh if not self._state else a > off_thresh
        elif op == "LT":
            on_thresh, off_thresh = b, b + hyst
            self._state = a < on_thresh if not self._state else a < off_thresh
        elif op == "GE":
            # C-3: GE/LE used to ignore HYST entirely. With HYST=0 the two
            # thresholds coincide and this is the previous bare comparison.
            on_thresh, off_thresh = b, b - hyst
            self._state = a >= on_thresh if not self._state else a >= off_thresh
        elif op == "LE":
            on_thresh, off_thresh = b, b + hyst
            self._state = a <= on_thresh if not self._state else a <= off_thresh
        elif op == "EQ":
            self._state = abs(a - b) <= hyst

        self.set_output("OUT", self._state)

        # Azeo CMP outputs (§2497) — raw comparisons, all four every scan.
        self.set_output("LT", bool(a < b))
        self.set_output("GT", bool(a > b))
        self.set_output("EQ", bool(a == b))
        self.set_output("NEQ", bool(a != b))

        # Range check (§2498): bounds may be given in either order.
        ranged = self._range_active()
        if ranged:
            c2 = self.get_input("COMP_VAL2")
            lo, hi = (b, c2) if b <= c2 else (c2, b)
            self.set_output("IN_RANGE", bool(lo <= a <= hi))
        else:
            self.set_output("IN_RANGE", False)

        # Status (§2502): Uncertain is treated as Good, so only Bad degrades.
        bad_primary = Quality.BAD in (self.input_status("IN1"),
                                      self.input_status("IN2"))
        primary = Quality.BAD if bad_primary else Quality.GOOD
        for name in ("OUT", "LT", "GT", "EQ", "NEQ"):
            self.set_output_status(name, primary)
        in_range_bad = bad_primary or (
            ranged and self.input_status("COMP_VAL2") is Quality.BAD)
        self.set_output_status(
            "IN_RANGE", Quality.BAD if in_range_bad else Quality.GOOD)

    def reset(self):
        # C-4: the hysteresis latch used to survive reset(), so a restarted
        # strategy could come up holding a stale True.
        super().reset()
        self._state = False


@register_block
class SRLatchBlock(FunctionBlock):
    block_type = "SR_LATCH"
    category = BlockCategory.LOGIC
    display_name = "SR Latch"
    description = "Set-Reset latch (Set dominant)"
    # Azeo names the flip-flop pins SET / RESET_IN (spec §3327).
    terminal_aliases = {"SET": "SET_D", "RESET_IN": "RESET_D",
                        "RESET": "RESET_D", "Q": "OUT_D",
                        "NOT_OUT_D": "OUT_D_INV"}

    def __init__(self, instance_name=""):
        self._q = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("SET_D", DataType.BOOL, False, "Set input")
        self.add_input("RESET_D", DataType.BOOL, False, "Reset input")
        self.add_output("OUT_D", DataType.BOOL, False, "Latch output")
        self.add_output("OUT_D_INV", DataType.BOOL, True, "Inverted output")

    def execute(self, dt: float):
        s = self.get_input("SET_D")
        r = self.get_input("RESET_D")
        if s:
            self._q = True
        elif r:
            self._q = False
        self.set_output("OUT_D", self._q)
        self.set_output("OUT_D_INV", not self._q)
        # Spec §3327: output status = worst status among the inputs. A latch
        # driven by a Bad set/reset is holding a state nobody can vouch for.
        self.propagate_status(inputs=("SET_D", "RESET_D"))

    def reset(self):
        super().reset()
        self._q = False


@register_block
class RSLatchBlock(FunctionBlock):
    """RS Latch — Reset-dominant flip-flop (Azeo RS_FF)."""
    block_type = "RS_LATCH"
    category = BlockCategory.LOGIC
    display_name = "RS Latch"
    description = "Reset-dominant latch (Reset takes priority)"
    # Azeo names the flip-flop pins SET / RESET_IN (spec §3358).
    terminal_aliases = {"SET": "SET_D", "RESET_IN": "RESET_D",
                        "RESET": "RESET_D", "Q": "OUT_D",
                        "NOT_OUT_D": "OUT_D_INV"}

    def __init__(self, instance_name=""):
        self._q = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("SET_D", DataType.BOOL, False, "Set input")
        self.add_input("RESET_D", DataType.BOOL, False, "Reset input")
        self.add_output("OUT_D", DataType.BOOL, False, "Latch output")
        self.add_output("OUT_D_INV", DataType.BOOL, True, "Inverted output")

    def execute(self, dt: float):
        s = self.get_input("SET_D")
        r = self.get_input("RESET_D")
        # Reset dominant: reset wins when both are true
        if r:
            self._q = False
        elif s:
            self._q = True
        self.set_output("OUT_D", self._q)
        self.set_output("OUT_D_INV", not self._q)
        # Spec §3358: output status = worst status among the inputs.
        self.propagate_status(inputs=("SET_D", "RESET_D"))

    def reset(self):
        super().reset()
        self._q = False


@register_block
class PosEdgeBlock(FunctionBlock):
    """Positive Edge Trigger — pulses OUT_D for one scan on rising edge.

    The first execution only *primes* the block: an edge is a change
    between two consecutive executions, and before the first one there is
    no previous execution to compare against. So scan 1 latches IN_D and
    holds OUT_D False whatever the input is (same rule as the Azeo BDE
    block); the first real edge can be detected from scan 2 onward.
    """
    block_type = "POS_EDGE"
    category = BlockCategory.LOGIC
    display_name = "Positive Edge"
    description = "OUT_D pulses true on rising edge of IN_D"

    def __init__(self, instance_name=""):
        self._prev = None      # None = not primed; seeded on the first scan
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN_D", DataType.BOOL, False, "Input signal")
        self.add_output("OUT_D", DataType.BOOL, False, "Edge pulse")

    def execute(self, dt: float):
        curr = bool(self.get_input("IN_D"))
        if self._prev is None:
            self.set_output("OUT_D", False)
        else:
            self.set_output("OUT_D", curr and not self._prev)
        self._prev = curr
        # Spec §3279: output status = input status.
        self.set_output_status("OUT_D", self.input_status("IN_D"))

    def reset(self):
        super().reset()
        self._prev = None


@register_block
class NegEdgeBlock(FunctionBlock):
    """Negative Edge Trigger — pulses OUT_D for one scan on falling edge.

    Like POS_EDGE / BDE, the first execution only primes the block from
    the observed input. Seeding ``_prev`` to a constant True instead (the
    former behaviour) made the very first scan report a falling edge that
    never happened whenever IN_D started False — a spurious trip pulse on
    every module download.
    """
    block_type = "NEG_EDGE"
    category = BlockCategory.LOGIC
    display_name = "Negative Edge"
    description = "OUT_D pulses true on falling edge of IN_D"

    def __init__(self, instance_name=""):
        self._prev = None      # None = not primed; seeded on the first scan
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN_D", DataType.BOOL, False, "Input signal")
        self.add_output("OUT_D", DataType.BOOL, False, "Edge pulse")

    def execute(self, dt: float):
        curr = bool(self.get_input("IN_D"))
        if self._prev is None:
            self.set_output("OUT_D", False)
        else:
            self.set_output("OUT_D", not curr and self._prev)
        self._prev = curr
        # Spec §3303: output status = input status.
        self.set_output_status("OUT_D", self.input_status("IN_D"))

    def reset(self):
        super().reset()
        self._prev = None


# ──────────────────────────────────────────────────────────────────
# Tier 1 — XOR, Counter, Pulse
# ──────────────────────────────────────────────────────────────────

@register_block
class XORBlock(FunctionBlock):
    """Exclusive OR gate (Honeywell XOR)."""
    block_type = "XOR"
    category = BlockCategory.LOGIC
    display_name = "XOR"
    description = "OUT_D = IN_D1 XOR IN_D2"
    # Strategies authored with IEC-style pin names (IN1/IN2/OUT) predate the
    # Azeo-style IN_D*/OUT_D naming; accept both so their wires survive load.
    terminal_aliases = {
        "IN1": "IN_D1", "IN2": "IN_D2", "IN3": "IN_D3",
        "IN_1": "IN_D1", "IN_2": "IN_D2", "IN_3": "IN_D3",
        "OUT": "OUT_D",
    }

    def _define_terminals(self):
        self.add_input("IN_D1", DataType.BOOL, False, "Input 1")
        self.add_input("IN_D2", DataType.BOOL, False, "Input 2")
        self.add_output("OUT_D", DataType.BOOL, False, "XOR result")

    def execute(self, dt: float):
        self.set_output("OUT_D", bool(self.get_input("IN_D1")) != bool(self.get_input("IN_D2")))
        # No Azeo XOR to quote: both inputs always decide the answer, so the
        # generic computation-block rule (worst of the inputs) applies.
        self.propagate_status(inputs=("IN_D1", "IN_D2"), outputs=("OUT_D",))


@register_block
class CounterBlock(FunctionBlock):
    """Up/Down counter (Honeywell CTU/CTD/CTUD, Azeo CTR §2702).

    Counts events on CU (up) and CD (down).  The Azeo CTR is a single
    input with a direction selector; this block keeps its two inputs (8
    terminals are wired in shipped strategies' block palettes) and adds
    the Azeo parameters on top:

    * ``COUNTER_TYPE`` — 0 = up counter (RESET → 0), 1 = down counter
      (RESET → PRESET), per Azeo.  Default 0 = today's behaviour.
    * ``DETECT_TYPE`` — 0 = count on the rising edge, 1 = count every
      scan the input is True.  Default 0 = today's behaviour.
    * ``HOLD_AT_PRESET`` — Azeo holds COUNT once the trip value is
      reached (``COUNT ≥ PRESET`` up, ``COUNT ≤ 0`` down).  Default False
      keeps the previous free-running count.

    Any change to COUNTER_TYPE / DETECT_TYPE self-resets the counter for
    one scan, as the spec requires.

    ``QD`` ("reached zero") is a platform extra, and on a fresh up counter it
    reads True because the count starts at 0 — surprising when QD gates
    logic. ``QD_ARM`` (default False = the historical behaviour) suppresses
    that: with it set, QD only asserts once the count has been above zero and
    come back down, and a RESET disarms it again. Azeo's single ``OUT_D``
    is unaffected either way — it already tracks the configured direction.

    ``OUT_D``/``QU``/``QD`` status follows ``IN_D`` (``CU``); ``RESET_IN``
    status does not affect the output, per spec §2702.
    """
    block_type = "COUNTER"
    category = BlockCategory.LOGIC
    display_name = "Counter"
    description = "Up/down counter with preset, reset and Azeo CTR options"
    # Azeo spellings: IN_D (single input), RESET_IN, COUNT, PRESET.
    terminal_aliases = {"IN_D": "CU", "IN_D1": "CU", "RESET_IN": "RESET",
                        "COUNT": "CV"}
    config_aliases = {"PRESET": "PV"}

    def __init__(self, instance_name=""):
        self._count = 0
        self._prev_cu = False
        self._prev_cd = False
        self._prev_types: tuple[int, int] | None = None
        self._qd_armed = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("CU", DataType.BOOL, False, "Count up event (IN_D)")
        self.add_input("CD", DataType.BOOL, False, "Count down event")
        self.add_input("RESET", DataType.BOOL, False, "Reset the counter")
        self.add_input("LOAD", DataType.BOOL, False, "Load preset value")
        self.add_output("CV", DataType.INT, 0, "Counter value (COUNT)")
        self.add_output("QU", DataType.BOOL, False, "Reached preset (CV >= PV)")
        self.add_output("QD", DataType.BOOL, False, "Reached zero (CV <= 0)")
        self.add_output("OUT_D", DataType.BOOL, False,
                        "Trip: QU for an up counter, QD for a down counter")

    def get_config_schema(self):
        return {
            "PV": (int, 100, "Preset value (Azeo PRESET)"),
            "COUNTER_TYPE": (int, 0, "0 = up counter, 1 = down counter"),
            "DETECT_TYPE": (int, 0, "0 = count on rising edge, 1 = count while True"),
            "HOLD_AT_PRESET": (bool, False,
                               "Hold COUNT at the trip value instead of counting past it"),
            "QD_ARM": (bool, False,
                       "Suppress QD until the count has been above zero "
                       "(stops a fresh up counter reading QD=True)"),
        }

    def _reset_value(self, pv: int) -> int:
        """RESET target: 0 for an up counter, PRESET for a down counter."""
        return pv if int(self.config.params.get("COUNTER_TYPE", 0)) else 0

    def execute(self, dt: float):
        p = self.config.params
        pv = int(p.get("PV", 100))
        down = bool(int(p.get("COUNTER_TYPE", 0)))
        level = bool(int(p.get("DETECT_TYPE", 0)))
        hold = bool(p.get("HOLD_AT_PRESET", False))

        cu = bool(self.get_input("CU"))
        cd = bool(self.get_input("CD"))

        # Spec §2716: changing COUNTER_TYPE or DETECT_TYPE asserts
        # RESET_IN internally for one scan.
        types = (int(down), int(level))
        type_change = self._prev_types is not None and types != self._prev_types
        self._prev_types = types

        if self.get_input("RESET") or type_change:
            self._count = self._reset_value(pv)
            self._qd_armed = False
        elif self.get_input("LOAD"):
            self._count = pv
        else:
            cu_evt = cu if level else (cu and not self._prev_cu)
            cd_evt = cd if level else (cd and not self._prev_cd)
            if cu_evt:
                self._count += -1 if down else 1
            if cd_evt:
                self._count -= -1 if down else 1
            if hold:
                # Azeo: COUNT holds once the trip value is reached.
                if down:
                    self._count = max(0, self._count)
                else:
                    self._count = min(pv, self._count)

        # Edge memory is updated every scan, including while RESET/LOAD is
        # held — otherwise an edge that arrived during the reset was banked
        # and counted one scan after the reset cleared.
        self._prev_cu = cu
        self._prev_cd = cd

        if self._count > 0:
            self._qd_armed = True

        qu = self._count >= pv
        qd = self._count <= 0
        if bool(p.get("QD_ARM", False)) and not self._qd_armed:
            qd = False              # CTR-4: never trip QD on the way up from 0
        self.set_output("CV", self._count)
        self.set_output("QU", qu)
        self.set_output("QD", qd)
        self.set_output("OUT_D", qd if down else qu)
        # Spec §2702: OUT_D status = IN_D status; RESET_IN status is ignored.
        cu_status = self.input_status("CU")
        for name in ("CV", "QU", "QD", "OUT_D"):
            self.set_output_status(name, cu_status)

    def reset(self):
        super().reset()
        self._count = self._reset_value(int(self.config.params.get("PV", 100)))
        self._prev_cu = False
        self._prev_cd = False
        self._prev_types = None
        self._qd_armed = False


@register_block
class PulseBlock(FunctionBlock):
    """Pulse timer / one-shot — Azeo Timed Pulse (TP).

    A False→True transition of IN sets OUT True and holds it True for PT
    seconds, even if IN goes back False. The block is **retriggerable**:
    per the TP spec ("Any 0→True transition of IN_D resets (retriggers)
    the timer"), a new rising edge while the pulse is still active
    restarts the pulse from zero rather than being ignored — so a stream
    of triggers keeps the output continuously True until PT seconds after
    the last one.

    ET reports the time elapsed since the (most recent) trigger while the
    pulse is active, and 0 when it is not. The spec's ``ELAPSED_TIMER`` is
    "the time since the input transitioned to True" and is never described
    as zeroing, so ``ET_HOLD`` (default False = the historical zeroing) keeps
    it running after the pulse ends instead, until the next trigger restarts
    it. Before the first trigger ET is 0 either way.

    ``OUT_D`` status is GoodNonCascade per the spec: a pulse is generated by
    the block, so a Bad trigger does not make the pulse itself Bad.
    """
    block_type = "PULSE"
    category = BlockCategory.LOGIC
    display_name = "Pulse Timer"
    description = "Fixed-duration pulse on rising edge trigger"
    terminal_aliases = {"Q": "OUT", "IN_D": "IN", "ET_OUT": "ET"}
    config_aliases = {"preset": "PT", "PRESET": "PT", "pt": "PT"}

    def __init__(self, instance_name=""):
        self._active = False
        self._elapsed = 0.0
        self._prev_in = False
        self._fired = False        # has the block ever been triggered?
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", DataType.BOOL, False, "Trigger input")
        self.add_output("OUT", DataType.BOOL, False, "Pulse output")
        self.add_output("ET", description="Elapsed time [s]")

    def get_config_schema(self):
        return {
            "PT": (float, 1.0, "Pulse duration [s]"),
            "ET_HOLD": (bool, False,
                        "Keep ET running after the pulse (time since the last "
                        "trigger) instead of zeroing it"),
        }

    def execute(self, dt: float):
        pt = self.config.params.get("PT", 1.0)
        hold_et = bool(self.config.params.get("ET_HOLD", False))
        inp = bool(self.get_input("IN"))

        # Rising edge trigger — retriggerable: an edge during an active
        # pulse restarts the timer instead of being swallowed.
        if inp and not self._prev_in:
            self._active = True
            self._elapsed = 0.0
            self._fired = True
        self._prev_in = inp

        if self._active:
            self._elapsed += dt
            if self._elapsed >= pt:
                self._active = False
        elif hold_et and self._fired:
            # ELAPSED_TIMER = time since the input transitioned to True, which
            # keeps accruing once the pulse has dropped (TMR-7).
            self._elapsed += dt

        self.set_output("OUT", self._active)
        self.set_output("ET", self._elapsed
                        if (self._active or (hold_et and self._fired)) else 0.0)
        # Spec §2858: OUT_D status is GoodNonCascade — the trigger's status
        # deliberately does not propagate through the pulse.
        self.set_output_status("OUT", Quality.GOOD)

    def reset(self):
        super().reset()
        self._active = False
        self._elapsed = 0.0
        self._prev_in = False
        self._fired = False


# ──────────────────────────────────────────────────────────────────
# Tier 3-4 — NAND, NOR, TruthTable, Schedule, SeqTimer
# ──────────────────────────────────────────────────────────────────

@register_block
class NANDBlock(_ExtensibleGate):
    """NAND gate (Honeywell NAND) — extensible like the Azeo AND."""
    block_type = "NAND"
    display_name = "NAND"
    description = "OUT_D = NOT (AND of every used input)"
    _NEUTRAL = True
    _OUT_DEFAULT = True

    def execute(self, dt: float):
        self._sync_visible_inputs()
        self.set_output("OUT_D", not all(bool(v) for v in self._active_values()))
        self._propagate_gate_status(False)      # a good False decides a NAND


@register_block
class NORBlock(_ExtensibleGate):
    """NOR gate (Honeywell NOR) — extensible like the Azeo OR."""
    block_type = "NOR"
    display_name = "NOR"
    description = "OUT_D = NOT (OR of every used input)"
    _NEUTRAL = False
    _OUT_DEFAULT = True

    def execute(self, dt: float):
        self._sync_visible_inputs()
        self.set_output("OUT_D", not any(bool(v) for v in self._active_values()))
        self._propagate_gate_status(True)       # a good True decides a NOR


@register_block
class TruthTableBlock(FunctionBlock):
    """Configurable truth table (Honeywell TRUTH).

    3 boolean inputs → 1 boolean output, configured by 8-bit truth vector.
    Bit 0 = output when all inputs false, Bit 7 = output when all true.
    """
    block_type = "TRUTH_TABLE"
    category = BlockCategory.LOGIC
    display_name = "Truth Table"
    description = "Configurable 3-input truth table (8-bit output vector)"

    def _define_terminals(self):
        self.add_input("IN_D1", DataType.BOOL, False, "Input 1 (bit 0)")
        self.add_input("IN_D2", DataType.BOOL, False, "Input 2 (bit 1)")
        self.add_input("IN_D3", DataType.BOOL, False, "Input 3 (bit 2)")
        self.add_output("OUT_D", DataType.BOOL, False, "Truth table output")

    def get_config_schema(self):
        return {
            "TABLE": (int, 0b10000000, "8-bit truth vector (decimal, e.g. 128=AND, 254=OR)"),
        }

    def execute(self, dt: float):
        idx = (int(bool(self.get_input("IN_D1"))) |
               (int(bool(self.get_input("IN_D2"))) << 1) |
               (int(bool(self.get_input("IN_D3"))) << 2))
        table = int(self.config.params.get("TABLE", 128))
        self.set_output("OUT_D", bool(table & (1 << idx)))


@register_block
class ScheduleBlock(FunctionBlock):
    """Time-of-day schedule block (Honeywell SCHEDBLK).

    Outputs True during configured active hours of the day.
    Uses wall-clock time.
    """
    block_type = "SCHEDULE"
    category = BlockCategory.LOGIC
    display_name = "Schedule"
    description = "Time-of-day on/off schedule (wall clock)"

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, True, "Enable schedule")
        self.add_output("OUT_D", DataType.BOOL, False, "Schedule active")
        self.add_output("HOUR", DataType.INT, 0, "Current hour (0-23)")

    def get_config_schema(self):
        return {
            "START_HOUR": (int, 8, "Start hour (0-23)"),
            "END_HOUR": (int, 17, "End hour (0-23)"),
            "DAYS": (str, "MON,TUE,WED,THU,FRI", "Active days (comma-separated)"),
        }

    def execute(self, dt: float):
        if not self.get_input("ENABLE"):
            self.set_output("OUT_D", False)
            return

        p = self.config.params
        now = _time.localtime()
        hour = now.tm_hour
        wday = now.tm_wday  # 0=Monday
        day_names = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
        active_days = [d.strip().upper() for d in str(p.get("DAYS", "MON,TUE,WED,THU,FRI")).split(",")]

        day_ok = day_names[wday] in active_days
        start = int(p.get("START_HOUR", 8))
        end = int(p.get("END_HOUR", 17))

        if start <= end:
            hour_ok = start <= hour < end
        else:
            hour_ok = hour >= start or hour < end  # overnight span

        self.set_output("OUT_D", day_ok and hour_ok)
        self.set_output("HOUR", hour)


@register_block
class SeqTimerBlock(FunctionBlock):
    """Sequence timer (Honeywell SEQTMR).

    N-step sequential timer. Each step activates its output for a configured
    duration, then advances to the next step.
    """
    block_type = "SEQ_TIMER"
    category = BlockCategory.LOGIC
    display_name = "Sequence Timer"
    description = "N-step sequential timer with per-step durations"

    def __init__(self, instance_name=""):
        self._step = 0
        self._elapsed = 0.0
        self._running = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("START", DataType.BOOL, False, "Start sequence")
        self.add_input("STOP", DataType.BOOL, False, "Stop sequence")
        self.add_input("RESET", DataType.BOOL, False, "Reset to step 0")
        self.add_output("STEP", DataType.INT, 0, "Current step (1-based, 0=idle)")
        self.add_output("OUT_1", DataType.BOOL, False, "Step 1 active")
        self.add_output("OUT_2", DataType.BOOL, False, "Step 2 active")
        self.add_output("OUT_3", DataType.BOOL, False, "Step 3 active")
        self.add_output("OUT_4", DataType.BOOL, False, "Step 4 active")
        self.add_output("OUT_5", DataType.BOOL, False, "Step 5 active")
        self.add_output("OUT_6", DataType.BOOL, False, "Step 6 active")
        self.add_output("OUT_7", DataType.BOOL, False, "Step 7 active")
        self.add_output("OUT_8", DataType.BOOL, False, "Step 8 active")
        self.add_output("COMPLETE", DataType.BOOL, False, "Sequence complete")
        self.add_output("RUNNING", DataType.BOOL, False, "Sequence running")

    def get_config_schema(self):
        return {
            "N_STEPS": (int, 4, "Number of steps (1-8)"),
            "TIME_1": (float, 10.0, "Step 1 duration [s]"),
            "TIME_2": (float, 10.0, "Step 2 duration [s]"),
            "TIME_3": (float, 10.0, "Step 3 duration [s]"),
            "TIME_4": (float, 10.0, "Step 4 duration [s]"),
            "TIME_5": (float, 10.0, "Step 5 duration [s]"),
            "TIME_6": (float, 10.0, "Step 6 duration [s]"),
            "TIME_7": (float, 10.0, "Step 7 duration [s]"),
            "TIME_8": (float, 10.0, "Step 8 duration [s]"),
            "CYCLE": (bool, False, "Auto-restart when complete"),
        }

    def execute(self, dt: float):
        p = self.config.params
        n = min(max(int(p.get("N_STEPS", 4)), 1), 8)

        if self.get_input("RESET"):
            self._step = 0
            self._elapsed = 0.0
            self._running = False

        if self.get_input("START") and not self._running:
            self._running = True
            self._step = 0
            self._elapsed = 0.0

        if self.get_input("STOP"):
            self._running = False

        complete = False
        if self._running and self._step < n:
            duration = p.get(f"TIME_{self._step + 1}", 10.0)
            self._elapsed += dt
            if self._elapsed >= duration:
                self._step += 1
                self._elapsed = 0.0
                if self._step >= n:
                    complete = True
                    if p.get("CYCLE", False):
                        self._step = 0
                    else:
                        self._running = False

        for i in range(8):
            self.set_output(f"OUT_{i+1}", self._running and self._step == i and i < n)
        self.set_output("STEP", self._step + 1 if self._running and self._step < n else 0)
        self.set_output("COMPLETE", complete)
        self.set_output("RUNNING", self._running)

    def reset(self):
        super().reset()
        self._step = 0
        self._elapsed = 0.0
        self._running = False
