"""SFC (Sequential Function Chart) blocks — IEC 61131-3 compliant.

Implements the core SFC elements for sequential control:
  - STEP          — A state in the sequence (tracks active/elapsed time)
  - INITIAL_STEP  — Starting step (auto-activates on go-online)
  - END_STEP      — Terminal step (signals sequence complete)
  - TRANSITION    — Condition gate between steps
  - ACTION        — Qualifier-based action (N/P/S/R/L/D)
  - PARALLEL_SPLIT  — Simultaneous divergence (activates all outputs)
  - PARALLEL_JOIN   — Simultaneous convergence (waits for all inputs)
  - SELECTOR_BRANCH — Alternative divergence (first true transition wins)

Execution model:
  Steps hold a token (ACTIVE=True). On each scan, the runtime evaluates
  transitions from active steps. When a transition fires, the upstream
  step deactivates and the downstream step activates (token moves).
  Actions execute while their parent step is active, subject to qualifier.
"""
from __future__ import annotations

from ..model.block_base import FunctionBlock, BlockCategory, BlockStatus, DataType
from ..model.block_registry import register_block


# ═══════════════════════════════════════════════════════════════════════
# STEP — basic sequence step
# ═══════════════════════════════════════════════════════════════════════

@register_block
class StepBlock(FunctionBlock):
    """SFC Step — a state in the sequential function chart.

    Becomes ACTIVE when its ACTIVATE input goes True (from upstream
    transition). Tracks elapsed time in the step. Deactivates when
    DEACTIVATE goes True (downstream transition fired).
    """
    block_type = "STEP"
    category = BlockCategory.SFC
    display_name = "Step"
    description = "SFC step — holds a state with elapsed timer"

    def __init__(self, instance_name=""):
        self._elapsed = 0.0
        self._was_active = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ACTIVATE", DataType.BOOL, False, "Activate step (from transition)")
        self.add_input("DEACTIVATE", DataType.BOOL, False, "Deactivate step (transition fired)")
        self.add_input("RESET", DataType.BOOL, False, "Reset step to inactive")
        self.add_output("ACTIVE", DataType.BOOL, False, "Step is currently active")
        self.add_output("T", DataType.FLOAT, 0.0, "Elapsed time in step [s]")

    def get_config_schema(self):
        return {
            "step_name": (str, "", "Step display name"),
            "max_time": (float, 0.0, "Max time in step [s] (0=unlimited)"),
            "timeout_action": (str, "NONE", "On timeout: NONE / ALARM / FAULT"),
        }

    def execute(self, dt: float):
        active = bool(self.get_output("ACTIVE"))

        # Reset
        if self.get_input("RESET"):
            active = False
            self._elapsed = 0.0

        # Activation (rising edge from transition)
        if self.get_input("ACTIVATE") and not active:
            active = True
            self._elapsed = 0.0

        # Deactivation (downstream transition fired)
        if self.get_input("DEACTIVATE") and active:
            active = False

        # Track time
        if active:
            self._elapsed += dt
            # Timeout check
            max_t = self.config.params.get("max_time", 0.0)
            if max_t > 0 and self._elapsed >= max_t:
                timeout_action = self.config.params.get("timeout_action", "NONE")
                if timeout_action == "ALARM":
                    self.status = BlockStatus.UNCERTAIN
                elif timeout_action == "FAULT":
                    self.status = BlockStatus.BAD
        else:
            if self.status != BlockStatus.GOOD:
                self.status = BlockStatus.GOOD

        self.set_output("ACTIVE", active)
        self.set_output("T", self._elapsed)

    def reset(self):
        super().reset()
        self._elapsed = 0.0


# ═══════════════════════════════════════════════════════════════════════
# INITIAL_STEP — auto-activates when strategy goes online
# ═══════════════════════════════════════════════════════════════════════

