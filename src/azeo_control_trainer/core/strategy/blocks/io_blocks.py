"""I/O function blocks — interface between strategy and SharedDataStore.

The four blocks here (AI / AO / DI / DO) model the Azeo I/O blocks of the
same name (``doc/AZEO_FUNCTION_BLOCKS.md`` §146 AI, §241 AO, §564 DI,
§628 DO).  Everything added while closing the block audit is **additive**:
new config keys and new terminals whose defaults reproduce the previous
behaviour exactly, because shipped strategy JSON references the existing
names (``tag``, ``out_lo``/``out_hi``, ``OUT``, ``CAS_IN``, ``IN`` …) and
none of them may be renamed.
"""
from __future__ import annotations

import logging
import math

from ..model.terminal import Quality, LimitStatus
from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType)
from ..model.block_registry import register_block

log = logging.getLogger("strategy.io")


#: Alarm keys shared by the AI limit checks, in Azeo order.
_AI_ALARMS = ("HI_HI", "HI", "LO", "LO_LO")

#: A limited channel — either end of the range.
_LIMITED = (LimitStatus.HIGH_LIMITED, LimitStatus.LOW_LIMITED)

#: Quality → BlockStatus, for the block-level status summary.
_BLOCK_STATUS = {
    Quality.GOOD: BlockStatus.GOOD,
    Quality.UNCERTAIN: BlockStatus.UNCERTAIN,
    Quality.BAD: BlockStatus.BAD,
}


