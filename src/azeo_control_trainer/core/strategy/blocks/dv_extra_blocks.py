"""Azeo-compatibility blocks — MANLD, SGGN, AT, CEM, CND.

Five new function blocks rounding out the Azeo-compatible palette.
Each block matches the Azeo behavior described in the standard
function-block reference; tracking inputs use the conventional
``TRK_VAL`` (analog) + ``TRK_IN_D`` (discrete enable) pair so they
inter-operate with PID, BG, Manual Loader, and similar.

Layout:
    MANLD — Manual Loader (operator-set output, supports tracking)
    SGGN  — Signal Generator (sine + square + bias + noise)
    AT    — Analog Tracking (produces TRK_VAL / TRK_IN_D for PID)
    CEM   — Cause & Effect Matrix (16x16 trip matrix)
    CND   — Condition (expression + time-true delay)
"""
from __future__ import annotations

import math
import random
from typing import Any

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block
from ..model.terminal import Quality, LimitStatus


# ═══════════════════════════════════════════════════════════════════════
#  MANLD — Manual Loader
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ManualLoaderBlock(FunctionBlock):
    """Manual Loader (MANLD) — operator-driven output station.

    The output (``OUT``) follows the operator setpoint (``SP``) unless
    tracking is requested via ``TRK_IN_D``, in which case the output
    follows ``TRK_VAL`` for bumpless transfer. When tracking releases,
    the next ``SP`` write should be initialised by the operator from
    ``SP_FB`` (the current output value).

    Typical use: an OUT that drives an AO directly when no automatic
    controller is in service, or as the "manual" leg of a control
    selector (CTLSL / SWITCH).

    **Signal quality.** While tracking, ``OUT``/``SP_FB`` publish the
    quality of ``TRK_VAL`` — a manual loader forced to follow a Bad
    tracking value must not present that value downstream as a healthy
    operator setpoint. Off tracking the output is the operator's own SP
    and is therefore Good. Either way ``OUT`` reports ``HIGH_LIMITED`` /
    ``LOW_LIMITED`` while it sits on ``OUT_HI`` / ``OUT_LO``, so an
    upstream controller wired through the BKCAL chain stops winding
    reset into the limit.
    """
    block_type = "MANLD"
    category = BlockCategory.CONTROL
    display_name = "Manual Loader (MANLD)"
    description = "Operator-set analog output with tracking and alarm limits"

    def _define_terminals(self):
        self.add_input("TRK_VAL", description="Tracking value")
        self.add_input("TRK_IN_D", DataType.BOOL, False,
                       "Tracking enable — force OUT to TRK_VAL")
        self.add_output("OUT", description="Output value")
        self.add_output("SP_FB", description="Setpoint feedback (current OUT)")
        self.add_output("TRACKING", DataType.BOOL, False,
                        "True while tracking")
        self.add_output("HI_ALM", DataType.BOOL, False, "High alarm active")
        self.add_output("LO_ALM", DataType.BOOL, False, "Low alarm active")

    def get_config_schema(self):
        return {
            "SP":     (float, 0.0,  "Operator setpoint"),
            "OUT_LO": (float, 0.0,  "Output low limit"),
            "OUT_HI": (float, 100.0, "Output high limit"),
            "HI_LIM": (float, 1e9,  "High alarm limit (engineering units)"),
            "LO_LIM": (float, -1e9, "Low alarm limit"),
            "ALM_DB": (float, 0.0,  "Alarm deadband (units)"),
        }

    def __init__(self, instance_name: str = ""):
        # State flag remembers prior tracking so we can bump SP after release
        self._was_tracking = False
        super().__init__(instance_name)

    def execute(self, dt: float):
        p = self.config.params
        sp = float(p.get("SP", 0.0))
        lo = float(p.get("OUT_LO", 0.0))
        hi = float(p.get("OUT_HI", 100.0))

        tracking = bool(self.get_input("TRK_IN_D"))
        if tracking:
            raw = float(self.get_input("TRK_VAL"))
        else:
            # On the scan when tracking releases, snap SP to last OUT
            # so the operator continues from the current value (bumpless).
            if self._was_tracking:
                p["SP"] = self.get_output("OUT")
                sp = p["SP"]
            raw = sp

        out = max(lo, min(hi, raw))
        self._was_tracking = tracking

        self.set_output("OUT", out)
        self.set_output("SP_FB", out)
        self.set_output("TRACKING", tracking)

        # Quality: a tracked output is only as trustworthy as TRK_VAL; an
        # operator-entered SP is Good.  Limit state reports the clamp so a
        # BKCAL-connected upstream block can stop integrating into it.
        quality = self.input_status("TRK_VAL") if tracking else Quality.GOOD
        if raw >= hi:
            limit = LimitStatus.HIGH_LIMITED
        elif raw <= lo:
            limit = LimitStatus.LOW_LIMITED
        else:
            limit = LimitStatus.NOT_LIMITED
        self.set_output_status("OUT", quality, limit)
        self.set_output_status("SP_FB", quality, limit)
        self.set_output_status("TRACKING", Quality.GOOD, LimitStatus.NOT_LIMITED)

        # Alarm detection with deadband
        hi_lim = float(p.get("HI_LIM", 1e9))
        lo_lim = float(p.get("LO_LIM", -1e9))
        db = max(0.0, float(p.get("ALM_DB", 0.0)))
        hi_alm = self.get_output("HI_ALM")
        lo_alm = self.get_output("LO_ALM")
        hi_alm = out > (hi_lim - db) if hi_alm else out >= hi_lim
        lo_alm = out < (lo_lim + db) if lo_alm else out <= lo_lim
        self.set_output("HI_ALM", hi_alm)
        self.set_output("LO_ALM", lo_alm)
        # Alarm discretes are block-computed flags, always Good.
        self.set_output_status("HI_ALM", Quality.GOOD, LimitStatus.NOT_LIMITED)
        self.set_output_status("LO_ALM", Quality.GOOD, LimitStatus.NOT_LIMITED)


