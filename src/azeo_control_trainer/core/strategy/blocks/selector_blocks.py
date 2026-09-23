"""Selector blocks — MIN, MAX, MID, AVG, MUX, DEMUX, Switch, Mode switch.

Every block in this file uses the **real terminal signal-quality model**
(:mod:`azeo_control_trainer.core.strategy.model.terminal`): each terminal carries a
:class:`Quality` and a :class:`LimitStatus` that travel along forward and
BKCAL wires. That is what lets these blocks behave like their Azeo
counterparts (MLTX §3412, ISEL §1664, SGSL §1793, CTLSL §1604):

* **Bad inputs are excluded from the selection.** A MIN/MAX/MID/AVG selector
  picks among the inputs it can trust.
* **OUT reports the quality of what was actually selected** — including its
  limit status, so a downstream controller still sees "this signal is pinned
  at its high limit".
* **A selector with no usable input goes Bad.** It still publishes a value
  (computed over the Bad inputs, per SGSL) so nothing sees a discontinuity,
  but the status says the value must not be acted on.

Because a terminal defaults to Good / Not limited, a strategy whose sources
never publish a status behaves exactly as it did before this existed — the
94 MIN_SELECT, 58 MAX_SELECT and 320 SWITCH blocks in the shipped strategies
are unaffected.
"""
from __future__ import annotations

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality, LimitStatus


def _worst(*qualities: Quality) -> Quality:
    """Worst of a set of qualities (Bad > Uncertain > Good)."""
    return max(qualities, key=lambda q: q.value) if qualities else Quality.GOOD


@register_block
class MinSelectBlock(FunctionBlock):
    """Low selector — the Azeo SGSL ``min`` algorithm over three inputs.

    Bad inputs are excluded from the comparison; OUT carries the selected
    input's quality and limit status. With every input Bad the block still
    outputs the minimum of the Bad values, flagged Bad.
    """
    block_type = "MIN_SELECT"
    category = BlockCategory.SIGNAL
    display_name = "Min Select"
    description = "OUT = min(IN1, IN2, IN3), Bad inputs excluded"

    _NAMES = ("IN1", "IN2", "IN3")

    def _define_terminals(self):
        self.add_input("IN1", description="Input 1")
        self.add_input("IN2", default=float('inf'), description="Input 2 (unused = ignored)")
        self.add_input("IN3", default=float('inf'), description="Input 3 (unused = ignored)")
        self.add_output("OUT", description="Minimum value")
        self.add_output("SELECTED", DataType.INT, 1, "Index of selected input (1-3)")

    def _select(self, pool, vals):
        return min(pool, key=lambda i: vals[i])

    def execute(self, dt: float):
        names = self._NAMES
        vals = [self.get_input(n) for n in names]
        usable = [i for i in range(3)
                  if self.input_status(names[i]) is not Quality.BAD]
        # Nothing usable: still compute (SGSL "executes using the Bad inputs")
        # but publish Bad so downstream control sheds instead of acting.
        pool = usable or [0, 1, 2]
        idx = self._select(pool, vals)
        self.set_output("OUT", vals[idx])
        self.set_output("SELECTED", idx + 1)

        q = self.input_status(names[idx]) if usable else Quality.BAD
        self.set_output_status("OUT", q, self.input_limit(names[idx]))
        self.set_output_status("SELECTED", q, LimitStatus.NOT_LIMITED)


@register_block
class MaxSelectBlock(MinSelectBlock):
    """High selector — the Azeo SGSL ``max`` algorithm. See MIN_SELECT."""
    block_type = "MAX_SELECT"
    category = BlockCategory.SIGNAL
    display_name = "Max Select"
    description = "OUT = max(IN1, IN2, IN3), Bad inputs excluded"

    def _define_terminals(self):
        self.add_input("IN1", description="Input 1")
        self.add_input("IN2", default=float('-inf'), description="Input 2 (unused = ignored)")
        self.add_input("IN3", default=float('-inf'), description="Input 3 (unused = ignored)")
        self.add_output("OUT", description="Maximum value")
        self.add_output("SELECTED", DataType.INT, 1, "Index of selected input (1-3)")

    def _select(self, pool, vals):
        return max(pool, key=lambda i: vals[i])


