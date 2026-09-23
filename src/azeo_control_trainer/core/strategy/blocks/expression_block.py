"""Structured Text Expression Block — IEC 61131-3 style calculations.

Provides a configurable expression block that evaluates mathematical
and logical expressions at runtime using a safe subset of Python.

Supports:
  - Arithmetic: +, -, *, /, **, %, //
  - Comparison: ==, !=, <, >, <=, >=
  - Logical: and, or, not
  - Math functions: abs, min, max, sqrt, sin, cos, tan, exp, log, log10,
                    atan, atan2, pow, clamp, sign, ceil, floor, round
  - Conditional: IF/THEN/ELSE via Python ternary (x if cond else y)
  - Constants: PI, E, TRUE, FALSE
  - Inputs: IN1..IN8 (float), BIN1..BIN4 (bool)
  - Outputs: OUT (float), BOUT (bool)

Expressions are compiled once on config change for fast repeated evaluation.

Example expressions:
  - "IN1 * 2.5 + IN2"
  - "max(IN1, IN2, IN3)"
  - "sqrt(IN1**2 + IN2**2)"
  - "IN1 if BIN1 else IN2"
  - "clamp(IN1, 0, 100)"
  - "abs(IN1 - IN2) > 5.0"   (sets BOUT)
"""
from __future__ import annotations

import logging
import math

from ..model.block_base import FunctionBlock, BlockCategory, BlockStatus, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality
from . import bounded_script

log = logging.getLogger("strategy.blocks.expression")

# Safe builtins allowed in expressions
_SAFE_BUILTINS = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "int": int,
    "float": float,
    "bool": bool,
    # Math functions
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "pow": pow,
    "ceil": math.ceil,
    "floor": math.floor,
    "sign": lambda x: (1.0 if x > 0 else (-1.0 if x < 0 else 0.0)),
    "clamp": lambda x, lo, hi: max(lo, min(hi, x)),
    "deadband": lambda x, db: (x if abs(x) > db else 0.0),
    "lerp": lambda a, b, t: a + (b - a) * t,
    "ramp_limit": lambda x, prev, rate, dt: max(prev - rate * dt,
                                                  min(prev + rate * dt, x)),
    # Constants
    "PI": math.pi,
    "E": math.e,
    "TRUE": True,
    "FALSE": False,
}

def _validate_expression(expr: str) -> str | None:
    try:
        _compile_expression(expr)
    except (ValueError, SyntaxError, TypeError) as error:
        return str(error)
    return None


def _compile_expression(expr: str):
    return bounded_script.parse(expr)


@register_block
class ExpressionBlock(FunctionBlock):
    """IEC 61131-3 Structured Text expression evaluator.

    Evaluates a configurable mathematical/logical expression each scan.
    Uses 8 float inputs (IN1-IN8) and 4 bool inputs (BIN1-BIN4).
    Writes result to OUT (float) and BOUT (bool).
    """

    block_type = "EXPRESSION"
    category = BlockCategory.MATH
    display_name = "Expression (ST)"
    description = "IEC 61131-3 Structured Text expression evaluator"

    def __init__(self, instance_name=""):
        self._compiled_expr = None
        self._compiled_bool = None
        self._last_expr = ""
        self._last_bool_expr = ""
        self._error_msg = ""
        super().__init__(instance_name)

    def _define_terminals(self):
        # Float inputs
        self.add_input("IN1", DataType.FLOAT, 0.0, "Input 1")
        self.add_input("IN2", DataType.FLOAT, 0.0, "Input 2")
        self.add_input("IN3", DataType.FLOAT, 0.0, "Input 3")
        self.add_input("IN4", DataType.FLOAT, 0.0, "Input 4")
        self.add_input("IN5", DataType.FLOAT, 0.0, "Input 5")
        self.add_input("IN6", DataType.FLOAT, 0.0, "Input 6")
        self.add_input("IN7", DataType.FLOAT, 0.0, "Input 7")
        self.add_input("IN8", DataType.FLOAT, 0.0, "Input 8")
        # Bool inputs
        self.add_input("BIN1", DataType.BOOL, False, "Boolean input 1")
        self.add_input("BIN2", DataType.BOOL, False, "Boolean input 2")
        self.add_input("BIN3", DataType.BOOL, False, "Boolean input 3")
        self.add_input("BIN4", DataType.BOOL, False, "Boolean input 4")
        # Outputs
        self.add_output("OUT", DataType.FLOAT, 0.0, "Expression result")
        self.add_output("BOUT", DataType.BOOL, False, "Boolean expression result")
        self.add_output("ERROR", DataType.BOOL, False, "Expression error flag")

    def get_config_schema(self):
        return {
            "expression": (str, "IN1", "Main expression (result -> OUT)"),
            "bool_expression": (str, "", "Boolean expression (result -> BOUT, optional)"),
            "description": (str, "", "Description / comment"),
            "clamp_lo": (float, -1e12, "Output lower clamp"),
            "clamp_hi": (float, 1e12, "Output upper clamp"),
        }

    def execute(self, dt: float):
        params = self.config.params
        expr = params.get("expression", "IN1")
        bool_expr = params.get("bool_expression", "")
        try:
            if expr != self._last_expr or self._compiled_expr is None:
                self._compiled_expr = _compile_expression(expr)
                self._last_expr = expr
            if bool_expr != self._last_bool_expr:
                self._compiled_bool = _compile_expression(bool_expr) if bool_expr.strip() else None
                self._last_bool_expr = bool_expr
            namespace = dict(_SAFE_BUILTINS, dt=dt, PREV=self.outputs["OUT"].value)
            for i in range(1, 9):
                namespace[f"IN{i}"] = float(self.get_input(f"IN{i}"))
            for i in range(1, 5):
                namespace[f"BIN{i}"] = bool(self.get_input(f"BIN{i}"))
            result = float(bounded_script.evaluate(self._compiled_expr, namespace))
            if not math.isfinite(result):
                raise ValueError("Expression result must be finite")
            boolean = bool(bounded_script.evaluate(self._compiled_bool, namespace)) if self._compiled_bool else None
            result = max(params.get("clamp_lo", -1e12), min(params.get("clamp_hi", 1e12), result))
            self.set_output("OUT", result)
            if boolean is not None:
                self.set_output("BOUT", boolean)
            self._error_msg = ""
            self.set_output("ERROR", False)
            self.set_output_status("OUT", Quality.GOOD)
            self.set_output_status("BOUT", Quality.GOOD)
            self.status = BlockStatus.GOOD
        except Exception as error:
            self._error_msg = str(error)
            self.set_output("ERROR", True)
            self.set_output_status("OUT", Quality.BAD)
            self.set_output_status("BOUT", Quality.BAD)
            self.status = BlockStatus.BAD
            log.debug("Expression error in %s: %s", self.instance_name, error)