@register_block
class InitialStepBlock(FunctionBlock):
    """SFC Initial Step — the entry point of a sequence.

    Automatically becomes ACTIVE on the first scan (strategy go-online).
    Otherwise behaves like a normal step.
    """
    block_type = "INITIAL_STEP"
    category = BlockCategory.SFC
    display_name = "Initial Step"
    description = "SFC initial step — auto-activates on startup"

    def __init__(self, instance_name=""):
        self._elapsed = 0.0
        self._first_scan = True
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ACTIVATE", DataType.BOOL, False, "Re-activate (from loop-back)")
        self.add_input("DEACTIVATE", DataType.BOOL, False, "Deactivate (transition fired)")
        self.add_input("RESET", DataType.BOOL, False, "Reset to initial active state")
        self.add_output("ACTIVE", DataType.BOOL, False, "Step is currently active")
        self.add_output("T", DataType.FLOAT, 0.0, "Elapsed time in step [s]")

    def get_config_schema(self):
        return {
            "step_name": (str, "IDLE", "Step display name"),
        }

    def execute(self, dt: float):
        active = bool(self.get_output("ACTIVE"))

        # First scan — auto-activate
        if self._first_scan:
            active = True
            self._elapsed = 0.0
            self._first_scan = False

        # Reset returns to active (initial step restarts)
        if self.get_input("RESET"):
            active = True
            self._elapsed = 0.0

        # Activation from loop-back
        if self.get_input("ACTIVATE") and not active:
            active = True
            self._elapsed = 0.0

        # Deactivation
        if self.get_input("DEACTIVATE") and active:
            active = False

        if active:
            self._elapsed += dt

        self.set_output("ACTIVE", active)
        self.set_output("T", self._elapsed)

    def reset(self):
        super().reset()
        self._elapsed = 0.0
        self._first_scan = True


# ═══════════════════════════════════════════════════════════════════════
# END_STEP — terminal step (sequence complete)
# ═══════════════════════════════════════════════════════════════════════

@register_block
class EndStepBlock(FunctionBlock):
    """SFC End Step — marks sequence completion.

    When activated, sets DONE=True. Can optionally loop back by
    connecting RESTART to the initial step's ACTIVATE.
    """
    block_type = "END_STEP"
    category = BlockCategory.SFC
    display_name = "End Step"
    description = "SFC end step — signals sequence complete"

    def __init__(self, instance_name=""):
        self._elapsed = 0.0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ACTIVATE", DataType.BOOL, False, "Activate (from transition)")
        self.add_input("RESET", DataType.BOOL, False, "Reset")
        self.add_output("ACTIVE", DataType.BOOL, False, "End step is active")
        self.add_output("DONE", DataType.BOOL, False, "Sequence complete")
        self.add_output("RESTART", DataType.BOOL, False, "Pulse to restart sequence")
        self.add_output("T", DataType.FLOAT, 0.0, "Elapsed time [s]")

    def get_config_schema(self):
        return {
            "auto_restart": (bool, False, "Auto-restart sequence on completion"),
            "hold_time": (float, 0.0, "Hold in DONE before restart [s]"),
        }

    def execute(self, dt: float):
        active = bool(self.get_output("ACTIVE"))
        restart = False

        if self.get_input("RESET"):
            active = False
            self._elapsed = 0.0

        if self.get_input("ACTIVATE") and not active:
            active = True
            self._elapsed = 0.0

        if active:
            self._elapsed += dt
            hold = self.config.params.get("hold_time", 0.0)
            auto = self.config.params.get("auto_restart", False)
            if auto and self._elapsed >= hold:
                restart = True
                active = False
                self._elapsed = 0.0

        self.set_output("ACTIVE", active)
        self.set_output("DONE", active)
        self.set_output("RESTART", restart)
        self.set_output("T", self._elapsed)

    def reset(self):
        super().reset()
        self._elapsed = 0.0


# ═══════════════════════════════════════════════════════════════════════
# TRANSITION — condition gate between steps
# ═══════════════════════════════════════════════════════════════════════

