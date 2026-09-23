"""Data bridge between strategy blocks and SharedDataStore.

AI blocks read their configured tag from the store.
AO blocks write their input value to the store via queue_write.
PID blocks publish their state to ctrl.<instance_name>.* keys so that
P&ID dynamos and faceplates can display live data.
"""
from __future__ import annotations
import logging

from ..model.terminal import LimitStatus as TermLimit, Quality
from ..model.strategy_graph import StrategyGraph
from .bridge_contract import PID_SIGNAL_EXPORTS

log = logging.getLogger("strategy.bridge")

#: Sentinel telling "tag absent from the store" apart from "tag is None"
#: and from a legitimate 0.0 — both of which used to look the same.
_MISSING = object()


class DataBridge:
    """Bridges function block I/O to SharedDataStore."""

    # TAG monitor blocks mirror PLC control-tag members. They are read-only
    # monitors, not aliases for AI/AO/DI/DO, so they belong only in the input
    # sampling phase and can never reach the field-output write paths.
    _TAG_MONITOR_TYPES = frozenset({"TAGAI", "TAGAO", "TAGDI", "TAGDO"})

    def __init__(self, store):
        """
        Args:
            store: SharedDataStore instance (or any object with get_all() and queue_write())
        """
        self._store = store
        self._dmc_hb: dict = {}   # PID instance -> last-seen DMC heartbeat
        self._bad_tags: set = set()   # (block, tag) already logged as Bad
        # (block, tag) pairs that have produced a real reading at least
        # once. Until then a missing tag means "engine has not published
        # yet", not "measurement failed" — but only inside the startup
        # grace window (see _STARTUP_GRACE_SCANS).
        self._seen_tags: set = set()
        self._scan_count: int = 0

    # ----------------------------------------------------------------
    # Maximum BKCAL chain depth for propagation passes.
    # Covers chains like: AO → SCALER → PID (depth 3) or nested
    # cascade PID → SCALER → PID → SCALER → PID (depth 3).
    # ----------------------------------------------------------------
    _MAX_BKCAL_DEPTH = 3

    # ----------------------------------------------------------------
    # Scans during which a tag that has never been read is treated as
    # "not published yet" rather than a failed measurement.  Engines
    # publish their first values on their own first step, which happens
    # after the strategy is already online, so scan 1 legitimately finds
    # some tags absent (SHOF's level/pressure tags among them).  Past
    # this window a still-absent tag is a real Bad — a mistyped tag must
    # not read Good forever.  Keep this at 1: the runtime copies wire
    # values before the source block executes, so a downstream block sees
    # a status one scan late, and a longer window delays the Bad past the
    # point where a shed would still be timely.
    # ----------------------------------------------------------------
    _STARTUP_GRACE_SCANS = 1

    def initialize_outputs(self, graph: StrategyGraph):
        """Pre-scan initialization: seed AO/PID from current store values.

        Called once before the first scan so that BKCAL wires carry the real
        valve positions into PID blocks, preventing output bumps on startup.

        Steps:
            0. _init_ai_blocks — read AI tags so PID PV is available
            1. _init_ao_blocks / _init_do_blocks — seed field outputs from
               current store values
            2. _propagate_bkcal — propagate BKCAL wires + SCALER inverse
            3. _init_pid_from_bkcal — initialize PID outputs from BKCAL_IN
            4. _propagate_forward_cascade — forward wire + SCALER execution
            5. _condition_pid_integrators — final integrator conditioning
        """
        data = self._store.get_all()

        self._init_ai_blocks(graph, data)
        self._init_ao_blocks(graph, data)
        self._init_do_blocks(graph, data)
        self._propagate_bkcal(graph)
        self._init_pid_from_bkcal(graph)
        self._propagate_forward_cascade(graph)
        self._condition_pid_integrators(graph)

    # ---- Step 0: pre-read AI blocks so PID PV is available ----

    def _init_ai_blocks(self, graph: StrategyGraph, data: dict):
        """Read AI tags from store and propagate to PID PV inputs.

        Without this, PID.PV defaults to 0 which causes a massive integrator
        error on first scan.
        """
        # 0a. Read AI outputs from store.
        # This one-shot seed runs for every AI, including one configured OOS:
        # a block that goes online out of service has no "last value" yet, so
        # the live measurement at go-online is the value it then holds. Only
        # the per-scan refresh in read_inputs() is gated on the block mode.
        for block in graph.blocks.values():
            if block.block_type == "AI":
                tag = block.config.params.get("tag", "")
                if tag:
                    val = data.get(tag, 0.0)
                    try:
                        block.set_output("OUT", float(val) if val is not None else 0.0)
                    except (TypeError, ValueError):
                        block.set_output("OUT", 0.0)

        # 0b. Propagate AI → PID forward wires so PID.PV is updated
        for wire in graph.wires.values():
            if not wire.is_bkcal:
                src_block = graph.blocks.get(wire.src_block_id)
                dst_block = graph.blocks.get(wire.dst_block_id)
                if (src_block and dst_block
                        and src_block.block_type == "AI"
                        and dst_block.block_type == "PID"):
                    src_term = src_block.outputs.get(wire.src_terminal)
                    dst_term = dst_block.inputs.get(wire.dst_terminal)
                    if src_term and dst_term and src_term.value is not None:
                        dst_term.value = src_term.value

        # 0c. Let PID blocks latch the PV from their IN terminal
        for block in graph.blocks.values():
            if block.block_type == "PID" and hasattr(block, '_pid_core'):
                block._ensure_block()
                pid = block._pid_core
                if pid is None:
                    continue
                in_term = block.inputs.get("IN")
                if in_term and in_term.value is not None:
                    from azeo_control_trainer.core.pid.core import SignalStatus
                    pid.IN = SignalStatus(value=float(in_term.value))
                    pid.PV = float(in_term.value)

    # ---- Step 1: seed AO blocks from current store values ----

    def _init_ao_blocks(self, graph: StrategyGraph, data: dict):
        """Seed AO blocks with current valve position from store."""
        for block in graph.blocks.values():
            if block.block_type == "AO":
                tag = block.config.params.get("tag", "")
                if tag:
                    current = data.get(tag)
                    if current is not None:
                        try:
                            current_f = float(current)
                        except (ValueError, TypeError):
                            log.warning(
                                "AO %s: store tag '%s' has non-numeric "
                                "value %r, skipping init",
                                block.instance_name, tag, current)
                            continue
                        ao_lo = float(block.config.params.get("out_lo", 0.0))
                        ao_hi = float(block.config.params.get("out_hi", 100.0))
                        val = max(ao_lo, min(ao_hi, current_f))
                        if hasattr(block, 'initialize_from_readback'):
                            block.initialize_from_readback(val)
                        else:
                            block.set_output("BKCAL_OUT", val)
                        block.inputs["CAS_IN"].value = val
                        log.info("AO %s init from store '%s' = %.4f",
                                 block.instance_name, tag, val)

    def _init_do_blocks(self, graph: StrategyGraph, data: dict):
        """Seed DO blocks with the current field state before first scan."""
        for block in graph.blocks.values():
            if block.block_type != "DO":
                continue
            tag = block.config.params.get("tag", "")
            if not tag or tag not in data:
                continue
            initialise = getattr(block, "initialize_from_readback", None)
            if callable(initialise):
                initialise(bool(data[tag]))
                log.info("DO %s init from store '%s' = %s",
                         block.instance_name, tag, bool(data[tag]))

    # ---- Step 2: propagate BKCAL wires + SCALER inverse ----

    @staticmethod
    def _propagate_bkcal_wires(graph: StrategyGraph) -> bool:
        """One pass of BKCAL wire propagation. Returns True if any value changed."""
        changed = False
        for wire in graph.wires.values():
            if wire.is_bkcal:
                src_block = graph.blocks.get(wire.src_block_id)
                dst_block = graph.blocks.get(wire.dst_block_id)
                if src_block and dst_block:
                    src_term = src_block.outputs.get(wire.src_terminal)
                    dst_term = dst_block.inputs.get(wire.dst_terminal)
                    if src_term and dst_term and src_term.value is not None:
                        if dst_term.value != src_term.value:
                            dst_term.value = src_term.value
                            changed = True
        return changed

    @staticmethod
    def _run_scaler_inverse(graph: StrategyGraph):
        """Compute SCALER inverse on all blocks that have BKCAL_IN connected."""
        from ..blocks.signal_blocks import scaler_inverse
        for block in graph.blocks.values():
            if block.block_type == "SCALER":
                bk_in = block.inputs.get("BKCAL_IN")
                if bk_in and bk_in.connected and bk_in.value is not None:
                    p = block.config.params
                    bk_out = scaler_inverse(
                        float(bk_in.value),
                        p.get("in_lo", 0.0), p.get("in_hi", 100.0),
                        p.get("out_lo", 0.0), p.get("out_hi", 100.0),
                        p.get("invert", False))
                    block.set_output("BKCAL_OUT", bk_out)

    def _propagate_bkcal(self, graph: StrategyGraph):
        """Propagate BKCAL wires so PID blocks receive current valve positions.

        Multiple passes handle chains with intermediate blocks
        (e.g. AO → SCALER → PID).
        """
        for _ in range(self._MAX_BKCAL_DEPTH):
            changed = self._propagate_bkcal_wires(graph)
            self._run_scaler_inverse(graph)
            if not changed:
                break

    # ---- Step 3: initialize PID outputs from BKCAL_IN ----

    def _init_pid_from_bkcal(self, graph: StrategyGraph):
        """Initialize PID output from BKCAL_IN (like IMan initialization).

        Multiple passes handle cascade chains (inner PID → outer PID).
        After each pass, re-propagate BKCAL wires for the next cascade level.
        Integrator conditioning uses OUT/gain_a (same as IMan mode in PID core),
        deferring full p_term conditioning to Step 5 after cascade SP is known.
        """
        from azeo_control_trainer.core.pid.core import SignalStatus, LimitStatus

        for cascade_pass in range(self._MAX_BKCAL_DEPTH):
            changed = False
            for block in graph.blocks.values():
                if block.block_type == "PID" and hasattr(block, '_pid_core'):
                    block._ensure_block()
                    pid = block._pid_core
                    if pid is None:
                        continue
                    bkcal_term = block.inputs.get("BKCAL_IN")
                    if bkcal_term and bkcal_term.connected and bkcal_term.value is not None:
                        bkcal_val = float(bkcal_term.value)
                        current_out = pid.OUT.value if pid.OUT else 0
                        if cascade_pass == 0 or abs(bkcal_val - current_out) > 0.01:
                            pid.BKCAL_IN = SignalStatus(
                                value=bkcal_val, limit=LimitStatus.NOT_LIMITED)
                            pid.OUT = SignalStatus(value=bkcal_val)
                            block.set_output("OUT", bkcal_val)
                            gain_a = pid._normalized_gain()
                            if abs(gain_a) > 1e-12:
                                pid._integral = bkcal_val / gain_a
                                log.info("PID %s init OUT=%.4f from BKCAL (pass %d)",
                                         block.instance_name, bkcal_val, cascade_pass)
                            else:
                                log.warning("PID %s: gain too small (%.6f) for "
                                            "integrator conditioning",
                                            block.instance_name, gain_a)
                            changed = True

                    # Update BKCAL_OUT so cascade chain can propagate to outer loops
                    bkcal_out_val = pid.PV if pid.use_pv_for_bkcal_out else pid.SP_WRK
                    block.set_output("BKCAL_OUT", bkcal_out_val)

            if not changed and cascade_pass > 0:
                break

            # Re-propagate BKCAL wires + SCALER inverse for cascade chains
            for _ in range(self._MAX_BKCAL_DEPTH):
                self._propagate_bkcal_wires(graph)
                self._run_scaler_inverse(graph)

    # ---- Step 4: forward cascade propagation ----

    def _propagate_forward_cascade(self, graph: StrategyGraph):
        """Propagate outer PID OUT → cascade SCALER → inner PID SP.

        Without this, inner cascade PIDs see SP=0 on the first scan because
        the runtime's wire propagation runs before block execution, and
        SCALERs haven't computed OUT yet.

        Also initializes stateful functional blocks (LEAD_LAG) so they
        don't produce a startup transient from _prev_in/out = 0.
        """
        from ..blocks.signal_blocks import scaler_forward

        for _ in range(self._MAX_BKCAL_DEPTH):
            # Propagate forward wires
            for wire in graph.wires.values():
                if not wire.is_bkcal:
                    src_block = graph.blocks.get(wire.src_block_id)
                    dst_block = graph.blocks.get(wire.dst_block_id)
                    if src_block and dst_block:
                        src_term = src_block.outputs.get(wire.src_terminal)
                        dst_term = dst_block.inputs.get(wire.dst_terminal)
                        if src_term and dst_term and src_term.value is not None:
                            dst_term.value = src_term.value
            # Execute SCALERs so their OUT is computed from IN
            for block in graph.blocks.values():
                if block.block_type == "SCALER":
                    in_term = block.inputs.get("IN")
                    if in_term and in_term.value is not None:
                        p = block.config.params
                        out_val = scaler_forward(
                            float(in_term.value),
                            p.get("in_lo", 0.0), p.get("in_hi", 1.0),
                            p.get("out_lo", 0.0), p.get("out_hi", 100.0),
                            p.get("invert", False))
                        block.set_output("OUT", out_val)

        # Initialize stateful functional blocks (LEAD_LAG):
        # Seed _prev_in = _prev_out = current input so the first scan
        # doesn't produce a derivative spike from (input - 0).
        for block in graph.blocks.values():
            if block.block_type == "LEAD_LAG":
                in_term = block.inputs.get("IN")
                if in_term and in_term.value is not None:
                    val = float(in_term.value)
                    gain = block.config.params.get("GAIN", 1.0)
                    block._prev_in = val
                    block._prev_out = gain * val  # DC gain = GAIN
                    block.set_output("OUT", gain * val)
                    log.info("LEAD_LAG %s init: _prev_in=%.4f _prev_out=%.4f",
                             block.instance_name, val, gain * val)

    # ---- Step 5: re-condition PID integrators ----

    def _condition_pid_integrators(self, graph: StrategyGraph):
        """Re-condition PID dynamic state after cascade SP propagation.

        Reset is back-calculated after proportional/feedforward action and
        derivative memory is seated at the live PV/SP.  The first automatic
        scan therefore starts from the downstream readback instead of adding
        P and D on top of it.
        """
        for block in graph.blocks.values():
            if block.block_type != "PID" or not hasattr(block, '_pid_core'):
                continue
            pid = block._pid_core
            if pid is None:
                continue
            # Latch SP from input terminal (cascade SCALER output or config sp_init)
            sp_term = block.inputs.get("SP")
            if sp_term and sp_term.value is not None:
                sp_val = float(sp_term.value)
                if sp_val != 0.0 or sp_term.connected:
                    pid.SP = sp_val
            # Feedforward participates in the operating point exactly as it
            # will when the wrapper executes the core on the first scan.
            ff_term = block.inputs.get("FF_VAL")
            if (pid.ff_enable and ff_term and ff_term.connected
                    and ff_term.value is not None):
                from azeo_control_trainer.core.pid.core import SignalStatus
                pid.FF_VAL = SignalStatus(value=float(ff_term.value))

            target_out = pid.OUT.value if pid.OUT else 0.0
            gain_a = pid._normalized_gain()
            if abs(gain_a) <= 1e-12:
                log.warning("PID %s: gain too small (%.6f) for "
                            "bumpless conditioning",
                            block.instance_name, gain_a)
            pid.condition_for_output(
                pv=pid.PV,
                sp=pid.SP,
                output=target_out,
                first_dt=graph.scan_ms / 1000.0,
            )
            block.set_output("OUT", target_out)
            log.info("PID %s conditioned: SP=%.4f PV=%.4f OUT=%.4f "
                     "P=%.6f integral=%.6f",
                     block.instance_name, pid.SP, pid.PV,
                     target_out, pid.p_term, pid._integral)

    # ---- mode gating: an OOS block is not serviced against the field ----

    @staticmethod
    def _drives_from_field(block) -> bool:
        """True when an input block's OUT may be refreshed from the store.

        Blocks that model Azeo modes expose ``field_input_enabled()`` and
        return False while out of service.  Anything without the hook (older
        or third-party input blocks) keeps the previous behaviour.
        """
        hook = getattr(block, "field_input_enabled", None)
        return bool(hook()) if callable(hook) else True

    @staticmethod
    def _drives_to_field(block) -> bool:
        """True when an output block's OUT may be written to the store.

        Mirror of :meth:`_drives_from_field` for AO/DO-style blocks, via an
        optional ``field_output_enabled()`` hook.
        """
        hook = getattr(block, "field_output_enabled", None)
        return bool(hook()) if callable(hook) else True

    def _sample_quality(self, tag: str):
        """Translate provider-neutral sample metadata to terminal Quality."""
        getter = getattr(self._store, "get_sample", None)
        sample = getter(tag) if callable(getter) else None
        if sample is None:
            return None
        if bool(getattr(sample, "stale", False)):
            return Quality.BAD
        raw = getattr(sample, "quality", None)
        if raw is None:
            return Quality.GOOD
        name = str(getattr(raw, "name", raw)).upper()
        if "BAD" in name:
            return Quality.BAD
        if "UNCERTAIN" in name or name in {"1", "WARNING"}:
            return Quality.UNCERTAIN
        if "GOOD" in name or name in {"0", "OK"}:
            return Quality.GOOD
        try:
            level = int(raw)
        except (TypeError, ValueError):
            # An unrecognised status is not evidence that a channel is Good.
            return Quality.BAD
        return (Quality.GOOD if level <= 0 else
                Quality.UNCERTAIN if level == 1 else Quality.BAD)

    def _refresh_ai(self, block, tag: str, data: dict,
                    drive_out: bool = True) -> None:
        """Refresh one AI from the store, with signal quality.

        Terminals carry a :class:`Quality` (``model/terminal.py``), so the
        bridge now says *how good* the measurement is instead of silently
        substituting 0.0:

        * tag present and numeric → OUT updated, channel status **Good**;
        * tag **missing** from the store, ``None``, or **non-numeric** →
          OUT holds its last value and the channel status is **Bad**, which
          the AI publishes on its OUT terminal.  A downstream PID with
          *Target to Manual if Bad IN* then sheds, exactly as Azeo does
          on a failed measurement.

        ``drive_out`` is the ``field_input_enabled()`` gate: False for an
        OOS or Man block, whose OUT it must not touch.  The measurement is
        still handed over via ``set_field_value()`` so a Man block keeps PV
        tracking the field.

        Blocks predating ``set_field_status`` fall back to the previous
        ``BlockStatus`` handling so nothing third-party breaks.
        """
        from ..model.block_base import BlockStatus
        val = data.get(tag, _MISSING)
        sample_quality = self._sample_quality(tag)
        fval = None
        key_seen = (id(block), tag)
        if val is _MISSING or val is None:
            # A tag that has never been published is *not yet available*
            # during the startup grace window, not a failed measurement:
            # engines publish their first values on their own first step,
            # after the strategy is already online, and calling that Bad
            # sheds level loops at startup (SHOF's LIC-201 among them).
            # Once the window closes, or once the tag has been read and
            # then vanished, it is a genuine failure.
            first_read = key_seen not in self._seen_tags
            quality = (Quality.GOOD
                       if first_read and self._scan_count <= self._STARTUP_GRACE_SCANS
                       else Quality.BAD)
        else:
            try:
                fval = float(val)
            except (TypeError, ValueError):
                quality = Quality.BAD
            else:
                quality = sample_quality or Quality.GOOD
                self._seen_tags.add(key_seen)
                if drive_out:
                    block.set_output("OUT", fval)

        offer = getattr(block, "set_field_value", None)
        if callable(offer):
            offer(fval, quality)

        # One log line per transition, not per scan.
        key = (id(block), tag)
        if quality is Quality.BAD:
            if key not in self._bad_tags:
                self._bad_tags.add(key)
                log.warning("AI %s: store tag '%s' is %s — publishing Bad "
                            "status and holding OUT=%r", block.instance_name, tag,
                            "missing" if val is _MISSING else
                            f"field status {quality.name}" if fval is not None else
                            f"non-numeric ({val!r})",
                            block.get_output("OUT"))
        else:
            self._bad_tags.discard(key)

        hook = getattr(block, "set_field_status", None)
        if callable(hook):
            hook(quality)
        elif quality is Quality.BAD:
            block.status = BlockStatus.BAD
        elif block.status is BlockStatus.BAD:
            block.status = BlockStatus.GOOD

    @staticmethod
    def _all_io_blocks(graph):
        """Yield every block in the graph, recursing into composite interiors.

        IO blocks (AI/AO/DI/DO) and PID blocks placed *inside* a composite
        must be serviced against the data store exactly like top-level blocks,
        otherwise an AI inside a composite never reads its tag (it outputs 0,
        starving any downstream controller) and an AO inside never drives its
        field tag. Nested composites are handled recursively.
        """
        from azeo_control_trainer.core.strategy.blocks.composite_blocks import CompositeBlock
        for block in graph.blocks.values():
            yield block
            if isinstance(block, CompositeBlock):
                yield from DataBridge._all_io_blocks(block.inner_graph)

    def read_inputs(self, graph: StrategyGraph):
        """Read field and PLC-monitor values before block execution.

        Also routes operator write-backs (SP, Mode, ManOut) from faceplates
        into PID block inputs so the faceplate controls work. Recurses into
        composite interiors so inner IO/PID blocks are serviced too.
        """
        data = self._store.get_all()
        tag_samples = None
        self._scan_count += 1

        for block in self._all_io_blocks(graph):
            if block.block_type in {"MEM_FLOAT", "MEM_BOOL", "MEM_INT", "MEM_STRING"}:
                block._memory_tags = getattr(getattr(self._store, "tagdb", None), "memory", None)
            if block.block_type in self._TAG_MONITOR_TYPES:
                if tag_samples is None:
                    try:
                        getter = getattr(self._store, "get_samples", None)
                        tag_samples = getter() if callable(getter) else {}
                    except Exception:
                        # Provider status failures are signal quality failures;
                        # they must not abort the controller scan.
                        tag_samples = {}
                update = getattr(block, "sample_from_bridge", None)
                if not callable(update):
                    update = getattr(block, "update_from_snapshot", None)
                if callable(update):
                    update(data, tag_samples)

            elif block.block_type == "AI":
                # Operator write-backs (faceplate → ai.<name>.wb.*), the
                # mirror of the AO route below.  Azeo Man mode on an AI is
                # "operator writes OUT while PV keeps tracking the field";
                # without this route the block-level API had no way in.
                name = block.instance_name
                prefix = f"ai.{name}."
                mode_wb = data.get(f"{prefix}wb.Mode")
                if mode_wb is not None and hasattr(block, "set_mode"):
                    block.set_mode(str(mode_wb))
                man_wb = data.get(f"{prefix}wb.ManOut")
                if man_wb is not None and hasattr(block, "set_manual_output"):
                    try:
                        block.set_manual_output(float(man_wb))
                    except (TypeError, ValueError):
                        log.warning("AI %s: invalid ManOut write-back value: %r",
                                    name, man_wb)

                tag = block.config.params.get("tag", "")
                # An out-of-service block does not execute: its OUT holds the
                # last value it produced.  Refreshing OUT from the field here
                # regardless of mode would make OOS cosmetic — the live value
                # would keep driving every downstream block while the AI's
                # own status/alarm outputs sat frozen.  The measurement is
                # still *offered* to the block (PV tracks the field in Man);
                # only the drive onto OUT is gated.
                if tag:
                    self._refresh_ai(block, tag, data,
                                     drive_out=self._drives_from_field(block))

            elif block.block_type == "DI":
                # Same mode gating as the AI: a DI in Man or OOS owns its OUT
                # and must not be overwritten with the live contact.
                tag = block.config.params.get("tag", "")
                if tag:
                    val = data.get(tag, _MISSING)
                    key_seen = (id(block), tag)
                    quality = self._sample_quality(tag)
                    if val is _MISSING:
                        quality = (Quality.GOOD
                                   if key_seen not in self._seen_tags
                                   and self._scan_count
                                   <= self._STARTUP_GRACE_SCANS
                                   else Quality.BAD)
                    else:
                        self._seen_tags.add(key_seen)
                        quality = quality or Quality.GOOD
                        if self._drives_from_field(block):
                            bval = bool(val)
                            if block.config.params.get("invert", False):
                                bval = not bval
                            block.set_output("OUT", bval)
                    hook = getattr(block, "set_field_status", None)
                    if callable(hook):
                        hook(quality)

            elif block.block_type == "AO":
                # Route operator write-backs for AO blocks
                name = block.instance_name
                prefix = f"ao.{name}."

                # Mode write-back
                mode_wb = data.get(f"{prefix}wb.Mode")
                if mode_wb is not None and hasattr(block, 'set_mode'):
                    block.set_mode(str(mode_wb))

                # SP write-back (Auto mode)
                sp_wb = data.get(f"{prefix}wb.SP")
                if sp_wb is not None and hasattr(block, 'set_sp'):
                    try:
                        block.set_sp(float(sp_wb))
                    except (TypeError, ValueError):
                        log.warning("AO %s: invalid SP write-back value: %r", name, sp_wb)

                # ManOut write-back (Manual mode)
                man_wb = data.get(f"{prefix}wb.ManOut")
                if man_wb is not None and hasattr(block, 'set_manual_output'):
                    try:
                        block.set_manual_output(float(man_wb))
                    except (TypeError, ValueError):
                        log.warning("AO %s: invalid ManOut write-back value: %r", name, man_wb)

            elif block.block_type == "PID":
                # Route operator write-backs from faceplates into PID inputs
                name = block.instance_name
                prefix = f"ctrl.{name}."

                # Ensure PID core is initialized before processing write-backs
                if hasattr(block, '_ensure_block'):
                    block._ensure_block()

                # Operator SP write-back (faceplate writes ctrl.<tag>.wb.SP)
                sp_val = data.get(f"{prefix}wb.SP")
                if sp_val is not None and block.inputs.get("SP"):
                    if not block.inputs["SP"].connected:
                        block.inputs["SP"].value = float(sp_val)

                # Operator mode write-back
                mode_str = data.get(f"{prefix}wb.Mode")
                if mode_str is not None and hasattr(block, '_pid_core'):
                    pid_core = block._pid_core
                    if pid_core is not None:
                        from ..model.modes import str_to_mode
                        target = str_to_mode(str(mode_str))
                        if target is not None:
                            pid_core.set_target_mode(target)

                # Cross-module cascade demand.  A PID in CAS consumes CAS_IN,
                # not operator SP; keeping these write-back namespaces
                # separate prevents a supervisory handoff from changing a
                # dormant parameter while the live cascade remains at zero.
                cas_val = data.get(f"{prefix}wb.CAS_IN", _MISSING)
                if hasattr(block, '_pid_core') and block._pid_core is not None:
                    pid_core = block._pid_core
                    if cas_val is _MISSING:
                        # Prime CAS_IN from the current working setpoint while
                        # a newly requested CAS mode waits for its handoff.
                        # This breaks the mode/ownership circularity without a
                        # first-CAS-scan setpoint bump.
                        from azeo_control_trainer.core.pid.core import Mode
                        if (not block.inputs["CAS_IN"].connected
                                and (pid_core.target_mode is Mode.Cas
                                     or pid_core.actual_mode is Mode.Cas)):
                            block.inputs["CAS_IN"].value = float(pid_core.SP_WRK)
                            block.inputs["CAS_IN"].status = Quality.GOOD
                            block._bridge_cas_available = True
                    else:
                        try:
                            block.inputs["CAS_IN"].value = float(cas_val)
                            quality_name = str(data.get(
                                f"{prefix}wb.CAS_IN.quality", "BAD"
                            )).strip().upper()
                            block.inputs["CAS_IN"].status = (
                                Quality.__members__.get(
                                    quality_name, Quality.BAD
                                )
                            )
                            limit_name = str(data.get(
                                f"{prefix}wb.CAS_IN.limit", "NOT_LIMITED"
                            )).strip().upper()
                            block.inputs["CAS_IN"].limit = (
                                TermLimit.__members__.get(
                                    limit_name, TermLimit.NOT_LIMITED
                                )
                            )
                            block._bridge_cas_available = True
                        except (TypeError, ValueError):
                            block.inputs["CAS_IN"].status = Quality.BAD
                            block._bridge_cas_available = False

                # RCAS_IN write-back (from DMC strategy or external MPC)
                rcas_val = data.get(f"{prefix}wb.RCAS_IN")
                if rcas_val is not None and hasattr(block, '_pid_core'):
                    pid_core = block._pid_core
                    if pid_core is not None:
                        from azeo_control_trainer.core.pid.core import SignalStatus
                        pid_core.RCAS_IN = SignalStatus(value=float(rcas_val))
                        # Also set the strategy block input for execute()
                        if "RCAS_IN" in block.inputs:
                            block.inputs["RCAS_IN"].value = float(rcas_val)

                # ROUT_IN write-back (DMC/host remote OUTPUT, used in ROUT mode).
                # This is the Azeo DMC->OP path (vs RCAS_IN for DMC->SP).
                rout_val = data.get(f"{prefix}wb.ROUT_IN")
                if rout_val is not None and hasattr(block, '_pid_core'):
                    pid_core = block._pid_core
                    if pid_core is not None:
                        from azeo_control_trainer.core.pid.core import SignalStatus
                        pid_core.ROUT_IN = SignalStatus(value=float(rout_val))
                        if "ROUT_IN" in block.inputs:
                            block.inputs["ROUT_IN"].value = float(rout_val)

                # DMC watchdog heartbeat — a *changed* heartbeat is a fresh host
                # write and resets the shed timer; a stalled one trips the core's
                # shed-on-timeout (SHED_TIME/SHED_OPT). Only active if a loop
                # configures shed_time > 0.
                hb = data.get(f"{prefix}wb.DMC_HB")
                if hb is not None and hasattr(block, '_pid_core') \
                        and block._pid_core is not None:
                    if self._dmc_hb.get(name) != hb:
                        self._dmc_hb[name] = hb
                        block._pid_core.feed_watchdog()

                # Operator ManOut write-back (faceplate sends 0-100%,
                # convert to PID internal range [out_lo, out_hi])
                man_out = data.get(f"{prefix}wb.ManOut")
                if man_out is not None and hasattr(block, '_pid_core'):
                    pid_core = block._pid_core
                    if pid_core is not None:
                        man_val = float(man_out)
                        _out_lo = float(block.config.params.get("out_lo", 0.0))
                        _out_hi = float(block.config.params.get("out_hi", 100.0))
                        _out_span = _out_hi - _out_lo
                        if abs(_out_span) > 1e-12 and abs(_out_span - 100.0) > 0.01:
                            man_val = _out_lo + man_val / 100.0 * _out_span
                        pid_core._manual_output = man_val   # faceplate telemetry
                        # Apply to the real output: in Man/ROut the core keeps
                        # OUT as-is, so the manual value must be written into OUT.
                        # set_output_manual() is a no-op outside Man/ROut, so
                        # AUTO/CAS/RCAS loops are unaffected.
                        if hasattr(pid_core, "set_output_manual"):
                            pid_core.set_output_manual(man_val)

                # Tuning parameter write-backs from faceplate
                if hasattr(block, '_pid_core') and block._pid_core is not None:
                    pid_core = block._pid_core
                    _TUNING_KEYS = {
                        'GAIN': 'gain', 'RESET': 'reset', 'RATE': 'rate',
                        'Kp': 'gain', 'Ti': 'reset', 'Td': 'rate',
                        'Alpha': 'alpha', 'Beta': 'beta', 'Gamma': 'gamma',
                        'pv_ftime': 'pv_ftime', 'sp_ftime': 'sp_ftime',
                        'sp_rate_up': 'sp_rate_up', 'sp_rate_dn': 'sp_rate_dn',
                        'ff_gain': 'ff_gain', 'ideadband': 'ideadband',
                        'bias': 'bias', 'recovery_fltr': 'recovery_fltr',
                        'nl_gap': 'nl_gap', 'nl_hyst': 'nl_hyst',
                        'nl_tband': 'nl_tband', 'nl_minmod': 'nl_minmod',
                        'alarm_hys': 'alarm_hys',
                        'hi_hi_lim': 'hi_hi_lim', 'hi_lim': 'hi_lim',
                        'lo_lim': 'lo_lim', 'lo_lo_lim': 'lo_lo_lim',
                        'dv_hi_lim': 'dv_hi_lim', 'dv_lo_lim': 'dv_lo_lim',
                        'OP_Max': 'out_hi_lim', 'OP_Min': 'out_lo_lim',
                        'arw_hi_lim': 'arw_hi_lim', 'arw_lo_lim': 'arw_lo_lim',
                        'SP_Max': 'sp_hi_lim', 'SP_Min': 'sp_lo_lim',
                    }
                    for store_key, attr in _TUNING_KEYS.items():
                        wb_key = f"{prefix}wb.{store_key}"
                        val = data.get(wb_key)
                        if val is not None:
                            try:
                                setattr(pid_core, attr, float(val))
                            except (TypeError, ValueError):
                                log.warning(
                                    "PID %s: invalid write-back value for %s: %r",
                                    name, store_key, val)

                    # Boolean option write-backs
                    _BOOL_KEYS = [
                        'use_pidplus', 'use_nonlinear_gain',
                        'dynamic_reset_limit', 'use_delayed_out_bad_pv',
                        'direct_acting', 'bypass', 'bypass_enable',
                        'sp_pv_track_man', 'sp_pv_track_lo_iman',
                        'sp_pv_track_rout', 'use_pv_for_bkcal_out',
                        'no_out_limits_in_man', 'obey_sp_lim_cas_rcas',
                        'track_enable', 'track_in_manual',
                        'ff_enable', 'simulate_enabled',
                    ]
                    for attr in _BOOL_KEYS:
                        wb_key = f"{prefix}wb.{attr}"
                        val = data.get(wb_key)
                        if val is not None:
                            setattr(pid_core, attr, bool(val))

                    # Form / structure write-backs (string enum values)
                    from azeo_control_trainer.core.pid.core import PIDForm, Structure
                    form_val = data.get(f"{prefix}wb.form")
                    if form_val is not None:
                        for f in PIDForm:
                            if f.value == form_val:
                                pid_core.form = f
                                break
                    struct_val = data.get(f"{prefix}wb.structure")
                    if struct_val is not None:
                        for s in Structure:
                            if s.value == struct_val:
                                pid_core.structure = s
                                break

            # ── APC_CONTROL: route operator enable write-back ──
            elif block.block_type == "APC_CONTROL":
                enable_val = data.get("dmc.enable")
                if enable_val is not None:
                    if not block.inputs["ENABLE"].connected:
                        block.inputs["ENABLE"].value = bool(enable_val)
                # Route watchdog timeout and cycle time SP
                wt = data.get("dmc.watchdog_timeout")
                if wt is not None:
                    block.config.params["cycle_period"] = float(wt)
                ct_sp = data.get("dmc.cycle_time_sp")
                if ct_sp is not None:
                    block.config.params["cycle_period"] = float(ct_sp)

            # ── SP_HANDOFF: auto-read PID SP from controller_tag ──
            elif block.block_type == "SP_HANDOFF":
                ctrl_tag = block.config.params.get("controller_tag", "")
                if ctrl_tag:
                    # Read current PID SP into DCS_SP (if not wired)
                    if not block.inputs["DCS_SP"].connected:
                        sp_val = data.get(f"ctrl.{ctrl_tag}.SP_WRK")
                        if sp_val is None:
                            # Backwards compatibility for a producer that has
                            # not yet published the explicit working SP.
                            sp_val = data.get(f"ctrl.{ctrl_tag}.SP")
                        if sp_val is not None:
                            block.inputs["DCS_SP"].value = float(sp_val)
                            block.inputs["DCS_SP"].status = Quality.GOOD
                        else:
                            block.inputs["DCS_SP"].status = Quality.BAD
                    # A configured downstream-mode gate is evaluated inside
                    # SP_HANDOFF, before its rising-edge capture and ramp.
                    # Gating only the eventual write would let the hidden
                    # target move while the slave was not accepting cascade,
                    # then jump when its mode finally changed.
                    set_mode = getattr(block, "set_controller_mode", None)
                    if callable(set_mode):
                        set_mode(str(data.get(f"ctrl.{ctrl_tag}.Mode") or ""))

            # ── MV_CLAMP: auto-read PID OUT from controller_tag ──
            elif block.block_type == "MV_CLAMP":
                ctrl_tag = block.config.params.get("controller_tag", "")
                if ctrl_tag:
                    # Read current PID OUT into MV_READBACK (if not wired)
                    if not block.inputs["MV_READBACK"].connected:
                        out_val = data.get(f"ctrl.{ctrl_tag}.OUT")
                        if out_val is not None:
                            block.inputs["MV_READBACK"].value = float(out_val)

    def write_outputs(self, graph: StrategyGraph):
        """Write AO/DO block values and PID state to the data store.

        Recurses into composite interiors so inner AO blocks drive their
        field tags and inner PID/AI state is published for faceplates.
        """
        for block in self._all_io_blocks(graph):
            if block.block_type == "AI":
                name = block.instance_name
                tag = block.config.params.get("tag", "")
                s = self._store.set
                s(f"io.{name}.type", "AI")
                s(f"io.{name}.tag", tag)
                s(f"io.{name}.value", block.get_output("OUT"))
                s(f"io.{name}.status", block.get_output("STATUS"))
                # Signal quality / limit as carried on the OUT terminal, so
                # the HMI can distinguish Uncertain (e.g. a limited channel)
                # from a flat-out Bad measurement.
                out_term = block.outputs.get("OUT")
                if out_term is not None:
                    s(f"io.{name}.quality", out_term.status.name)
                    s(f"io.{name}.limit", out_term.limit.name)
                s(f"io.{name}.mode",
                  block.get_output("MODE") if "MODE" in block.outputs else "AUTO")
                s(f"io.{name}.scale_lo", block.config.params.get("scale_lo", 0.0))
                s(f"io.{name}.scale_hi", block.config.params.get("scale_hi", 100.0))
                s(f"io.{name}.eng_units", block.config.params.get("eng_units", ""))

            elif block.block_type == "AO":
                tag = block.config.params.get("tag", "")
                # An OOS AO does not execute and must not drive the process:
                # skip the field write (telemetry below is still published so
                # the HMI shows the held value and the OOS mode).
                if tag and self._drives_to_field(block):
                    # Write OUT to the field device in AO engineering units.
                    # The engine clamps to its valid range (e.g. 0-1 for heater,
                    # 0-100 for TE valves).
                    val = block.get_output("OUT")
                    self._store.queue_write(tag, val)
                name = block.instance_name
                p = block.config.params
                s = self._store.set
                s(f"io.{name}.type", "AO")
                s(f"io.{name}.tag", tag)
                s(f"io.{name}.value", block.get_output("OUT"))
                s(f"io.{name}.out_lo", p.get("out_lo", 0.0))
                s(f"io.{name}.out_hi", p.get("out_hi", 1.0))
                # Publish actual mode from block (Azeo-style)
                ao_mode = block.get_output("MODE") if "MODE" in block.outputs else "AUTO"
                s(f"io.{name}.mode", ao_mode)
                if tag:
                    s(f"valve.{tag}.mode", ao_mode)
                # Publish AO state for faceplates
                s(f"io.{name}.PV", block.get_output("PV") if "PV" in block.outputs else 0.0)
                s(f"io.{name}.SP", block.get_output("SP") if "SP" in block.outputs else 0.0)
                s(f"io.{name}.BKCAL_OUT", block.get_output("BKCAL_OUT"))
                s(f"io.{name}.sp_hi_lim", p.get("sp_hi_lim", p.get("out_hi", 1.0)))
                s(f"io.{name}.sp_lo_lim", p.get("sp_lo_lim", p.get("out_lo", 0.0)))
                s(f"io.{name}.sp_rate_up", p.get("sp_rate_up", 0.0))
                s(f"io.{name}.sp_rate_dn", p.get("sp_rate_dn", 0.0))
                s(f"io.{name}.increase_to_close", p.get("increase_to_close", False))
                s(f"io.{name}.eng_units", p.get("eng_units", "%"))
                s(f"io.{name}.description", p.get("description", ""))

            elif block.block_type == "DI":
                name = block.instance_name
                tag = block.config.params.get("tag", "")
                s = self._store.set
                s(f"io.{name}.type", "DI")
                s(f"io.{name}.tag", tag)
                s(f"io.{name}.value", block.get_output("OUT"))
                s(f"io.{name}.status", block.get_output("STATUS"))
                s(f"io.{name}.mode",
                  block.get_output("MODE") if "MODE" in block.outputs else "AUTO")

            elif block.block_type == "DO":
                tag = block.config.params.get("tag", "")
                # The value the field actually sees.  OUT_D already carries
                # the selected setpoint *and* the Invert I/O option; the
                # legacy invert(IN) computation is the fallback for a DO
                # block that predates that output.
                raw = bool(block.get_input("IN"))
                if "OUT_D" in block.outputs:
                    field_val = bool(block.get_output("OUT_D"))
                else:
                    field_val = (not raw) if block.config.params.get(
                        "invert", False) else raw
                # An OOS DO holds its last commanded state and must not keep
                # driving the process (mirrors the AO gate).
                if tag and self._drives_to_field(block):
                    self._store.queue_write(tag, field_val)
                name = block.instance_name
                s = self._store.set
                s(f"io.{name}.type", "DO")
                s(f"io.{name}.tag", tag)
                # Publish what is written to the field, not the pre-invert
                # input — with invert set the two disagree and the HMI used
                # to show the opposite of the field.  The raw input is still
                # available as io.<name>.value_raw.
                s(f"io.{name}.value", field_val)
                s(f"io.{name}.value_raw", raw)
                s(f"io.{name}.mode",
                  block.get_output("MODE") if "MODE" in block.outputs else "CAS")

            elif block.block_type == "PID":
                # Publish PID state so dynamos and faceplates can read it
                name = block.instance_name
                prefix = f"ctrl.{name}."
                s = self._store.set

                # Build a helper to normalize PID output from [out_lo, out_hi]
                # to 0-100% for faceplate display.  The PID block may operate
                # in 0-1 (TE strategies) or 0-100 (heater strategies);
                # faceplates always expect 0-100%.
                p = block.config.params
                _out_lo = float(p.get("out_lo", 0.0))
                _out_hi = float(p.get("out_hi", 100.0))
                _out_span = _out_hi - _out_lo
                if abs(_out_span) > 1e-12 and abs(_out_span - 100.0) > 0.01:
                    def _to_pct(v, lo=_out_lo, span=_out_span):
                        # Clamp to [0, 100] so a stale / mis-initialized OUT
                        # never paints as e.g. 9940% on the dynamo.
                        pct = (v - lo) / span * 100.0
                        if pct < 0.0:
                            return 0.0
                        if pct > 100.0:
                            return 100.0
                        return pct
                    def _from_pct(v, lo=_out_lo, span=_out_span):
                        return lo + v / 100.0 * span
                else:
                    # Already 0-100% (or close enough)
                    def _to_pct(v):
                        if v < 0.0:
                            return 0.0
                        if v > 100.0:
                            return 100.0
                        return v
                    def _from_pct(v):
                        return v

                # Core values
                for field, terminal in PID_SIGNAL_EXPORTS.items():
                    value = block.get_output(terminal)
                    s(f"{prefix}{field}", _to_pct(value) if field == "OUT" else value)
                # Typed companions make a cross-module reference equivalent
                # to an in-module wire.  Without them a remote selector could
                # choose a Bad constraint and an external-reset master could
                # keep integrating through a downstream travel limit.
                for _terminal in ("PV", "SP_WRK", "OUT", "BKCAL_OUT"):
                    _signal = block.outputs.get(_terminal)
                    if _signal is None:
                        continue
                    s(f"{prefix}{_terminal}.quality", _signal.status.name)
                    s(f"{prefix}{_terminal}.limit", _signal.limit.name)
                s(f"{prefix}P_Term", block.get_output("P_TERM"))
                s(f"{prefix}I_Term", block.get_output("I_TERM"))
                s(f"{prefix}D_Term", block.get_output("D_TERM"))

                # Publish all params from the underlying PID core block
                if hasattr(block, '_pid_core') and block._pid_core is not None:
                    pid = block._pid_core

                    # Tuning (publish both Azeo and legacy names)
                    s(f"{prefix}GAIN", pid.gain)
                    s(f"{prefix}RESET", pid.reset)
                    s(f"{prefix}RATE", pid.rate)
                    s(f"{prefix}Kp", pid.gain)
                    s(f"{prefix}Ti", pid.reset)
                    s(f"{prefix}Td", pid.rate)
                    s(f"{prefix}Alpha", pid.alpha)
                    s(f"{prefix}Beta", pid.beta)
                    s(f"{prefix}Gamma", pid.gamma)
                    s(f"{prefix}bias", pid.bias)
                    s(f"{prefix}ideadband", pid.ideadband)

                    # Filtering & rate limiting
                    s(f"{prefix}pv_ftime", pid.pv_ftime)
                    s(f"{prefix}sp_ftime", pid.sp_ftime)
                    s(f"{prefix}sp_rate_up", pid.sp_rate_up)
                    s(f"{prefix}sp_rate_dn", pid.sp_rate_dn)
                    s(f"{prefix}ff_gain", pid.ff_gain)

                    # Manual output (0-100% for faceplate)
                    s(f"{prefix}ManOut",
                      _to_pct(getattr(pid, '_manual_output', 0.0)))

                    # Limits (OP limits in 0-100% for faceplate)
                    s(f"{prefix}SP_Min", pid.sp_lo_lim)
                    s(f"{prefix}SP_Max", pid.sp_hi_lim)
                    s(f"{prefix}OP_Min", _to_pct(pid.out_lo_lim))
                    s(f"{prefix}OP_Max", _to_pct(pid.out_hi_lim))
                    s(f"{prefix}arw_hi_lim", _to_pct(pid.arw_hi_lim))
                    s(f"{prefix}arw_lo_lim", _to_pct(pid.arw_lo_lim))

                    # Alarm limits
                    s(f"{prefix}hi_hi_lim", pid.hi_hi_lim)
                    s(f"{prefix}hi_lim", pid.hi_lim)
                    s(f"{prefix}lo_lim", pid.lo_lim)
                    s(f"{prefix}lo_lo_lim", pid.lo_lo_lim)
                    s(f"{prefix}dv_hi_lim", pid.dv_hi_lim)
                    s(f"{prefix}dv_lo_lim", pid.dv_lo_lim)
                    s(f"{prefix}alarm_hys", pid.alarm_hys)

                    # Structure / form
                    s(f"{prefix}form", pid.form.value)
                    s(f"{prefix}structure", pid.structure.value)
                    s(f"{prefix}direct_acting", pid.direct_acting)

                    # FRSIPID options
                    s(f"{prefix}use_pidplus", pid.use_pidplus)
                    s(f"{prefix}use_nonlinear_gain", pid.use_nonlinear_gain)
                    s(f"{prefix}dynamic_reset_limit", pid.dynamic_reset_limit)
                    s(f"{prefix}use_delayed_out_bad_pv", pid.use_delayed_out_bad_pv)
                    s(f"{prefix}recovery_fltr", pid.recovery_fltr)

                    # Nonlinear gain params
                    s(f"{prefix}nl_gap", pid.nl_gap)
                    s(f"{prefix}nl_hyst", pid.nl_hyst)
                    s(f"{prefix}nl_tband", pid.nl_tband)
                    s(f"{prefix}nl_minmod", pid.nl_minmod)

                    # Control options
                    s(f"{prefix}sp_pv_track_man", pid.sp_pv_track_man)
                    s(f"{prefix}sp_pv_track_lo_iman", pid.sp_pv_track_lo_iman)
                    s(f"{prefix}sp_pv_track_rout",
                      getattr(pid, 'sp_pv_track_rout', False))
                    s(f"{prefix}use_pv_for_bkcal_out", pid.use_pv_for_bkcal_out)
                    s(f"{prefix}no_out_limits_in_man", pid.no_out_limits_in_man)
                    s(f"{prefix}obey_sp_lim_cas_rcas", pid.obey_sp_lim_cas_rcas)
                    s(f"{prefix}track_enable", pid.track_enable)
                    s(f"{prefix}track_in_manual", pid.track_in_manual)
                    s(f"{prefix}bypass", pid.bypass)
                    s(f"{prefix}bypass_enable", pid.bypass_enable)
                    s(f"{prefix}ff_enable", pid.ff_enable)
                    s(f"{prefix}simulate_enabled", pid.simulate_enabled)

                    # ARW / limit status
                    out_sig = pid.OUT
                    if hasattr(out_sig, 'limit'):
                        s(f"{prefix}ARWStatus", out_sig.limit.name)

                    # Alarm state
                    alm = pid.alarm_state
                    s(f"{prefix}alarm_state", {
                        'hi_hi_act': alm.hi_hi_act,
                        'hi_act': alm.hi_act,
                        'lo_act': alm.lo_act,
                        'lo_lo_act': alm.lo_lo_act,
                        'dv_hi_act': alm.dv_hi_act,
                        'dv_lo_act': alm.dv_lo_act,
                    })

                    # Block errors
                    s(f"{prefix}block_err", {
                        'any_active': pid.block_err.any_active(),
                        'summary': pid.block_err.summary(),
                    })

                    # Integrator diagnostic
                    s(f"{prefix}_integral", pid._integral)

            # ── APC blocks → dmc.* store tags ──
            elif block.block_type == "APC_CONTROL":
                s = self._store.set
                s("dmc.enable", block.get_input("ENABLE"))
                s("dmc.active", block.get_output("ACTIVE"))
                s("dmc.heartbeat", block.get_output("HEARTBEAT"))
                s("dmc.status", block.get_output("STATUS"))
                s("dmc.cycle_time", block.get_output("CYCLE_TIME"))

            elif block.block_type == "WATCHDOG":
                s = self._store.set
                s("dmc.watchdog_ok", block.get_output("HEALTHY"))
                s("dmc.comm_fail", block.get_output("COMM_FAIL"))
                s("dmc.time_since_hb", block.get_output("TIME_SINCE_HB"))

            elif block.block_type == "SHED_LOGIC":
                s = self._store.set
                s("dmc.shed_status", block.get_output("SHED_LEVEL"))
                s("dmc.shed_count",
                  6 - block.get_output("MV_COUNT_OK"))
                # Also publish the master active signal
                s("dmc.active", block.get_output("ACTIVE"))

            # ── SP_HANDOFF: write TARGET_SP back to PID ──
            elif block.block_type == "SP_HANDOFF":
                ctrl_tag = block.config.params.get("controller_tag", "")
                active = bool(getattr(
                    block, "effective_active", block.get_input("ACTIVE")
                ))
                invalidate_target = bool(getattr(
                    block, "invalidate_target", False
                ))
                if ctrl_tag:
                    target_sp = block.get_output("TARGET_SP")
                    # An inactive handoff may track the downstream SP for a
                    # future bumpless engage, but it does not own that SP.
                    # Queueing while inactive turned an unwired/default
                    # enable into a real controller command at startup.
                    if active or invalidate_target:
                        target_parameter = str(
                            block.config.params.get("target_parameter", "SP")
                        ).strip().upper()
                        if target_parameter in {"SP", "CAS_IN", "RCAS_IN"}:
                            write_key = (
                                f"ctrl.{ctrl_tag}.wb.{target_parameter}"
                            )
                            target_terminal = block.outputs.get("TARGET_SP")
                            self._store.queue_write(write_key, target_sp)
                            self._store.queue_write(
                                f"{write_key}.quality",
                                (
                                    target_terminal.status.name
                                    if active else "BAD"
                                ),
                            )
                            self._store.queue_write(
                                f"{write_key}.limit",
                                target_terminal.limit.name,
                            )
                        else:
                            log.error(
                                "SP_HANDOFF %s has invalid target_parameter %r",
                                block.instance_name, target_parameter,
                            )
                    # Publish handoff status
                    name = block.instance_name
                    s = self._store.set
                    s(f"apc.{name}.controller", ctrl_tag)
                    s(f"apc.{name}.target_sp", target_sp)
                    s(f"apc.{name}.requested_active",
                      bool(block.get_input("ACTIVE")))
                    s(f"apc.{name}.active", active)
                    s(f"apc.{name}.tracking", block.get_output("TRACKING"))
                    s(f"apc.{name}.at_target", block.get_output("AT_TARGET"))

            # ── MV_CLAMP: publish clamp status ──
            elif block.block_type == "MV_CLAMP":
                ctrl_tag = block.config.params.get("controller_tag", "")
                name = block.instance_name
                s = self._store.set
                s(f"apc.{name}.controller", ctrl_tag)
                s(f"apc.{name}.mv_out", block.get_output("MV_OUT"))
                s(f"apc.{name}.at_hi", block.get_output("AT_HI"))
                s(f"apc.{name}.at_lo", block.get_output("AT_LO"))
                s(f"apc.{name}.clamped", block.get_output("CLAMPED"))
                s(f"apc.{name}.mv_ok", block.get_output("MV_OK"))
