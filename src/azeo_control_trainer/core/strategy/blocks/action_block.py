"""Action (ACT) block — Custom calculation block block.

Supports two modes:
1. Expression mode: restricted IEC 61131-3 Structured Text or simple math
2. Azeo Python Extension mode: restricted Python for complex calculations

Uses safe evaluation with restricted builtins and 60+ DCS scripting functions.

Expression mode examples::

    OUT1 = IN1 + IN2 * 0.5
    OUT2 = clamp(IN3 - IN4, 0, 100)
    OUT3 = sqrt(IN1**2 + IN2**2)

    # Structured text
    IF IN1 > 50 THEN
        OUT1 := IN2 * 0.8
    ELSE
        OUT1 := IN2 * 1.2
    END_IF

Azeo Python Extension examples::

    # Stateful filtering
    filtered = ewma(state, 'pv', IN1, 0.2)
    OUT1 = clamp(filtered, 0, 100)
    tripped = rising_edge(state, 'btn', IN2 > 50)
    OUT2 = 100 if tripped else OUT2_PREV

Available variables:
    IN1..IN8      input terminal values
    x, y, z       aliases for IN1, IN2, IN3
    OUT1..OUT4    output values (writable)
    OUT1_PREV     previous scan output values
    state         persistent dict for stateful functions
    dt            scan period in seconds (0.1)
    time          accumulated run time in seconds

Tag-store I/O (when the strategy runtime is attached):
    get_tag(name, default=None)   read any tag from SharedDataStore
    set_tag(name, value)          write a tag (allow-list enforced)
    tag_exists(name)              bool probe

    Writes are denied unless the active plugin explicitly opts in via
    ``plugin.script_writable_tags`` (exact names) or
    ``plugin.script_writable_patterns`` (fnmatch — e.g. ``"ctrl.*.SP"``).
    Refused writes raise PermissionError; ACT's error handler catches
    it and holds last-good outputs.

Built-in functions:
    abs, round, min, max, sum, pow, len, int, float, bool
    sqrt, sin, cos, tan, asin, acos, atan, atan2
    sinh, cosh, tanh, exp, log, log10, log2
    floor, ceil, fabs, fmod, degrees, radians, hypot

Engineering functions (60+):
    clamp, scale, deadband, hysteresis, lerp, normalize, denormalize, remap
    ewma, rate_of_change, integrate, rate_limit
    rising_edge, falling_edge, sr_latch, timer_on, timer_off, counter
    alarm_hi_lo, alarm_rate, alarm_dev
    select_hi, select_lo, select_mid, first_good
    valve_eq_pct, valve_quick_open, valve_installed
    get_bit, set_bit, clear_bit, toggle_bit, test_bits
    temp_convert, press_convert, flow_sq_root
    pulse, blink, ramp, buffer_push, peak_detect, time_avg
    avg, std_dev, median, vote_2oo3, vote_1oo2, watchdog
    poly, heat_duty

Constants:
    pi, e, inf, True, False
"""
from __future__ import annotations

import ast
import math
import re
from typing import Any

from ..model.block_base import FunctionBlock, BlockCategory, BlockStatus, DataType
from ..model.block_registry import register_block
from ..model.terminal import Quality
from .script_functions import get_script_functions
from . import bounded_script


class TagAccessError(RuntimeError):
    """A tag read/write from inside an ACT script failed.

    Azeo's ACT spec distinguishes a *read error* (a referenced parameter
    could not be reached) from a calculation error, because ``ALGO_OPTS``
    AbortOnReadErrors governs only the former. This is a ``RuntimeError``
    subclass so any existing ``except RuntimeError`` still catches it.
    """


# ── Safe math namespace ────────────────────────────────────────────────

SAFE_MATH_FUNCTIONS = {
    # Basic math
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "len": len,
    "int": int,
    "float": float,
    "bool": bool,
    # Math module functions
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "floor": math.floor,
    "ceil": math.ceil,
    "fabs": math.fabs,
    "fmod": math.fmod,
    "degrees": math.degrees,
    "radians": math.radians,
    "hypot": math.hypot,
    "copysign": math.copysign,
    "isnan": math.isnan,
    "isinf": math.isinf,
    # Constants
    "pi": math.pi,
    "PI": math.pi,
    "e": math.e,
    "E": math.e,
    "inf": float("inf"),
    "INF": float("inf"),
    # Boolean
    "True": True,
    "False": False,
    # Conditional
    "if_else": lambda cond, t, f: t if cond else f,
}

