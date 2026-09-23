"""Utility / memory blocks — internal strategy variables.

These blocks provide user-configurable constants, setpoints, and memory
registers so strategies can be designed without requiring every value
to come from a physical I/O tag.
"""
from __future__ import annotations

from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality


def _shared_memory(block, output, write=False, value=None):
    path = block.config.params.get("memory_tag", "").strip()
    if not path:
        return False
    try:
        store = getattr(block, "_memory_tags", None)
        if store is None:
            raise ValueError("Project memory database is unavailable")
        if write:
            dtype = {DataType.FLOAT: "float", DataType.INT: "int", DataType.BOOL: "bool", DataType.STRING: "str"}[block.outputs[output].data_type]
            store.write_many({path: value}, types={path: dtype}, timeout=0)
        # The scan runs on Qt's thread. A busy database must report Bad and
        # recover on a later scan, never wait five seconds per memory block.
        value = store.read(path, timeout=0)
        dtype = block.outputs[output].data_type
        valid = {DataType.FLOAT: type(value) in {int, float}, DataType.INT: type(value) is int,
                 DataType.BOOL: type(value) is bool, DataType.STRING: type(value) is str}[dtype]
        if not valid:
            raise ValueError("Memory tag and block types do not match")
        block.set_output(output, value)
        block.set_output_status(output, Quality.GOOD)
        block._memory_error = ""
    except Exception as error:
        block._memory_error = str(error)
        block.set_output_status(output, Quality.BAD)
    return True


@register_block
class ConstantBlock(FunctionBlock):
    """Constant — outputs a fixed value configured by the user."""

    block_type = "CONSTANT"
    category = BlockCategory.MATH
    display_name = "Constant"
    description = "Outputs a fixed numeric value"

    def _define_terminals(self):
        self.add_output("OUT", description="Constant value")

    def get_config_schema(self):
        return {
            "value": (float, 0.0, "Constant output value"),
            "label": (str, "", "Descriptive label"),
        }

    def _apply_config(self):
        self.set_output("OUT", self.config.params.get("value", 0.0))

    def execute(self, dt: float):
        self.set_output("OUT", self.config.params.get("value", 0.0))


@register_block
class SetpointBlock(FunctionBlock):
    """Setpoint — user-adjustable setpoint with hi/lo limits.

    Similar to a Constant but semantically represents a process setpoint
    that operators may adjust at runtime. Supports tracking input.
    """

    block_type = "SETPOINT"
    category = BlockCategory.IO
    display_name = "Setpoint (SP)"
    description = "User-adjustable setpoint with limits"

    def _define_terminals(self):
        self.add_input("TRK_VAL", description="External tracking value")
        self.add_input("TRK_IN_D", DataType.BOOL, False, "Enable tracking")
        self.add_output("OUT", description="Setpoint value")

    def get_config_schema(self):
        return {
            "value": (float, 50.0, "Setpoint value"),
            "lo_limit": (float, 0.0, "Low limit"),
            "hi_limit": (float, 100.0, "High limit"),
            "eng_units": (str, "", "Engineering units"),
            "label": (str, "", "Descriptive label"),
        }

    def _apply_config(self):
        val = self.config.params.get("value", 50.0)
        lo = self.config.params.get("lo_limit", 0.0)
        hi = self.config.params.get("hi_limit", 100.0)
        self.set_output("OUT", max(lo, min(hi, val)))

    def execute(self, dt: float):
        if self.get_input("TRK_IN_D"):
            val = self.get_input("TRK_VAL")
        else:
            val = self.config.params.get("value", 50.0)
        lo = self.config.params.get("lo_limit", 0.0)
        hi = self.config.params.get("hi_limit", 100.0)
        self.set_output("OUT", max(lo, min(hi, val)))