# ═══════════════════════════════════════════════════════════════════════
#  SGGN — Signal Generator
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SignalGeneratorBlock(FunctionBlock):
    """Signal Generator (SGGN) — synthetic waveform source for testing.

    Output is the sum of a primary waveform (sine / square / triangle /
    sawtooth), a constant bias, and optional random noise.
    Useful for step tests, model ID, faceplate exercising, and
    operator-training scenarios where a known disturbance shape is
    needed.

    **Azeo component form (spec §SGGN).** Azeo does not *select* a
    waveform — it sums four components every scan::

        OUT = SIN_AMP·sin(2π t / SIN_PERIOD)
            + SQUARE_AMP·square(t, SQUARE_PERIOD)
            + filtered random of amplitude RAND_AMP (time constant RAND_FTIME)
            + BIAS

    Those components are configured here with their Azeo names and are
    added **on top of** the selected ``WAVE`` (finding G-1). Both
    amplitudes default to ``0.0``, so an existing block generates exactly
    what it did before. ``WAVE = "OFF"`` with a non-zero ``SIN_AMP`` /
    ``SQUARE_AMP`` gives the pure Azeo form.

    The random component is passed through a first-order filter with time
    constant ``RAND_FTIME`` (finding G-2); the default ``0.0`` is today's
    unfiltered white noise. ``RAND_AMP`` is accepted as the Azeo
    spelling of ``NOISE`` (finding G-3).

    Output status is always Good — a signal generator has no measurement
    to be uncertain about (spec: "OUT status is always Good: Non-cascade").
    """
    block_type = "SGGN"
    category = BlockCategory.SIGNAL
    display_name = "Signal Generator (SGGN)"
    description = "sine / square / triangle / sawtooth + bias + noise"

    _WAVES = ("SINE", "SQUARE", "TRIANGLE", "SAWTOOTH", "OFF")

    #: Azeo spelling of the same parameter (spec §SGGN).
    config_aliases = {"RAND_AMP": "NOISE"}
    config_choices = {"WAVE": _WAVES}
    config_units = {"PERIOD": "s", "SIN_PERIOD": "s", "SQUARE_PERIOD": "s",
                    "RAND_FTIME": "s", "PHASE": "deg"}

    def __init__(self, instance_name: str = ""):
        self._t = 0.0
        self._rand = 0.0      # filtered random component (RAND_FTIME)
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, True,
                       "When False, OUT = BIAS only (waveform paused)")
        self.add_input("RESET", DataType.BOOL, False,
                       "Pulse True to reset phase to zero")
        self.add_output("OUT", description="Generated signal")

    def get_config_schema(self):
        return {
            "WAVE":      (str,   "SINE", "Waveform: SINE / SQUARE / TRIANGLE / SAWTOOTH / OFF"),
            "AMPLITUDE": (float, 1.0,    "Peak amplitude"),
            "PERIOD":    (float, 10.0,   "Period (s) — must be > 0"),
            "PHASE":     (float, 0.0,    "Phase offset (deg)"),
            "BIAS":      (float, 0.0,    "Constant bias added to waveform"),
            "NOISE":     (float, 0.0,    "Uniform noise amplitude (peak); Azeo RAND_AMP"),
            "DUTY":      (float, 0.5,    "Square wave duty cycle (0..1)"),
            # Azeo component form — summed on top of WAVE, zero by default.
            "SIN_AMP":       (float, 0.0,  "Azeo sine component amplitude (0 = off)"),
            "SIN_PERIOD":    (float, 10.0, "Azeo sine component period (s)"),
            "SQUARE_AMP":    (float, 0.0,  "Azeo square component amplitude (0 = off)"),
            "SQUARE_PERIOD": (float, 10.0, "Azeo square component period (s)"),
            "RAND_FTIME":    (float, 0.0,
                "Filter time constant of the random component (s); "
                "0 = unfiltered white noise"),
        }

    def reset(self):
        super().reset()
        self._t = 0.0
        self._rand = 0.0

    def execute(self, dt: float):
        p = self.config.params
        wave = str(p.get("WAVE", "SINE")).upper()
        amp = float(p.get("AMPLITUDE", 1.0))
        period = max(1e-6, float(p.get("PERIOD", 10.0)))
        phase = math.radians(float(p.get("PHASE", 0.0)))
        bias = float(p.get("BIAS", 0.0))
        # RAND_AMP is the Azeo spelling; from_dict normalises it, but read
        # both so a config written at runtime works either way.
        noise = max(0.0, float(p.get("NOISE", p.get("RAND_AMP", 0.0))))
        duty = max(0.0, min(1.0, float(p.get("DUTY", 0.5))))

        # Azeo component form — summed on top of WAVE (G-1).
        sin_amp = float(p.get("SIN_AMP", 0.0))
        sq_amp = float(p.get("SQUARE_AMP", 0.0))
        dv_active = sin_amp != 0.0 or sq_amp != 0.0

        # OUT is generated, never measured: always Good (spec §SGGN).
        self.set_output_status("OUT", Quality.GOOD, LimitStatus.NOT_LIMITED)

        if bool(self.get_input("RESET")):
            self._t = 0.0
            self._rand = 0.0
        if not bool(self.get_input("ENABLE")) or (wave == "OFF" and not dv_active):
            self.set_output("OUT", bias)
            return

        self._t += dt
        t = self._t / period   # cycles
        cyc = (t + phase / (2.0 * math.pi)) % 1.0   # 0..1 within cycle

        if wave == "SINE":
            wf = math.sin(2.0 * math.pi * cyc)
        elif wave == "SQUARE":
            wf = 1.0 if cyc < duty else -1.0
        elif wave == "TRIANGLE":
            # 0..0.5 -> -1..+1, 0.5..1 -> +1..-1
            wf = (4.0 * cyc - 1.0) if cyc < 0.5 else (3.0 - 4.0 * cyc)
        elif wave == "SAWTOOTH":
            wf = 2.0 * cyc - 1.0
        else:
            wf = 0.0

        out = bias + amp * wf

        # Azeo sine + square components (zero amplitude ⇒ no contribution).
        if sin_amp != 0.0:
            sin_period = max(1e-6, float(p.get("SIN_PERIOD", 10.0)))
            out += sin_amp * math.sin(2.0 * math.pi * self._t / sin_period)
        if sq_amp != 0.0:
            sq_period = max(1e-6, float(p.get("SQUARE_PERIOD", 10.0)))
            half = (self._t % sq_period) < (sq_period * 0.5)
            out += sq_amp if half else -sq_amp

        # Random component, optionally first-order filtered (RAND_FTIME).
        if noise > 0.0:
            target = (random.random() * 2.0 - 1.0) * noise
            ftime = max(0.0, float(p.get("RAND_FTIME", 0.0)))
            if ftime > 0.0 and dt > 0.0:
                alpha = dt / (ftime + dt)
                self._rand += alpha * (target - self._rand)
            else:
                self._rand = target
            out += self._rand
        else:
            self._rand = 0.0
        self.set_output("OUT", out)