@register_block
class TransitionBlock(FunctionBlock):
    """SFC Transition — evaluates a condition to move between steps.

    Fires (FIRE=True) when ENABLE is True and CONDITION is met.
    FIRE is a one-scan pulse — the runtime uses it to deactivate
    the upstream step and activate the downstream step.

    Condition modes:
      - BOOL_TRUE:    CONDITION input is truthy
      - GREATER_THAN: CONDITION > threshold
      - LESS_THAN:    CONDITION < threshold
      - TIMER:        CONDITION (elapsed time) >= threshold
    """
    block_type = "TRANSITION"
    category = BlockCategory.SFC
    display_name = "Transition"
    description = "SFC transition — condition gate between steps"

    def __init__(self, instance_name=""):
        self._prev_fire = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, False,
                       "Enable transition (from upstream step ACTIVE)")
        self.add_input("CONDITION", DataType.FLOAT, 0.0,
                       "Condition value to evaluate")
        self.add_output("FIRE", DataType.BOOL, False,
                        "Transition fires (one-scan pulse)")

    def get_config_schema(self):
        return {
            "condition_mode": (str, "BOOL_TRUE",
                               "BOOL_TRUE / GREATER_THAN / LESS_THAN / TIMER"),
            "threshold": (float, 1.0, "Threshold for comparison modes"),
        }

    def execute(self, dt: float):
        enabled = bool(self.get_input("ENABLE"))
        condition_val = self.get_input("CONDITION")
        mode = self.config.params.get("condition_mode", "BOOL_TRUE")
        threshold = self.config.params.get("threshold", 1.0)

        fire = False
        if enabled:
            if mode == "BOOL_TRUE":
                fire = bool(condition_val)
            elif mode == "GREATER_THAN":
                fire = float(condition_val) > float(threshold)
            elif mode == "LESS_THAN":
                fire = float(condition_val) < float(threshold)
            elif mode == "TIMER":
                fire = float(condition_val) >= float(threshold)
            else:
                fire = bool(condition_val)

        self.set_output("FIRE", fire)

    def reset(self):
        super().reset()
        self._prev_fire = False


# ═══════════════════════════════════════════════════════════════════════
# ACTION — qualifier-based action block
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ActionBlock(FunctionBlock):
    """SFC Action — executes an action while parent step is active.

    Action qualifiers (IEC 61131-3):
      N  — Non-stored (continuous while step active)
      P  — Pulse (one-scan on entry)
      S  — Set (latch on, stays until R)
      R  — Reset (clear a previously set action)
      L  — Time-limited (active for duration, then off)
      D  — Time-delayed (starts after delay)
    """
    block_type = "SFC_ACTION"
    category = BlockCategory.SFC
    display_name = "Action"
    description = "SFC action — qualifier-based output"

    def __init__(self, instance_name=""):
        self._elapsed = 0.0
        self._prev_activate = False
        self._latched = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("ACTIVATE", DataType.BOOL, False,
                       "Activate (from parent step ACTIVE)")
        self.add_input("IN", DataType.FLOAT, 0.0,
                       "Value to output when active")
        self.add_output("OUT", DataType.FLOAT, 0.0, "Action output value")
        self.add_output("Q", DataType.BOOL, False, "Action qualifier output")

    def get_config_schema(self):
        return {
            "qualifier": (str, "N", "Action qualifier: N/P/S/R/L/D"),
            "duration": (float, 5.0, "Duration for L (limit) / D (delay) [s]"),
            "action_value": (float, 1.0,
                             "Output value when active (if IN not wired)"),
        }

    def execute(self, dt: float):
        activate = bool(self.get_input("ACTIVATE"))
        qualifier = self.config.params.get("qualifier", "N").upper()
        duration = self.config.params.get("duration", 5.0)

        # Detect rising edge
        rising = activate and not self._prev_activate
        self._prev_activate = activate

        q = False  # qualifier output

        if qualifier == "N":
            # Non-stored: active while step active
            q = activate

        elif qualifier == "P":
            # Pulse: one scan on entry
            q = rising

        elif qualifier == "S":
            # Set: latch on
            if rising:
                self._latched = True
            q = self._latched

        elif qualifier == "R":
            # Reset: clear latch
            if activate:
                self._latched = False
            q = False

        elif qualifier == "L":
            # Time-limited
            if rising:
                self._elapsed = 0.0
            if activate and self._elapsed < duration:
                self._elapsed += dt
                q = True
            else:
                q = False

        elif qualifier == "D":
            # Time-delayed
            if activate:
                self._elapsed += dt
                q = self._elapsed >= duration
            else:
                self._elapsed = 0.0
                q = False

        # Output
        if q:
            in_val = self.get_input("IN")
            action_val = self.config.params.get("action_value", 1.0)
            # Use IN if wired (non-zero), otherwise action_value
            out = in_val if self.inputs["IN"].connected else action_val
            self.set_output("OUT", out)
        else:
            self.set_output("OUT", 0.0)
        self.set_output("Q", q)

    def reset(self):
        super().reset()
        self._elapsed = 0.0
        self._prev_activate = False
        self._latched = False