@register_block
class MemoryFloatBlock(FunctionBlock):
    """Memory (Analog) — retains a float value across scans.

    Can be written to by an input wire or by the user via the properties
    panel. Acts as a general-purpose analog memory register / tag.
    """

    block_type = "MEM_FLOAT"
    category = BlockCategory.SIGNAL
    display_name = "Memory (Analog)"
    description = "Analog memory register — retains value across scans"

    def _define_terminals(self):
        self.add_input("IN", description="Value to write")
        self.add_input("WRITE_EN", DataType.BOOL, False, "Write enable")
        self.add_input("RESET", DataType.BOOL, False, "Reset to initial value")
        self.add_output("OUT", description="Stored value")

    def get_config_schema(self):
        return {
            "initial_value": (float, 0.0, "Initial / reset value"),
            "label": (str, "", "Tag label"),
            "memory_tag": (str, "", "Shared Tag DB path (MEMORY/name/VALUE); blank retains module-local memory"),
        }

    def _apply_config(self):
        # Only set output to initial if it hasn't been written yet
        if self.get_output("OUT") == 0.0:
            self.set_output("OUT", self.config.params.get("initial_value", 0.0))

    def execute(self, dt: float):
        reset = bool(self.get_input("RESET"))
        write = reset or bool(self.get_input("WRITE_EN"))
        value = self.config.params.get("initial_value", 0.0) if reset else self.get_input("IN")
        if _shared_memory(self, "OUT", write, value):
            return
        if self.get_input("RESET"):
            self.set_output("OUT", self.config.params.get("initial_value", 0.0))
        elif self.get_input("WRITE_EN"):
            self.set_output("OUT", self.get_input("IN"))
        # else: retain previous value


@register_block
class MemoryBoolBlock(FunctionBlock):
    """Memory (Digital) — retains a boolean value across scans.

    Acts as a general-purpose digital memory register / tag.
    """

    block_type = "MEM_BOOL"
    category = BlockCategory.LOGIC
    display_name = "Memory (Digital)"
    description = "Digital memory register — retains boolean across scans"

    def _define_terminals(self):
        self.add_input("SET_D", DataType.BOOL, False, "Set to True")
        self.add_input("RESET_D", DataType.BOOL, False, "Reset to False")
        self.add_output("OUT_D", DataType.BOOL, False, "Stored value")

    def get_config_schema(self):
        return {
            "initial_value": (bool, False, "Initial / reset value"),
            "label": (str, "", "Tag label"),
            "memory_tag": (str, "", "Shared Tag DB path (MEMORY/name/VALUE); blank retains module-local memory"),
        }

    def _apply_config(self):
        self.set_output(
            "OUT_D", bool(self.config.params.get("initial_value", False))
        )

    def reset(self):
        """Restore the configured memory state on a fresh download."""
        super().reset()
        self.set_output(
            "OUT_D", bool(self.config.params.get("initial_value", False))
        )

    def execute(self, dt: float):
        reset = bool(self.get_input("RESET_D"))
        write = reset or bool(self.get_input("SET_D"))
        value = self.config.params.get("initial_value", False) if reset else True
        if _shared_memory(self, "OUT_D", write, value):
            return
        if self.get_input("RESET_D"):
            self.set_output("OUT_D", self.config.params.get("initial_value", False))
        elif self.get_input("SET_D"):
            self.set_output("OUT_D", True)
        # else: retain previous value


@register_block
class MemoryIntBlock(MemoryFloatBlock):
    block_type = "MEM_INT"
    display_name = "Memory (Integer)"
    description = "Integer memory register; optionally linked to the project Tag DB"

    def _define_terminals(self):
        self.add_input("IN", DataType.INT, 0, "Value to write")
        self.add_input("WRITE_EN", DataType.BOOL, False, "Write enable")
        self.add_input("RESET", DataType.BOOL, False, "Reset to initial value")
        self.add_output("OUT", DataType.INT, 0, "Stored value")

    def get_config_schema(self):
        return dict(super().get_config_schema(), initial_value=(int, 0, "Initial / reset value"))


@register_block
class MemoryStringBlock(MemoryIntBlock):
    block_type = "MEM_STRING"
    display_name = "Memory (Text)"
    description = "Text memory register; optionally linked to the project Tag DB"

    def _define_terminals(self):
        super()._define_terminals()
        for terminal in (self.inputs["IN"], self.outputs["OUT"]):
            terminal.data_type = DataType.STRING
            terminal.value = terminal.default_value = ""

    def get_config_schema(self):
        return dict(super().get_config_schema(), initial_value=(str, "", "Initial / reset value"))

    def _apply_config(self):
        if self.get_output("OUT") == "":
            self.set_output("OUT", self.config.params.get("initial_value", ""))