# ═══════════════════════════════════════════════════════════════════════
#  AT — Analog Tracking
# ═══════════════════════════════════════════════════════════════════════

@register_block
class AnalogTrackingBlock(FunctionBlock):
    """Analog Tracking (AT) — generate TRK_VAL/TRK_IN_D for a downstream PID.

    Asserts the tracking pair when ``TRK_REQ_D`` is True (or, optionally,
    when an analog input differs from the target by more than a
    threshold). Wire ``TRK_VAL_OUT`` → PID's ``TRK_VAL`` and
    ``TRK_IN_D_OUT`` → PID's ``TRK_IN_D`` to force a PID's output to a
    specific value for bumpless override transfer.

    Typical pattern: feeds a PID's external-tracking inputs while an
    override controller is active; releases when the override is no
    longer required, returning control bumplessly to the primary PID.

    **Signal quality.** ``TRK_VAL_OUT`` carries the quality of ``IN``
    while tracking — forcing a PID's output to a Bad number must not look
    Good on the wire — plus ``HIGH_LIMITED`` / ``LOW_LIMITED`` when the
    value is sitting on ``OUT_HI`` / ``OUT_LO``. The discrete
    ``TRK_IN_D_OUT`` / ``ACTIVE`` flags are block-computed and always
    Good, matching Azeo's GoodNonCascade discretes.
    """
    block_type = "AT"
    category = BlockCategory.CONTROL
    display_name = "Analog Tracking (AT)"
    description = "Generate TRK_VAL / TRK_IN_D for PID bumpless transfer"

    def _define_terminals(self):
        self.add_input("IN", description="Value to track (e.g. override OUT)")
        self.add_input("TRK_REQ_D", DataType.BOOL, False,
                       "Request tracking — when True, assert TRK_IN_D_OUT")
        self.add_output("TRK_VAL_OUT", description="Tracking value (= IN when tracking)")
        self.add_output("TRK_IN_D_OUT", DataType.BOOL, False,
                        "Tracking enable signal (True when active)")
        self.add_output("ACTIVE", DataType.BOOL, False,
                        "Mirror of TRK_IN_D_OUT for monitoring")

    def get_config_schema(self):
        return {
            "BIAS":    (float, 0.0,  "Offset added to IN before tracking"),
            "OUT_LO":  (float, -1e9, "Tracking value low limit"),
            "OUT_HI":  (float, 1e9,  "Tracking value high limit"),
            "INVERT_REQ": (int, 0,   "If 1, track when TRK_REQ_D is FALSE"),
        }

    def execute(self, dt: float):
        p = self.config.params
        bias = float(p.get("BIAS", 0.0))
        lo = float(p.get("OUT_LO", -1e9))
        hi = float(p.get("OUT_HI", 1e9))

        req = bool(self.get_input("TRK_REQ_D"))
        if int(p.get("INVERT_REQ", 0)):
            req = not req

        raw = float(self.get_input("IN")) + bias
        val = max(lo, min(hi, raw))
        self.set_output("TRK_VAL_OUT", val if req else 0.0)
        self.set_output("TRK_IN_D_OUT", req)
        self.set_output("ACTIVE", req)

        # Quality: the tracking value is only as good as IN while tracking;
        # off tracking the published 0.0 is a block constant, hence Good.
        if req:
            if raw >= hi:
                limit = LimitStatus.HIGH_LIMITED
            elif raw <= lo:
                limit = LimitStatus.LOW_LIMITED
            else:
                limit = LimitStatus.NOT_LIMITED
            self.set_output_status("TRK_VAL_OUT", self.input_status("IN"), limit)
        else:
            self.set_output_status("TRK_VAL_OUT", Quality.GOOD,
                                   LimitStatus.CONSTANT)
        for name in ("TRK_IN_D_OUT", "ACTIVE"):
            self.set_output_status(name, Quality.GOOD, LimitStatus.NOT_LIMITED)


# ═══════════════════════════════════════════════════════════════════════
#  CEM — Cause & Effect Matrix
# ═══════════════════════════════════════════════════════════════════════

