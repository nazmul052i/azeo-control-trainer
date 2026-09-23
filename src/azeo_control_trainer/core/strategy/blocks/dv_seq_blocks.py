"""Azeo sequencing function blocks — SEQ, STD.

Two single function blocks from the Azeo Advanced Function Blocks
palette (see ``doc/AZEO_FUNCTION_BLOCKS.md``). Together they
approximate an SFC: the STD block associates *transitions* with states,
the SEQ block associates *states* with actions. The normal wiring is
``STD.STATE -> SEQ.STATE_IN`` with ``SEQ.STATE_IN_D = 1``.

These are NOT the chart elements in ``sfc_blocks.py`` (STEP /
TRANSITION / ...) — those build Sequential Function Charts; SEQ and STD
are self-contained state machines living in a single block.

Layout:
    SEQ — Sequencer (state -> discrete output pattern, up to 16x16)
    STD — State Transition Diagram (transition inputs -> next state)
"""
from __future__ import annotations

import json
from typing import Any

from ..model.block_base import (
    FunctionBlock, BlockCategory, BlockStatus, DataType,
)
from ..model.block_registry import register_block


_MAX_IO = 16          # Azeo hard limit: 16 states, 16 outputs, 16 transitions


def _clamp_count(value: Any, default: int, lo: int = 1) -> int:
    """Coerce an extensible-parameter count into 1..16."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(_MAX_IO, n))


def _sync_visible(terminals: dict, prefix: str, count: int) -> None:
    """Hide the ``prefix``N terminals above ``count``.

    The full 16-terminal set always exists (so strategy JSON written
    against a larger count still loads); the ones above the configured
    count are simply not drawn — this repo's stand-in for Azeo's
    Extensible Parameters dialog.
    """
    for i in range(1, _MAX_IO + 1):
        t = terminals.get(f"{prefix}{i}")
        if t is not None:
            t.hidden = i > count


# ═══════════════════════════════════════════════════════════════════════
#  SEQ — Sequencer
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SequencerBlock(FunctionBlock):
    """Sequencer (SEQ) — maps up to 16 states to discrete output patterns.

    Each scan the block drives ``OUT_D1..OUT_Dn`` from the ``MATRIX`` row
    configured for the current ``STATE``. No modes, no alarm detection;
    input status has no effect and outputs always carry Good status.

    State source
        ``STATE_IN_D = 1`` (default): ``STATE`` follows ``STATE_IN``
        (normally wired from an STD block's ``STATE``). ``STATE_IN_D = 0``:
        the block holds its state and ``INCREMENT`` / ``DECREMENT`` step
        it — saturating at 1 / ``NUM_STATES`` unless ``WRAP`` is set, in
        which case NUM_STATES->1 and 1->NUM_STATES. ``RESET_SEQ`` returns
        to state 1 (auto-clears).

    Enable
        ``ENABLE = False`` forces STATE = 0 (Disabled) and all outputs 0.
        On False->True with ``STATE_IN_D`` clear, STATE goes to 1.

    Override
        ``OUTPUT_MASK`` bit *k* = 1 prevents ``OUT_D(k+1)`` from going
        True regardless of the state's configured pattern (in Azeo this
        is typically written from a CALC block, e.g. per batch phase).

    MATRIX schema (``MATRIX`` config param, a JSON string)
        A list of one dict per configured state::

            [{"state": 1, "outputs": [1, 3]},
             {"state": 2, "mask": 6},
             {"state": 3, "outputs": []}]

        ``state``    1-based state number (1..NUM_STATES).
        ``outputs``  list of 1-based output numbers driven True, or
        ``mask``     equivalent uint16 bitstring, bit *k* -> ``OUT_D(k+1)``.

        A bare mapping of state -> outputs/mask is also accepted::

            {"1": [1, 3], "2": 6}

    An empty or malformed MATRIX leaves the block at STATE = 0 with all
    outputs 0 and ``status = BAD`` (Azeo's MATRIX default is None, i.e.
    unconfigured) — it never raises.

    Not modelled: Azeo named sets (``$ctlr_std_seq_states``) for the
    STATE display text; ``STATE`` is published as the plain state number
    and ``DESC_OUTn`` carries the per-output label.
    """
    block_type = "SEQ"
    category = BlockCategory.LOGIC
    display_name = "Sequencer (SEQ)"
    description = "State -> discrete output pattern matrix (up to 16 x 16)"

    # SEQ has no modes, no named-set configuration parameters (STATE is a
    # named-set *output*, not configuration) and no engineering units, so
    # it declares neither config_choices nor config_units.

    def __init__(self, instance_name: str = ""):
        self._state = 0
        self._prev_enable = False
        self._prev_inc = False
        self._prev_dec = False
        self._matrix: dict[int, int] | None = None   # state -> output bitmask
        self._matrix_src: Any = None                 # cache key for MATRIX text
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, True,
                       "False: STATE = 0 (Disabled) and all outputs 0")
        self.add_input("STATE_IN", DataType.INT, 1,
                       "Target state when STATE_IN_D = 1 (0..NUM_STATES)")
        self.add_input("STATE_IN_D", DataType.BOOL, True,
                       "When 1, STATE is set from STATE_IN")
        self.add_input("INCREMENT", DataType.BOOL, False,
                       "Rising edge steps to the next state (STATE_IN_D = 0)")
        self.add_input("DECREMENT", DataType.BOOL, False,
                       "Rising edge steps to the previous state (STATE_IN_D = 0)")
        self.add_input("RESET_SEQ", DataType.BOOL, False,
                       "True forces state 1 when STATE_IN_D = 0; auto-clears")
        self.add_input("OUTPUT_MASK", DataType.INT, 0,
                       "Bitstring: bit k = 1 blocks OUT_D(k+1) from going True")
        for i in range(1, _MAX_IO + 1):
            self.add_output(f"OUT_D{i}", DataType.BOOL, False,
                            description=f"Discrete output {i}")
        self.add_output("STATE", DataType.INT, 0,
                        "Current state (0 = Disabled)")

    def get_config_schema(self):
        schema: dict[str, tuple] = {
            "NUM_STATES": (int, 16, "Number of valid states, excluding state 0 (1..16)"),
            "NUM_OUTPUTS": (int, 2, "Number of discrete outputs OUT_Dn (1..16)"),
            "WRAP": (bool, False,
                     "Increment/decrement wraps past the first/last state"),
            "MATRIX": (str, "",
                       'JSON: [{"state": 1, "outputs": [1, 3]}, '
                       '{"state": 2, "mask": 6}] — see block docstring'),
        }
        # Keep every supported description addressable to the HMI.  The
        # NUM_OUTPUTS value still decides which terminals are visible and
        # executed; a changing extensible count must not make an already
        # published faceplate binding disappear.
        for i in range(1, _MAX_IO + 1):
            schema[f"DESC_OUT{i}"] = (str, "", f"Label for OUT_D{i}")
        return schema

    def _apply_config(self):
        _sync_visible(self.outputs, "OUT_D",
                      _clamp_count(self.config.params.get("NUM_OUTPUTS"), 2))

    def reset(self):
        super().reset()
        self._state = 0
        self._prev_enable = False
        self._prev_inc = False
        self._prev_dec = False
        self._matrix = None
        self._matrix_src = None

    # -- MATRIX parsing -------------------------------------------------
    def _parse_matrix(self, raw: Any) -> dict[int, int] | None:
        """Return {state: output bitmask}, or None when unconfigured/bad."""
        if raw == self._matrix_src:
            return self._matrix
        self._matrix_src = raw
        self._matrix = None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None

        rows: dict[int, int] = {}
        try:
            if isinstance(data, dict):
                items = [(k, v) for k, v in data.items()]
            elif isinstance(data, list):
                items = []
                for entry in data:
                    if not isinstance(entry, dict):
                        return None
                    items.append((entry.get("state"),
                                  entry.get("outputs", entry.get("mask", 0))))
            else:
                return None
            for state_key, spec in items:
                state = int(state_key)
                if not 1 <= state <= _MAX_IO:
                    return None
                if isinstance(spec, (list, tuple)):
                    mask = 0
                    for out_no in spec:
                        k = int(out_no)
                        if not 1 <= k <= _MAX_IO:
                            return None
                        mask |= 1 << (k - 1)
                else:
                    mask = int(spec)
                rows[state] = mask & 0xFFFF
        except (TypeError, ValueError):
            return None
        if not rows:
            return None
        self._matrix = rows
        return rows

    def execute(self, dt: float):
        p = self.config.params
        n_states = _clamp_count(p.get("NUM_STATES"), 16)
        n_out = _clamp_count(p.get("NUM_OUTPUTS"), 2)
        _sync_visible(self.outputs, "OUT_D", n_out)

        matrix = self._parse_matrix(p.get("MATRIX", ""))
        enable = bool(self.get_input("ENABLE"))

        # Edge memory is refreshed every scan (even when disabled) so a
        # level held True across a disable does not step on re-enable.
        inc = bool(self.get_input("INCREMENT"))
        dec = bool(self.get_input("DECREMENT"))
        rise_inc = inc and not self._prev_inc
        rise_dec = dec and not self._prev_dec
        self._prev_inc, self._prev_dec = inc, dec

        if matrix is None:
            # Unconfigured / malformed MATRIX — hold Disabled, flag BAD.
            self._state = 0
            self._prev_enable = enable
            self._drive_outputs(0, None, n_out)
            self.status = BlockStatus.BAD
            return

        state_in_d = bool(self.get_input("STATE_IN_D"))

        if not enable:
            self._state = 0
        else:
            if not self._prev_enable and not state_in_d:
                # False->True with STATE_IN_D clear: start at state 1.
                self._state = 1
            if state_in_d:
                try:
                    target = int(self.get_input("STATE_IN"))
                except (TypeError, ValueError):
                    target = 0
                self._state = max(0, min(n_states, target))
            else:
                if bool(self.get_input("RESET_SEQ")):
                    self._state = 1
                    self.inputs["RESET_SEQ"].value = False   # auto-clears
                self._step(rise_inc, rise_dec, n_states,
                           bool(p.get("WRAP", False)))
                if self._state < 1:
                    self._state = 1
        self._prev_enable = enable

        self._drive_outputs(self._state, matrix, n_out)
        self.status = BlockStatus.GOOD

    def _step(self, rise_inc: bool, rise_dec: bool, n_states: int, wrap: bool):
        """Apply INCREMENT / DECREMENT.

        The doc gives no auto-clear for INCREMENT / DECREMENT (unlike
        RESET_SEQ), so they are taken as momentary: a *rising edge* steps
        once, otherwise a held-True wire would run the whole sequence in
        as many scans.
        """
        if rise_inc == rise_dec:        # neither, or both in the same scan
            return
        if rise_inc:
            self._state = (1 if wrap else n_states) if self._state >= n_states \
                else self._state + 1
        else:
            self._state = (n_states if wrap else 1) if self._state <= 1 \
                else self._state - 1

    def _drive_outputs(self, state: int, matrix: dict[int, int] | None, n_out: int):
        mask_row = 0 if (state <= 0 or matrix is None) else matrix.get(state, 0)
        try:
            block_mask = int(self.get_input("OUTPUT_MASK"))
        except (TypeError, ValueError):
            block_mask = 0
        mask_row &= ~block_mask          # OUTPUT_MASK bits can only force False
        for i in range(1, _MAX_IO + 1):
            on = bool(mask_row & (1 << (i - 1))) if i <= n_out else False
            self.set_output(f"OUT_D{i}", on)
        self.set_output("STATE", int(state))


# ═══════════════════════════════════════════════════════════════════════
#  STD — State Transition Diagram
# ═══════════════════════════════════════════════════════════════════════

@register_block
class StateTransitionDiagramBlock(FunctionBlock):
    """State Transition Diagram (STD) — user-defined state machine.

    Up to 16 states (each with a discrete output ``OUT_Dn``) and up to 16
    transition inputs ``IN_Dn``. ``MATRIX`` holds, for each (current
    state, transition) pair, the **next-state number** to go to when that
    transition is active (0 = no transition configured). Each scan the
    block walks the transition inputs in order until it finds an active
    one with a non-zero matrix entry for the current state, sets STATE to
    that entry and stops — first match wins. No modes, no alarm
    detection; OUT_Dn always carries Good status.

    Enable / reset
        ``ENABLE = False`` sets STATE = 0 (Disabled) and all OUT_Dn 0; on
        False->True the block is forced to state 1 with ``OUT_D1`` True
        (this is how a parent STD gates child sub-state machines).
        ``RESET_STATE`` returns to state 1 and auto-clears.

    Overrides
        ``TRANSITION_MASK`` bit *k* = 1 makes ``IN_D(k+1)`` never seen as
        active. Forcing: ``STATE_IN_D = 1`` drives STATE from ``STATE_IN``.
        ``OVERRIDE`` reports (lowest->highest priority) ``NONE``,
        ``ALL_ASSOCIATED_TRANSITIONS_MASKED``, ``STATE_FORCED``.
        ``VALID_TRANS`` is the bitstring of transitions possible from the
        current state, excluding masked ones; ``TERMINAL`` is True when
        the current state has no non-zero matrix entries or masking
        prevents any transition.

    Status handling
        ``STATUS_OPT``: ``ALWAYS_USE_VALUE`` (default), ``IGNORE_IF_BAD``
        (a Bad input has no effect), ``USE_LAST_GOOD_VALUE`` (input value
        changes are ignored while status is Bad). This repo's terminals
        carry no per-signal status, so Bad status is supplied as the
        ``IN_STATUS_BAD`` bitstring input: bit *k* = 1 means ``IN_D(k+1)``
        is Bad.

    MATRIX schema (``MATRIX`` config param, a JSON string)
        A list of cell dicts, or one row dict per state::

            [{"state": 1, "trans": 1, "next": 2},
             {"state": 2, "trans": 2, "next": 3},
             {"state": 3, "next": [1, 0, 2]}]

        ``state``  1-based current-state number.
        ``trans``  1-based transition-input number, with ``next`` the
                   next-state number for that cell; or
        ``next``   a list giving the next-state number per transition
                   input (index 0 = IN_D1), 0 meaning "not configured".

        A bare mapping of state -> next-state list is also accepted::

            {"1": [2, 0, 0], "2": [0, 3, 0]}

    An empty or malformed MATRIX leaves the block at STATE = 0 with all
    outputs 0 and ``status = BAD`` (Azeo's MATRIX default is None) — it
    never raises.

    Not modelled: Azeo named sets (``$ctlr_std_seq_states``) for the
    STATE display text; ``STATE`` is published as the plain state number
    and ``DESC_INn`` carries the per-transition label.
    """
    block_type = "STD"
    category = BlockCategory.LOGIC
    display_name = "State Transition Diagram (STD)"
    description = "State machine: transition inputs -> next state, 16 x 16 matrix"

    _STATUS_OPTS = ("ALWAYS_USE_VALUE", "IGNORE_IF_BAD", "USE_LAST_GOOD_VALUE")

    # STD has no modes (doc: "No modes and no alarm detection"); STATUS_OPT
    # is the doc's "Always Use Value / Ignore if Bad / Use Last Good Value"
    # named set. No parameter carries an engineering unit.
    config_choices = {
        "STATUS_OPT": _STATUS_OPTS,
    }

    def __init__(self, instance_name: str = ""):
        self._state = 0
        self._prev_enable = False
        self._last_good = [False] * _MAX_IO
        self._matrix: dict[int, list[int]] | None = None   # state -> next per trans
        self._matrix_src: Any = None
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, True,
                       "False: STATE = 0 (Disabled); on True, forced to state 1")
        for i in range(1, _MAX_IO + 1):
            self.add_input(f"IN_D{i}", DataType.BOOL, False,
                           description=f"Transition input {i} (1 = active)")
        self.add_input("IN_STATUS_BAD", DataType.INT, 0,
                       "Bitstring: bit k = 1 means IN_D(k+1) has Bad status")
        self.add_input("TRANSITION_MASK", DataType.INT, 0,
                       "Bitstring: bit k = 1 hides IN_D(k+1) (never active)")
        self.add_input("STATE_IN", DataType.INT, 1,
                       "Target state when STATE_IN_D = 1 (0..NUM_STATES)")
        self.add_input("STATE_IN_D", DataType.BOOL, False,
                       "When 1, STATE is forced to STATE_IN")
        self.add_input("RESET_STATE", DataType.BOOL, False,
                       "True forces state 1; auto-clears")
        for i in range(1, _MAX_IO + 1):
            self.add_output(f"OUT_D{i}", DataType.BOOL, False,
                            description=f"State {i} active")
        self.add_output("STATE", DataType.INT, 0,
                        "Current state (0 = Disabled)")
        self.add_output("TERMINAL", DataType.BOOL, False,
                        "True when no transition can leave the current state")
        self.add_output("VALID_TRANS", DataType.INT, 0,
                        "Bitstring of unmasked transitions possible from STATE")
        self.add_output("OVERRIDE", DataType.STRING, "NONE",
                        "NONE | ALL_ASSOCIATED_TRANSITIONS_MASKED | STATE_FORCED")

    def get_config_schema(self):
        schema: dict[str, tuple] = {
            "NUM_STATES": (int, 2, "Number of states / OUT_Dn outputs (1..16)"),
            "NUM_TRANS": (int, 3, "Number of transition inputs IN_Dn (1..16)"),
            "STATUS_OPT": (str, "ALWAYS_USE_VALUE",
                           "ALWAYS_USE_VALUE | IGNORE_IF_BAD | USE_LAST_GOOD_VALUE"),
            "MATRIX": (str, "",
                       'JSON: [{"state": 1, "trans": 1, "next": 2}] or '
                       '[{"state": 1, "next": [2, 0, 0]}] — see block docstring'),
        }
        # As with SEQ, description parameters have stable addresses across
        # extensible-count edits.  NUM_TRANS remains the live row count.
        for i in range(1, _MAX_IO + 1):
            schema[f"DESC_IN{i}"] = (str, "", f"Label for IN_D{i}")
        return schema

    def _apply_config(self):
        p = self.config.params
        _sync_visible(self.inputs, "IN_D", _clamp_count(p.get("NUM_TRANS"), 3))
        _sync_visible(self.outputs, "OUT_D", _clamp_count(p.get("NUM_STATES"), 2))

    def reset(self):
        super().reset()
        self._state = 0
        self._prev_enable = False
        self._last_good = [False] * _MAX_IO
        self._matrix = None
        self._matrix_src = None

    # -- MATRIX parsing -------------------------------------------------
    def _parse_matrix(self, raw: Any) -> dict[int, list[int]] | None:
        """Return {state: [next per transition]}, or None when bad."""
        if raw == self._matrix_src:
            return self._matrix
        self._matrix_src = raw
        self._matrix = None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None

        rows: dict[int, list[int]] = {}

        def _cell(state: int, trans: int, nxt: int) -> bool:
            if not (1 <= state <= _MAX_IO and 1 <= trans <= _MAX_IO):
                return False
            if not 0 <= nxt <= _MAX_IO:
                return False
            rows.setdefault(state, [0] * _MAX_IO)[trans - 1] = nxt
            return True

        try:
            if isinstance(data, dict):
                entries = [{"state": k, "next": v} for k, v in data.items()]
            elif isinstance(data, list):
                entries = data
            else:
                return None
            for entry in entries:
                if not isinstance(entry, dict):
                    return None
                state = int(entry.get("state"))
                nxt = entry.get("next", 0)
                if isinstance(nxt, (list, tuple)):
                    for idx, val in enumerate(nxt):
                        if not _cell(state, idx + 1, int(val)):
                            return None
                else:
                    if not _cell(state, int(entry.get("trans", 0)), int(nxt)):
                        return None
        except (TypeError, ValueError):
            return None
        if not rows:
            return None
        self._matrix = rows
        return rows

    # -- transition input reading --------------------------------------
    def _read_inputs(self, n_trans: int, status_opt: str) -> list[bool]:
        try:
            bad_mask = int(self.get_input("IN_STATUS_BAD"))
        except (TypeError, ValueError):
            bad_mask = 0
        active: list[bool] = []
        for i in range(1, _MAX_IO + 1):
            raw = bool(self.get_input(f"IN_D{i}"))
            bad = bool(bad_mask & (1 << (i - 1)))
            if i > n_trans:
                active.append(False)
                continue
            if bad and status_opt == "IGNORE_IF_BAD":
                val = False
            elif bad and status_opt == "USE_LAST_GOOD_VALUE":
                val = self._last_good[i - 1]
            else:
                val = raw
            if not bad:
                self._last_good[i - 1] = raw
            active.append(val)
        return active

    def execute(self, dt: float):
        p = self.config.params
        n_states = _clamp_count(p.get("NUM_STATES"), 2)
        n_trans = _clamp_count(p.get("NUM_TRANS"), 3)
        _sync_visible(self.inputs, "IN_D", n_trans)
        _sync_visible(self.outputs, "OUT_D", n_states)

        matrix = self._parse_matrix(p.get("MATRIX", ""))
        enable = bool(self.get_input("ENABLE"))

        if matrix is None:
            self._state = 0
            self._prev_enable = enable
            self._publish(0, n_states, 0, False, "NONE")
            self.status = BlockStatus.BAD
            return

        status_opt = str(p.get("STATUS_OPT", "ALWAYS_USE_VALUE")).upper()
        if status_opt not in self._STATUS_OPTS:
            status_opt = "ALWAYS_USE_VALUE"
        active = self._read_inputs(n_trans, status_opt)
        try:
            trans_mask = int(self.get_input("TRANSITION_MASK"))
        except (TypeError, ValueError):
            trans_mask = 0

        override = "NONE"
        forced_init = enable and not self._prev_enable
        if not enable:
            self._state = 0
        else:
            if forced_init:
                # False->True forces the initial state (OUT_D1 True); no
                # transition is taken on that scan.
                self._state = 1
            if bool(self.get_input("RESET_STATE")):
                self._state = 1
                self.inputs["RESET_STATE"].value = False     # auto-clears
                # The doc says only "returns to state 1"; taken like the
                # ENABLE False->True force, no transition is evaluated on
                # the reset scan (else a stale active input would leave
                # state 1 immediately).
                forced_init = True
            if bool(self.get_input("STATE_IN_D")):
                try:
                    target = int(self.get_input("STATE_IN"))
                except (TypeError, ValueError):
                    target = 0
                self._state = max(0, min(n_states, target))
                override = "STATE_FORCED"          # highest priority
            elif not forced_init:
                override = self._evaluate(matrix, active, trans_mask,
                                          n_states, n_trans)
        self._prev_enable = enable

        row = matrix.get(self._state, [0] * _MAX_IO) if self._state > 0 else []
        valid = 0
        for t in range(1, n_trans + 1):
            if row and row[t - 1] and not (trans_mask & (1 << (t - 1))):
                valid |= 1 << (t - 1)
        terminal = self._state > 0 and valid == 0
        self._publish(self._state, n_states, valid, terminal, override)
        self.status = BlockStatus.GOOD

    def _evaluate(self, matrix, active, trans_mask, n_states, n_trans) -> str:
        """Take the first active, unmasked, configured transition.

        Returns the OVERRIDE text: ALL_ASSOCIATED_TRANSITIONS_MASKED when
        every transition that would have fired this scan is masked out
        (the doc's "every would-be-active transition is masked"), else
        NONE.
        """
        if self._state <= 0:
            return "NONE"
        row = matrix.get(self._state)
        if not row:
            return "NONE"
        masked_hit = False
        for t in range(1, n_trans + 1):
            nxt = row[t - 1]
            if not nxt or not active[t - 1]:
                continue
            if trans_mask & (1 << (t - 1)):
                masked_hit = True
                continue
            self._state = max(0, min(n_states, nxt))
            return "NONE"              # first match wins; stop this scan
        return "ALL_ASSOCIATED_TRANSITIONS_MASKED" if masked_hit else "NONE"

    def _publish(self, state: int, n_states: int, valid: int,
                 terminal: bool, override: str):
        for i in range(1, _MAX_IO + 1):
            self.set_output(f"OUT_D{i}", i <= n_states and i == state)
        self.set_output("STATE", int(state))
        self.set_output("VALID_TRANS", int(valid))
        self.set_output("TERMINAL", bool(terminal))
        self.set_output("OVERRIDE", override)