# IEC float32 range limits
_FLOAT32_MAX = 3.40282e+38

# Maximum I/O count
MAX_IO = 16


# ── Tag-store I/O closures (bound per-scan to the RuntimeContext) ─────

def _make_get_tag(ctx):
    """Build a ``get_tag(name, default=None)`` closure bound to ``ctx``."""
    if ctx is None:
        def _no_ctx_get(name, default=None):
            return default
        return _no_ctx_get

    def _get_tag(name, default=None):
        try:
            return ctx.read(name, default=default)
        except Exception as exc:            # store unreachable -> read error
            raise TagAccessError(f"get_tag({name!r}): {exc}") from exc
    return _get_tag


def _make_set_tag(ctx):
    """Build a ``set_tag(name, value)`` closure bound to ``ctx``.

    Writes are gated by the plugin's allow-list. Refused writes raise
    ``PermissionError``; the ACT block's existing exception handling
    converts that into ERROR=True + hold-last-good outputs.
    """
    if ctx is None:
        def _no_ctx_set(name, value):
            raise TagAccessError(
                f"set_tag({name!r}): block has no runtime context")
        return _no_ctx_set

    def _set_tag(name, value):
        try:
            ctx.write(name, value)
        except PermissionError:
            raise           # allow-list refusal keeps its own message + type
        except Exception as exc:
            raise TagAccessError(f"set_tag({name!r}): {exc}") from exc
    return _set_tag


#: Exceptions that mean "a referenced parameter could not be read/written",
#: i.e. Azeo's *read error*, as opposed to a calculation error.
_READ_ERRORS = (PermissionError, TagAccessError)


def _make_tag_exists(ctx):
    """Build a ``tag_exists(name)`` probe closure bound to ``ctx``."""
    if ctx is None or ctx.store is None:
        def _no_ctx_exists(name):
            return False
        return _no_ctx_exists

    def _tag_exists(name):
        try:
            return name in ctx.store.get_all()
        except Exception:
            return False
    return _tag_exists


# ── Structured text converter ──────────────────────────────────

def _convert_structured_text(expr: str) -> str:
    """Convert Structured text to Python.

    Handles:
    - := assignment operator
    - IF...THEN...ELSE...END_IF control flow (with nesting)
    - REM and (* *) comments
    - <> not-equal operator
    - = equality test (in conditions)
    - Trailing semicolons
    """
    if len(expr) > bounded_script.MAX_SOURCE:
        raise ValueError("Script source exceeds 16384 characters")
    lines: list[str] = []
    indent_level = 0
    in_block_comment = False

    for line_number, raw_line in enumerate(expr.split("\n"), start=1):
        stripped = raw_line.strip()

        # Handle block comments (* ... *)
        if in_block_comment:
            if "*)" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("(*"):
            if "*)" not in stripped:
                in_block_comment = True
            continue

        # Skip line comments and empty lines
        if stripped.upper().startswith("REM") or not stripped or stripped == ";":
            continue

        # Handle multiple statements on one line separated by ;
        statements = stripped.split(";")
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue

            # Convert operators
            converted = stmt
            converted = converted.replace(":=", "=")
            converted = converted.replace("<>", "!=")

            upper = converted.upper()

            # Handle END_IF / ENDIF
            if upper in ("END_IF", "ENDIF"):
                if indent_level == 0:
                    raise ValueError(
                        f"END_IF without a matching IF at line {line_number}")
                indent_level -= 1
                continue

            # Handle ELSE
            if upper == "ELSE":
                if indent_level == 0:
                    raise ValueError(
                        f"ELSE without a matching IF at line {line_number}")
                indent_level -= 1
                lines.append("    " * indent_level + "else:")
                indent_level += 1
                continue

            # Handle IF...THEN
            if upper.startswith("IF ") and "THEN" in upper:
                then_idx = upper.index("THEN")
                condition = converted[3:then_idx].strip()
                # In ISA, single = is equality test in conditions
                if "==" not in condition and "!=" not in condition:
                    condition = re.sub(r'(?<!=)=(?!=)', '==', condition)
                lines.append("    " * indent_level + f"if {condition}:")
                indent_level += 1
                # Check for inline THEN statement
                rest = converted[then_idx + 4:].strip()
                if rest:
                    lines.append("    " * indent_level + rest)
                continue

            lines.append("    " * indent_level + converted)

    if in_block_comment:
        raise ValueError("Unterminated (* ... *) comment")
    if indent_level:
        raise ValueError(f"{indent_level} IF statement(s) missing END_IF")
    return "\n".join(lines)