@register_block
class CauseEffectMatrixBlock(FunctionBlock):
    """Cause & Effect Matrix (CEM) — 16×16 interlock matrix.

    Up to 16 boolean *causes* (IN1..IN16) map to up to 16 boolean
    *effects* (OUT1..OUT16) via a 16×16 matrix. When any cause linked
    to an effect is active, that effect trips. The first cause asserted
    is latched in ``FIRST_OUT`` so the operator can see *which* input
    triggered the trip.

    **Signal polarity — Azeo convention (default).** On both the cause
    and the effect side Azeo uses *fail-safe* de-energise-to-trip
    signalling: ``1 = inactive/Normal``, ``0 = active/Tripped``
    (see the CEM section of ``doc/AZEO_FUNCTION_BLOCKS.md``: "CAUSEn …
    inactive (1) / active (0)", "EFFECTn … Normal (1) / Tripped (0)",
    both defaulting to 0). A cause whose wire breaks, or is simply not
    wired, therefore reads 0 = *active* and trips; an effect that has
    not been solved yet reads 0 = *Tripped* and holds the final elements
    safe. ``SIGNAL_POLARITY`` selects the convention:

    * ``"AZEO"`` (default, correct) — 1 = Normal, 0 = Tripped on
      causes *and* effects; every effect initialises Tripped.
    * ``"ACTIVE_HIGH"`` — legacy pre-fix behaviour of this block:
      True = active cause, True = tripped effect, effects initialise
      Normal. **Fail-danger**; provided only so an existing strategy can
      be migrated deliberately rather than silently inverted.

    *Migration*: before this fix the block was unconditionally
    ``ACTIVE_HIGH`` and initialised every effect Normal. No strategy
    JSON in ``src/strategies/`` instantiated a CEM at the time of the
    change, so the default was switched to the Azeo convention. Any
    externally authored strategy that relied on the old polarity must
    either set ``SIGNAL_POLARITY = "ACTIVE_HIGH"`` or invert the wiring
    on both sides of the block (and re-check that its effects may now
    start Tripped).

    ``RESET`` and ``ANY_ACTIVE`` are block housekeeping signals, not
    process discretes, and stay active-high in both conventions: pulse
    ``RESET`` True to clear latched effects; ``ANY_ACTIVE`` is True while
    any effect is Tripped.

    Matrix is stored as 16 integers in ``MATRIX_ROW_N`` (one per cause)
    — each integer's bit *k* (LSB-first) decides whether cause *N+1*
    drives effect *k+1*. Bit math keeps the schema flat for JSON
    round-trip.

    ``LATCH_MODE`` (default 1): once an effect trips it stays tripped
    until RESET is pulsed — the analogue of Azeo's ``REQUIRE_RESETn``
    default of True, so under the Azeo convention the effects come up
    Tripped and need an operator reset before they go Normal.
    ``LATCH_MODE=0`` follows causes directly (the effects still read
    Tripped until the first solve).

    **Bad causes — ``STATUS_OPT``.** A cause is a value *and* a quality;
    terminals carry both (``model/terminal.py``), so the Azeo
    ``STATUS_OPT`` named set is honoured rather than reading a failed
    transmitter's leftover ``1`` as a healthy "inactive":

    * ``"ALWAYS_USE_VALUE"`` (default, Azeo default) — quality ignored,
      the value drives the matrix. A sensor failure can still shut the
      unit down through its value. This is exactly what the block did
      before quality existed, so the default changes nothing.
    * ``"USE_LAST_GOOD"`` — a Bad cause freezes at the last value it
      carried while Good, buying time to repair the transmitter without
      a spurious trip (and without a stuck-active cause either).
    * ``"TRIP_IF_BAD"`` — a Bad cause is treated as active and trips every
      effect associated with it.

    A masked cause (``CAUSE_MASK``) is inert in all three options — it
    can neither trip nor degrade an effect.

    Effect output quality follows the spec too: ``OUTn`` publishes **Bad**
    while any unmasked cause associated with it has Bad quality and the
    effect is not forced, otherwise Good. ``BAD_CAUSES`` exposes the
    bitmask of Bad causes for diagnostics; the housekeeping outputs
    (``ANY_ACTIVE``, ``FIRST_OUT``, ``STATEn`` …) stay Good.
    """
    block_type = "CEM"
    category = BlockCategory.SAFETY
    display_name = "Cause & Effect Matrix (CEM)"
    description = "16 causes × 16 effects interlock matrix with first-out"

    _NUM_IO = 16

    # Per-effect STATEn values (Azeo named set, spec §4934).
    _ST_NORMAL = 0
    _ST_TRIP_DELAYED = 1
    _ST_TRIPPED = 2
    _ST_READY_TO_RESET = 4

    # FORCE_EFFECTn values.
    _FORCE_NONE = 0
    _FORCE_NORMAL = 1
    _FORCE_TRIPPED = 2

    # STATUS_OPT (Azeo named set) — how a Bad cause is treated.
    _SO_ALWAYS = "ALWAYS_USE_VALUE"
    _SO_LAST_GOOD = "USE_LAST_GOOD"
    _SO_TRIP_IF_BAD = "TRIP_IF_BAD"

    config_choices = {
        "SIGNAL_POLARITY": ("AZEO", "ACTIVE_HIGH"),
        "STATUS_OPT": (_SO_ALWAYS, _SO_LAST_GOOD, _SO_TRIP_IF_BAD),
    }

    def __init__(self, instance_name: str = ""):
        # None = "not solved yet"; the first execute() seeds it from the
        # configured polarity (Azeo: every effect starts Tripped).
        self._latched: list[bool] | None = None
        self._first_out_idx = -1   # which cause (1..16) tripped first
        # Per-effect trip-delay timers and first-out cause bitstrings.
        self._delay_t = [0.0] * self._NUM_IO
        self._first_out = [0] * self._NUM_IO
        self._prev_reset = False
        # Last value each cause carried while its quality was Good
        # (STATUS_OPT = "USE_LAST_GOOD"); None until the first Good read.
        self._last_good: list[bool | None] = [None] * self._NUM_IO
        super().__init__(instance_name)

    def _define_terminals(self):
        for i in range(1, self._NUM_IO + 1):
            # Default 0: Azeo CAUSEn default = active/Tripped (fail-safe);
            # under ACTIVE_HIGH the same 0 reads as inactive.
            self.add_input(f"IN{i}", DataType.BOOL, False,
                           description=f"Cause {i} (Azeo: 1=Normal, 0=active)")
        self.add_input("RESET", DataType.BOOL, False,
                       "Reset latched effects (pulse True, active-high)")
        self.add_input("RESET_PERMIT", DataType.BOOL, True,
                       "Permit for RESET (active-high; True = reset allowed)")
        for i in range(1, self._NUM_IO + 1):
            # Default 0: Azeo EFFECTn default = Tripped, so a module that
            # has not solved yet fails safe rather than fail-danger.
            self.add_output(f"OUT{i}", DataType.BOOL, False,
                            description=f"Effect {i} (Azeo: 1=Normal, 0=Tripped)")
        self.add_output("ANY_ACTIVE", DataType.BOOL, False,
                        "True if any effect is tripped (active-high flag)")
        self.add_output("FIRST_OUT", DataType.INT, 0,
                        "Index (1..16) of first cause that tripped, 0 if none")
        self.add_output("ACTIVE_CAUSES", DataType.INT, 0,
                        "Bitmask of the causes currently active (bit k = cause k+1)")
        self.add_output("LATENT_TRIP", DataType.BOOL, False,
                        "True when a forced-Normal effect would otherwise be Tripped")
        t = self.add_output("BAD_CAUSES", DataType.INT, 0,
                            "Bitmask of the causes whose quality is Bad "
                            "(bit k = cause k+1)")
        t.hidden = True
        # Per-effect detail — declared for Azeo capability but hidden so the
        # canvas keeps the compact 16-cause / 16-effect footprint.
        for i in range(1, self._NUM_IO + 1):
            t = self.add_output(f"STATE{i}", DataType.INT, 0,
                                f"Effect {i} state: 0=Normal 1=Trip delayed "
                                f"2=Tripped 4=Ready to reset")
            t.hidden = True
        for i in range(1, self._NUM_IO + 1):
            t = self.add_output(f"FIRST_OUT_{i}", DataType.INT, 0,
                                f"Bitmask of the cause(s) that first tripped effect {i}")
            t.hidden = True

    def get_config_schema(self):
        schema = {
            "LATCH_MODE": (int, 1,
                "1 = effects latch until RESET, 0 = follow causes directly"),
            "SIGNAL_POLARITY": (str, "AZEO",
                "AZEO = 1 inactive/Normal, 0 active/Tripped on causes and "
                "effects, effects init Tripped (fail-safe). "
                "ACTIVE_HIGH = legacy True-is-active/tripped, init Normal"),
            "CAUSE_MASK": (int, 0,
                "Bitmask of causes to ignore (bit k = cause k+1); 0 = none masked"),
            "STATUS_OPT": (str, self._SO_ALWAYS,
                "Bad-cause handling: ALWAYS_USE_VALUE (Azeo default — "
                "quality ignored), USE_LAST_GOOD (freeze the cause at its "
                "last Good value), TRIP_IF_BAD (a Bad cause trips its effects)"),
            "RESET_AUTOCLEAR": (bool, False,
                "Azeo RESETn self-clears each execution: act on the rising "
                "edge only, so a held RESET cannot defeat latching"),
            "FORCE_PERMIT": (bool, False, "Permit FORCE_EFFECT_n overrides"),
        }
        for i in range(1, self._NUM_IO + 1):
            schema[f"MATRIX_ROW_{i}"] = (
                int, 0,
                f"Bitmask: bit k=1 means cause {i} drives effect (k+1)")
        for i in range(1, self._NUM_IO + 1):
            schema[f"DELAY_TIME_{i}"] = (
                float, 0.0, f"Trip delay for effect {i} [s]; 0 = immediate")
            schema[f"REQUIRE_RESET_{i}"] = (
                int, -1,
                f"Effect {i} latching: -1 = follow LATCH_MODE, 0 = no, 1 = yes")
            schema[f"FORCE_EFFECT_{i}"] = (
                int, 0,
                f"Force effect {i}: 0 = do not force, 1 = Normal, 2 = Tripped "
                f"(needs FORCE_PERMIT)")
        return schema

    def reset(self):
        super().reset()
        self._latched = None          # re-seeded (Tripped) on the next solve
        self._first_out_idx = -1
        self._delay_t = [0.0] * self._NUM_IO
        self._first_out = [0] * self._NUM_IO
        self._prev_reset = False
        self._last_good = [None] * self._NUM_IO

    def _fail_safe(self) -> bool:
        """True when the block uses the Azeo 1=Normal / 0=Tripped convention."""
        mode = str(self.config.params.get("SIGNAL_POLARITY", "AZEO")).strip().upper()
        return mode not in ("ACTIVE_HIGH", "HIGH", "LEGACY")

    def _status_opt(self) -> str:
        """Normalised ``STATUS_OPT``; anything unrecognised = Azeo default."""
        raw = str(self.config.params.get("STATUS_OPT", self._SO_ALWAYS))
        opt = raw.strip().upper().replace(" ", "_")
        aliases = {
            "ALWAYS_USE_VALUE": self._SO_ALWAYS, "ALWAYS": self._SO_ALWAYS,
            "USE_LAST_GOOD": self._SO_LAST_GOOD,
            "USE_LAST_GOOD_VALUE_IF_BAD": self._SO_LAST_GOOD,
            "LAST_GOOD": self._SO_LAST_GOOD,
            "TRIP_IF_BAD": self._SO_TRIP_IF_BAD, "TRIP": self._SO_TRIP_IF_BAD,
        }
        return aliases.get(opt, self._SO_ALWAYS)

    def _read_causes(self) -> tuple[list[bool], list[bool]]:
        """Cause discretes after ``STATUS_OPT``, plus their Bad-quality flags.

        Returns ``(values, bad)`` where ``values`` are the raw discretes as
        the matrix should see them (still in the configured polarity — the
        caller converts to "active") and ``bad[i]`` is True when cause i+1
        carries Bad quality.
        """
        opt = self._status_opt()
        values: list[bool] = []
        bad: list[bool] = []
        for i in range(self._NUM_IO):
            pin = f"IN{i + 1}"
            val = bool(self.get_input(pin))
            is_bad = self.input_status(pin) is Quality.BAD
            bad.append(is_bad)
            if not is_bad:
                # Remember the healthy reading for USE_LAST_GOOD.
                self._last_good[i] = val
            elif opt == self._SO_LAST_GOOD and self._last_good[i] is not None:
                val = self._last_good[i]
            values.append(val)
        return values, bad

    def _require_reset(self, k: int, latch: bool) -> bool:
        """Per-effect REQUIRE_RESETn, falling back to the global LATCH_MODE."""
        try:
            val = int(self.config.params.get(f"REQUIRE_RESET_{k + 1}", -1))
        except (TypeError, ValueError):
            val = -1
        return latch if val < 0 else bool(val)

    def execute(self, dt: float):
        p = self.config.params
        latch = bool(int(p.get("LATCH_MODE", 1)))
        fail_safe = self._fail_safe()

        if self._latched is None:
            # Azeo: "After download/restart every effect starts Tripped."
            # ACTIVE_HIGH keeps the legacy (fail-danger) Normal start.
            self._latched = [fail_safe] * self._NUM_IO

        # RESET — level-triggered by default; RESET_AUTOCLEAR reproduces
        # Azeo's RESETn, which clears itself every execution so holding
        # the input True cannot keep every effect permanently reset.
        reset_in = bool(self.get_input("RESET"))
        reset_now = (reset_in and not self._prev_reset) \
            if p.get("RESET_AUTOCLEAR", False) else reset_in
        self._prev_reset = reset_in
        if reset_now and bool(self.get_input("RESET_PERMIT")):
            self._latched = [False] * self._NUM_IO
            self._first_out_idx = -1
            self._first_out = [0] * self._NUM_IO
            self._delay_t = [0.0] * self._NUM_IO

        # A cause is *active* when its discrete reads 0 under the Azeo
        # convention, or True under the legacy active-high convention.
        # STATUS_OPT decides what a Bad-quality cause contributes.
        raw_causes, bad_causes = self._read_causes()
        causes = [v != fail_safe for v in raw_causes]
        if self._status_opt() == self._SO_TRIP_IF_BAD:
            causes = [c or bad_causes[i] for i, c in enumerate(causes)]
        try:
            cause_mask = int(p.get("CAUSE_MASK", 0))
        except (TypeError, ValueError):
            cause_mask = 0
        if cause_mask:
            # A masked cause is inert: it can neither trip an effect nor
            # degrade its quality.
            causes = [c and not (cause_mask & (1 << i))
                      for i, c in enumerate(causes)]
            bad_causes = [b and not (cause_mask & (1 << i))
                          for i, b in enumerate(bad_causes)]
        bad_bits = 0
        for i, b in enumerate(bad_causes):
            if b:
                bad_bits |= 1 << i
        active_bits = 0
        for i, c in enumerate(causes):
            if c:
                active_bits |= 1 << i

        # Per-effect Bad-cause association: an effect's quality is Bad while
        # any unmasked cause wired to it reads Bad (spec §CEM status handling).
        effect_bad = [False] * self._NUM_IO
        for c_idx, is_bad in enumerate(bad_causes):
            if not is_bad:
                continue
            try:
                row = int(p.get(f"MATRIX_ROW_{c_idx + 1}", 0))
            except (TypeError, ValueError):
                row = 0
            for k in range(self._NUM_IO):
                if row & (1 << k):
                    effect_bad[k] = True

        # Effect k is asserted if any cause c with matrix[c][k]=1 is active
        effects = [False] * self._NUM_IO
        effect_causes = [0] * self._NUM_IO
        for c_idx, c_active in enumerate(causes):
            if not c_active:
                continue
            row = int(p.get(f"MATRIX_ROW_{c_idx + 1}", 0))
            for k in range(self._NUM_IO):
                if row & (1 << k):
                    effects[k] = True
                    effect_causes[k] |= 1 << c_idx
            # First-out tracking — capture first cause that maps to ANY effect
            if row != 0 and self._first_out_idx < 0:
                self._first_out_idx = c_idx + 1

        # Per-effect trip delay (DELAY_TIME_n, 0 = immediate).
        delayed = [False] * self._NUM_IO
        for k in range(self._NUM_IO):
            try:
                delay = max(0.0, float(p.get(f"DELAY_TIME_{k + 1}", 0.0)))
            except (TypeError, ValueError):
                delay = 0.0
            if not effects[k]:
                self._delay_t[k] = 0.0
                continue
            if delay <= 0.0:
                delayed[k] = True
                continue
            self._delay_t[k] += dt
            delayed[k] = self._delay_t[k] >= delay
        effects = delayed

        out_states = [False] * self._NUM_IO
        for k in range(self._NUM_IO):
            require_reset = self._require_reset(k, latch)
            if effects[k]:
                self._latched[k] = True
            elif not require_reset:
                self._latched[k] = False
            out_states[k] = self._latched[k] if require_reset else effects[k]
            # Per-effect first-out: the cause(s) that tripped *this* effect,
            # held until the effect returns Normal.
            if out_states[k]:
                if not self._first_out[k] and effect_causes[k]:
                    self._first_out[k] = effect_causes[k]
            else:
                self._first_out[k] = 0
        if not latch and not any(causes):
            # Non-latching mode tracks first-out only while causes are active
            self._first_out_idx = -1

        # Forcing (FORCE_EFFECT_n, gated by FORCE_PERMIT).  A forced-Normal
        # effect that would otherwise be Tripped raises LATENT_TRIP.
        force_permit = bool(p.get("FORCE_PERMIT", False))
        latent = False
        forced = [False] * self._NUM_IO
        if force_permit:
            for k in range(self._NUM_IO):
                try:
                    force = int(p.get(f"FORCE_EFFECT_{k + 1}", 0))
                except (TypeError, ValueError):
                    force = self._FORCE_NONE
                if force == self._FORCE_NORMAL:
                    if out_states[k]:
                        latent = True
                    out_states[k] = False
                    forced[k] = True
                elif force == self._FORCE_TRIPPED:
                    out_states[k] = True
                    forced[k] = True

        # out_states[k] is True when effect k+1 is *tripped*; publish it in
        # the configured polarity (Azeo: Tripped = 0, Normal = 1).
        any_active = False
        for k, tripped in enumerate(out_states):
            self.set_output(f"OUT{k + 1}", (not tripped) if fail_safe else tripped)
            if tripped:
                any_active = True
            if tripped:
                state = (self._ST_READY_TO_RESET if not effects[k]
                         else self._ST_TRIPPED)
            elif self._delay_t[k] > 0.0:
                state = self._ST_TRIP_DELAYED
            else:
                state = self._ST_NORMAL
            self.set_output(f"STATE{k + 1}", state)
            self.set_output(f"FIRST_OUT_{k + 1}", self._first_out[k])
            # Effect quality: Bad while an unmasked associated cause is Bad
            # and the effect is not forced; otherwise Good (spec §CEM).
            self.set_output_status(
                f"OUT{k + 1}",
                Quality.BAD if (effect_bad[k] and not forced[k]) else Quality.GOOD,
                LimitStatus.CONSTANT if forced[k] else LimitStatus.NOT_LIMITED)
            self.set_output_status(f"STATE{k + 1}", Quality.GOOD)
            self.set_output_status(f"FIRST_OUT_{k + 1}", Quality.GOOD)
        self.set_output("ANY_ACTIVE", any_active)
        self.set_output("ACTIVE_CAUSES", active_bits)
        self.set_output("BAD_CAUSES", bad_bits)
        self.set_output("LATENT_TRIP", latent)
        self.set_output("FIRST_OUT",
                        max(0, self._first_out_idx) if any_active or self._first_out_idx > 0
                        else 0)
        # Housekeeping outputs are block-computed and always Good.
        for name in ("ANY_ACTIVE", "ACTIVE_CAUSES", "BAD_CAUSES",
                     "LATENT_TRIP", "FIRST_OUT"):
            self.set_output_status(name, Quality.GOOD, LimitStatus.NOT_LIMITED)