# ═══════════════════════════════════════════════════════════════════════
# PARALLEL_SPLIT — simultaneous divergence
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ParallelSplitBlock(FunctionBlock):
    """SFC Parallel Split — activates multiple parallel branches.

    When IN goes True, all outputs (OUT_1..OUT_4) go True simultaneously.
    """
    block_type = "PARALLEL_SPLIT"
    category = BlockCategory.SFC
    display_name = "Parallel Split"
    description = "SFC simultaneous divergence — activates all branches"

    def _define_terminals(self):
        self.add_input("IN", DataType.BOOL, False, "Input from transition FIRE")
        self.add_output("OUT_1", DataType.BOOL, False, "Branch 1 activate")
        self.add_output("OUT_2", DataType.BOOL, False, "Branch 2 activate")
        self.add_output("OUT_3", DataType.BOOL, False, "Branch 3 activate")
        self.add_output("OUT_4", DataType.BOOL, False, "Branch 4 activate")

    def execute(self, dt: float):
        active = bool(self.get_input("IN"))
        self.set_output("OUT_1", active)
        self.set_output("OUT_2", active)
        self.set_output("OUT_3", active)
        self.set_output("OUT_4", active)


# ═══════════════════════════════════════════════════════════════════════
# PARALLEL_JOIN — simultaneous convergence
# ═══════════════════════════════════════════════════════════════════════

@register_block
class ParallelJoinBlock(FunctionBlock):
    """SFC Parallel Join — waits for all branches to complete.

    OUT goes True only when all connected inputs (IN_1..IN_4) are True.
    """
    block_type = "PARALLEL_JOIN"
    category = BlockCategory.SFC
    display_name = "Parallel Join"
    description = "SFC simultaneous convergence — waits for all branches"

    def _define_terminals(self):
        self.add_input("IN_1", DataType.BOOL, True, "Branch 1 complete")
        self.add_input("IN_2", DataType.BOOL, True, "Branch 2 complete")
        self.add_input("IN_3", DataType.BOOL, True, "Branch 3 complete")
        self.add_input("IN_4", DataType.BOOL, True, "Branch 4 complete")
        self.add_output("OUT", DataType.BOOL, False, "All branches complete")

    def execute(self, dt: float):
        # Only check connected inputs (unconnected default to True)
        all_done = True
        for name in ("IN_1", "IN_2", "IN_3", "IN_4"):
            if self.inputs[name].connected:
                if not bool(self.get_input(name)):
                    all_done = False
                    break
        self.set_output("OUT", all_done)


# ═══════════════════════════════════════════════════════════════════════
# SELECTOR_BRANCH — alternative divergence
# ═══════════════════════════════════════════════════════════════════════

@register_block
class SelectorBranchBlock(FunctionBlock):
    """SFC Selector Branch — first true condition selects the path.

    Evaluates conditions in priority order (COND_1 first).
    Only the first matching output fires.
    """
    block_type = "SELECTOR_BRANCH"
    category = BlockCategory.SFC
    display_name = "Selector Branch"
    description = "SFC alternative divergence — first true path wins"

    def _define_terminals(self):
        self.add_input("ENABLE", DataType.BOOL, False, "Enable evaluation")
        self.add_input("COND_1", DataType.BOOL, False, "Condition 1 (highest priority)")
        self.add_input("COND_2", DataType.BOOL, False, "Condition 2")
        self.add_input("COND_3", DataType.BOOL, False, "Condition 3")
        self.add_output("OUT_1", DataType.BOOL, False, "Path 1 selected")
        self.add_output("OUT_2", DataType.BOOL, False, "Path 2 selected")
        self.add_output("OUT_3", DataType.BOOL, False, "Path 3 selected")

    def execute(self, dt: float):
        enabled = bool(self.get_input("ENABLE"))
        selected = 0
        if enabled:
            if self.get_input("COND_1"):
                selected = 1
            elif self.get_input("COND_2"):
                selected = 2
            elif self.get_input("COND_3"):
                selected = 3
        self.set_output("OUT_1", selected == 1)
        self.set_output("OUT_2", selected == 2)
        self.set_output("OUT_3", selected == 3)