# ── Safe expression evaluator (AST-based) ─────────────────────────────

def _safe_eval(expression: str, namespace: dict):
    return bounded_script.evaluate(parse_restricted_expression(expression), namespace)


def _is_statement_expression(source: str) -> bool:
    """Whether expression-mode *source* uses ACT's IEC ST statement path.

    A bare comparison such as ``IN1 >= 10`` must remain an expression.  The
    former ``=`` heuristic treated ``>=``, ``<=`` and ``!=`` as assignments,
    so the parser and scan runtime silently disagreed about their result.
    """
    upper = source.upper()
    assignment = bool(re.search(r"(?<![<>=!])=(?!=)", source))
    return (assignment or ":=" in source or "IF " in upper
            or "\n" in source or ";" in source)


def compile_restricted_script(source: str, filename: str = "<act_script>"):
    """Prepare bounded syntax for the controller interpreter."""
    return bounded_script.parse(source, "exec", filename)


def parse_restricted_expression(source: str) -> ast.Expression:
    return bounded_script.parse(source)


def compile_action_source(source: str, mode: int = 1):
    """Parse ACT source using the same grammar the scan runtime executes.

    Returns ``(kind, parsed)`` where ``kind`` is ``expression``, ``iec_st`` or
    ``azeo_python``.  This is intentionally the single public parse boundary
    used by the block configuration and by Control Designer's Parse command.
    """
    source = str(source or "")
    if not source.strip():
        raise ValueError("The expression is empty (Configuration Error on scan).")
    if int(mode) == 1:
        if _is_statement_expression(source):
            converted = _convert_structured_text(source)
            return "iec_st", compile_restricted_script(converted, "<act_st>")
        tree = parse_restricted_expression(source)
        return "expression", tree
    if int(mode) == 2:
        return "azeo_python", compile_restricted_script(
            source, "<azeo_python_extension>")
    raise ValueError(f"Unsupported ACT script mode: {mode}")


# ── ACT Block ──────────────────────────────────────────────────────────