# ═══════════════════════════════════════════════════════════════════════
#  CND — Condition (expression + time-true delay)
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ConditionBlock(FunctionBlock):
    """Condition (CND) — boolean expression with built-in time-true delay.

    Evaluates an EXPRESSION using the same safe-AST evaluator as the
    ACT block. Output ``OUT_D`` goes True once the expression has been
    continuously True for at least ``TIME_TRUE`` seconds; clears to
    False as soon as the expression evaluates False.

    Variables in the expression:
        IN1..IN8   — analog input terminal values
        b1..b8     — boolean view of IN1..IN8 (input != 0)
        x, y, z    — aliases for IN1..IN2..IN3
        dt, time   — scan period, accumulated runtime
    Plus the full ACT script namespace (clamp, hysteresis, etc.) and
    plugin-registered helpers.

    **Signal quality (spec "Status handling").** ``OUT_D`` / ``PRE_OUT_D``
    / ``RAW`` are normally GoodNonCascade, modelled here as the worst
    quality among ``IN1``…``IN8`` — an expression over a Bad measurement
    yields a Bad verdict rather than a confident-looking False. An empty
    expression is Azeo's *Bad: Configuration Error*, and an evaluation
    error is *BadNoComm*; with ``ALGO_OPTS_ABORT_ON_READ_ERR`` the
    evaluation aborts and both the value **and** the status are left
    untouched, exactly as the spec requires.

    **Timing (CND-4).** Azeo satisfies a delay of one block period or
    less at the next execution; this block accumulates ``dt`` and compares
    ``>=``, so ``TIME_TRUE = 0`` likewise sets ``OUT_D`` on the first
    execution the expression is True. The two agree to within one scan.

    **Expression language (CND-5).** Azeo writes structured text over
    ``'/MOD/PARAM.CV'`` external references; this block evaluates a Python
    subset over the wired ``IN1``…``IN8`` terminals via the same safe-AST
    evaluator as ACT. A deliberate, documented divergence — the platform
    wires signals instead of resolving module references — so Azeo
    expressions are transcribed, not pasted.
    """
    block_type = "CND"
    category = BlockCategory.LOGIC
    display_name = "Condition (CND)"
    description = "Expression with time-true delay; OUT_D=True after stable for T"

    #: Azeo spellings of the same pins/parameters (spec §3511).
    terminal_aliases = {"TIMER": "ET"}
    config_aliases = {"TIME_DURATION": "TIME_TRUE"}
    config_choices = {"ERROR_OPT": ("FALSE", "TRUE", "HOLD")}
    config_units = {"TIME_TRUE": "s"}

    def __init__(self, instance_name: str = ""):
        self._t_true = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        for i in range(1, 9):
            self.add_input(f"IN{i}", description=f"Input {i}")
        self.add_input("DISABLE", DataType.BOOL, False,
                       "True = bypass the block logic (OUT_D never set)")
        self.add_output("OUT_D", DataType.BOOL, False,
                        "True after expression stays True for TIME_TRUE")
        self.add_output("PRE_OUT_D", DataType.BOOL, False,
                        "Delayed result ignoring DISABLE (Azeo PRE_OUT_D)")
        self.add_output("RAW",   DataType.BOOL, False,
                        "Raw (un-delayed) result of the expression")
        self.add_output("ET",    DataType.FLOAT, 0.0,
                        "Elapsed time the expression has been True")

    def get_config_schema(self):
        return {
            "EXPRESSION": (str, "IN1 > 50",
                "Boolean expression — uses ACT-block syntax"),
            "TIME_TRUE":  (float, 0.0,
                "Seconds expression must be True before OUT_D=True"),
            "ERROR_OPT": (str, "FALSE",
                "Evaluation-error result: FALSE (Azeo default), TRUE, or "
                "HOLD (keep the last value)"),
            "ALGO_OPTS_ABORT_ON_READ_ERR": (bool, False,
                "Abort evaluation on an error, leaving OUT_D / PRE_OUT_D "
                "value and status unchanged"),
        }

    #: Outputs that carry the verdict's quality (ET is a block timer).
    _RESULT_OUTS = ("OUT_D", "PRE_OUT_D", "RAW")

    def reset(self):
        super().reset()
        self._t_true = 0.0

    def _set_result_status(self, quality: "Quality") -> None:
        for name in self._RESULT_OUTS:
            self.set_output_status(name, quality, LimitStatus.NOT_LIMITED)
        self.set_output_status("ET", Quality.GOOD, LimitStatus.NOT_LIMITED)

    def execute(self, dt: float):
        from .action_block import _safe_eval, SAFE_MATH_FUNCTIONS
        from .script_functions import get_script_functions
        p = self.config.params
        expr = str(p.get("EXPRESSION", "")).strip()
        if not expr:
            # BLOCK_ERR "Configuration Error — the expression is empty";
            # Azeo publishes Bad: Configuration Error on both discretes.
            self.set_output("OUT_D", False)
            self.set_output("PRE_OUT_D", False)
            self.set_output("RAW", False)
            self.set_output("ET", 0.0)
            self._set_result_status(Quality.BAD)
            return

        ns: dict[str, Any] = dict(SAFE_MATH_FUNCTIONS)
        ns.update(get_script_functions())
        for i in range(1, 9):
            v = self.get_input(f"IN{i}")
            ns[f"IN{i}"] = v
            ns[f"b{i}"] = bool(v) if not isinstance(v, bool) else v
        ns["x"] = ns["IN1"]; ns["y"] = ns["IN2"]; ns["z"] = ns["IN3"]
        ns["dt"] = dt
        ns["step_time"] = self._t_true      # same value ET publishes

        # Azeo-style direct references — the reason the real CND needs
        # no wired inputs at all: the expression IS the wiring.
        # `param('PID1/OUT')` reads another block's terminal (or config
        # parameter) in this module; `tag('LI-101.PV')` reads a store
        # point. The quality of every terminal referenced folds into the
        # verdict's status exactly as the wired inputs' quality does — a
        # verdict over a Bad reference must not look confident.
        wired_status = self.worst_input_status(
            *(f"IN{i}" for i in range(1, 9)))
        ref_qualities = [wired_status]
        graph = getattr(self, "_module_graph", None)
        context = getattr(self, "runtime_context", None)

        def param(path):
            if graph is None:
                raise ValueError("param(): module not on scan yet")
            name, _, item = str(path).strip("/").partition("/")
            other = next((b for b in graph.blocks.values()
                          if b.instance_name == name), None)
            if other is None:
                raise ValueError(f"param(): no block {name!r} in module")
            terminal = other.outputs.get(item) or other.inputs.get(item)
            if terminal is not None:
                ref_qualities.append(terminal.status)
                return terminal.value
            if item in other.config.params:
                return other.config.params[item]
            raise ValueError(
                f"param(): {name} has no terminal or parameter {item!r}")

        def tag(key, default=None):
            if context is None:
                raise ValueError("tag(): no runtime context")
            value = context.read(str(key))
            if value is None:
                if default is not None:
                    return default
                raise ValueError(f"tag(): {key!r} not in the store")
            return value

        ns["param"] = param
        ns["tag"] = tag
        # Module parameters resolve *bare* — `SP_HI_LIM`, not a call —
        # exactly as a Azeo expression reads them.
        if graph is not None:
            for _name, _spec in graph.module_parameters().items():
                ns.setdefault(_name, _spec.get("value"))

        # Normally GoodNonCascade, degraded to the worst wired input's
        # quality — a verdict is only as trustworthy as its measurements.
        prev_status = self.outputs["OUT_D"].status
        self._set_result_status(wired_status)

        try:
            result = bool(_safe_eval(expr, ns))
            # Fold in the references the evaluation actually touched.
            # (`Quality` is a plain Enum — max() needs the value key, and
            # a TypeError here would masquerade as an expression error.)
            self._set_result_status(
                max(ref_qualities, key=lambda quality: quality.value))
        except Exception:
            # Evaluation error — mark the block BAD and apply the Azeo
            # error handling.  AbortOnReadErrors leaves OUT_D / PRE_OUT_D
            # (and the timer) exactly as they were; otherwise ERROR_OPT
            # decides the substituted result, defaulting to False as
            # Azeo does.
            self.status = BlockStatus.BAD
            if p.get("ALGO_OPTS_ABORT_ON_READ_ERR", False):
                # "Value and status remain unchanged" — restore the quality
                # this scan's propagation would otherwise have overwritten.
                self._set_result_status(prev_status)
                return
            # Azeo: on a read error the discretes go BadNoComm.
            self._set_result_status(Quality.BAD)
            opt = str(p.get("ERROR_OPT", "FALSE")).strip().upper()
            if opt == "HOLD":
                result = bool(self.get_output("RAW"))
            else:
                result = opt == "TRUE"

        if result:
            self._t_true += dt
        else:
            self._t_true = 0.0

        t_required = max(0.0, float(p.get("TIME_TRUE", 0.0)))
        pre_out_d = result and self._t_true >= t_required
        # DISABLE bypasses the block logic: OUT_D is never set, but
        # PRE_OUT_D still tracks so it can be used elsewhere.
        out_d = pre_out_d and not bool(self.get_input("DISABLE"))
        self.set_output("RAW", result)
        self.set_output("PRE_OUT_D", pre_out_d)
        self.set_output("OUT_D", out_d)
        self.set_output("ET", self._t_true)