@register_block
class MidSelectBlock(FunctionBlock):
    """Middle selector — the Azeo ISEL ``MID`` algorithm.

    Bad inputs are excluded. With an odd number of usable inputs OUT takes the
    middle input's value, quality and limit status; with an even number OUT is
    the mean of the two middle values, its quality the worst of the usable
    inputs and its limit status forced Not Limited (spec §1692).
    """
    block_type = "MID_SELECT"
    category = BlockCategory.SIGNAL
    display_name = "Mid Select"
    description = "OUT = median(IN1, IN2, IN3), Bad inputs excluded"

    _NAMES = ("IN1", "IN2", "IN3")

    def _define_terminals(self):
        self.add_input("IN1", description="Input 1")
        self.add_input("IN2", description="Input 2")
        self.add_input("IN3", description="Input 3")
        self.add_output("OUT", description="Median value")

    def execute(self, dt: float):
        names = self._NAMES
        usable = [n for n in names
                  if self.input_status(n) is not Quality.BAD]
        pool = usable or list(names)
        ranked = sorted(((self.get_input(n), n) for n in pool),
                        key=lambda p: p[0])
        k = len(ranked)
        if k % 2:
            val, src = ranked[k // 2]
            q = self.input_status(src)
            limit = self.input_limit(src)
        else:
            val = (ranked[k // 2 - 1][0] + ranked[k // 2][0]) / 2.0
            q = _worst(*(self.input_status(n) for _, n in ranked))
            limit = LimitStatus.NOT_LIMITED

        self.set_output("OUT", val)
        self.set_output_status("OUT", q if usable else Quality.BAD, limit)


@register_block
class SwitchBlock(FunctionBlock):
    """Two-way switch. OUT quality = worst of the selected input and SELECT.

    A switch cannot exclude its selected input — the whole point is that the
    selector decides — so it propagates instead: a Bad IN1 with SELECT true
    makes OUT Bad, and a Bad SELECT makes OUT Bad whichever way it points.
    """
    block_type = "SWITCH"
    category = BlockCategory.SIGNAL
    display_name = "Switch"
    description = "OUT = IN1 if SELECT else IN2"

    def _define_terminals(self):
        self.add_input("IN1", description="Input when SELECT=True")
        self.add_input("IN2", description="Input when SELECT=False")
        self.add_input("SELECT", DataType.BOOL, False, "Selection signal")
        self.add_output("OUT", description="Selected output")

    def execute(self, dt: float):
        sel = self.get_input("SELECT")
        src = "IN1" if sel else "IN2"
        self.set_output("OUT", self.get_input(src))
        self.set_output_status(
            "OUT",
            _worst(self.input_status(src), self.input_status("SELECT")),
            self.input_limit(src),
        )


@register_block
class ModeSwitchBlock(FunctionBlock):
    """APC mode switch — outputs target mode based on APC active signal.

    When ACTIVE transitions high:  AUTO → RCAS, MANUAL → ROUT
    When ACTIVE transitions low:   RCAS → AUTO, ROUT → MANUAL
    When no transition:            outputs empty string (no mode change)

    Wire: ACTIVE ← dmc_active signal
          MODE_IN ← PID.MODE output (current mode)
          MODE_OUT → PID.MODE_TARGET input

    Quality: a Bad ACTIVE or MODE_IN suppresses the mode change entirely
    (MODE_OUT = "" with Bad status) — an untrustworthy engagement signal must
    never move a controller between AUTO and RCAS.
    """
    block_type = "MODE_SWITCH"
    category = BlockCategory.SIGNAL
    display_name = "Mode Switch"
    description = "APC mode switching: AUTO↔RCAS, MAN↔ROUT"

    def _define_terminals(self):
        self.add_input("ACTIVE", DataType.BOOL, False, "APC active signal")
        self.add_input("MODE_IN", DataType.STRING, "", "Current PID mode")
        self.add_output("MODE_OUT", DataType.STRING, "", "Target mode (empty=no change)")

    # When APC active: these modes should transition
    _ACTIVATE = {"AUTO": "RCAS", "MAN": "ROUT", "MANUAL": "ROUT"}
    # When APC inactive: reverse transitions
    _DEACTIVATE = {"RCAS": "AUTO", "ROUT": "MAN"}

    def execute(self, dt: float):
        q = _worst(self.input_status("ACTIVE"), self.input_status("MODE_IN"))
        if q is Quality.BAD:
            self.set_output("MODE_OUT", "")
            self.set_output_status("MODE_OUT", Quality.BAD,
                                   LimitStatus.NOT_LIMITED)
            return

        active = bool(self.get_input("ACTIVE"))
        mode_in = str(self.get_input("MODE_IN")).upper().strip()

        if active:
            # APC active: drive AUTO→RCAS, MAN→ROUT (continuously)
            target = self._ACTIVATE.get(mode_in, "")
        else:
            # APC inactive: drive RCAS→AUTO, ROUT→MAN (continuously)
            target = self._DEACTIVATE.get(mode_in, "")

        self.set_output("MODE_OUT", target)
        self.set_output_status("MODE_OUT", q, LimitStatus.NOT_LIMITED)


# ──────────────────────────────────────────────────────────────────
# Tier 2 — AVG_SELECT, MUX, DEMUX
# ──────────────────────────────────────────────────────────────────

@register_block
class AvgSelectBlock(FunctionBlock):
    """Average selector with bad-value exclusion (Honeywell AVGSEL).

    Averages valid inputs. An input is excluded if its **quality is Bad** or
    if its value exceeds the HI/LO reject limits. OUT quality is the worst of
    the inputs that actually contributed; with nothing valid OUT is 0.0 and
    Bad. Per ISEL §1692 an average never carries a limit status.
    """
    block_type = "AVG_SELECT"
    category = BlockCategory.SIGNAL
    display_name = "Avg Selector"
    description = "Average of valid inputs (with bad-value exclusion)"

    def _define_terminals(self):
        self.add_input("IN1", description="Input 1")
        self.add_input("IN2", description="Input 2")
        self.add_input("IN3", description="Input 3")
        self.add_input("IN4", description="Input 4")
        self.add_output("OUT", description="Average of valid inputs")
        self.add_output("VALID_COUNT", DataType.INT, 0, "Number of valid inputs")

    def get_config_schema(self):
        return {
            "N_INPUTS": (int, 3, "Number of active inputs (1-4)"),
            "HI_REJECT": (float, 1e6, "Reject inputs above this"),
            "LO_REJECT": (float, -1e6, "Reject inputs below this"),
        }

    def execute(self, dt: float):
        p = self.config.params
        n = min(max(int(p.get("N_INPUTS", 3)), 1), 4)
        hi = p.get("HI_REJECT", 1e6)
        lo = p.get("LO_REJECT", -1e6)
        names = ["IN1", "IN2", "IN3", "IN4"]

        valid = []
        qualities = []
        for i in range(n):
            name = names[i]
            if self.input_status(name) is Quality.BAD:
                continue
            v = self.get_input(name)
            if lo <= v <= hi:
                valid.append(v)
                qualities.append(self.input_status(name))

        if valid:
            self.set_output("OUT", sum(valid) / len(valid))
            q = _worst(*qualities)
        else:
            self.set_output("OUT", 0.0)
            q = Quality.BAD
        self.set_output("VALID_COUNT", len(valid))
        self.set_output_status("OUT", q, LimitStatus.NOT_LIMITED)
        self.set_output_status("VALID_COUNT", q, LimitStatus.NOT_LIMITED)


@register_block
class MuxBlock(FunctionBlock):
    """Multiplexer — N inputs to 1 output (Azeo MLTX, spec §3412).

    Selects one of up to **16** inputs by an integer selector. Closes the
    audit's MUX-1..MUX-5:

    * ``NOF_INPUTS`` (default 8) sets how many inputs exist; IN9..IN16 are
      hidden until it is raised, so the default block is exactly today's
      8-input MUX.
    * ``SELECT_NEXT_GOOD`` (default False) — on a Bad selected input, take the
      next non-Bad one searching upward and rotating last→first. With every
      input Bad the originally selected value is output with Bad status.
    * ``BAL_TIME`` (default 0.0 = instant) ramps OUT from the old value to the
      newly selected one over that many seconds.
    * ``SELECTOR`` is the Azeo name for ``INDEX``; it is used when ``INDEX``
      is unconnected, so a Azeo-authored strategy wires straight up.
    * **An out-of-range selector holds the previous OUT** and sets
      ``VALID`` False (a platform extra — Azeo has no VALID). The held value
      is stale, so OUT is flagged Bad.

    OUT quality = worst of the selected input's quality and the selector's.
    """
    block_type = "MUX"
    category = BlockCategory.SIGNAL
    display_name = "Multiplexer"
    description = "N-to-1 multiplexer (index-selected input)"

    config_units = {"BAL_TIME": "s"}

    _MAX_INPUTS = 16
    _DEFAULT_NOF = 8

    def __init__(self, instance_name=""):
        self._out = 0.0          # current output (BAL_TIME ramp start point)
        self._prev_sel = 0       # last selected input number, 0 = none yet
        self._bal_rem = 0.0      # seconds left in the switchover ramp
        self._bal_start = 0.0
        self._nof_cache = -1
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("INDEX", DataType.INT, 1, "Input selector (1-based)")
        self.add_input("SELECTOR", DataType.INT, 1,
                       "Azeo name for INDEX (used when INDEX is unconnected)")
        for i in range(1, self._MAX_INPUTS + 1):
            t = self.add_input(f"IN{i}", description=f"Input {i}")
            if i > self._DEFAULT_NOF:
                t.hidden = True   # extensible: pins appear as NOF_INPUTS grows
        self.add_output("OUT", description="Selected input")
        self.add_output("VALID", DataType.BOOL, True, "Index in range")
        self.add_output("SELECTED", DataType.INT, 1,
                        "Input number actually placed on OUT")

    def get_config_schema(self):
        return {
            "NOF_INPUTS": (int, self._DEFAULT_NOF, "Number of active inputs (1-16)"),
            "SELECT_NEXT_GOOD": (bool, False,
                                 "On a Bad selected input, take the next Good "
                                 "one (ascending, rotating last→first)"),
            "BAL_TIME": (float, 0.0,
                         "Seconds to ramp OUT to a newly selected input "
                         "(0 = instantaneous)"),
        }

    def _nof(self) -> int:
        n = int(self.config.params.get("NOF_INPUTS", self._DEFAULT_NOF))
        return min(max(n, 1), self._MAX_INPUTS)

    def _sync_pins(self, n: int):
        """Show IN1..INn, hide the rest — Azeo's extensible input count."""
        if n == self._nof_cache:
            return
        self._nof_cache = n
        for i in range(1, self._MAX_INPUTS + 1):
            self.inputs[f"IN{i}"].hidden = i > n

    def _apply_config(self):
        self._sync_pins(self._nof())

    def execute(self, dt: float):
        p = self.config.params
        n = self._nof()
        self._sync_pins(n)

        # MUX-5: SELECTOR is the Azeo name; INDEX wins when it is wired.
        if self.inputs["INDEX"].connected or not self.inputs["SELECTOR"].connected:
            sel_name = "INDEX"
        else:
            sel_name = "SELECTOR"
        idx = int(self.get_input(sel_name))
        sel_q = self.input_status(sel_name)

        if not (1 <= idx <= n):
            # MUX-4: hold the previous OUT. The held value is stale, so it is
            # published Bad rather than silently passing as a live reading.
            self.set_output("VALID", False)
            self.set_output_status("VALID", sel_q, LimitStatus.NOT_LIMITED)
            self.set_output_status("OUT", Quality.BAD, LimitStatus.CONSTANT)
            self.set_output_status("SELECTED", Quality.BAD,
                                   LimitStatus.NOT_LIMITED)
            return

        self.set_output("VALID", True)
        self.set_output_status("VALID", sel_q, LimitStatus.NOT_LIMITED)

        # MUX-2: fail over to the next non-Bad input, rotating last→first.
        chosen, all_bad = idx, False
        if p.get("SELECT_NEXT_GOOD", False):
            order = [((idx - 1 + k) % n) + 1 for k in range(n)]
            for cand in order:
                if self.input_status(f"IN{cand}") is not Quality.BAD:
                    chosen = cand
                    break
            else:
                all_bad = True   # spec: output the originally selected input

        src = f"IN{chosen}"
        target = self.get_input(src)

        # MUX-3: BAL_TIME switchover ramp.
        bal = float(p.get("BAL_TIME", 0.0) or 0.0)
        if chosen != self._prev_sel and self._prev_sel and bal > 0.0:
            self._bal_rem = bal
            self._bal_start = self._out
        self._prev_sel = chosen

        if self._bal_rem > 0.0 and bal > 0.0:
            self._bal_rem = max(0.0, self._bal_rem - dt)
            frac = 1.0 - self._bal_rem / bal
            out = self._bal_start + (target - self._bal_start) * frac
        else:
            self._bal_rem = 0.0
            out = target
        self._out = out

        self.set_output("OUT", out)
        self.set_output("SELECTED", chosen)
        q = Quality.BAD if all_bad else _worst(self.input_status(src), sel_q)
        self.set_output_status("OUT", q, self.input_limit(src))
        self.set_output_status("SELECTED", q, LimitStatus.NOT_LIMITED)

    def reset(self):
        super().reset()
        self._out = 0.0
        self._prev_sel = 0
        self._bal_rem = 0.0
        self._bal_start = 0.0


@register_block
class DemuxBlock(FunctionBlock):
    """Demultiplexer — 1 input to N outputs (Honeywell DEMUX).

    Routes a single input to one of up to 8 outputs based on INDEX.
    Non-selected outputs hold their last value (or default to 0).

    Quality: the selected output takes the worst of IN's and INDEX's quality
    plus IN's limit status; every other output **holds its stored quality**
    alongside its stored value and is flagged ``CONSTANT`` — it genuinely
    cannot move until it is selected again. An out-of-range INDEX updates no
    output, and CLEAR resets every output to 0.0 / Good.
    """
    block_type = "DEMUX"
    category = BlockCategory.SIGNAL
    display_name = "Demultiplexer"
    description = "1-to-N demultiplexer (index-selected output)"

    def __init__(self, instance_name=""):
        self._values = [0.0] * 8
        self._status = [Quality.GOOD] * 8
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Input value")
        self.add_input("INDEX", DataType.INT, 1, "Output selector (1-based)")
        self.add_input("CLEAR", DataType.BOOL, False, "Clear all outputs to 0")
        self.add_output("OUT1", description="Output 1")
        self.add_output("OUT2", description="Output 2")
        self.add_output("OUT3", description="Output 3")
        self.add_output("OUT4", description="Output 4")
        self.add_output("OUT5", description="Output 5")
        self.add_output("OUT6", description="Output 6")
        self.add_output("OUT7", description="Output 7")
        self.add_output("OUT8", description="Output 8")

    def execute(self, dt: float):
        if self.get_input("CLEAR"):
            self._values = [0.0] * 8
            self._status = [Quality.GOOD] * 8

        idx = int(self.get_input("INDEX"))
        sel = -1
        if 1 <= idx <= 8:
            sel = idx - 1
            self._values[sel] = self.get_input("IN")
            self._status[sel] = _worst(self.input_status("IN"),
                                       self.input_status("INDEX"))

        in_limit = self.input_limit("IN")
        for i in range(8):
            self.set_output(f"OUT{i+1}", self._values[i])
            self.set_output_status(
                f"OUT{i+1}", self._status[i],
                in_limit if i == sel else LimitStatus.CONSTANT,
            )

    def reset(self):
        super().reset()
        self._values = [0.0] * 8
        self._status = [Quality.GOOD] * 8
