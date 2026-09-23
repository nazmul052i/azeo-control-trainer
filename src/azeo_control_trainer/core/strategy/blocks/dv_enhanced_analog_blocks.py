"""Azeo enhanced analog blocks: ECTLSL and ERAMP.

The algorithms in this module follow the Azeo function-block contract
Reference topics for Enhanced Control Selector and Enhanced Ramp.  Every
parameter needed while online is either a primitive config value or a normal
``Terminal``.  That keeps strategy JSON stable and makes values, quality, and
limit state visible to the online FBD debugger.

Azeo cascade substatuses do not fit the model's three-value ``Quality``
enum.  ECTLSL therefore publishes companion ``*_ST`` and ``*_LIM`` integer
terminals as well as stamping native terminal quality/limit metadata.  The
companions are hidden by default but remain available for wiring and online
inspection.

ERAMP external references are deliberately module-local.  A configured path
such as ``PID1/SP`` resolves against the graph attached by ``StrategyRuntime``;
the block never reaches around the controller boundary or hard-codes a plant
transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..model.block_base import (
    BlockCategory,
    BlockStatus,
    DataType,
    FunctionBlock,
)
from ..model.block_registry import register_block
from ..model.terminal import LimitStatus, Quality, Terminal
from .dv_analog2_blocks import (
    LIM_CONST,
    LIM_HIGH,
    LIM_LOW,
    LIM_NONE,
    ST_BAD,
    ST_GOOD,
    ST_INIT_REQ,
    ST_NOT_INVITED,
    ST_NOT_SELECTED,
    ST_UNCERTAIN,
    _StatusPinMixin,
)


_QUALITY_TO_STATUS = {
    Quality.GOOD: ST_GOOD,
    Quality.UNCERTAIN: ST_UNCERTAIN,
    Quality.BAD: ST_BAD,
}
_STATUS_TO_QUALITY = {
    ST_UNCERTAIN: Quality.UNCERTAIN,
    ST_BAD: Quality.BAD,
}
_LIMIT_TO_CODE = {
    LimitStatus.NOT_LIMITED: LIM_NONE,
    LimitStatus.LOW_LIMITED: LIM_LOW,
    LimitStatus.HIGH_LIMITED: LIM_HIGH,
    LimitStatus.CONSTANT: LIM_CONST,
}
_CODE_TO_LIMIT = {
    LIM_NONE: LimitStatus.NOT_LIMITED,
    LIM_LOW: LimitStatus.LOW_LIMITED,
    LIM_HIGH: LimitStatus.HIGH_LIMITED,
    LIM_CONST: LimitStatus.CONSTANT,
}

# ECTLSL's IMan OUT carries Good: Cascade, Initiate Acknowledge.  The shared
# selector codes stop at Initialization Request because CTLSL historically
# collapsed IA to plain Good; retain IA explicitly on this enhanced block.
ST_INIT_ACK = 6


def _quality_for(code: int) -> Quality:
    """Map a companion status code to its native terminal quality."""
    return _STATUS_TO_QUALITY.get(int(code), Quality.GOOD)


def _limit_for(code: int) -> LimitStatus:
    """Map a companion limit code to native terminal limit metadata."""
    return _CODE_TO_LIMIT.get(int(code), LimitStatus.NOT_LIMITED)


def _clamp(value: float, low: float, high: float) -> tuple[float, int]:
    if value < low:
        return low, LIM_LOW
    if value > high:
        return high, LIM_HIGH
    return value, LIM_NONE


@register_block
class EnhancedControlSelectorBlock(_StatusPinMixin, FunctionBlock):
    """Enhanced Control Selector (ECTLSL), with two through sixteen paths.

    ``OP_SELECTION=0`` runs the configured Low/Middle/High algorithm;
    ``1..16`` directly selects that SEL input.  Middle is valid only for the
    documented three-input configuration.  Direct and automatic selection
    have distinct non-selected BKCAL substatuses, as Azeo requires.
    """

    block_type = "ECTLSL"
    category = BlockCategory.CONTROL
    display_name = "Enhanced Control Selector (ECTLSL)"
    description = "2-16 input override selector with per-path back calculation"

    config_choices = {
        "MODE": ("AUTO", "MAN", "OOS"),
        "SEL_TYPE": ("LOW", "MIDDLE", "HIGH"),
    }
    config_units = {
        "OUT_SCALE_HI": "EU of OUT_SCALE",
        "OUT_SCALE_LO": "EU of OUT_SCALE",
        "OUT_HI_LIM": "EU of OUT_SCALE",
        "OUT_LO_LIM": "EU of OUT_SCALE",
        "OUT_MAN": "EU of OUT_SCALE",
    }

    def __init__(self, instance_name: str = ""):
        self._actual_mode = "AUTO"
        super().__init__(instance_name)

    def _define_terminals(self) -> None:
        for index in range(1, 17):
            self.add_input(f"SEL_{index}", description=f"Selector input {index}")
        self.add_input(
            "OP_SELECTION",
            DataType.INT,
            0,
            "0 = Auto selection; 1..16 directly selects SEL_X",
        )
        self.add_input(
            "BKCAL_IN",
            is_bkcal=True,
            description="Back-calculation from the downstream block",
        )

        for index in range(1, 17):
            self.add_input(
                f"SEL_{index}_ST",
                DataType.INT,
                ST_GOOD,
                f"Cascade status code for SEL_{index}",
            )
            self.add_input(
                f"SEL_{index}_LIM",
                DataType.INT,
                LIM_NONE,
                f"Limit status code for SEL_{index}",
            )
        self.add_input(
            "BKCAL_IN_ST",
            DataType.INT,
            ST_GOOD,
            "BKCAL status; 3 is Initialization Request",
        )
        self.add_input(
            "BKCAL_IN_LIM",
            DataType.INT,
            LIM_NONE,
            "BKCAL limit status",
        )

        self.add_output("OUT", description="Selected analog output")
        self.add_output("SELECTED", DataType.INT, 0, "Selected SEL input index")
        for index in range(1, 17):
            self.add_output(
                f"BKCAL_SEL{index}",
                is_bkcal=True,
                description=f"Back-calculation for SEL_{index}",
            )

        self.add_output("MODE_ACT", DataType.STRING, "AUTO", "Actual block mode")
        self.add_output("BLOCK_ERR", DataType.INT, 0, "Bit 0: block is Out of Service")
        self.add_output("BAD_ACTIVE", DataType.BOOL, False, "Masked error is active")
        self.add_output(
            "ABNORM_ACTIVE",
            DataType.BOOL,
            False,
            "Unmasked error is active",
        )

        self.add_output("OUT_ST", DataType.INT, ST_GOOD, "Cascade status of OUT")
        self.add_output("OUT_LIM", DataType.INT, LIM_NONE, "Limit status of OUT")
        self.add_output(
            "SELECTED_ST", DataType.INT, ST_GOOD, "Cascade status of SELECTED"
        )
        self.add_output(
            "SELECTED_LIM", DataType.INT, LIM_CONST, "SELECTED is constant"
        )
        for index in range(1, 17):
            self.add_output(
                f"BKCAL_SEL{index}_ST",
                DataType.INT,
                ST_GOOD,
                f"Cascade status of BKCAL_SEL{index}",
            )
            self.add_output(
                f"BKCAL_SEL{index}_LIM",
                DataType.INT,
                LIM_NONE,
                f"Limit status of BKCAL_SEL{index}",
            )

        # The reference starts with three selections.  The fixed terminal set
        # is intentional: changing NOF_TOTAL_SEL never changes serialized IDs
        # or the online debugger's terminal schema.
        for index in range(4, 17):
            self.inputs[f"SEL_{index}"].hidden = True
            self.outputs[f"BKCAL_SEL{index}"].hidden = True
        self._hide_status_pins()

    def _status_pin_names(self) -> tuple[list[str], list[str]]:
        inputs = ["BKCAL_IN_ST", "BKCAL_IN_LIM"]
        outputs = ["OUT_ST", "OUT_LIM", "SELECTED_ST", "SELECTED_LIM"]
        for index in range(1, 17):
            inputs.extend((f"SEL_{index}_ST", f"SEL_{index}_LIM"))
            outputs.extend(
                (f"BKCAL_SEL{index}_ST", f"BKCAL_SEL{index}_LIM")
            )
        return inputs, outputs

    def get_config_schema(self) -> dict[str, tuple[type, Any, str]]:
        return {
            "MODE": (str, "AUTO", "Target mode: AUTO, MAN, or OOS"),
            "SEL_TYPE": (str, "HIGH", "Automatic selection: LOW, MIDDLE, or HIGH"),
            "NOF_TOTAL_SEL": (int, 3, "Class-level number of generated selections (2-16)"),
            "NOF_USED_SEL": (int, 3, "Number of selections evaluated (2-NOF_TOTAL_SEL)"),
            "OP_SELECTION": (int, 0, "0 = Auto selection; 1..16 = direct selection"),
            "OUT_SCALE_HI": (float, 100.0, "OUT_SCALE EU100"),
            "OUT_SCALE_LO": (float, 0.0, "OUT_SCALE EU0"),
            "OUT_HI_LIM": (float, 100.0, "Configured maximum output"),
            "OUT_LO_LIM": (float, 0.0, "Configured minimum output"),
            "OUT_MAN": (float, 0.0, "Operator value in Manual"),
            "OPT_SHED_TO_MAN_ON_BAD": (
                bool,
                False,
                "STATUS_OPTS: Shed to MAN on a bad connected SEL input",
            ),
            "BAD_MASK_OOS": (bool, True, "BAD_MASK includes the OOS block error"),
            "SHOW_STATUS_PINS": (bool, False, "Expose cascade status/limit pins"),
        }

    def reset(self) -> None:
        super().reset()
        self._actual_mode = "AUTO"

    def _counts(self) -> tuple[int, int]:
        params = self.config.params
        total = max(2, min(16, int(params.get("NOF_TOTAL_SEL", 3))))
        used = max(2, min(total, int(params.get("NOF_USED_SEL", 3))))
        return total, used

    def _apply_config(self) -> None:
        total, _used = self._counts()
        show_status = bool(self.config.params.get("SHOW_STATUS_PINS", False))
        for index in range(1, 17):
            structural = index <= total
            for name, pool in (
                (f"SEL_{index}", self.inputs),
                (f"BKCAL_SEL{index}", self.outputs),
            ):
                terminal = pool[name]
                terminal.hidden = not structural and not terminal.connected
            for name, pool in (
                (f"SEL_{index}_ST", self.inputs),
                (f"SEL_{index}_LIM", self.inputs),
                (f"BKCAL_SEL{index}_ST", self.outputs),
                (f"BKCAL_SEL{index}_LIM", self.outputs),
            ):
                terminal = pool[name]
                terminal.hidden = (
                    (not structural or not show_status) and not terminal.connected
                )
        for name in (
            "BKCAL_IN_ST",
            "BKCAL_IN_LIM",
            "OUT_ST",
            "OUT_LIM",
            "SELECTED_ST",
            "SELECTED_LIM",
        ):
            pool = self.inputs if name in self.inputs else self.outputs
            terminal = pool[name]
            terminal.hidden = not show_status and not terminal.connected

    def _limits(self) -> tuple[float, float]:
        params = self.config.params
        scale_high = float(params.get("OUT_SCALE_HI", 100.0))
        scale_low = float(params.get("OUT_SCALE_LO", 0.0))
        if scale_high < scale_low:
            scale_low, scale_high = scale_high, scale_low
        span = scale_high - scale_low
        high = min(
            float(params.get("OUT_HI_LIM", scale_high)),
            scale_high + 0.1 * span,
        )
        low = max(
            float(params.get("OUT_LO_LIM", scale_low)),
            scale_low - 0.1 * span,
        )
        if low > high:
            low, high = high, low
        return low, high

    def _input_status_code(self, name: str) -> int:
        companion = self.inputs[f"{name}_ST"]
        if companion.connected or int(companion.value) != ST_GOOD:
            return int(companion.value)
        return _QUALITY_TO_STATUS.get(self.inputs[name].status, ST_GOOD)

    def _input_limit_code(self, name: str) -> int:
        companion = self.inputs[f"{name}_LIM"]
        if companion.connected or int(companion.value) != LIM_NONE:
            return int(companion.value)
        return _LIMIT_TO_CODE.get(self.inputs[name].limit, LIM_NONE)

    def _set_output_signal(self, name: str, value: Any, status: int, limit: int) -> None:
        self.set_output(name, value)
        self.set_output_status(name, _quality_for(status), _limit_for(limit))

    def _publish_errors(self, out_of_service: bool) -> None:
        error = 1 if out_of_service else 0
        masked = bool(self.config.params.get("BAD_MASK_OOS", True))
        self.set_output("BLOCK_ERR", error)
        self.set_output("BAD_ACTIVE", bool(error and masked))
        self.set_output("ABNORM_ACTIVE", bool(error and not masked))
        for name in ("BLOCK_ERR", "BAD_ACTIVE", "ABNORM_ACTIVE"):
            self.set_output_status(
                name,
                Quality.BAD if out_of_service else Quality.GOOD,
                LimitStatus.NOT_LIMITED,
            )

    def _publish(
        self,
        *,
        out: float,
        selected: int,
        out_status: int,
        out_limit: int,
        bk_values: list[float],
        bk_statuses: list[int],
        bk_limits: list[int],
        actual_mode: str,
    ) -> None:
        self._set_output_signal("OUT", out, out_status, out_limit)
        self._set_output_signal("SELECTED", selected, out_status, LIM_CONST)
        self.set_output("OUT_ST", out_status)
        self.set_output("OUT_LIM", out_limit)
        self.set_output("SELECTED_ST", out_status)
        self.set_output("SELECTED_LIM", LIM_CONST)
        companion_quality = _quality_for(out_status)
        for name in ("OUT_ST", "OUT_LIM", "SELECTED_ST", "SELECTED_LIM"):
            self.set_output_status(name, companion_quality, LimitStatus.NOT_LIMITED)
        for index in range(1, 17):
            self._set_output_signal(
                f"BKCAL_SEL{index}",
                bk_values[index - 1],
                bk_statuses[index - 1],
                bk_limits[index - 1],
            )
            self.set_output(f"BKCAL_SEL{index}_ST", bk_statuses[index - 1])
            self.set_output(f"BKCAL_SEL{index}_LIM", bk_limits[index - 1])
            for suffix in ("ST", "LIM"):
                self.set_output_status(
                    f"BKCAL_SEL{index}_{suffix}",
                    _quality_for(bk_statuses[index - 1]),
                    LimitStatus.NOT_LIMITED,
                )
        self.set_output("MODE_ACT", actual_mode)
        self.set_output_status(
            "MODE_ACT",
            Quality.BAD if actual_mode == "OOS" else Quality.GOOD,
            LimitStatus.NOT_LIMITED,
        )
        self._actual_mode = actual_mode

    def _manual(self, out: float) -> None:
        low, high = self._limits()
        out, _limit = _clamp(out, low, high)
        values = [out] * 16
        statuses = [ST_NOT_INVITED] * 16
        limits = [LIM_NONE] * 16
        self.status = BlockStatus.GOOD
        self._publish(
            out=out,
            selected=0,
            out_status=ST_GOOD,
            out_limit=LIM_CONST,
            bk_values=values,
            bk_statuses=statuses,
            bk_limits=limits,
            actual_mode="MAN",
        )
        self._publish_errors(False)

    def execute(self, dt: float) -> None:  # noqa: ARG002 - scan API contract
        params = self.config.params
        _total, used = self._counts()
        target_mode = str(params.get("MODE", "AUTO")).strip().upper()
        previous_out = float(self.get_output("OUT"))

        if target_mode == "OOS":
            values = [float(self.get_output(f"BKCAL_SEL{i}")) for i in range(1, 17)]
            self.status = BlockStatus.OOS
            self._publish(
                out=previous_out,
                selected=0,
                out_status=ST_BAD,
                out_limit=LIM_NONE,
                bk_values=values,
                bk_statuses=[ST_BAD] * 16,
                bk_limits=[LIM_NONE] * 16,
                actual_mode="OOS",
            )
            self._publish_errors(True)
            return

        bkcal_status = self._input_status_code("BKCAL_IN")
        bkcal_limit = self._input_limit_code("BKCAL_IN")
        bkcal_value = float(self.get_input("BKCAL_IN"))
        if bkcal_status == ST_INIT_REQ:
            low, high = self._limits()
            out, limit = _clamp(bkcal_value, low, high)
            self.config.params["OUT_MAN"] = out
            self.status = BlockStatus.GOOD
            self._publish(
                out=out,
                selected=0,
                out_status=ST_INIT_ACK,
                out_limit=limit if limit != LIM_NONE else bkcal_limit,
                bk_values=[bkcal_value] * 16,
                bk_statuses=[bkcal_status] * 16,
                bk_limits=[bkcal_limit] * 16,
                actual_mode="IMAN",
            )
            self._publish_errors(False)
            return

        participating = list(range(1, used + 1))
        connected = [i for i in participating if self.inputs[f"SEL_{i}"].connected]
        shed_candidates = connected or participating
        bad_input = any(
            _quality_for(self._input_status_code(f"SEL_{i}")) is Quality.BAD
            for i in shed_candidates
        )
        if (
            target_mode == "AUTO"
            and bool(params.get("OPT_SHED_TO_MAN_ON_BAD", False))
            and bad_input
        ):
            params["OUT_MAN"] = previous_out
            self._manual(previous_out)
            return
        if target_mode == "MAN":
            self._manual(float(params.get("OUT_MAN", previous_out)))
            return

        op_input = self.inputs["OP_SELECTION"]
        op_selection = int(
            op_input.value
            if op_input.connected
            else params.get("OP_SELECTION", 0)
        )
        automatic = op_selection == 0
        values = {index: float(self.get_input(f"SEL_{index}")) for index in participating}

        if automatic:
            selector_type = str(params.get("SEL_TYPE", "HIGH")).strip().upper()
            order = sorted(participating, key=lambda index: (values[index], index))
            if selector_type == "LOW":
                selected = order[0]
            elif selector_type == "MIDDLE" and used == 3:
                selected = order[1]
            elif selector_type == "HIGH":
                selected = order[-1]
            else:
                # The named-set editor prevents this configuration.  Holding
                # Bad is safer than silently interpreting an invalid Middle.
                self.status = BlockStatus.BAD
                self._publish(
                    out=previous_out,
                    selected=0,
                    out_status=ST_BAD,
                    out_limit=LIM_NONE,
                    bk_values=[previous_out] * 16,
                    bk_statuses=[ST_NOT_INVITED] * 16,
                    bk_limits=[LIM_NONE] * 16,
                    actual_mode="AUTO",
                )
                self._publish_errors(False)
                return
        elif 1 <= op_selection <= used:
            selector_type = str(params.get("SEL_TYPE", "HIGH")).strip().upper()
            selected = op_selection
            order = sorted(participating, key=lambda index: (values[index], index))
        else:
            self.status = BlockStatus.BAD
            self._publish(
                out=previous_out,
                selected=0,
                out_status=ST_BAD,
                out_limit=LIM_NONE,
                bk_values=[previous_out] * 16,
                bk_statuses=[ST_NOT_INVITED] * 16,
                bk_limits=[LIM_NONE] * 16,
                actual_mode="AUTO",
            )
            self._publish_errors(False)
            return

        source_value = values[selected]
        low, high = self._limits()
        out, clamp_limit = _clamp(source_value, low, high)
        selected_limit = self._input_limit_code(f"SEL_{selected}")
        out_limit = clamp_limit if clamp_limit != LIM_NONE else selected_limit

        # With a limited OUT the upstream block receives the selected input's
        # unclamped value.  Otherwise a downstream limit/value takes priority.
        selected_bkcal_value = source_value if out_limit != LIM_NONE else out
        selected_bkcal_limit = out_limit
        if out_limit == LIM_NONE and bkcal_limit != LIM_NONE:
            selected_bkcal_value = bkcal_value
            selected_bkcal_limit = bkcal_limit

        bk_values = [selected_bkcal_value] * 16
        bk_statuses = [ST_NOT_INVITED] * 16
        bk_limits = [LIM_NONE] * 16
        bk_statuses[selected - 1] = ST_GOOD
        bk_limits[selected - 1] = selected_bkcal_limit

        for index in participating:
            if index == selected:
                continue
            if not automatic:
                bk_statuses[index - 1] = ST_NOT_INVITED
                continue
            bk_statuses[index - 1] = ST_NOT_SELECTED
            if selector_type == "LOW":
                bk_limits[index - 1] = LIM_HIGH
            elif selector_type == "HIGH":
                bk_limits[index - 1] = LIM_LOW
            elif values[index] < values[selected]:
                bk_limits[index - 1] = LIM_LOW
            elif values[index] > values[selected]:
                bk_limits[index - 1] = LIM_HIGH

        self.status = BlockStatus.GOOD
        self._publish(
            out=out,
            selected=selected,
            out_status=ST_GOOD,
            out_limit=out_limit,
            bk_values=bk_values,
            bk_statuses=bk_statuses,
            bk_limits=bk_limits,
            actual_mode="AUTO",
        )
        self._publish_errors(False)


@dataclass(frozen=True)
class _ModuleReference:
    owner: FunctionBlock
    kind: str
    key: str
    terminal: Terminal | None = None


@register_block
class EnhancedRampBlock(FunctionBlock):
    """Enhanced Ramp (ERAMP) for a parameter in another block in this module."""

    block_type = "ERAMP"
    category = BlockCategory.CONTROL
    display_name = "Enhanced Ramp (ERAMP)"
    description = "Rate/time ramp for mode-specific same-module parameters"

    _MODES = ("AUTO", "LO", "MAN", "RCAS", "ROUT")
    _STATES = ("STOPPED", "ENABLED", "PAUSED", "RAMPING", "COMPLETE")
    config_choices = {
        "ERAMP_IN_MODE": _MODES,
        "ERAMP_TYPE": ("USE_RATE", "USE_TIME"),
        "ERAMP_DIR": ("UP", "DOWN", "BOTH"),
        "TIME_UNIT": ("SECONDS", "MINUTES", "HOURS", "DAYS"),
    }
    config_units = {
        "ERAMP_RATE": "EU of IN per TIME_UNIT",
        "ERAMP_TIME": "TIME_UNIT",
        "LO_LIM": "EU of IN",
        "HI_LIM": "EU of IN",
        "VAR_THRESHOLD": "EU of IN",
    }

    def __init__(self, instance_name: str = ""):
        self._state = "STOPPED"
        self._out = 0.0
        self._remaining_s = 0.0
        self._rate_per_s = 0.0
        self._direction = 0.0
        self._end_at_start = 0.0
        self._complete = False
        self._active = False
        self._paused_by_mode = False
        self._prev_enable = False
        self._last_signature: tuple[Any, ...] | None = None
        super().__init__(instance_name)

    def _define_terminals(self) -> None:
        self.add_input("IN", description="Ramp starting point")
        self.add_input("ERAMP_END_VALUE", description="Ramp endpoint")
        self.add_input(
            "ERAMP_ENABLE", DataType.BOOL, False, "False-to-True enables a ramp"
        )
        self.add_input("TRK_IN_D", DataType.BOOL, False, "Track IN while inactive")
        self.add_input("PAUSE", DataType.BOOL, False, "Pause active ramp")
        self.add_input(
            "RESUME",
            DataType.BOOL,
            False,
            "Resume a ramp paused after target-mode change",
        )

        self.add_output("OUT", description="Current ramp output")
        self.add_output("COMPLETE", DataType.BOOL, False, "Ramp is complete")
        self.add_output("ERAMP_ACTIVE", DataType.BOOL, False, "Ramp calculation active")
        self.add_output(
            "ERAMP_STATE",
            DataType.STRING,
            "STOPPED",
            "STOPPED, ENABLED, PAUSED, RAMPING, or COMPLETE",
        )
        self.add_output("TIME_REMAIN", description="Time remaining in TIME_UNIT")
        self.add_output("REF_RAMP_VAL", description="Current target reference value")
        self.add_output(
            "REF_RAMP_PATH",
            DataType.STRING,
            "",
            "Current mode-specific target path",
        )

    def get_config_schema(self) -> dict[str, tuple[type, Any, str]]:
        return {
            "ERAMP_IN_MODE": (str, "AUTO", "Target mode in which ramping is allowed"),
            "ERAMP_TYPE": (str, "USE_RATE", "Calculate with ERAMP_RATE or ERAMP_TIME"),
            "ERAMP_DIR": (str, "BOTH", "UP, DOWN, or BOTH"),
            "TIME_UNIT": (str, "SECONDS", "SECONDS, MINUTES, HOURS, or DAYS"),
            "ERAMP_RATE": (float, 1.0, "Positive ramp rate per TIME_UNIT"),
            "ERAMP_TIME": (float, 1.0, "Positive ramp duration in TIME_UNIT"),
            "LO_LIM": (float, -3.40282e38, "Low value limit"),
            "HI_LIM": (float, 3.40282e38, "High value limit"),
            "VAR_THRESHOLD": (float, 0.0, "Variable endpoint equality threshold"),
            "REF_MODE": (str, "", "Required external reference to target MODE"),
            "REF_RAMP_AUTO": (str, "", "Parameter ramped in AUTO"),
            "REF_RAMP_LO": (str, "#IGNORE", "Parameter ramped in LO"),
            "REF_RAMP_MAN": (str, "#IGNORE", "Parameter ramped in MAN"),
            "REF_RAMP_RCAS": (str, "#IGNORE", "Parameter ramped in RCAS"),
            "REF_RAMP_ROUT": (str, "#IGNORE", "Parameter ramped in ROUT"),
            "OPT_AUTOTRACK": (bool, True, "ERAMP_OPTS: Autotracking"),
            "OPT_OBEY_LIMITS": (bool, False, "ERAMP_OPTS: Obey the limits"),
            "OPT_PAUSE_IF_MODE_CHANGES": (
                bool,
                False,
                "ERAMP_OPTS: Pause if target mode changes",
            ),
            "OPT_RESET_IF_IN_CHANGES": (
                bool,
                False,
                "ERAMP_OPTS: Recalculate when IN changes",
            ),
            "OPT_USE_REF_AS_IN": (
                bool,
                True,
                "ERAMP_OPTS: Use REF_RAMP_VAL when IN is not wired",
            ),
            "OPT_VARIABLE_ENDPOINT": (
                bool,
                False,
                "ERAMP_OPTS: Continue reading a moving endpoint",
            ),
        }

    def reset(self) -> None:
        super().reset()
        self._state = "STOPPED"
        self._out = 0.0
        self._remaining_s = 0.0
        self._rate_per_s = 0.0
        self._direction = 0.0
        self._end_at_start = 0.0
        self._complete = False
        self._active = False
        self._paused_by_mode = False
        self._prev_enable = False
        self._last_signature = None

    @staticmethod
    def _normal_mode(value: Any) -> str:
        if hasattr(value, "name"):
            value = value.name
        elif hasattr(value, "value") and isinstance(value.value, str):
            value = value.value
        return str(value).strip().upper().replace(" ", "")

    def _reference(self, path: str, *, prefer_output: bool) -> _ModuleReference | None:
        graph = getattr(self, "_module_graph", None)
        clean = str(path or "").strip().strip("/")
        if graph is None or not clean or clean.upper() == "#IGNORE":
            return None
        parts = [part for part in clean.split("/") if part]
        if len(parts) < 2:
            return None
        block_name, parameter = parts[-2], parts[-1]
        owner = next(
            (
                block
                for block in graph.blocks.values()
                if block.instance_name.casefold() == block_name.casefold()
            ),
            None,
        )
        if owner is None:
            return None

        def terminal_in(pool: dict[str, Terminal]) -> tuple[str, Terminal] | None:
            return next(
                (
                    (key, terminal)
                    for key, terminal in pool.items()
                    if key.casefold() == parameter.casefold()
                ),
                None,
            )

        pools = (
            (("output", owner.outputs), ("input", owner.inputs))
            if prefer_output
            else (("input", owner.inputs), ("output", owner.outputs))
        )
        for kind, pool in pools:
            found = terminal_in(pool)
            if found is not None:
                key, terminal = found
                return _ModuleReference(owner, kind, key, terminal)
        config_key = next(
            (
                key
                for key in owner.config.params
                if key.casefold() == parameter.casefold()
            ),
            None,
        )
        if config_key is not None:
            return _ModuleReference(owner, "config", config_key)
        # A freshly constructed block may rely on its schema default without
        # materialising that key in config.params yet.  Azeo external
        # references still resolve such a parameter, so initialise only the
        # referenced default instead of requiring a save/reload first.
        schema = owner.get_config_schema()
        schema_key = next(
            (key for key in schema if key.casefold() == parameter.casefold()),
            None,
        )
        if schema_key is not None:
            owner.config.params[schema_key] = schema[schema_key][1]
            return _ModuleReference(owner, "config", schema_key)
        return None

    @staticmethod
    def _read_reference(reference: _ModuleReference | None) -> tuple[Any, Quality]:
        if reference is None:
            return 0.0, Quality.BAD
        if reference.terminal is not None:
            return reference.terminal.value, reference.terminal.status
        return reference.owner.config.params[reference.key], Quality.GOOD

    @staticmethod
    def _write_reference(reference: _ModuleReference | None, value: float) -> bool:
        if reference is None:
            return False
        if reference.terminal is not None:
            reference.terminal.value = value
        else:
            reference.owner.config.params[reference.key] = value
        return True

    def _mode_and_ramp_reference(
        self,
    ) -> tuple[str, str, _ModuleReference | None, Quality, bool]:
        params = self.config.params
        desired = self._normal_mode(params.get("ERAMP_IN_MODE", "AUTO"))
        path = str(params.get(f"REF_RAMP_{desired}", ""))
        mode_ref = self._reference(str(params.get("REF_MODE", "")), prefer_output=True)
        target_mode, _mode_quality = self._read_reference(mode_ref)
        ramp_ref = self._reference(path, prefer_output=False)
        _value, ramp_quality = self._read_reference(ramp_ref)
        return (
            self._normal_mode(target_mode),
            path,
            ramp_ref,
            ramp_quality,
            mode_ref is not None,
        )

    def _options(self) -> dict[str, bool]:
        params = self.config.params
        variable = bool(params.get("OPT_VARIABLE_ENDPOINT", False))
        return {
            "variable": variable,
            "autotrack": True if variable else bool(params.get("OPT_AUTOTRACK", True)),
            "obey": bool(params.get("OPT_OBEY_LIMITS", False)),
            "pause_mode": bool(params.get("OPT_PAUSE_IF_MODE_CHANGES", False)),
            "reset_in": False
            if variable
            else bool(params.get("OPT_RESET_IF_IN_CHANGES", False)),
            "use_ref": True if variable else bool(params.get("OPT_USE_REF_AS_IN", True)),
        }

    def _source_value(self, ref_value: float, options: dict[str, bool]) -> float:
        if options["use_ref"] and not self.inputs["IN"].connected:
            return ref_value
        return float(self.get_input("IN"))

    def _limits(self) -> tuple[float, float]:
        low = float(self.config.params.get("LO_LIM", -3.40282e38))
        high = float(self.config.params.get("HI_LIM", 3.40282e38))
        return (low, high) if low <= high else (high, low)

    def _bound(self, value: float, options: dict[str, bool]) -> tuple[float, bool]:
        if not options["obey"]:
            return value, False
        low, high = self._limits()
        bounded = min(high, max(low, value))
        return bounded, bounded != value

    def _time_factor(self) -> float:
        return {
            "SECONDS": 1.0,
            "MINUTES": 60.0,
            "HOURS": 3600.0,
            "DAYS": 86400.0,
        }.get(str(self.config.params.get("TIME_UNIT", "SECONDS")).upper(), 1.0)

    def _signature(
        self,
        source: float,
        endpoint: float,
        options: dict[str, bool],
    ) -> tuple[Any, ...]:
        params = self.config.params
        signature: list[Any] = [
            str(params.get("ERAMP_TYPE", "USE_RATE")).upper(),
            float(params.get("ERAMP_RATE", 1.0)),
            float(params.get("ERAMP_TIME", 1.0)),
        ]
        if not options["variable"]:
            signature.append(endpoint)
        if options["reset_in"] and not options["use_ref"]:
            signature.append(source)
        return tuple(signature)

    def _calculate_ramp(self, start: float, endpoint: float) -> bool:
        """Configure rate/duration; return False for an invalid configuration."""
        params = self.config.params
        distance = abs(endpoint - start)
        self._direction = 0.0 if distance == 0.0 else (1.0 if endpoint > start else -1.0)
        direction = str(params.get("ERAMP_DIR", "BOTH")).strip().upper()
        if (direction == "UP" and self._direction < 0) or (
            direction == "DOWN" and self._direction > 0
        ):
            return False
        factor = self._time_factor()
        ramp_type = str(params.get("ERAMP_TYPE", "USE_RATE")).strip().upper()
        if distance == 0.0:
            self._rate_per_s = 0.0
            self._remaining_s = 0.0
            return True
        if ramp_type == "USE_TIME":
            duration = float(params.get("ERAMP_TIME", 1.0)) * factor
            if duration <= 0.0:
                return False
            self._remaining_s = duration
            self._rate_per_s = distance / duration
            return True
        rate = float(params.get("ERAMP_RATE", 1.0))
        if rate <= 0.0:
            return False
        self._rate_per_s = rate / factor
        self._remaining_s = distance / self._rate_per_s
        return True

    def _complete_ramp(
        self,
        endpoint: float,
        *,
        variable: bool,
        reference: _ModuleReference | None,
    ) -> None:
        if not variable:
            self._out = endpoint
        self._write_reference(reference, self._out)
        self._state = "COMPLETE"
        self._complete = True
        self._active = False
        self._remaining_s = 0.0
        self._paused_by_mode = False
        if not self.inputs["PAUSE"].connected:
            self.inputs["PAUSE"].value = False
        if not self.inputs["ERAMP_ENABLE"].connected:
            self.inputs["ERAMP_ENABLE"].value = False

    def _disable(self, source: float, ref_value: float, options: dict[str, bool]) -> None:
        self._state = "STOPPED"
        self._complete = False
        self._active = False
        self._remaining_s = 0.0
        self._paused_by_mode = False
        if options["autotrack"] or bool(self.get_input("TRK_IN_D")):
            self._out = source
        elif options["use_ref"]:
            self._out = ref_value
        self._out, _clamped = self._bound(self._out, options)

    def _begin(
        self,
        source: float,
        endpoint: float,
        ref_value: float,
        options: dict[str, bool],
        reference: _ModuleReference | None,
    ) -> bool:
        if options["autotrack"] or bool(self.get_input("TRK_IN_D")):
            start = source
        elif options["use_ref"]:
            start = ref_value
        else:
            start = self._out
        start, start_clamped = self._bound(start, options)
        endpoint, end_clamped = self._bound(endpoint, options)
        self._out = start
        self._end_at_start = endpoint
        self._last_signature = self._signature(source, endpoint, options)
        if options["obey"] and (start_clamped or end_clamped):
            # The product contract ends a ramp when an input/endpoint/output is
            # clamped.  Publishing the bounded endpoint makes the result
            # deterministic while never writing outside configured limits.
            self._out = endpoint
            self._complete_ramp(
                endpoint,
                variable=options["variable"],
                reference=reference,
            )
            return False
        if not self._calculate_ramp(start, endpoint):
            self._state = "STOPPED"
            self._complete = False
            self._active = False
            self._remaining_s = 0.0
            return False
        if self._remaining_s == 0.0:
            self._complete_ramp(
                endpoint,
                variable=options["variable"],
                reference=reference,
            )
            return False
        self._state = "RAMPING"
        self._complete = False
        self._active = True
        self._paused_by_mode = False
        return True

    def _output_quality(self, ramp_ref_quality: Quality) -> Quality:
        qualities = [
            self.inputs[name].status
            for name in (
                "ERAMP_ENABLE",
                "ERAMP_END_VALUE",
                "IN",
                "PAUSE",
                "TRK_IN_D",
            )
        ]
        qualities.append(ramp_ref_quality)
        return max(qualities, key=lambda quality: quality.value)

    def _publish(
        self,
        path: str,
        ref_value: float,
        quality: Quality,
    ) -> None:
        self.set_output("OUT", self._out)
        self.set_output("COMPLETE", self._complete)
        self.set_output("ERAMP_ACTIVE", self._active)
        self.set_output("ERAMP_STATE", self._state)
        self.set_output("TIME_REMAIN", self._remaining_s / self._time_factor())
        self.set_output("REF_RAMP_VAL", ref_value)
        self.set_output("REF_RAMP_PATH", path)
        for name in self.outputs:
            self.set_output_status(name, quality, LimitStatus.NOT_LIMITED)
        self.status = {
            Quality.GOOD: BlockStatus.GOOD,
            Quality.UNCERTAIN: BlockStatus.UNCERTAIN,
            Quality.BAD: BlockStatus.BAD,
        }[quality]

    def execute(self, dt: float) -> None:
        params = self.config.params
        desired_mode = self._normal_mode(params.get("ERAMP_IN_MODE", "AUTO"))
        (
            target_mode,
            path,
            reference,
            reference_quality,
            mode_reference_valid,
        ) = self._mode_and_ramp_reference()
        ref_raw, _ = self._read_reference(reference)
        try:
            ref_value = float(ref_raw)
        except (TypeError, ValueError):
            ref_value = 0.0
            reference_quality = Quality.BAD
        options = self._options()
        source = self._source_value(ref_value, options)
        endpoint = float(self.get_input("ERAMP_END_VALUE"))
        enable = bool(self.get_input("ERAMP_ENABLE"))
        pause = bool(self.get_input("PAUSE"))
        resume = bool(self.get_input("RESUME"))
        rising_enable = enable and not self._prev_enable
        mode_matches = desired_mode in self._MODES and target_mode == desired_mode
        quality = self._output_quality(reference_quality)
        config_valid = (
            mode_reference_valid
            and reference is not None
            and desired_mode in self._MODES
        )

        try:
            if not config_valid:
                self._disable(source, ref_value, options)
                quality = Quality.BAD
            elif not enable:
                self._disable(source, ref_value, options)
            else:
                if rising_enable:
                    self._state = "ENABLED"
                    self._complete = False
                    self._active = False
                    self._remaining_s = 0.0
                    self._paused_by_mode = False

                if self._state == "COMPLETE":
                    pass  # requires a False-to-True ERAMP_ENABLE transition
                elif self._state == "RAMPING" and not mode_matches:
                    if options["pause_mode"]:
                        self._state = "PAUSED"
                        self._active = False
                        self._paused_by_mode = True
                    else:
                        self._disable(source, ref_value, options)
                elif self._state == "RAMPING" and pause:
                    self._state = "PAUSED"
                    self._active = False
                    self._paused_by_mode = False
                elif self._state == "PAUSED":
                    can_resume = (
                        not pause
                        and mode_matches
                        and (not self._paused_by_mode or resume)
                    )
                    if can_resume:
                        self._state = "RAMPING"
                        self._active = True
                        self._paused_by_mode = False
                elif self._state == "ENABLED" and mode_matches and not pause:
                    self._begin(source, endpoint, ref_value, options, reference)

                if self._state == "RAMPING":
                    signature = self._signature(source, endpoint, options)
                    if self._last_signature is not None and signature != self._last_signature:
                        reset_start = source if options["reset_in"] else self._out
                        self._begin(reset_start, endpoint, ref_value, options, reference)

                if self._state == "RAMPING":
                    step_seconds = max(0.0, float(dt))
                    self._out += self._direction * self._rate_per_s * step_seconds
                    self._remaining_s = max(0.0, self._remaining_s - step_seconds)
                    self._out, output_clamped = self._bound(self._out, options)

                    if options["variable"]:
                        threshold = max(0.0, float(params.get("VAR_THRESHOLD", 0.0)))
                        comparison = (
                            self._out
                            if options["use_ref"] and not self.inputs["IN"].connected
                            else source
                        )
                        reached = abs(comparison - endpoint) <= threshold
                        timed_out = self._remaining_s <= 0.0
                        wrong_direction = (
                            self._direction > 0 and self._out > endpoint
                        ) or (self._direction < 0 and self._out < endpoint)
                        if reached or timed_out or wrong_direction or output_clamped:
                            self._complete_ramp(
                                endpoint,
                                variable=True,
                                reference=reference,
                            )
                    else:
                        reached = (
                            self._direction > 0 and self._out >= self._end_at_start
                        ) or (
                            self._direction < 0 and self._out <= self._end_at_start
                        )
                        if reached or self._remaining_s <= 0.0 or output_clamped:
                            self._complete_ramp(
                                self._end_at_start,
                                variable=False,
                                reference=reference,
                            )
                        else:
                            self._write_reference(reference, self._out)

            refreshed, refreshed_quality = self._read_reference(reference)
            try:
                displayed_ref = float(refreshed)
            except (TypeError, ValueError):
                displayed_ref = ref_value
                refreshed_quality = Quality.BAD
            quality = max((quality, refreshed_quality), key=lambda item: item.value)
            self._publish(path, displayed_ref, quality)
        finally:
            # Completion clears unwired ERAMP_ENABLE above.  Reading the
            # terminal here, rather than retaining the scan's local value,
            # preserves the next legitimate rising edge.
            self._prev_enable = bool(self.get_input("ERAMP_ENABLE"))