@register_block
class ActionBlock(FunctionBlock):
    """Custom calculation block block.

    Supports two execution modes:
    1. Expression mode — single expressions or Structured text
       (IF/THEN/ELSE/END_IF, :=, <>)
    2. Azeo Python Extension mode — restricted multi-line Python with
       persistent state and 60+ built-in DCS functions

    Error handling: on calculation error, holds last good output values
    and sets ERROR=True, BLOCK_ERR code, and OUT_STATUS="Bad".

    Azeo capability (§3548), all defaults preserving today's behaviour:

    * ``IN_D`` (BOOL, **default True**) — "the expression is evaluated on
      every execution in which IN_D is True". This is the mechanism behind
      Azeo's *force* pattern: while the trigger stays True the writes the
      expression makes (mode, SP) override operator changes; when it goes
      False the block simply does not run and its outputs hold. Defaulting
      True and leaving it unconnected keeps every existing ACT block — and
      the HDA/NGL SFC modules that gate themselves on ``IN1`` — unchanged.
    * Per spec §3560 the *status* of ``IN_D`` does not affect evaluation:
      only its value gates the expression, and its quality is deliberately
      excluded from the published output quality.
    * ``ALGO_OPTS_ABORT_ON_READ_ERR`` (**default True** = the Azeo option
      selected) — on a read error (a ``get_tag`` / ``set_tag`` that could
      not be served, including an allow-list refusal) the expression aborts
      and output values *and status* are left unchanged, which is exactly
      what this block already did. Clear it to get the un-optioned Azeo
      behaviour instead: values still hold, but the outputs go **Bad**
      (BadNoComm) so downstream blocks see the communication failure rather
      than a stale-looking Good number.

    Output quality: ``OUT1``…``OUT4`` carry the worst quality of the
    connected value inputs (``IN1``…``IN8``) — a calculation is only as
    trustworthy as what fed it — and go Bad on a calculation error, whose
    held last-good values are stale by definition.
    """

    block_type = "ACT"
    category = BlockCategory.MATH
    display_name = "Action (ACT)"
    description = "Custom calculation with expression or script mode"

    def __init__(self, instance_name: str = ""):
        self._elapsed_time: float = 0.0
        self._error_count: int = 0
        self._user_state: dict[str, Any] = {}
        self._last_good: dict[str, float] = {}
        self._last_error: str = ""
        # Cache of validated+compiled code objects, keyed by source text, so
        # the AST sandbox check and compile run once per unique script rather
        # than every scan.
        self._code_cache: dict[str, Any] = {}
        super().__init__(instance_name)

    def _define_terminals(self):
        for i in range(1, 9):
            self.add_input(f"IN{i}", description=f"Input {i}")
        # Declared last so the existing IN1..IN8 pin order on the canvas is
        # untouched. Default True => an unwired IN_D never gates anything.
        self.add_input("IN_D", data_type=DataType.BOOL, default=True,
                       description="Evaluate the expression while True "
                                   "(Azeo ACT trigger; status ignored)")
        for i in range(1, 5):
            self.add_output(f"OUT{i}", description=f"Output {i}")
        self.add_output("ERROR", data_type=DataType.BOOL, default=False,
                        description="True on script error")
        self.add_output("BLOCK_ERR", data_type=DataType.INT, default=0,
                        description="Error code (ISA compatible)")

    def get_config_schema(self):
        return {
            "EXPRESSION": (str, "OUT1 = IN1", "Expression or structured text"),
            "SCRIPT_MODE": (int, 1,
                            "Mode: 1=IEC ST/Expression, "
                            "2=Azeo Python Extension"),
            "SCRIPT": (str, "", "Azeo Python Extension source (mode 2)"),
            "OUT_MIN": (float, -3.4e+38, "Output minimum limit"),
            "OUT_MAX": (float, 3.4e+38, "Output maximum limit"),
            "ALGO_OPTS_ABORT_ON_READ_ERR": (
                bool, True,
                "Azeo ALGO_OPTS AbortOnReadErrors: on a tag read/write "
                "error abort the expression, leaving output values AND "
                "status unchanged. Clear it to publish Bad (BadNoComm) "
                "output quality on a read error instead"),
        }

    def _apply_config(self):
        # Pre-validate syntax
        self._last_error = ""
        mode = int(self.config.params.get("SCRIPT_MODE", 1))
        try:
            source_key = "EXPRESSION" if mode == 1 else "SCRIPT"
            compile_action_source(
                str(self.config.params.get(source_key, "")), mode)
            self.status = BlockStatus.GOOD
        except SyntaxError as exc:
            self._last_error = f"Syntax error line {exc.lineno}: {exc.msg}"
            self.status = BlockStatus.BAD
        except Exception as exc:
            self._last_error = str(exc)
            self.status = BlockStatus.BAD

    def execute(self, dt: float):
        self._elapsed_time += dt
        block_err = 0

        mode = int(self.config.params.get("SCRIPT_MODE", 1))

        # Azeo ACT: the expression runs only on executions where IN_D is
        # True. Only the *value* is consulted — §3560 is explicit that the
        # status of IN_D does not affect evaluation.
        if not bool(self.get_input("IN_D")):
            self._hold_last_good()
            self.set_output("ERROR", False)
            self.set_output("BLOCK_ERR", 0)
            # Not evaluating is not a failure: the held values keep the
            # quality they were last computed with.
            return

        try:
            if mode == 1:
                self._execute_expression(dt)
            else:
                self._execute_script(dt)

            self.set_output("ERROR", False)
            self._last_error = ""
            self._publish_out_status(self._input_quality())

        except ZeroDivisionError:
            self._error_count += 1
            self.set_output("ERROR", True)
            block_err |= 0x10  # Configuration Error (ISA: divide by zero)
            self._last_error = "Division by zero"
            self._hold_last_good()
            self._publish_out_status(Quality.BAD)
        except (OverflowError, FloatingPointError) as exc:
            self._error_count += 1
            self.set_output("ERROR", True)
            self._last_error = f"Overflow: {exc}"
            self._hold_last_good()
            self._publish_out_status(Quality.BAD)
        except _READ_ERRORS as exc:
            # Azeo read error, governed by ALGO_OPTS AbortOnReadErrors.
            self._error_count += 1
            self.set_output("ERROR", True)
            self._last_error = f"Runtime: {exc}"
            self._hold_last_good()
            if not bool(self.config.params.get(
                    "ALGO_OPTS_ABORT_ON_READ_ERR", True)):
                # Option not selected: status becomes BadNoComm.
                self._publish_out_status(Quality.BAD)
            # Option selected (default): "value and status remain
            # unchanged" — leave the output statuses exactly as they were.
        except Exception as exc:
            self._error_count += 1
            self.set_output("ERROR", True)
            self._last_error = f"Runtime: {exc}"
            self._hold_last_good()
            self._publish_out_status(Quality.BAD)

        self.set_output("BLOCK_ERR", block_err)

    def _input_quality(self) -> Quality:
        """Worst quality across the connected value inputs (IN1..IN8).

        ``IN_D`` is deliberately excluded — per spec its status has no
        bearing on the block.
        """
        worst = Quality.GOOD
        for i in range(1, 9):
            t = self.inputs.get(f"IN{i}")
            if t is not None and t.connected and t.status.value > worst.value:
                worst = t.status
        return worst

    def _publish_out_status(self, quality: Quality) -> None:
        """Stamp *quality* on OUT1..OUT4 (ERROR/BLOCK_ERR stay diagnostics)."""
        for i in range(1, 5):
            self.set_output_status(f"OUT{i}", quality)

    def _safe_compile(self, source: str, filename: str):
        """Validate *source* against the script sandbox and compile it.

        Results are cached by source text. Raises ``ValueError`` for unsafe
        scripts and ``SyntaxError`` for malformed ones; both propagate to the
        caller's error handling (config -> BAD status, runtime -> hold-last-good).
        """
        cached = self._code_cache.get(source)
        if cached is not None:
            return cached
        code = compile_restricted_script(source, filename)
        if len(self._code_cache) >= 16:
            self._code_cache.pop(next(iter(self._code_cache)))
        self._code_cache[source] = code
        return code

    def _execute_expression(self, dt: float):
        """Execute expression mode (mode 1)."""
        expression = str(self.config.params.get("EXPRESSION", "OUT1 = IN1"))
        if len(expression) > bounded_script.MAX_SOURCE:
            raise ValueError("Script source exceeds 16384 characters")
        if not expression.strip():
            return

        if _is_statement_expression(expression):
            # Multi-statement structured text
            code = _convert_structured_text(expression)
            namespace = self._build_namespace(dt)
            # Seed outputs in namespace
            for i in range(1, 5):
                namespace[f"OUT{i}"] = self._last_good.get(f"OUT{i}", 0.0)
            compiled = self._safe_compile(code, "<act_st>")
            self._run_prepared_script(compiled, namespace)
        else:
            # Simple single expression -> safe eval for OUT1
            namespace = self._build_namespace(dt)
            result = bounded_script.evaluate(parse_restricted_expression(expression), namespace,
                lambda value: self._checked_output(0.0 if value is None else value))
            self._set_output(result, "OUT1")

    def _execute_script(self, dt: float):
        """Execute script mode (mode 2)."""
        script = str(self.config.params.get("SCRIPT", "OUT1 = IN1"))
        if not script.strip():
            return

        namespace = self._build_namespace(dt)

        # Seed outputs for read-modify-write
        for i in range(1, 5):
            key = f"OUT{i}"
            namespace[key] = self._last_good.get(key, 0.0)

        compiled = self._safe_compile(script, "<act_script>")
        self._run_prepared_script(compiled, namespace)

    def _run_prepared_script(self, compiled, namespace):
        # Validate all four outputs before committing state or any output;
        # otherwise a later bad OUT2 could silently advance OUT1/state.
        def validate(values):
            return {key: self._checked_output(values[key])
                    for key in ("OUT1", "OUT2", "OUT3", "OUT4") if key in values}
        values = bounded_script.execute(compiled, namespace, validate)
        for key, value in values.items():
            self.set_output(key, value)
            self._last_good[key] = value

    def _build_namespace(self, dt: float) -> dict:
        """Build the execution namespace with inputs, functions, constants."""
        namespace = dict(SAFE_MATH_FUNCTIONS)

        # Add 60+ DCS scripting functions + any plugin-registered helpers.
        namespace.update(get_script_functions())

        # Tag-store I/O bound to the runtime context the strategy runtime
        # attached on this scan. If no context is present (block being
        # executed outside the strategy runtime — e.g. unit test) we still
        # expose stubs that raise a clear error instead of NameError.
        ctx = getattr(self, "runtime_context", None)
        namespace["get_tag"] = _make_get_tag(ctx)
        namespace["set_tag"] = _make_set_tag(ctx)
        namespace["tag_exists"] = _make_tag_exists(ctx)
        namespace["tag"] = _make_get_tag(ctx)      # CND-compatible spelling

        # Azeo module parameters and direct references — the reason the
        # real ACT has no output pins: the expression itself reads and
        # writes named things. Module parameters resolve bare (`MAX_MOVE`);
        # `write_param` assigns only records whose independent Write Access
        # permits it (legacy internal-write records remain compatible);
        # `param('PID1/OUT')` reads another block's terminal.
        graph = getattr(self, "_module_graph", None)

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
                return terminal.value
            if item in other.config.params:
                return other.config.params[item]
            raise ValueError(
                f"param(): {name} has no terminal or parameter {item!r}")

        def write_param(name, value):
            if graph is None:
                raise ValueError("write_param(): module not on scan yet")
            spec = graph.module_parameters().get(str(name))
            if spec is None:
                raise ValueError(
                    f"write_param(): no module parameter {name!r}")
            if not graph.module_parameter_is_writeable(spec):
                raise ValueError(
                    f"write_param(): {name!r} is not writeable")
            spec["value"] = value
            return value

        namespace["param"] = param
        namespace["write_param"] = write_param
        if graph is not None:
            for _name, _spec in graph.module_parameters().items():
                namespace.setdefault(_name, _spec.get("value"))

        # Add inputs
        for i in range(1, 9):
            key = f"IN{i}"
            namespace[key] = self.get_input(key)

        # Aliases
        namespace["x"] = namespace["IN1"]
        namespace["y"] = namespace["IN2"]
        namespace["z"] = namespace["IN3"]

        # ISA mode constants
        namespace["MAN"] = "Man"
        namespace["AUTO"] = "Auto"
        namespace["CAS"] = "Cas"
        namespace["RCAS"] = "RCas"
        namespace["OOS"] = "OOS"

        # Time variables
        namespace["dt"] = dt
        namespace["DT"] = dt
        namespace["time"] = self._elapsed_time
        namespace["T"] = self._elapsed_time

        # Persistent state dict (for stateful functions)
        namespace["state"] = self._user_state

        # Previous outputs
        for i in range(1, 5):
            key = f"OUT{i}"
            namespace[f"{key}_PREV"] = self._last_good.get(key, 0.0)

        return namespace

    def _checked_output(self, value):
        """Set output with ISA-compatible range checking."""
        try:
            value = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("ACT outputs must be numeric") from error
        if not math.isfinite(value):
            raise ValueError("ACT outputs must be finite")

        # ISA: out of float32 range -> Bad status
        if abs(value) > _FLOAT32_MAX:
            value = max(-_FLOAT32_MAX, min(_FLOAT32_MAX, value))

        # User-configured limits
        out_min = float(self.config.params.get("OUT_MIN", -_FLOAT32_MAX))
        out_max = float(self.config.params.get("OUT_MAX", _FLOAT32_MAX))
        value = max(out_min, min(out_max, value))
        return value

    def _set_output(self, value, output_name: str = "OUT1"):
        value = self._checked_output(value)
        self.set_output(output_name, value)
        self._last_good[output_name] = value

    def _hold_last_good(self):
        """On error, hold last good output values."""
        for i in range(1, 5):
            key = f"OUT{i}"
            self.set_output(key, self._last_good.get(key, 0.0))

    def reset(self):
        super().reset()
        self._elapsed_time = 0.0
        self._error_count = 0
        self._user_state.clear()
        self._code_cache.clear()
        self._last_good.clear()
        self._last_error = ""

    def get_script_error(self) -> str:
        """Return the last compilation or runtime error message."""
        return self._last_error