def _as_float(value, fallback: float) -> float:
    """``float(value)`` or ``fallback`` — never raises."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


@register_block
class AIBlock(FunctionBlock):
    """Azeo-style Analog Input function block.

    Reads a process value from SharedDataStore.  Supports OOS/Man/Auto
    modes, signal conversion (``L_TYPE``), low cutoff, a first-order PV
    filter, simulation, and alarm limits (HI_HI, HI, LO, LO_LO with
    hysteresis and on/off delays).

    **Signal path** (Azeo §146 "Signal conversion"/"Filtering")::

        field tag ─▶ FIELD_VAL (% of XD_SCALE)
                  ─▶ L_TYPE conversion ─▶ LOW_CUT ─▶ PV_FTIME filter ─▶ PV
                  ─▶ mode switch ─▶ OUT (Auto: OUT = PV, Man: operator)
                  ─▶ alarm detection (on OUT, per the spec)

    ``L_TYPE`` defaults to ``DIRECT``, ``LOW_CUT`` is disabled by default
    and ``mode`` defaults to AUTO, so a block configured the way every
    shipped strategy configures one (``tag`` + scale + alarm limits)
    behaves exactly as it did before these parameters existed.

    *Man mode* — the operator writes OUT while PV keeps tracking the
    field (``set_manual_output()``).  The :class:`DataBridge` routes
    ``ai.<name>.wb.Mode`` / ``ai.<name>.wb.ManOut`` into ``set_mode()`` /
    ``set_manual_output()``, mirroring the AO write-back path, so a
    faceplate can drive it.

    **Status handling** (Azeo §146 "Status handling").  Terminals carry
    a :class:`Quality` and a :class:`LimitStatus` (``model/terminal.py``),
    so the manual's status rules are real behaviour rather than a
    side-channel pin:

    * the channel quality comes from the bridge (``set_field_status()``)
      — a missing or non-numeric store tag is **Bad**, not a silent 0.0 —
      combined with over/under-range and NAMUR detection on the channel
      value;
    * ``STATUS_OPTS`` are the ``OPT_*`` booleans: *Bad if Limited*,
      *Uncertain if Limited*, *Use Uncertain as Good*, *Uncertain if Man
      mode*, *Target to Manual if Bad IN* and *IFS if Bad IN*;
    * in Auto ``OUT`` reflects PV value/status; in Man ``OUT`` limit is
      Constant and its status Good (unless *Uncertain if Man mode*); in
      OOS ``OUT`` status is Bad.

    Every detector is off by default (``RANGE_CHECK_ENA``, ``NAMUR_ENA``,
    ``NAMUR_ALARM_ENA`` = False and all six ``OPT_*`` False), so a block
    reading a healthy tag publishes Good / Not limited exactly as before.
    """
    block_type = "AI"
    category = BlockCategory.IO
    display_name = "Analog Input"
    description = "Azeo-style AI — modes, conversion, PV filter, alarms"

    _OOS = "OOS"
    _MAN = "MAN"
    _AUTO = "AUTO"

    _L_TYPES = ("DIRECT", "DIRECT_INDEPENDENT", "INDIRECT", "INDIRECT_SQRT")

    config_choices = {
        "L_TYPE": _L_TYPES,
        "mode": ("AUTO", "MAN", "OOS"),
    }
    config_units = {
        "PV_FTIME": "s",
        "HI_HI_DELAY_ON": "s", "HI_HI_DELAY_OFF": "s",
        "HI_DELAY_ON": "s", "HI_DELAY_OFF": "s",
        "LO_DELAY_ON": "s", "LO_DELAY_OFF": "s",
        "LO_LO_DELAY_ON": "s", "LO_LO_DELAY_OFF": "s",
        "NAMUR_HI_MA": "mA", "NAMUR_LO_MA": "mA",
        "NAMUR_TIME": "s", "NAMUR_ALARM_TIME": "s",
        "NAMUR_ALARM_PCT": "% of OUT_SCALE",
        "OVERRANGE_HI_PCT": "% of XD_SCALE",
        "UNDERRANGE_LO_PCT": "% of XD_SCALE",
        "CHANNEL_BAD_HI_PCT": "% of XD_SCALE",
        "CHANNEL_BAD_LO_PCT": "% of XD_SCALE",
    }

    def __init__(self, instance_name: str = ""):
        self._actual_mode: str = self._AUTO
        self._pv_filt: float = 0.0
        self._man_out: float = 0.0
        self._initialized: bool = False
        # Alarm latch + on/off-delay timer per alarm key
        self._alm: dict[str, bool] = {k: False for k in _AI_ALARMS}
        self._alm_t: dict[str, float] = {k: 0.0 for k in _AI_ALARMS}
        # Channel quality published by the DataBridge (set_field_status).
        self._chan_status: Quality = Quality.GOOD
        # (value, quality) offered by the bridge — see set_field_value()
        self._field_offer: tuple | None = None
        # NAMUR / overrange persistence timers (the spec's 4 s hold-off)
        self._namur_t: float = 0.0
        self._namur_bad: bool = False
        super().__init__(instance_name)

    def _define_terminals(self):
        # Inputs
        self.add_input("SIMULATE_IN", description="Simulation input value")
        # Outputs
        self.add_output("OUT", description="Process value (filtered)")
        self.add_output("PV", description="Process variable (same as OUT in Auto)")
        self.add_output("FIELD_VAL", description="Field value [% of XD_SCALE]")
        self.add_output("STATUS", DataType.BOOL, True, "Signal good")
        self.add_output("MODE", DataType.STRING, "AUTO", "Current mode")
        self.add_output("HI_HI_ACT", DataType.BOOL, False, "Hi-Hi alarm active")
        self.add_output("HI_ACT", DataType.BOOL, False, "Hi alarm active")
        self.add_output("LO_ACT", DataType.BOOL, False, "Lo alarm active")
        self.add_output("LO_LO_ACT", DataType.BOOL, False, "Lo-Lo alarm active")
        self.add_output("OVERRANGE", DataType.BOOL, False,
                        "Channel value above the overrange limit")
        self.add_output("UNDERRANGE", DataType.BOOL, False,
                        "Channel value below the underrange limit")
        self.add_output("IFS_ACT", DataType.BOOL, False,
                        "Initiate Fault State substatus active (IFS if Bad IN)")

    def get_config_schema(self):
        schema = {
            "tag": (str, "", "Data store tag to read (e.g. 'COT', 'ctrl.TIC101.PV')"),
            "label": (str, "", "Display label for faceplate (defaults to instance name)"),
            "scale_lo": (float, 0.0, "Engineering units low (OUT_SCALE EU0)"),
            "scale_hi": (float, 100.0, "Engineering units high (OUT_SCALE EU100)"),
            "eng_units": (str, "", "Engineering units label"),
            "L_TYPE": (str, "DIRECT",
                       "Signal conversion: DIRECT (pass through), "
                       "DIRECT_INDEPENDENT, INDIRECT (XD_SCALE->OUT_SCALE), "
                       "INDIRECT_SQRT"),
            "XD_SCALE_LO": (float, 0.0, "Channel (transducer) scale low"),
            "XD_SCALE_HI": (float, 100.0, "Channel (transducer) scale high"),
            "IO_OPTS_LOW_CUTOFF": (bool, False, "Enable the Low Cutoff I/O option"),
            "LOW_CUT": (float, 0.0, "With Low Cutoff enabled, PV=0 below this value"),
            "PV_FTIME": (float, 0.0, "PV filter time [s]"),
            "HI_HI_LIM": (float, float('inf'), "Hi-Hi alarm limit"),
            "HI_LIM": (float, float('inf'), "Hi alarm limit"),
            "LO_LIM": (float, float('-inf'), "Lo alarm limit"),
            "LO_LO_LIM": (float, float('-inf'), "Lo-Lo alarm limit"),
            "ALARM_HYS": (float, 0.0, "Alarm hysteresis"),
            "ALARM_HYS_PCT": (bool, False,
                              "Interpret ALARM_HYS as % of OUT_SCALE span "
                              "(Azeo) instead of engineering units"),
            "mode": (str, "AUTO", "Initial mode: OOS, MAN, AUTO"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
            "simulate_enabled": (bool, False, "Simulation mode"),
            "SIMULATE": (float, 0.0,
                         "Manually entered simulate value, used when "
                         "SIMULATE_IN is not connected"),
            "SIMULATE_STATUS_GOOD": (bool, True, "Status of the manual SIMULATE value"),
            # ── STATUS_OPTS (Azeo: settable in OOS only) ───────────
            "OPT_UNCERTAIN_IF_MAN": (
                bool, False, "STATUS_OPTS: Uncertain if Man mode "
                             "(OUT status Uncertain while in Man)"),
            "OPT_BAD_IF_LIMITED": (
                bool, False, "STATUS_OPTS: Bad if Limited "
                             "(PV Bad when the channel status is limited)"),
            "OPT_UNCERTAIN_IF_LIMITED": (
                bool, False, "STATUS_OPTS: Uncertain if Limited (PV Uncertain "
                             "when limited, even if the channel is Bad)"),
            "OPT_USE_UNCERTAIN_AS_GOOD": (
                bool, False, "STATUS_OPTS: Use Uncertain as Good "
                             "(an Uncertain PV is published Good)"),
            "OPT_TARGET_TO_MANUAL_IF_BAD_IN": (
                bool, False, "STATUS_OPTS: Target to Manual if Bad IN — a Bad PV "
                             "drives target and actual mode to Man and they stay "
                             "Man after the measurement recovers"),
            "OPT_IFS_IF_BAD_IN": (
                bool, False, "STATUS_OPTS: IFS if Bad IN — a Bad PV sets the "
                             "Initiate-Fault-State substatus (IFS_ACT) and forces "
                             "OUT status Bad even in Man"),
            # ── Channel over / under-range detection ─────────────────
            "RANGE_CHECK_ENA": (
                bool, False, "Enable channel over/under-range detection on "
                             "FIELD_VAL (also enables the OUT_SCALE -10..110 % "
                             "Uncertain rule)"),
            "OVERRANGE_HI_PCT": (
                float, 100.0, "Channel status goes high-limited above this "
                              "percent of XD_SCALE"),
            "UNDERRANGE_LO_PCT": (
                float, 0.0, "Channel status goes low-limited below this percent "
                            "of XD_SCALE"),
            "CHANNEL_BAD_HI_PCT": (
                float, 116.6, "Channel status goes Bad above this percent of "
                              "XD_SCALE (Classic I/O default)"),
            "CHANNEL_BAD_LO_PCT": (
                float, -20.12, "Channel status goes Bad below this percent of "
                               "XD_SCALE (Classic I/O default)"),
            # ── NAMUR limit detection ────────────────────────────────
            "NAMUR_ENA": (
                bool, False, "Enable NAMUR limit detection on the 4-20 mA signal"),
            "NAMUR_HI_MA": (float, 21.0, "PV goes Bad above this signal current"),
            "NAMUR_LO_MA": (float, 3.6, "PV goes Bad below this signal current"),
            "NAMUR_TIME": (
                float, 4.0, "Seconds the NAMUR condition must persist before PV "
                            "goes Bad"),
            "NAMUR_ALARM_ENA": (
                bool, False, "NAMUR alarming: HI_HI/LO_LO limits placed outside "
                             "OUT_SCALE annunciate overrange/underrange"),
            "NAMUR_ALARM_PCT": (
                float, 3.0, "Limits further than this percent outside the OUT "
                            "span delay the alarm by NAMUR_ALARM_TIME"),
            "NAMUR_ALARM_TIME": (
                float, 4.0, "Persistence required for a delayed NAMUR alarm"),
        }
        for key in _AI_ALARMS:
            schema[f"{key}_ENAB"] = (bool, True, f"Enable {key} alarm detection")
            schema[f"{key}_DELAY_ON"] = (
                float, 0.0, f"Seconds the {key} condition must persist before it activates")
            schema[f"{key}_DELAY_OFF"] = (
                float, 0.0, f"Seconds the {key} condition must clear before it deactivates")
        return schema

    def _apply_config(self):
        p = self.config.params
        mode_str = p.get("mode", "AUTO").upper()
        if mode_str == "MANUAL":
            mode_str = self._MAN
        self._actual_mode = mode_str if mode_str in (
            self._OOS, self._MAN, self._AUTO) else self._AUTO
        self._initialized = False
        self.set_terminal_eu("OUT", str(p.get("eng_units", "")),
                             self._out_scale())

    # ---------------------------------------------------------------- helpers
    def _out_scale(self) -> tuple[float, float]:
        p = self.config.params
        return float(p.get("scale_lo", 0.0)), float(p.get("scale_hi", 100.0))

    def _xd_scale(self) -> tuple[float, float]:
        p = self.config.params
        return float(p.get("XD_SCALE_LO", 0.0)), float(p.get("XD_SCALE_HI", 100.0))

    def _convert(self, raw: float) -> float:
        """Apply L_TYPE signal conversion (Azeo §146 "Signal conversion")."""
        l_type = str(self.config.params.get("L_TYPE", "DIRECT")).strip().upper()
        if l_type in ("", "DIRECT", "DIRECT_INDEPENDENT"):
            # Direct / Direct Independent both pass the channel value
            # through; they differ only in how OUT_SCALE is displayed.
            return raw
        xd_lo, xd_hi = self._xd_scale()
        eu_lo, eu_hi = self._out_scale()
        xd_span = xd_hi - xd_lo
        if xd_span == 0.0:
            return raw
        frac = (raw - xd_lo) / xd_span
        if l_type in ("INDIRECT_SQRT", "INDIRECT_SQUARE_ROOT", "SQRT"):
            if raw < 0.0:
                # Spec: below zero the conditioning stops and the input
                # passes directly to the output.
                return raw
            frac = math.sqrt(max(frac, 0.0))
        return eu_lo + frac * (eu_hi - eu_lo)

    def _hysteresis(self) -> float:
        p = self.config.params
        hys = float(p.get("ALARM_HYS", 0.0))
        if not p.get("ALARM_HYS_PCT", False):
            return hys
        lo, hi = self._out_scale()
        span = abs(hi - lo)
        # Azeo: ALARM_HYS is % of scale, clamped to 50% of span.
        return min(abs(hys) / 100.0 * span, 0.5 * span)

    def _alarm_step(self, key: str, cond: bool, dt: float,
                    min_delay_on: float = 0.0) -> bool:
        """Latch ``cond`` through the per-alarm enable and on/off delays.

        ``min_delay_on`` raises the activation delay — used by NAMUR
        alarming, where an overrange condition must persist longer than
        ``NAMUR_ALARM_TIME`` before the alarm activates.
        """
        p = self.config.params
        if not p.get(f"{key}_ENAB", True):
            self._alm[key] = False
            self._alm_t[key] = 0.0
            return False
        state = self._alm[key]
        if cond == state:
            self._alm_t[key] = 0.0
            return state
        delay = float(p.get(f"{key}_DELAY_ON" if cond else f"{key}_DELAY_OFF", 0.0))
        if cond:
            delay = max(delay, min_delay_on)
        self._alm_t[key] += dt
        if self._alm_t[key] >= delay:
            self._alm[key] = cond
            self._alm_t[key] = 0.0
        return self._alm[key]

    def _clear_alarms(self):
        for key in _AI_ALARMS:
            self._alm[key] = False
            self._alm_t[key] = 0.0
            self.set_output(f"{key}_ACT", False)
        self.set_output("OVERRANGE", False)
        self.set_output("UNDERRANGE", False)
        self.set_output("IFS_ACT", False)

    # ------------------------------------------------------- status handling
    def set_field_value(self, value, quality: Quality = Quality.GOOD):
        """Live measurement offered by the :class:`DataBridge` each scan.

        The bridge only *drives* ``OUT`` while ``field_input_enabled()`` is
        True.  It still offers the raw measurement here so a block in Man
        can keep ``PV`` tracking the field (Azeo Man mode) without the
        bridge touching the operator's ``OUT`` — used by ``_field_value()``
        when no runtime context is installed.
        """
        self._field_offer = (value, quality)

    def set_field_status(self, quality: Quality):
        """Channel quality for this scan, published by the :class:`DataBridge`.

        The bridge knows what the block cannot: whether the configured
        store tag exists at all and whether its value is numeric.  A
        missing or non-numeric tag is **Bad** — not a silent 0.0 — and
        that Bad travels down the wire like any other status.
        """
        if isinstance(quality, Quality):
            self._chan_status = quality

    def _channel_quality(self, fv_pct: float, dt: float) -> tuple[Quality, LimitStatus]:
        """Channel status + limit from range and NAMUR detection.

        Azeo §146: the card sets the channel input status high/low
        limited when the value exceeds the overrange/underrange limits,
        and Bad outside the hardware bounds (Classic I/O −20.12 %…116.6 %).
        NAMUR limit detection additionally declares Bad when the signal
        sits above ``NAMUR_HI_MA`` / below ``NAMUR_LO_MA`` for more than
        ``NAMUR_TIME`` seconds.  Both detectors are opt-in.
        """
        p = self.config.params
        st = self._chan_status
        lim = LimitStatus.NOT_LIMITED
        over = under = False

        if p.get("RANGE_CHECK_ENA", False):
            if fv_pct > float(p.get("OVERRANGE_HI_PCT", 100.0)):
                lim, over = LimitStatus.HIGH_LIMITED, True
            elif fv_pct < float(p.get("UNDERRANGE_LO_PCT", 0.0)):
                lim, under = LimitStatus.LOW_LIMITED, True
            if (fv_pct > float(p.get("CHANNEL_BAD_HI_PCT", 116.6))
                    or fv_pct < float(p.get("CHANNEL_BAD_LO_PCT", -20.12))):
                st = Quality.BAD

        if p.get("NAMUR_ENA", False):
            # 4-20 mA across 0-100 % of XD_SCALE.
            ma = 4.0 + 0.16 * fv_pct
            high = ma > float(p.get("NAMUR_HI_MA", 21.0))
            low = ma < float(p.get("NAMUR_LO_MA", 3.6))
            if high or low:
                self._namur_t += dt
                if self._namur_t > float(p.get("NAMUR_TIME", 4.0)):
                    self._namur_bad = True
            else:
                self._namur_t = 0.0
                self._namur_bad = False
            if self._namur_bad:
                st = Quality.BAD
                if high:
                    lim, over = LimitStatus.HIGH_LIMITED, True
                else:
                    lim, under = LimitStatus.LOW_LIMITED, True
        else:
            self._namur_t = 0.0
            self._namur_bad = False

        self.set_output("OVERRANGE", over)
        self.set_output("UNDERRANGE", under)
        return st, lim

    def _pv_quality(self, chan_st: Quality, chan_lim: LimitStatus) -> Quality:
        """Apply STATUS_OPTS to the channel status (Azeo §146/§1165)."""
        p = self.config.params
        st = chan_st
        if chan_lim in _LIMITED:
            if p.get("OPT_BAD_IF_LIMITED", False):
                st = Quality.BAD
            # "With Uncertain if Limited selected, a limited input keeps PV
            # Uncertain even when the input is Bad" — so it wins over both
            # the channel status and Bad if Limited.
            if p.get("OPT_UNCERTAIN_IF_LIMITED", False):
                st = Quality.UNCERTAIN
        if st is Quality.UNCERTAIN and p.get("OPT_USE_UNCERTAIN_AS_GOOD", False):
            st = Quality.GOOD
        return st

    def _out_quality(self, pv_st: Quality, chan_lim: LimitStatus,
                     out: float) -> tuple[Quality, LimitStatus]:
        """OUT status/limit from the PV status and the actual mode.

        Azeo §146: "In Auto, OUT reflects PV value/status, except that a
        Good PV more than −10 %…110 % outside the PV_SCALE span makes OUT
        status Uncertain.  In Man, OUT limit indication is set to Constant
        and OUT status is Good (unless *Uncertain if Man mode* is
        selected)."
        """
        p = self.config.params
        if self._actual_mode == self._MAN:
            st = (Quality.UNCERTAIN if p.get("OPT_UNCERTAIN_IF_MAN", False)
                  else Quality.GOOD)
            lim = LimitStatus.CONSTANT
        else:
            st, lim = pv_st, chan_lim
            if st is Quality.GOOD and p.get("RANGE_CHECK_ENA", False):
                eu_lo, eu_hi = self._out_scale()
                span = eu_hi - eu_lo
                if span and not (eu_lo - 0.1 * span <= out <= eu_hi + 0.1 * span):
                    st = Quality.UNCERTAIN
        # IFS if Bad IN: no fail-safe transport exists here, so the
        # Initiate-Fault-State substatus is realised as a Bad OUT plus the
        # IFS_ACT flag (the CTLSL block does the same).
        ifs = bool(p.get("OPT_IFS_IF_BAD_IN", False)) and pv_st is Quality.BAD
        if ifs:
            st = Quality.BAD
        self.set_output("IFS_ACT", ifs)
        return st, lim

    def _namur_alarm(self, out: float) -> tuple[dict[str, float], LimitStatus | None]:
        """NAMUR alarming: extra alarm delay + the PV limit it implies.

        Azeo §146: HI_HI/LO_LO limits moved outside OUT_SCALE annunciate
        overrange/underrange and set the PV status high/low limits; when a
        limit is more than ``NAMUR_ALARM_PCT`` outside the OUT span the
        alarm activates only after the condition persists longer than
        ``NAMUR_ALARM_TIME``.
        """
        p = self.config.params
        delays = {k: 0.0 for k in _AI_ALARMS}
        if not p.get("NAMUR_ALARM_ENA", False):
            return delays, None
        eu_lo, eu_hi = self._out_scale()
        span = eu_hi - eu_lo
        if not span:
            return delays, None
        margin = abs(span) * float(p.get("NAMUR_ALARM_PCT", 3.0)) / 100.0
        hold = float(p.get("NAMUR_ALARM_TIME", 4.0))
        hi_hi = _as_float(p.get("HI_HI_LIM", float("inf")), float("inf"))
        lo_lo = _as_float(p.get("LO_LO_LIM", float("-inf")), float("-inf"))
        lim = None
        if hi_hi > eu_hi and out >= hi_hi:
            self.set_output("OVERRANGE", True)
            lim = LimitStatus.HIGH_LIMITED
            if hi_hi > eu_hi + margin:
                delays["HI_HI"] = hold
        elif lo_lo < eu_lo and out <= lo_lo:
            self.set_output("UNDERRANGE", True)
            lim = LimitStatus.LOW_LIMITED
            if lo_lo < eu_lo - margin:
                delays["LO_LO"] = hold
        return delays, lim

    # ---------------------------------------------------------------- execute
    def _field_value(self):
        """The measurement this scan.

        In Auto the :class:`DataBridge` has already written the store tag
        into ``OUT``.  In Man the bridge leaves ``OUT`` alone (so the
        operator's value is what downstream blocks see — see
        ``field_input_enabled()``) but still offers the measurement through
        ``set_field_value()``, so PV keeps tracking the field as Azeo's
        Man mode does.  A runtime context, when one is installed, is used
        as a second source; with neither the filter holds its last value.
        """
        if self._actual_mode != self._MAN:
            return self.get_output("OUT")
        offer = getattr(self, "_field_offer", None)
        if offer is not None:
            value, quality = offer
            self._chan_status = quality
            if value is not None:
                return value
            return self._pv_filt
        tag = self.config.params.get("tag", "")
        ctx = self.runtime_context
        if tag and ctx is not None:
            try:
                val = ctx.read(tag, None)
            except Exception:
                val = None
            if val is not None:
                try:
                    out = float(val)
                except (TypeError, ValueError):
                    pass
                else:
                    self._chan_status = Quality.GOOD
                    return out
            # Nothing readable: the block owns the channel quality here.
            self._chan_status = Quality.BAD
        return self._pv_filt

    def execute(self, dt: float):
        p = self.config.params
        raw = self._field_value()  # Set by bridge before execution (Auto)

        # Simulation — Azeo: a connected SIMULATE_IN overrides the
        # manually entered SIMULATE value; with nothing wired the manual
        # value is used (previously simulation was a silent no-op unless
        # SIMULATE_IN happened to be wired).
        if p.get("simulate_enabled", False):
            if self.inputs["SIMULATE_IN"].connected:
                raw = self.get_input("SIMULATE_IN")
                self._chan_status = self.input_status("SIMULATE_IN")
            else:
                raw = float(p.get("SIMULATE", 0.0))
                self._chan_status = (Quality.GOOD
                                     if p.get("SIMULATE_STATUS_GOOD", True)
                                     else Quality.BAD)

        # ── OOS: the block does not execute ──────────────────────────
        # Azeo AI spec: "In OOS, OUT status is BAD."  OUT/PV hold the last
        # value the block produced (the DataBridge stops refreshing OUT from
        # the field tag while OOS — see field_input_enabled()), STATUS goes
        # False and the block status becomes OOS so downstream blocks, the
        # faceplates and the HMI all see an out-of-service measurement.
        # Alarms are not detected by an OOS block, so the *_ACT outputs are
        # cleared rather than frozen in whatever state they last held.
        if self._actual_mode == self._OOS:
            self.status = BlockStatus.OOS
            self.set_output("STATUS", False)
            # Publish the quality on the wire too, so downstream blocks see a
            # Bad measurement instead of a stale-but-Good-looking number.
            self.set_output_status("OUT", Quality.BAD, LimitStatus.CONSTANT)
            self.set_output("MODE", self._OOS)
            self._clear_alarms()
            return

        # FIELD_VAL — the accessed value in percent of XD_SCALE.
        xd_lo, xd_hi = self._xd_scale()
        xd_span = xd_hi - xd_lo
        try:
            fv_pct = 100.0 * (float(raw) - xd_lo) / xd_span if xd_span else 0.0
        except (TypeError, ValueError):
            fv_pct = 0.0
        self.set_output("FIELD_VAL", fv_pct)

        # Channel status: what the bridge saw on the tag, refined by the
        # over/under-range and NAMUR detectors, then shaped by STATUS_OPTS.
        chan_st, chan_lim = self._channel_quality(fv_pct, dt)
        pv_st = self._pv_quality(chan_st, chan_lim)

        # Signal conversion + low cutoff
        conv = self._convert(raw)
        if p.get("IO_OPTS_LOW_CUTOFF", False) and conv < float(p.get("LOW_CUT", 0.0)):
            conv = 0.0

        # First-order PV filter
        pv_ftime = p.get("PV_FTIME", 0.0)
        if not self._initialized:
            self._pv_filt = conv
            self._initialized = True
        elif pv_ftime > 0.0 and dt > 0.0:
            alpha = dt / (pv_ftime + dt)
            self._pv_filt = self._pv_filt + alpha * (conv - self._pv_filt)
        else:
            self._pv_filt = conv

        pv = self._pv_filt
        # Mode switch: Auto -> OUT = PV; Man -> operator writes OUT while
        # PV keeps tracking the field (Azeo §146 "Modes").
        # Target to Manual if Bad IN: a Bad PV drives target *and* actual
        # mode to Man, and they stay Man after the measurement recovers.
        if (pv_st is Quality.BAD and self._actual_mode == self._AUTO
                and p.get("OPT_TARGET_TO_MANUAL_IF_BAD_IN", False)):
            self.set_mode(self._MAN)
        out = self._man_out if self._actual_mode == self._MAN else pv

        # Alarm detection with hysteresis — Azeo detects alarms on the
        # OUT value (identical to PV outside Man mode).
        hys = self._hysteresis()
        hi_hi = p.get("HI_HI_LIM", float('inf'))
        hi = p.get("HI_LIM", float('inf'))
        lo = p.get("LO_LIM", float('-inf'))
        lo_lo = p.get("LO_LO_LIM", float('-inf'))

        raw_act = {
            "HI_HI": out >= hi_hi if not self._alm["HI_HI"] else out >= (hi_hi - hys),
            "HI": out >= hi if not self._alm["HI"] else out >= (hi - hys),
            "LO": out <= lo if not self._alm["LO"] else out <= (lo + hys),
            "LO_LO": out <= lo_lo if not self._alm["LO_LO"] else out <= (lo_lo + hys),
        }
        namur_delay, namur_lim = self._namur_alarm(out)
        for key in _AI_ALARMS:
            self.set_output(
                f"{key}_ACT",
                self._alarm_step(key, raw_act[key], dt, namur_delay[key]))
        if namur_lim is not None:
            chan_lim = namur_lim
            pv_st = self._pv_quality(chan_st, chan_lim)

        out_st, out_lim = self._out_quality(pv_st, chan_lim, out)
        self.set_output("OUT", out)
        self.set_output("PV", pv)
        self.set_output("STATUS", out_st is Quality.GOOD)
        self.set_output_status("OUT", out_st, out_lim)
        self.set_output_status("PV", pv_st, chan_lim)
        self.set_output("MODE", self._actual_mode)
        # Block-level summary: the worse of the published OUT status and the
        # measurement status, so a Bad channel still shows at block level in
        # Man (where the spec keeps OUT itself Good).
        self.status = _BLOCK_STATUS[max((out_st, pv_st), key=lambda q: q.value)]

    def field_input_enabled(self) -> bool:
        """True while the field value may drive OUT.

        The :class:`DataBridge` calls this before refreshing OUT from the
        configured store tag.  An out-of-service block does not execute, so
        its OUT must hold the last value it produced instead of tracking the
        live measurement — otherwise OOS would be cosmetic and the field
        value would keep driving every downstream block.

        Man mode is excluded for the same reason: forward wires are
        propagated from ``OUT`` *before* the block executes, so a bridge
        refresh in Man would hand downstream blocks the field value the
        operator is deliberately overriding.  The block keeps PV tracking
        the measurement by reading the tag itself (see ``_field_value()``).
        """
        return self._actual_mode not in (self._OOS, self._MAN)

    def set_mode(self, mode: str):
        mode = mode.upper()
        if mode == "MANUAL":
            mode = self._MAN
        if mode not in (self._OOS, self._MAN, self._AUTO):
            return
        if self._actual_mode == self._OOS and mode != self._OOS:
            # Returning to service: resume the PV filter from the value the
            # block held while OOS so the first in-service scan is bumpless
            # (with PV_FTIME > 0 it ramps from the held value to the field
            # value instead of stepping from a stale pre-OOS filter state).
            try:
                self._pv_filt = float(self.get_output("OUT"))
                self._initialized = True
            except (TypeError, ValueError):
                self._initialized = False
        if mode == self._MAN and self._actual_mode != self._MAN:
            # Bumpless into Man: the manual value starts at the current OUT.
            try:
                self._man_out = float(self.get_output("OUT"))
            except (TypeError, ValueError):
                self._man_out = 0.0
        self._actual_mode = mode

    def set_manual_output(self, value: float):
        """Operator-entered OUT, honoured while the block is in Man."""
        try:
            self._man_out = float(value)
        except (TypeError, ValueError):
            return
        if self._actual_mode == self._MAN:
            self.set_output("OUT", self._man_out)

    def reset(self):
        super().reset()
        self._initialized = False
        self._alm = {k: False for k in _AI_ALARMS}
        self._alm_t = {k: 0.0 for k in _AI_ALARMS}
        self._chan_status = Quality.GOOD
        self._field_offer = None
        self._namur_t = 0.0
        self._namur_bad = False


@register_block
class AOBlock(FunctionBlock):
    """Azeo-style Analog Output function block.

    Supports OOS / Manual / Auto / Cascade / RCascade modes, SP limiting
    and rate limiting, SP-PV tracking in Manual, 'Increase to close'
    output inversion, an optional readback channel, remote-cascade
    shedding, a fault state, and proper BKCAL_OUT (= SP_WRK) for upstream
    cascade initialization.

    In Cascade mode the CAS_IN terminal (aliased ``IN``) receives the
    remote setpoint from an upstream block (typically a PID).  In Auto
    mode the operator sets SP locally.  In Manual mode the operator sets
    OUT directly.

    Backward-compatible: existing strategies that wire PID OUT → AO IN
    with config ``{tag, out_lo, out_hi}`` default to Cascade mode and
    behave identically to the previous pass-through implementation.
    Every Azeo feature added later is opt-in:

    * ``use_pv_scale`` (default False) — when True, ``SP_WRK`` is EU of
      ``PV_SCALE`` and OUT is the converted **percent** mapped onto
      ``out_lo``/``out_hi``, which is also where *Increase to close*
      inverts (Azeo §241 "Conversion and status calculation").  With it
      False the historical formula (reflect the EU setpoint about the
      output span) is kept byte-for-byte.
    * ``RCAS_IN`` / ``RCAS_OUT`` + ``SHED_TIME``/``SHED_OPT`` — RCas reads
      ``RCAS_IN`` when it is wired and falls back to ``CAS_IN`` otherwise,
      which is what RCas did before.
    * ``READBACK_IN`` → ``READBACK`` / ``PV`` — unconnected, PV derives
      from OUT exactly as before.
    * ``FSTATE_VAL`` / ``FSTATE_TIME`` (``fstate_enable`` default False).

    ``TRK_IN_D`` / ``TRK_VAL`` are a platform extension — Azeo puts the
    tracking pair on the PID, not the AO — and are kept because existing
    strategies drive an AO's output through them.
    """
    block_type = "AO"
    category = BlockCategory.IO
    display_name = "Analog Output"
    description = "Azeo-style AO — modes, SP limiting, readback, BKCAL"

    # SFC-authored strategies wire the setpoint as "IN" and scale it with
    # scale_lo/scale_hi; accept both so those wires and limits take effect.
    terminal_aliases = {"IN": "CAS_IN", "SP_IN": "CAS_IN", "OUT_D": "OUT",
                        "IO_READBACK": "READBACK_IN"}
    config_aliases = {"scale_lo": "out_lo", "scale_hi": "out_hi"}

    # Mode constants
    _OOS = "OOS"
    _MAN = "MAN"
    _AUTO = "AUTO"
    _CAS = "CAS"
    _RCAS = "RCAS"
    _IMAN = "IMAN"
    _LO = "LO"

    _MODES = (_OOS, _MAN, _AUTO, _CAS, _RCAS, _IMAN, _LO)
    _SHED_OPTS = ("NoShed", "ShedToAuto", "ShedToMan")

    config_choices = {
        "mode": ("OOS", "MAN", "AUTO", "CAS", "RCAS"),
        "SHED_OPT": _SHED_OPTS,
    }
    config_units = {
        "sp_rate_up": "EU/s", "sp_rate_dn": "EU/s",
        "SHED_TIME": "s", "FSTATE_TIME": "s",
    }

    def __init__(self, instance_name: str = ""):
        # Internal state — initialized in _apply_config / _init_state
        self._actual_mode: str = self._CAS
        self._target_mode: str = self._CAS
        self._sp: float = 0.0
        self._sp_wrk: float = 0.0
        self._out: float = 0.0
        self._pv: float = 0.0
        self._initialized: bool = False
        self._rcas_last = None        # last RCAS_IN value seen
        self._rcas_age: float = 0.0   # seconds since RCAS_IN last changed
        self._fault_age: float = 0.0  # seconds the remote SP has been faulted
        super().__init__(instance_name)

    # ---------------------------------------------------------------- terminals
    def _define_terminals(self):
        # Inputs
        self.add_input("CAS_IN", description="Cascade setpoint from upstream block")
        self.add_input("RCAS_IN", description="Remote-cascade setpoint (host / APC)")
        self.add_input("READBACK_IN", description="Actuator position readback (OUT units)")
        self.add_input("SIMULATE_IN", description="Simulated readback [%] (overrides SIMULATE)")
        self.add_input("TRK_IN_D", DataType.BOOL, False, "Track mode enable")
        self.add_input("TRK_VAL", description="Track value when TRK_IN_D=True")

        # Outputs
        self.add_output("OUT", description="Block output to field device")
        self.add_output("BKCAL_OUT", description="Back-calculation output (SP_WRK)",
                        is_bkcal=True)
        self.add_output("PV", description="Process variable (readback in EU)")
        self.add_output("READBACK", description="Actuator position [%] of the OUT span")
        self.add_output("SP", description="Active setpoint (EU)")
        self.add_output("RCAS_OUT", description="Remote setpoint after limiting (SP_WRK)")
        self.add_output("MODE", DataType.STRING, "CAS", "Current mode")

    # ---------------------------------------------------------------- config
    def get_config_schema(self):
        return {
            "tag": (str, "", "Data store tag to write"),
            "out_lo": (float, 0.0, "Output low clamp"),
            "out_hi": (float, 1.0, "Output high clamp"),
            "sp_hi_lim": (float, None, "SP high limit (EU, default=out_hi)"),
            "sp_lo_lim": (float, None, "SP low limit (EU, default=out_lo)"),
            "sp_rate_up": (float, 0.0, "SP ramp up (EU/s, 0=off)"),
            "sp_rate_dn": (float, 0.0, "SP ramp down (EU/s, 0=off)"),
            "increase_to_close": (bool, False, "Invert output for fail-open"),
            "sp_pv_track_man": (bool, True, "SP tracks PV in Manual"),
            "sp_pv_track_lo_iman": (bool, False, "SP tracks PV in LO or IMan"),
            "use_pv_for_bkcal_out": (bool, False, "Use PV for BKCAL_OUT"),
            "mode": (str, "CAS", "Initial mode: OOS, MAN, AUTO, CAS, RCAS"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
            "pv_scale_lo": (float, 0.0, "PV scale low (EU)"),
            "pv_scale_hi": (float, 1.0, "PV scale high (EU)"),
            "use_pv_scale": (bool, False,
                             "Convert SP_WRK (EU of PV_SCALE) to percent and map "
                             "it onto out_lo..out_hi, Azeo-style"),
            "SHED_TIME": (float, 0.0,
                          "Max seconds between RCAS_IN updates before shedding "
                          "(0 = never shed)"),
            "SHED_OPT": (str, "NoShed", "Action on remote-host timeout"),
            "fstate_enable": (bool, False, "Enable the Fault State to value option"),
            "FSTATE_VAL": (float, 0.0, "Preset SP used when the remote setpoint faults"),
            "FSTATE_TIME": (float, 0.0,
                            "Seconds a remote-setpoint fault must persist before "
                            "FSTATE_VAL is applied"),
            "report_lo_when_tracking": (bool, False,
                                        "Report actual mode LO while TRK_IN_D forces OUT"),
            "allow_string_passthrough": (bool, True,
                                         "Pass a string CAS_IN straight to OUT "
                                         "(mode-writer AO blocks)"),
            "simulate_enabled": (bool, False,
                                 "Simulation: stop writing to the field tag and take "
                                 "PV/READBACK from the simulate value"),
            "SIMULATE": (float, 0.0,
                         "Simulated readback [% of the OUT span], used when "
                         "SIMULATE_IN is not connected"),
            "SIMULATE_STATUS_GOOD": (bool, True, "Status of the manual SIMULATE value"),
            "eng_units": (str, "%", "Engineering units"),
            "description": (str, "", "Block description"),
        }

    # ------------------------------------------------- the operator's handles
    # The block already reserves `_sp` (Auto) and `_out` (Manual) for an
    # external writer — the mode branches deliberately leave them alone. What
    # was missing was any *caller*: nothing reached them, so an operator
    # setting a manual output found it recomputed to zero on the next scan.
    #
    # Public methods rather than letting callers touch the attributes,
    # because the two rules below belong with the block:
    #
    # - **Which handle depends on the mode.** Manual owns OUT, Auto owns SP,
    #   and Cascade owns neither — an upstream block does. Writing the wrong
    #   one is accepted silently and then overwritten a scan later, which is
    #   the failure this whole project keeps meeting.
    # - **A write before the first scan is discarded**, because first-scan
    #   initialisation seeds SP from the current output to start bumplessly.
    #   Saying so is better than letting a caller wonder where the value went.

    def accepts_operator_write(self, what: str = "SP") -> tuple[bool, str]:
        """May an operator write this, in the mode the block is actually in?"""
        what = what.upper()
        if self._actual_mode == self._OOS:
            return False, "the block is out of service"
        if what in ("OP", "OUT"):
            if self._actual_mode not in (self._MAN, self._IMAN):
                return False, (f"OUT is owned by the block in "
                               f"{self._actual_mode}; switch to Man to set it")
            return True, ""
        if what == "SP":
            if self._actual_mode in (self._CAS, self._RCAS):
                return False, (f"SP comes from upstream in "
                               f"{self._actual_mode}")
            if self._actual_mode in (self._MAN, self._IMAN):
                return False, "SP tracks PV in Man; set OUT instead"
            return True, ""
        return False, f"{what} is not an operator handle on this block"

    def write_operator_value(self, value: float,
                             what: str = "SP") -> tuple[bool, str]:
        """Set the handle the current mode owns. `(accepted, why not)`."""
        allowed, why = self.accepts_operator_write(what)
        if not allowed:
            return False, why
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, "value must be a number"

        if what.upper() in ("OP", "OUT"):
            out_lo, out_hi = self._out_limits()
            self._out = max(out_lo, min(out_hi, number))
        else:
            sp_lo, sp_hi = self._sp_limits()
            self._sp = max(sp_lo, min(sp_hi, number))
            # An uninitialised block overwrites `_sp` from `_out` on its
            # first scan, so a write landing before that would vanish.
            self._initialized = True
        return True, ""

    def _apply_config(self):
        p = self.config.params
        mode_str = p.get("mode", "CAS").upper()
        self._target_mode = mode_str if mode_str in self._MODES else self._CAS
        self._actual_mode = self._target_mode
        self._initialized = False
        units = str(p.get("eng_units", ""))
        self.set_terminal_eu("OUT", units, self._out_limits())
        self.set_terminal_eu("CAS_IN", units, self._sp_limits())

    # ---------------------------------------------------------------- helpers
    def _sp_limits(self):
        """Return (sp_lo, sp_hi) with fallback to out_lo/out_hi."""
        p = self.config.params
        lo = p.get("sp_lo_lim")
        hi = p.get("sp_hi_lim")
        if lo is None:
            lo = p.get("out_lo", 0.0)
        if hi is None:
            hi = p.get("out_hi", 1.0)
        lo, hi = float(lo), float(hi)
        if p.get("use_pv_scale", False):
            # Azeo §241 "Setpoint limit scaling": the limits are held
            # inside PV_SCALE ± 10 % of span.  Only applied on the
            # PV_SCALE path — without it the block has no EU scale to
            # measure against and the configured limits stand as before.
            eu_lo, eu_hi = self._pv_scale()
            span = eu_hi - eu_lo
            hi = min(hi, eu_hi + 0.1 * span)
            lo = max(lo, eu_lo - 0.1 * span)
        return lo, hi

    def _out_limits(self):
        p = self.config.params
        return float(p.get("out_lo", 0.0)), float(p.get("out_hi", 1.0))

    def _pv_scale(self):
        p = self.config.params
        return float(p.get("pv_scale_lo", 0.0)), float(p.get("pv_scale_hi", 1.0))

    def _eu_to_out(self, sp_wrk: float) -> float:
        """Convert working setpoint (EU) to output value."""
        p = self.config.params
        itc = p.get("increase_to_close", False)
        if p.get("use_pv_scale", False):
            eu_lo, eu_hi = self._pv_scale()
            span = eu_hi - eu_lo
            pct = 100.0 * (sp_wrk - eu_lo) / span if span else 0.0
            if itc:
                pct = 100.0 - pct
            o_lo, o_hi = self._out_limits()
            return o_lo + pct / 100.0 * (o_hi - o_lo)
        out = sp_wrk
        if itc:
            lo, hi = self._out_limits()
            out = hi - (sp_wrk - lo)
        return out

    def _out_to_eu(self, out_val: float) -> float:
        """Convert output value back to EU for PV."""
        p = self.config.params
        itc = p.get("increase_to_close", False)
        if p.get("use_pv_scale", False):
            o_lo, o_hi = self._out_limits()
            o_span = o_hi - o_lo
            pct = 100.0 * (out_val - o_lo) / o_span if o_span else 0.0
            if itc:
                pct = 100.0 - pct
            eu_lo, eu_hi = self._pv_scale()
            return eu_lo + pct / 100.0 * (eu_hi - eu_lo)
        if itc:
            lo, hi = self._out_limits()
            return hi - (out_val - lo)
        return out_val

    def _out_to_pct(self, out_val: float) -> float:
        o_lo, o_hi = self._out_limits()
        span = o_hi - o_lo
        return 100.0 * (out_val - o_lo) / span if span else 0.0

    def _pct_to_out(self, pct: float) -> float:
        o_lo, o_hi = self._out_limits()
        return o_lo + pct / 100.0 * (o_hi - o_lo)

    def _simulate_pct(self):
        """Simulated readback in percent of the OUT span, or None.

        Azeo §245 "Simulation": with simulation enabled the block stops
        writing to hardware and PV/READBACK take the simulate value.  As on
        the AI block, a *connected* ``SIMULATE_IN`` overrides the manually
        entered ``SIMULATE``.
        """
        if not self.config.params.get("simulate_enabled", False):
            return None
        if self.inputs["SIMULATE_IN"].connected:
            try:
                return float(self.get_input("SIMULATE_IN"))
            except (TypeError, ValueError):
                return None
        return float(self.config.params.get("SIMULATE", 0.0))

    def _rate_limit(self, sp_new: float, dt: float) -> float:
        """Apply SP rate limiting, return SP_WRK."""
        p = self.config.params
        rate_up = float(p.get("sp_rate_up", 0.0))
        rate_dn = float(p.get("sp_rate_dn", 0.0))
        if rate_up <= 0.0 and rate_dn <= 0.0:
            return sp_new
        if dt <= 0.0:
            return self._sp_wrk
        delta = sp_new - self._sp_wrk
        if delta > 0.0 and rate_up > 0.0:
            max_change = rate_up * dt
            if delta > max_change:
                return self._sp_wrk + max_change
        elif delta < 0.0 and rate_dn > 0.0:
            max_change = rate_dn * dt
            if abs(delta) > max_change:
                return self._sp_wrk - max_change
        return sp_new

    def _remote_sp(self, dt: float) -> tuple[float, bool]:
        """Remote setpoint for RCas plus a 'remote host faulted' flag.

        RCAS_IN is used when it is wired; otherwise the block falls back
        to CAS_IN, which is what RCas mode did before RCAS_IN existed.
        ``SHED_TIME`` (0 = disabled) declares the host stale when the
        value has not moved for that long.
        """
        p = self.config.params
        if not self.inputs["RCAS_IN"].connected:
            self._rcas_age = 0.0
            return self.get_input("CAS_IN"), False
        val = self.get_input("RCAS_IN")
        if self._rcas_last is None or val != self._rcas_last:
            self._rcas_last = val
            self._rcas_age = 0.0
        else:
            self._rcas_age += dt
        shed_time = float(p.get("SHED_TIME", 0.0))
        return val, bool(shed_time > 0.0 and self._rcas_age > shed_time)

    # ---------------------------------------------------------------- execute
    def execute(self, dt: float):
        p = self.config.params
        out_lo, out_hi = self._out_limits()
        sp_lo, sp_hi = self._sp_limits()

        # --- String pass-through mode ---
        # AO blocks used for mode writing (e.g. AO_MODE_TC1501) receive
        # string values like 'MAN'/'AUTO'/'CAS' on CAS_IN.  Skip all
        # numeric clamping and just pass the string through.  Non-Azeo
        # extension, hence the explicit (default-on) opt-out.
        if (self._actual_mode in (self._CAS, self._RCAS)
                and p.get("allow_string_passthrough", True)):
            cas_in = self.get_input("CAS_IN")
            if isinstance(cas_in, str):
                self._out = cas_in
                self._sp = cas_in
                self._sp_wrk = 0.0
                self._pv = 0.0
                self.set_output("OUT", cas_in)
                self.set_output("BKCAL_OUT", cas_in)
                self.set_output("PV", 0.0)
                self.set_output("SP", 0.0)
                self.set_output("MODE", self._actual_mode)
                return

        # --- Tracking override (highest priority) ---
        if self.get_input("TRK_IN_D"):
            val = self.get_input("TRK_VAL")
            val = max(out_lo, min(out_hi, val))
            self._out = val
            self._sp_wrk = self._out_to_eu(val)
            self._sp = self._sp_wrk
            self._pv = self._sp_wrk
            # Azeo sheds an output block to LO while it is tracking.
            self._publish(mode=self._LO if p.get("report_lo_when_tracking", False)
                          else None)
            return

        # --- Mode shedding: CAS with no upstream → AUTO ---
        # Azeo sheds on a *Bad* CAS_IN status, which terminals cannot
        # carry here; an unconnected CAS_IN is the closest equivalent.
        # Log it once so the demotion is not completely silent.
        if self._actual_mode == self._CAS and not self.inputs["CAS_IN"].connected:
            self._actual_mode = self._AUTO
            if not getattr(self, "_shed_logged", False):
                self._shed_logged = True
                log.info("AO %s: CAS_IN not connected — shedding CAS -> AUTO",
                         self.instance_name)

        # --- First-scan initialization ---
        if not self._initialized:
            self._sp_wrk = self._out_to_eu(self._out)
            self._sp = self._sp_wrk
            self._initialized = True

        # --- Mode-specific SP/OUT computation ---
        if self._actual_mode == self._OOS:
            # Out of service: the block does not execute.  OUT holds its last
            # value, the block status reports OOS, and the DataBridge stops
            # driving the field tag (see field_output_enabled()) so an OOS
            # block never writes to the process.
            self.status = BlockStatus.OOS
            self._pv = self._readback_eu()
            self._publish()
            return

        if self.status is BlockStatus.OOS:
            self.status = BlockStatus.GOOD

        if self._actual_mode in (self._MAN, self._IMAN):
            # OUT is set externally by operator (via bridge write-back)
            # SP tracks PV if enabled
            if p.get("sp_pv_track_man", True):
                self._sp = self._pv

        elif self._actual_mode == self._LO:
            # Local override / tracking: OUT is held, SP may follow PV.
            if p.get("sp_pv_track_lo_iman", False):
                self._sp = self._pv

        elif self._actual_mode == self._AUTO:
            # SP set by operator (stored in self._sp by bridge write-back)
            sp = max(sp_lo, min(sp_hi, self._sp))
            self._sp = sp
            self._sp_wrk = self._rate_limit(sp, dt)
            self._out = self._eu_to_out(self._sp_wrk)
            self._out = max(out_lo, min(out_hi, self._out))

        elif self._actual_mode in (self._CAS, self._RCAS):
            # SP from upstream via CAS_IN / RCAS_IN
            if self._actual_mode == self._RCAS:
                cas_in, faulted = self._remote_sp(dt)
            else:
                cas_in, faulted = self.get_input("CAS_IN"), False
            # A non-numeric setpoint (a mode string with the pass-through
            # option switched off) cannot be scaled — hold the last SP, or
            # the low limit if that is a leftover string too.
            cas_in = _as_float(cas_in, _as_float(self._sp_wrk, sp_lo))
            cas_in = self._apply_fault_state(cas_in, faulted, dt)
            sp = max(sp_lo, min(sp_hi, cas_in))
            self._sp = sp
            self._sp_wrk = self._rate_limit(sp, dt)
            self._out = self._eu_to_out(self._sp_wrk)
            self._out = max(out_lo, min(out_hi, self._out))
            if faulted:
                self._shed()

        # --- PV derivation (readback channel when wired) ---
        self._pv = self._readback_eu()

        self._publish()

    def _readback_eu(self) -> float:
        """PV in EU — simulate value, else READBACK_IN when wired, else OUT."""
        sim = self._simulate_pct()
        if sim is not None:
            return self._out_to_eu(self._pct_to_out(sim))
        if self.inputs["READBACK_IN"].connected:
            try:
                return self._out_to_eu(float(self.get_input("READBACK_IN")))
            except (TypeError, ValueError):
                pass
        return self._out_to_eu(self._out)

    def _apply_fault_state(self, sp: float, faulted: bool, dt: float) -> float:
        """Azeo FSTATE_VAL / FSTATE_TIME on a remote-setpoint fault."""
        p = self.config.params
        if not faulted:
            self._fault_age = 0.0
            return sp
        self._fault_age += dt
        if not p.get("fstate_enable", False):
            return sp
        if self._fault_age >= float(p.get("FSTATE_TIME", 0.0)):
            return float(p.get("FSTATE_VAL", 0.0))
        return sp

    def _shed(self):
        """Shed the actual mode after a remote-host timeout (SHED_OPT)."""
        opt = str(self.config.params.get("SHED_OPT", "NoShed")).strip().lower()
        if opt == "shedtoauto":
            self._actual_mode = self._AUTO
        elif opt == "shedtoman":
            self._actual_mode = self._MAN

    def _publish(self, mode: str | None = None):
        """Push internal state to output terminals."""
        self.set_output("OUT", self._out)
        # BKCAL_OUT = SP_WRK (or PV if option set)
        if self.config.params.get("use_pv_for_bkcal_out", False):
            self.set_output("BKCAL_OUT", self._pv)
        else:
            self.set_output("BKCAL_OUT", self._sp_wrk)
        self.set_output("PV", self._pv)
        self.set_output("SP", self._sp)
        self.set_output("RCAS_OUT", self._sp_wrk)
        sim = self._simulate_pct()
        if sim is not None:
            self.set_output("READBACK", sim)
        else:
            try:
                rb = (float(self.get_input("READBACK_IN"))
                      if self.inputs["READBACK_IN"].connected else float(self._out))
                self.set_output("READBACK", self._out_to_pct(rb))
            except (TypeError, ValueError):
                self.set_output("READBACK", 0.0)
        # The simulate value carries its own status onto PV/READBACK; a wired
        # SIMULATE_IN carries the status of whatever drives it.
        if sim is not None:
            if self.inputs["SIMULATE_IN"].connected:
                q = self.input_status("SIMULATE_IN")
            else:
                q = (Quality.GOOD
                     if self.config.params.get("SIMULATE_STATUS_GOOD", True)
                     else Quality.BAD)
            self.set_output_status("PV", q)
            self.set_output_status("READBACK", q)
        self.set_output("MODE", mode or self._actual_mode)

    # ---------------------------------------------------------------- mode control
    def field_output_enabled(self) -> bool:
        """True while the block may drive its field tag.

        The :class:`DataBridge` calls this before writing OUT to the store.
        An out-of-service block does not execute, so it must not keep pushing
        its held OUT onto the process — the field value simply stays where it
        was, and another writer (operator override, another strategy) is free
        to move it.

        Simulation does the same: Azeo §245 says the block stops writing to
        hardware while it is simulating, holding the last OUT in the field.
        """
        return (self._actual_mode != self._OOS
                and not self.config.params.get("simulate_enabled", False))

    def set_mode(self, mode: str):
        """Request a mode change (called by bridge from faceplate)."""
        mode = mode.upper()
        if mode == "MANUAL":
            mode = self._MAN
        if mode not in self._MODES:
            return
        # CAS/RCAS requires upstream connection
        if mode == self._CAS and not self.inputs["CAS_IN"].connected:
            mode = self._AUTO
        if mode == self._RCAS and not (self.inputs["RCAS_IN"].connected
                                       or self.inputs["CAS_IN"].connected):
            mode = self._AUTO
        old = self._actual_mode
        self._actual_mode = mode
        self._target_mode = mode
        # Bumpless: when entering MAN, SP tracks PV
        if mode == self._MAN and old != self._MAN:
            if self.config.params.get("sp_pv_track_man", True):
                self._sp = self._pv
        # When entering AUTO/CAS from MAN, SP_WRK starts from current OUT
        if mode in (self._AUTO, self._CAS, self._RCAS) and old == self._MAN:
            self._sp_wrk = self._out_to_eu(self._out)
            self._sp = self._sp_wrk

    def set_sp(self, sp: float):
        """Set operator SP (Auto mode)."""
        self._sp = sp

    def set_manual_output(self, out: float):
        """Set manual output value."""
        if self._actual_mode in (self._MAN, self._IMAN):
            out_lo, out_hi = self._out_limits()
            self._out = max(out_lo, min(out_hi, out))

    def initialize_from_readback(self, val: float):
        """Seed internal state from current field value (called by bridge
        during go_online initialization)."""
        out_lo, out_hi = self._out_limits()
        self._out = max(out_lo, min(out_hi, val))
        self._sp_wrk = self._out_to_eu(self._out)
        self._sp = self._sp_wrk
        self._pv = self._sp_wrk
        self._initialized = True
        self._publish()

    def reset(self):
        super().reset()
        self._initialized = False
        self._rcas_last = None
        self._rcas_age = 0.0
        self._fault_age = 0.0


@register_block
class DIBlock(FunctionBlock):
    """Discrete Input — reads a boolean tag from SharedDataStore.

    Models the Azeo DI block (§564): OOS / Man / Auto modes, the Invert
    I/O option, the ``PV_FTIME`` on/off-delay contact filter, simulation,
    and ``DISC_LIM`` → ``DISC_ACT`` discrete alarm detection.

    **Where the invert happens.**  The :class:`DataBridge` reads the store
    tag and applies ``invert`` before writing the value into ``OUT``, so
    ``execute()`` receives an already-inverted field value and must not
    invert again.  The block applies the option itself only on the
    simulation path, which the bridge never touches.  (Azeo inverts
    ``FIELD_VAL_D`` into ``PV_D``; the on/off-delay filter is symmetric,
    so filtering before or after the inversion gives the same ``PV_D``.)

    Defaults reproduce the previous pass-through block exactly:
    ``mode=AUTO``, ``PV_FTIME=0`` (no filter), ``SIMULATE_D=False``,
    ``DISC_LIM=255`` (never alarms).
    """
    block_type = "DI"
    category = BlockCategory.IO
    display_name = "Digital Input"
    description = "Azeo-style DI — modes, contact filter, simulation, alarm"

    # Azeo names the discrete output OUT_D; 320 shipped wires use OUT,
    # so OUT stays canonical and OUT_D is accepted as an alias.
    terminal_aliases = {"OUT_D": "OUT", "SIMULATE_IN": "SIMULATE_IN_D"}

    _OOS = "OOS"
    _MAN = "MAN"
    _AUTO = "AUTO"

    config_choices = {"mode": ("AUTO", "MAN", "OOS")}
    config_units = {"PV_FTIME": "s"}

    def __init__(self, instance_name: str = ""):
        self._actual_mode: str = self._AUTO
        self._pv_d: bool = False
        self._pending: bool | None = None   # candidate state awaiting PV_FTIME
        self._pending_t: float = 0.0
        self._man_out: bool = False
        self._primed: bool = False
        self._chan_status: Quality = Quality.GOOD
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("SIMULATE_IN_D", DataType.BOOL, False,
                       "Simulated discrete input (overrides SIMULATE_VAL)")
        self.add_output("OUT", DataType.BOOL, False, "Digital value (OUT_D)")
        self.add_output("PV_D", DataType.BOOL, False, "Filtered process value")
        self.add_output("DISC_ACT", DataType.BOOL, False, "Discrete alarm active")
        self.add_output("STATUS", DataType.BOOL, True, "Signal good")
        self.add_output("MODE", DataType.STRING, "AUTO", "Current mode")

    def get_config_schema(self):
        return {
            "tag": (str, "", "Data store tag to read"),
            "invert": (bool, False, "Invert the input (IO_OPTS Invert)"),
            "PV_FTIME": (float, 0.0,
                         "Seconds the field value must hold a new state before "
                         "PV_D follows it (0 = no filter)"),
            "SIMULATE_D": (bool, False, "Enable simulation"),
            "SIMULATE_VAL": (bool, False,
                             "Manual simulate value, used when SIMULATE_IN_D "
                             "is not connected"),
            "DISC_LIM": (int, 255,
                         "OUT state that raises DISC_ACT: 0 or 1; 255 = never"),
            "mode": (str, "AUTO", "Initial mode: OOS, MAN, AUTO"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
        }

    def _apply_config(self):
        mode_str = str(self.config.params.get("mode", "AUTO")).upper()
        if mode_str == "MANUAL":
            mode_str = self._MAN
        self._actual_mode = mode_str if mode_str in (
            self._OOS, self._MAN, self._AUTO) else self._AUTO

    def execute(self, dt: float):
        p = self.config.params

        # OOS — hold OUT, report bad status, no alarm detection.  The
        # bridge stops refreshing OUT while OOS (field_input_enabled).
        if self._actual_mode == self._OOS:
            self.status = BlockStatus.OOS
            self.set_output("STATUS", False)
            self.set_output("MODE", self._OOS)
            self.set_output("DISC_ACT", False)
            return
        if self.status is BlockStatus.OOS:
            self.status = BlockStatus.GOOD

        # Field value: the bridge has already written the (inverted) tag
        # value into OUT.  Simulation substitutes it entirely.
        raw = self._field_value()
        if p.get("SIMULATE_D", False):
            sim = (bool(self.get_input("SIMULATE_IN_D"))
                   if self.inputs["SIMULATE_IN_D"].connected
                   else bool(p.get("SIMULATE_VAL", False)))
            raw = (not sim) if p.get("invert", False) else sim

        # PV_FTIME contact filter — the field value must hold its new
        # state for PV_FTIME seconds (both directions) before PV_D moves.
        ftime = float(p.get("PV_FTIME", 0.0))
        if not self._primed:
            self._pv_d = raw
            self._primed = True
            self._pending = None
            self._pending_t = 0.0
        elif ftime > 0.0:
            if raw == self._pv_d:
                self._pending = None
                self._pending_t = 0.0
            else:
                if self._pending != raw:
                    self._pending = raw
                    self._pending_t = 0.0
                self._pending_t += dt
                if self._pending_t >= ftime:
                    self._pv_d = raw
                    self._pending = None
                    self._pending_t = 0.0
        else:
            self._pv_d = raw

        out = self._man_out if self._actual_mode == self._MAN else self._pv_d
        self.set_output("OUT", out)
        self.set_output("PV_D", self._pv_d)
        self.set_output("STATUS", self._chan_status is Quality.GOOD)
        self.set_output("MODE", self._actual_mode)
        self.set_output_status("OUT", self._chan_status)
        self.set_output_status("PV_D", self._chan_status)

        # Discrete alarm: DISC_LIM selects the OUT state that alarms.
        # Any value except 0/1 (255 by default) never alarms.
        try:
            lim = int(p.get("DISC_LIM", 255))
        except (TypeError, ValueError):
            lim = 255
        self.set_output("DISC_ACT", lim in (0, 1) and bool(out) == bool(lim))

    def _field_value(self) -> bool:
        """The contact state this scan.

        In Auto the bridge has written the (already inverted) tag value
        into ``OUT``.  In Man the bridge leaves ``OUT`` alone — forward
        wires read ``OUT`` before the block executes, so a refresh would
        hand downstream logic the very signal the operator is overriding —
        and the block reads the tag itself through the runtime context so
        ``PV_D`` still shows the live contact, as Azeo's Man mode does.
        """
        if self._actual_mode != self._MAN:
            return bool(self.get_output("OUT"))
        tag = self.config.params.get("tag", "")
        ctx = self.runtime_context
        if tag and ctx is not None:
            try:
                val = ctx.read(tag, None)
            except Exception:
                val = None
            if val is not None:
                return (not bool(val)) if self.config.params.get("invert", False) \
                    else bool(val)
        return self._pv_d

    def field_input_enabled(self) -> bool:
        """False while OOS or in Man so the bridge stops refreshing OUT.

        Same reasoning as the AI block: wires are propagated from ``OUT``
        before ``execute()`` runs, so the bridge must not overwrite an
        out-of-service or operator-forced output with the live contact.
        """
        return self._actual_mode not in (self._OOS, self._MAN)

    def set_field_status(self, quality: Quality) -> None:
        """Accept the transport status carried beside the contact value."""
        self._chan_status = quality
        if self._actual_mode != self._OOS:
            self.status = _BLOCK_STATUS.get(quality, BlockStatus.BAD)
        self.set_output_status("OUT", quality)
        self.set_output_status("PV_D", quality)

    def set_mode(self, mode: str):
        mode = str(mode).upper()
        if mode == "MANUAL":
            mode = self._MAN
        if mode not in (self._OOS, self._MAN, self._AUTO):
            return
        if mode == self._MAN and self._actual_mode != self._MAN:
            self._man_out = bool(self.get_output("OUT"))   # bumpless
        self._actual_mode = mode

    def set_manual_output(self, value: bool):
        """Operator-entered OUT_D, honoured while the block is in Man."""
        self._man_out = bool(value)
        if self._actual_mode == self._MAN:
            self.set_output("OUT", self._man_out)

    def reset(self):
        super().reset()
        self._primed = False
        self._pending = None
        self._pending_t = 0.0
        self._chan_status = Quality.GOOD


@register_block
class DOBlock(FunctionBlock):
    """Discrete Output — writes a boolean to SharedDataStore.

    Models the Azeo DO block (§628): Cas/Auto/Man/OOS modes, the
    ``CAS_IN_D`` cascade input with ``BKCAL_OUT_D`` back-calculation, the
    Invert I/O option, and readback (``READBACK_D`` → ``PV_D``, defaulting
    to a one-execution-delayed copy of ``OUT_D`` exactly as Azeo does
    when no readback channel is configured).

    **How the field write still works.**  The :class:`DataBridge` writes
    the value it finds on the ``IN`` terminal (applying ``invert``) to the
    configured tag.  So that a cascade or a held/manual setpoint reaches
    the field, ``execute()`` publishes the effective ``SP_D`` back onto
    ``IN`` before the bridge reads it.  With ``CAS_IN_D`` unconnected and
    mode CAS — the defaults — ``SP_D`` *is* ``IN`` and nothing changes.
    """
    block_type = "DO"
    category = BlockCategory.IO
    display_name = "Digital Output"
    description = "Azeo-style DO — cascade SP, BKCAL, readback, invert"

    # Azeo calls the setpoint SP_D; 176 shipped wires use IN, so IN
    # stays canonical and the Azeo spellings are accepted as aliases.
    terminal_aliases = {"SP_D": "IN", "IN_D": "IN", "RCAS_IN_D": "CAS_IN_D",
                        "IO_READBACK": "READBACK_IN_D"}

    _OOS = "OOS"
    _MAN = "MAN"
    _AUTO = "AUTO"
    _CAS = "CAS"

    config_choices = {"mode": ("CAS", "AUTO", "MAN", "OOS")}

    def __init__(self, instance_name: str = ""):
        self._actual_mode: str = self._CAS
        self._sp_d: bool = False
        self._out_d: bool = False
        self._prev_out_d: bool = False
        self._man_out: bool = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", DataType.BOOL, False, "Value to write (SP_D)")
        self.add_input("CAS_IN_D", DataType.BOOL, False,
                       "Cascade setpoint from an upstream block")
        self.add_input("READBACK_IN_D", DataType.BOOL, False,
                       "Field readback of the discrete output")
        self.add_input("SIMULATE_IN_D", DataType.BOOL, False,
                       "Simulated readback (overrides SIMULATE_VAL)")
        self.add_output("OUT_D", DataType.BOOL, False, "Value driven to the field")
        self.add_output("PV_D", DataType.BOOL, False, "Process value from readback")
        self.add_output("READBACK_D", DataType.BOOL, False, "Readback value")
        self.add_output("BKCAL_OUT_D", DataType.BOOL, False,
                        "Back-calculation for the upstream block", is_bkcal=True)
        self.add_output("MODE", DataType.STRING, "CAS", "Current mode")

    def get_config_schema(self):
        return {
            "tag": (str, "", "Data store tag to write"),
            "invert": (bool, False, "Invert the output (IO_OPTS Invert)"),
            "mode": (str, "CAS", "Initial mode: OOS, MAN, AUTO, CAS"),
            "normal_mode": (str, "", "Normal mode — what this block SHOULD normally be in. Blank means 'same as the initial mode'. Azeo lights the abnormal-mode icon when actual differs from NORMAL or from target; without this only the target half is answerable."),
            "sp_pv_track_man": (bool, False,
                                "SP_D copies PV_D while in Man (Azeo default is "
                                "True; False here preserves the previous behaviour)"),
            "use_pv_for_bkcal_out": (bool, False, "Use PV_D for BKCAL_OUT_D"),
            "SIMULATE_D": (bool, False,
                           "Enable simulation: stop writing to the field tag, take "
                           "READBACK_D from the simulate value, force OUT_D/"
                           "BKCAL_OUT_D status Good"),
            "SIMULATE_VAL": (bool, False,
                             "Simulated readback, used when SIMULATE_IN_D is not "
                             "connected"),
        }

    def _apply_config(self):
        mode_str = str(self.config.params.get("mode", "CAS")).upper()
        if mode_str == "MANUAL":
            mode_str = self._MAN
        self._actual_mode = mode_str if mode_str in (
            self._OOS, self._MAN, self._AUTO, self._CAS) else self._CAS

    def execute(self, dt: float):
        p = self.config.params
        invert = bool(p.get("invert", False))

        # ── setpoint selection ───────────────────────────────────────
        if self._actual_mode == self._OOS:
            sp = self._sp_d                       # hold last commanded state
        elif self._actual_mode == self._MAN:
            sp = self._man_out
            if p.get("sp_pv_track_man", False):
                sp = bool(self.get_output("PV_D"))
                self._man_out = sp
        elif (self._actual_mode == self._CAS
                and self.inputs["CAS_IN_D"].connected):
            sp = bool(self.get_input("CAS_IN_D"))
        else:
            sp = bool(self.get_input("IN"))
        self._sp_d = bool(sp)

        # The bridge writes invert(IN) to the field tag, so republish the
        # effective SP_D onto IN.  In the default configuration this is a
        # no-op (sp came from IN).
        self.inputs["IN"].value = self._sp_d

        self._out_d = (not self._sp_d) if invert else self._sp_d

        # ── readback ─────────────────────────────────────────────────
        # Azeo §628: "the readback signal passes through the SIMULATE_D
        # switch to become READBACK_D, then through the Invert option to
        # become PV_D".  With no readback channel configured Azeo
        # substitutes a copy of OUT_D delayed one execution.
        simulating = bool(p.get("SIMULATE_D", False))
        if simulating:
            readback = (bool(self.get_input("SIMULATE_IN_D"))
                        if self.inputs["SIMULATE_IN_D"].connected
                        else bool(p.get("SIMULATE_VAL", False)))
        elif self.inputs["READBACK_IN_D"].connected:
            readback = bool(self.get_input("READBACK_IN_D"))
        else:
            readback = self._prev_out_d
        self._prev_out_d = self._out_d
        pv_d = (not readback) if invert else readback

        self.set_output("OUT_D", self._out_d)
        self.set_output("READBACK_D", readback)
        self.set_output("PV_D", pv_d)
        self.set_output("BKCAL_OUT_D",
                        pv_d if p.get("use_pv_for_bkcal_out", False) else self._sp_d)
        if simulating:
            # "OUT_D status is forced Good regardless of hardware condition";
            # BKCAL_OUT_D likewise reports Good: Cascade while simulating.
            self.set_output_status("OUT_D", Quality.GOOD)
            self.set_output_status("BKCAL_OUT_D", Quality.GOOD)
        self.set_output("MODE", self._actual_mode)
        if self._actual_mode == self._OOS:
            self.status = BlockStatus.OOS
        elif self.status is BlockStatus.OOS:
            self.status = BlockStatus.GOOD

    def field_output_enabled(self) -> bool:
        """False while OOS so the bridge stops driving the field tag.

        Mirror of the AO hook: an out-of-service output block holds its
        last commanded state and must not keep writing to the process.
        The default mode is CAS, so this changes nothing for a block that
        never configures ``mode``.

        Simulation stops the field write too — Azeo §628 sends OUT_D to
        hardware only "with SIMULATE_D disabled and mode not OOS".
        """
        return (self._actual_mode != self._OOS
                and not self.config.params.get("SIMULATE_D", False))

    def set_mode(self, mode: str):
        mode = str(mode).upper()
        if mode == "MANUAL":
            mode = self._MAN
        if mode not in (self._OOS, self._MAN, self._AUTO, self._CAS):
            return
        if mode == self._MAN and self._actual_mode != self._MAN:
            self._man_out = self._sp_d              # bumpless
        self._actual_mode = mode

    def set_manual_output(self, value: bool):
        """Operator-entered SP_D, honoured while the block is in Man."""
        self._man_out = bool(value)

    def initialize_from_readback(self, value: bool) -> None:
        """Adopt the current field state without a first-scan transition."""
        field_value = bool(value)
        invert = bool(self.config.params.get("invert", False))
        self._sp_d = not field_value if invert else field_value
        self._out_d = field_value
        self._prev_out_d = field_value
        self._man_out = self._sp_d
        self.inputs["IN"].value = self._sp_d
        self.set_output("OUT_D", self._out_d)
        self.set_output("READBACK_D", field_value)
        self.set_output("PV_D", not field_value if invert else field_value)
        self.set_output("BKCAL_OUT_D", self._sp_d)
        self.set_output("MODE", self._actual_mode)

    def reset(self):
        super().reset()
        self._sp_d = False
        self._out_d = False
        self._prev_out_d = False
