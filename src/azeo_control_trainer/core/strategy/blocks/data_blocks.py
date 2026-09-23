"""Data handling blocks — FIFO, LIFO, DATALOG, BIT_PACK, BIT_UNPACK, MSG."""
from __future__ import annotations
import collections
import logging
from ..model.block_base import FunctionBlock, BlockCategory, DataType
from ..model.block_registry import register_block

log = logging.getLogger("strategy.blocks.data")


@register_block
class FIFOBlock(FunctionBlock):
    """First-in first-out buffer (Honeywell FIFO).

    Push values in, read them out in order (queue semantics).
    """
    block_type = "FIFO"
    category = BlockCategory.SIGNAL
    display_name = "FIFO Buffer"
    description = "First-in first-out queue (N-deep)"

    def __init__(self, instance_name=""):
        self._queue: collections.deque = collections.deque()
        self._prev_push = False
        self._prev_pop = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Value to push")
        self.add_input("PUSH", DataType.BOOL, False, "Push on rising edge")
        self.add_input("POP", DataType.BOOL, False, "Pop on rising edge")
        self.add_input("RESET", DataType.BOOL, False, "Clear buffer")
        self.add_output("OUT", description="Front value (oldest)")
        self.add_output("COUNT", DataType.INT, 0, "Items in buffer")
        self.add_output("FULL", DataType.BOOL, False, "Buffer full")
        self.add_output("EMPTY", DataType.BOOL, True, "Buffer empty")

    def get_config_schema(self):
        return {"DEPTH": (int, 32, "Maximum buffer depth")}

    def execute(self, dt: float):
        depth = max(int(self.config.params.get("DEPTH", 32)), 1)
        if depth > 65536:
            raise ValueError("FIFO depth exceeds 65536 items")

        if self.get_input("RESET"):
            self._queue.clear()

        push = bool(self.get_input("PUSH"))
        pop = bool(self.get_input("POP"))

        if push and not self._prev_push and len(self._queue) < depth:
            self._queue.append(self.get_input("IN"))
        if pop and not self._prev_pop and self._queue:
            self._queue.popleft()

        self._prev_push = push
        self._prev_pop = pop

        self.set_output("OUT", self._queue[0] if self._queue else 0.0)
        self.set_output("COUNT", len(self._queue))
        self.set_output("FULL", len(self._queue) >= depth)
        self.set_output("EMPTY", len(self._queue) == 0)

    def reset(self):
        super().reset()
        self._queue.clear()
        self._prev_push = False
        self._prev_pop = False


@register_block
class LIFOBlock(FunctionBlock):
    """Last-in first-out stack (Honeywell LIFO)."""
    block_type = "LIFO"
    category = BlockCategory.SIGNAL
    display_name = "LIFO Stack"
    description = "Last-in first-out stack (N-deep)"

    def __init__(self, instance_name=""):
        self._stack: list = []
        self._prev_push = False
        self._prev_pop = False
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("IN", description="Value to push")
        self.add_input("PUSH", DataType.BOOL, False, "Push on rising edge")
        self.add_input("POP", DataType.BOOL, False, "Pop on rising edge")
        self.add_input("RESET", DataType.BOOL, False, "Clear stack")
        self.add_output("OUT", description="Top value (newest)")
        self.add_output("COUNT", DataType.INT, 0, "Items in stack")
        self.add_output("FULL", DataType.BOOL, False, "Stack full")
        self.add_output("EMPTY", DataType.BOOL, True, "Stack empty")

    def get_config_schema(self):
        return {"DEPTH": (int, 32, "Maximum stack depth")}

    def execute(self, dt: float):
        depth = max(int(self.config.params.get("DEPTH", 32)), 1)
        if depth > 65536:
            raise ValueError("LIFO depth exceeds 65536 items")

        if self.get_input("RESET"):
            self._stack.clear()

        push = bool(self.get_input("PUSH"))
        pop = bool(self.get_input("POP"))

        if push and not self._prev_push and len(self._stack) < depth:
            self._stack.append(self.get_input("IN"))
        if pop and not self._prev_pop and self._stack:
            self._stack.pop()

        self._prev_push = push
        self._prev_pop = pop

        self.set_output("OUT", self._stack[-1] if self._stack else 0.0)
        self.set_output("COUNT", len(self._stack))
        self.set_output("FULL", len(self._stack) >= depth)
        self.set_output("EMPTY", len(self._stack) == 0)

    def reset(self):
        super().reset()
        self._stack.clear()
        self._prev_push = False
        self._prev_pop = False


@register_block
class DatalogBlock(FunctionBlock):
    """Data logging trigger block (Honeywell DATALOG).

    Captures a snapshot of input values on rising edge of TRIGGER.
    Stores last N snapshots internally. Outputs most recent values.
    """
    block_type = "DATALOG"
    category = BlockCategory.IO
    display_name = "Data Log"
    description = "Event-triggered data snapshot capture"

    def __init__(self, instance_name=""):
        self._prev_trigger = False
        self._snap_count = 0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("TRIGGER", DataType.BOOL, False, "Capture on rising edge")
        self.add_input("IN1", description="Data channel 1")
        self.add_input("IN2", description="Data channel 2")
        self.add_input("IN3", description="Data channel 3")
        self.add_input("IN4", description="Data channel 4")
        self.add_output("SNAP_COUNT", DataType.INT, 0, "Total snapshots taken")
        self.add_output("LAST_1", description="Last captured value 1")
        self.add_output("LAST_2", description="Last captured value 2")
        self.add_output("LAST_3", description="Last captured value 3")
        self.add_output("LAST_4", description="Last captured value 4")
        self.add_output("TRIGGERED", DataType.BOOL, False, "Snapshot just taken")

    def execute(self, dt: float):
        trigger = bool(self.get_input("TRIGGER"))
        triggered = trigger and not self._prev_trigger
        self._prev_trigger = trigger

        if triggered:
            self._snap_count += 1
            self.set_output("LAST_1", self.get_input("IN1"))
            self.set_output("LAST_2", self.get_input("IN2"))
            self.set_output("LAST_3", self.get_input("IN3"))
            self.set_output("LAST_4", self.get_input("IN4"))
            log.debug("DATALOG %s: snapshot #%d", self.instance_name, self._snap_count)

        self.set_output("SNAP_COUNT", self._snap_count)
        self.set_output("TRIGGERED", triggered)

    def reset(self):
        super().reset()
        self._prev_trigger = False
        self._snap_count = 0


@register_block
class BitPackBlock(FunctionBlock):
    """Pack 16 boolean inputs into a single integer word (Honeywell DIGCOMB)."""
    block_type = "BIT_PACK"
    category = BlockCategory.LOGIC
    display_name = "Bit Pack"
    description = "Pack 16 bool inputs into integer word"

    def _define_terminals(self):
        for i in range(16):
            self.add_input(f"BIT_{i}", DataType.BOOL, False, f"Bit {i}")
        self.add_output("WORD", DataType.INT, 0, "Packed integer word")

    def execute(self, dt: float):
        word = 0
        for i in range(16):
            if self.get_input(f"BIT_{i}"):
                word |= (1 << i)
        self.set_output("WORD", word)


@register_block
class BitUnpackBlock(FunctionBlock):
    """Unpack integer word into 16 boolean outputs (Honeywell DIGSPLT)."""
    block_type = "BIT_UNPACK"
    category = BlockCategory.LOGIC
    display_name = "Bit Unpack"
    description = "Unpack integer word into 16 bool outputs"

    def _define_terminals(self):
        self.add_input("WORD", DataType.INT, 0, "Integer word to unpack")
        for i in range(16):
            self.add_output(f"BIT_{i}", DataType.BOOL, False, f"Bit {i}")

    def execute(self, dt: float):
        word = int(self.get_input("WORD"))
        for i in range(16):
            self.set_output(f"BIT_{i}", bool(word & (1 << i)))


@register_block
class MsgBlock(FunctionBlock):
    """Operator message block (Honeywell MSGBLK).

    Generates a text message to the HMI/log on rising edge of TRIGGER.
    Message includes configurable text and current values.
    """
    block_type = "MSG"
    category = BlockCategory.IO
    display_name = "Message"
    description = "Event-triggered operator message to HMI/log"

    def __init__(self, instance_name=""):
        self._prev_trigger = False
        self._msg_count = 0
        super().__init__(instance_name)

    def _define_terminals(self):
        self.add_input("TRIGGER", DataType.BOOL, False, "Send message on rising edge")
        self.add_input("VALUE", description="Value to include in message")
        self.add_input("PRIORITY", DataType.INT, 1, "Priority (1=low, 2=medium, 3=high)")
        self.add_output("SENT", DataType.BOOL, False, "Message just sent")
        self.add_output("MSG_COUNT", DataType.INT, 0, "Total messages sent")
        self.add_output("LAST_MSG", DataType.STRING, "", "Last message text")

    def get_config_schema(self):
        return {
            "TEXT": (str, "Process event", "Message text"),
            "INCLUDE_VALUE": (bool, True, "Append value to message"),
        }

    def execute(self, dt: float):
        trigger = bool(self.get_input("TRIGGER"))
        sent = trigger and not self._prev_trigger
        self._prev_trigger = trigger

        if sent:
            self._msg_count += 1
            text = self.config.params.get("TEXT", "Process event")
            if self.config.params.get("INCLUDE_VALUE", True):
                text = f"{text}: {self.get_input('VALUE'):.4f}"
            self.set_output("LAST_MSG", text)
            log.info("MSG %s [P%d]: %s",
                     self.instance_name,
                     int(self.get_input("PRIORITY")),
                     text)

        self.set_output("SENT", sent)
        self.set_output("MSG_COUNT", self._msg_count)

    def reset(self):
        super().reset()
        self._prev_trigger = False
        self._msg_count = 0
