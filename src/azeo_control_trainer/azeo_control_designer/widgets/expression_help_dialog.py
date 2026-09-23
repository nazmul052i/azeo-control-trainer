r"""Proper help dialog for the ACT / expression editor.

A modeless top-level dialog with a searchable tree on the left and a
rich-text reference panel on the right. Designed to be the
authoritative reference for everything the editor exposes:

    Overview
    Editor Modes (Expression / Script)
    Variables (IN1..IN8, OUT1..OUT4, state, dt, time)
    Operators (Python + Structured Text)
    Keyboard Shortcuts
    DCS Functions (Internal Parameter, Named State, etc.)
    Tag I/O (get_tag / set_tag / tag_exists)
    Built-in Functions
        Process Control      clamp / scale / deadband / hysteresis / lerp / ...
        Signal Processing    ewma / rate_of_change / integrate / rate_limit
        PLC Logic            rising_edge / sr_latch / timer_on / counter / ...
        Alarm                alarm_hi_lo / alarm_rate / alarm_dev
        Selection            select_hi / select_lo / select_mid / first_good
        Valve                valve_eq_pct / valve_quick_open / valve_installed
        Bit                  get_bit / set_bit / clear_bit / toggle_bit / test_bits
        Unit Conversion      temp_convert / press_convert / flow_sq_root
        Timing               pulse / blink / ramp
        Data                 buffer_push / peak_detect / time_avg
        Statistical          avg / std_dev / median
        Safety / Voting      vote_2oo3 / vote_1oo2 / watchdog
        Math                 poly / heat_duty
        Other / Plugin-Registered
    Examples
        Filtered PID input
        Trip with deadband
        Tag-driven setpoint hand-off

Search box at the top filters tree entries by name + description.
Function entries pull signature + docstring from the live function
objects via :mod:`inspect`, so plugin-registered helpers
(:func:`register_script_function`) appear automatically without
touching this file.
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import html
import inspect
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QPushButton, QSplitter, QTextBrowser,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)


# ───────────────────────────────────────────────────────────────────────
# Static section content
# ───────────────────────────────────────────────────────────────────────

_CSS = """
<style>
  body  { font-family: 'Segoe UI', sans-serif; color: #0E3260; }
  h2    { color: #0E3260; border-bottom: 2px solid #5B8BA0;
          padding-bottom: 4px; margin-top: 0; }
  h3    { color: #1A4F8B; margin-top: 18px; margin-bottom: 4px; }
  code  { background: #ECEFF4; padding: 1px 4px;
          font-family: Consolas, 'Courier New', monospace;
          color: #7B2D8C; border-radius: 2px; }
  pre   { background: #FAFBFD; border: 1px solid #B0B8C8;
          padding: 8px 12px; border-radius: 3px;
          font-family: Consolas, 'Courier New', monospace;
          font-size: 9pt; color: #0E3260; }
  table { border-collapse: collapse; margin: 8px 0; }
  th    { background: #E3F2FD; color: #0E3260; font-weight: bold;
          padding: 4px 10px; border: 1px solid #B0B8C8; text-align: left; }
  td    { padding: 3px 10px; border: 1px solid #B0B8C8; }
  kbd   { background: #FFFFFF; border: 1px solid #B0B8C8; border-radius: 3px;
          padding: 0 4px; font-family: Consolas, monospace;
          color: #1A4F8B; font-size: 9pt; }
  .sig    { font-family: Consolas, monospace; color: #1A4F8B; }
  .lead   { color: #555; font-size: 10pt; }
  .warn   { background: #FFF4E0; border-left: 3px solid #FFA000;
            padding: 6px 10px; margin: 8px 0; }
  .info   { background: #E3F2FD; border-left: 3px solid #1976D2;
            padding: 6px 10px; margin: 8px 0; }
</style>
"""


def _html(title: str, body: str) -> str:
    return f"<html>{_CSS}<body><h2>{title}</h2>{body}</body></html>"


_SECTIONS: dict[str, tuple[str, str]] = {
    # ----- Overview -----
    "Overview": ("Overview", """
<p class='lead'>The ACT block runs operator-authored logic on every scan.
It supports two modes — a single-line <b>Expression</b> (or short
Structured-Text block) and a multi-line <b>Python Script</b>. Both
share the same variable namespace and the same library of helper
functions.</p>

<p>Use the <b>ribbon</b> at the top to insert operators, function
calls, and DCS-specific references at the cursor. The <b>Parse</b>
button (or <kbd>Alt+P</kbd>) validates the current text and reports
syntax errors with line and column.</p>

<p>Plugin authors can extend the function library at runtime via
<code>register_script_function()</code> — those helpers appear in
the <i>Functions</i> menu and in this help under <b>Other /
Plugin-Registered</b>.</p>
"""),

    # ----- Editor Modes -----
    "Editor Modes": ("Editor Modes", """
<h3>Expression Mode (Mode 1)</h3>
<p>A single Python-style expression assigned to <code>OUT1</code>,
or a small Structured-Text block with <code>IF / THEN / ELSE /
END_IF</code> and <code>:=</code> assignment.</p>
<pre>OUT1 = clamp(IN1 + IN2 * 0.5, 0, 100)

IF IN1 &gt; 50 THEN
    OUT1 := IN2 * 0.8
ELSE
    OUT1 := IN2 * 1.2
END_IF</pre>
<p>Validated by Python's safe AST evaluator — no statements, no
imports, no arbitrary attribute access.</p>

<h3>Script Mode (Mode 2)</h3>
<p>Multi-line Python compiled with <code>exec</code>, evaluated with
a restricted namespace. State persists across scans via the
<code>state</code> dict.</p>
<pre>filtered = ewma(state, 'pv', IN1, 0.2)
OUT1 = clamp(filtered, 0, 100)

tripped = rising_edge(state, 'btn', IN2 &gt; 50)
OUT2 = 100 if tripped else OUT2_PREV</pre>

<div class='info'>Switch modes with the <b>Mode</b> combo above the
ribbon. Tab/Shift-Tab indents; Enter auto-indents to the previous
line.</div>
"""),

    # ----- Variables -----
    "Variables": ("Variables", """
<h3>Inputs (read-only)</h3>
<table>
<tr><th>Name</th><th>Type</th><th>Meaning</th></tr>
<tr><td><code>IN1</code>..<code>IN8</code></td>
    <td>float / bool</td>
    <td>Block input terminal values (auto-coerced)</td></tr>
<tr><td><code>x</code>, <code>y</code>, <code>z</code></td>
    <td>float</td>
    <td>Aliases for <code>IN1</code>, <code>IN2</code>, <code>IN3</code></td></tr>
</table>

<h3>Outputs (writable)</h3>
<table>
<tr><th>Name</th><th>Type</th><th>Meaning</th></tr>
<tr><td><code>OUT1</code>..<code>OUT4</code></td>
    <td>float</td>
    <td>Block output terminals</td></tr>
<tr><td><code>OUT1_PREV</code>..<code>OUT4_PREV</code></td>
    <td>float</td>
    <td>Last scan's output values (read-only)</td></tr>
</table>

<h3>Environment</h3>
<table>
<tr><th>Name</th><th>Type</th><th>Meaning</th></tr>
<tr><td><code>dt</code></td><td>float</td>
    <td>Scan period in seconds (typically 0.1)</td></tr>
<tr><td><code>time</code></td><td>float</td>
    <td>Accumulated runtime since block went online</td></tr>
<tr><td><code>state</code></td><td>dict</td>
    <td>Persistent state — keys survive across scans</td></tr>
</table>

<h3>Mode constants</h3>
<p><code>AUTO</code>, <code>MANUAL</code> / <code>MAN</code>,
<code>CAS</code>, <code>RCAS</code>, <code>OOS</code>,
<code>LO</code>, <code>IMAN</code> &mdash; ISA controller modes.</p>

<h3>Math constants</h3>
<p><code>pi</code>, <code>e</code>, <code>inf</code>,
<code>True</code>, <code>False</code>, <code>None</code></p>
"""),

    # ----- Operators -----
    "Operators": ("Operators", """
<h3>Arithmetic</h3>
<p><code>+</code> add, <code>-</code> subtract, <code>*</code>
multiply, <code>/</code> divide, <code>//</code> floor-divide,
<code>%</code> modulo, <code>**</code> exponent.</p>

<h3>Comparison</h3>
<p><code>==</code> equal, <code>!=</code> not-equal,
<code>&lt;</code>, <code>&lt;=</code>, <code>&gt;</code>,
<code>&gt;=</code>.</p>

<div class='warn'>Inside <b>IF</b> conditions (Structured-Text mode)
a bare <code>=</code> is treated as equality — written as
<code>==</code> in Python.</div>

<h3>Logical</h3>
<p><code>and</code>, <code>or</code>, <code>not</code> — Python
lowercase. The ribbon's <b>AND / OR / NOT</b> buttons insert these
with surrounding spaces.</p>

<h3>Assignment</h3>
<p>Python: <code>OUT1 = ...</code><br>
Structured-Text: <code>OUT1 := ...</code> (converted automatically).</p>

<h3>Conditional expression (ternary)</h3>
<pre>OUT1 = 100 if IN1 &gt; 50 else 0</pre>

<h3>Bit-level (integers only)</h3>
<p><code>&amp;</code> and, <code>|</code> or, <code>^</code> xor,
<code>~</code> invert, <code>&lt;&lt;</code> shift-left,
<code>&gt;&gt;</code> shift-right. See also <code>get_bit</code>,
<code>set_bit</code>, <code>clear_bit</code>, <code>toggle_bit</code>,
<code>test_bits</code>.</p>
"""),

    # ----- Keyboard Shortcuts -----
    "Keyboard Shortcuts": ("Keyboard Shortcuts", """
<table>
<tr><th>Key</th><th>Action</th></tr>
<tr><td><kbd>Alt+P</kbd></td><td>Parse — validate current expression</td></tr>
<tr><td><kbd>Ctrl+F</kbd></td><td>Find</td></tr>
<tr><td><kbd>Ctrl+H</kbd></td><td>Replace</td></tr>
<tr><td><kbd>F3</kbd></td><td>Find Next</td></tr>
<tr><td><kbd>Ctrl+G</kbd></td><td>Go To Line</td></tr>
<tr><td><kbd>Esc</kbd></td><td>Close find bar</td></tr>
<tr><td><kbd>Ctrl+Z</kbd></td><td>Undo</td></tr>
<tr><td><kbd>Ctrl+Y</kbd></td><td>Redo</td></tr>
<tr><td><kbd>Ctrl+S</kbd></td><td>Save to file (via toolbar)</td></tr>
<tr><td><kbd>Tab</kbd></td><td>Indent (4 spaces)</td></tr>
<tr><td><kbd>Shift+Tab</kbd></td><td>Dedent</td></tr>
<tr><td><kbd>Enter</kbd></td><td>Auto-indent to previous line; +4 after <code>:</code></td></tr>
</table>

<h3>Completion popup</h3>
<p>Type 2+ characters of a known identifier (variable, helper
function, plugin function) to bring up the autocomplete popup. Use
arrow keys + Enter to accept.</p>
"""),

    # ----- DCS Functions -----
    "DCS Functions": ("DCS Functions (Ribbon)", """
<p>The <b>DCS Functions</b> ribbon group inserts Azeo-style
references at the cursor.</p>

<h3>Internal Parameter</h3>
<p>Opens a tag picker from the active store and inserts a tag read:</p>
<pre>get_tag('xmeas_9')</pre>

<h3>External Parameter</h3>
<p>Opens the same picker but inserts a tag write template with the
cursor placed at the value slot:</p>
<pre>set_tag('ctrl.TIC109.SP', |)</pre>
<div class='warn'>Writes are denied unless the active plugin's
<code>script_writable_tags</code> or
<code>script_writable_patterns</code> include the target. Refused
writes raise <code>PermissionError</code>; the ACT block catches it
and holds last-good outputs.</div>

<h3>Alias</h3>
<p>Prompts for a name and inserts it as a bare identifier — used to
reference a local variable inside a script.</p>

<h3>Named State</h3>
<p>Inserts an ISA mode constant
(<code>AUTO</code> / <code>MANUAL</code> / <code>CAS</code> /
<code>RCAS</code> / <code>ROUT</code> / <code>OOS</code> / ...).</p>

<h3>SELSTR</h3>
<p>Inserts <code>select_hi(</code> — the high-select helper. Use
<code>select_lo</code>, <code>select_mid</code>, or
<code>first_good</code> for other selectors (see Selection
category).</p>

<h3>LOGEVENT</h3>
<p>Inserts <code>log_event(</code>. Plugins that opt in (via
<code>register_script_function('log_event', fn)</code>) handle the
call; otherwise it is a no-op.</p>
"""),

    # ----- Tag I/O -----
    "Tag I/O": ("Tag I/O", """
<p>When the strategy runtime is attached, ACT scripts can read and
write any tag in the active <code>SharedDataStore</code>. Writes
are gated by an allow-list the plugin opts into.</p>

<table>
<tr><th>Function</th><th>Behaviour</th></tr>
<tr><td><code>get_tag(name, default=None)</code></td>
    <td>Read any tag. Returns <code>default</code> if the tag isn't
        present in the store snapshot.</td></tr>
<tr><td><code>set_tag(name, value)</code></td>
    <td>Queue a write via <code>store.queue_write()</code>. Raises
        <code>PermissionError</code> if <code>name</code> isn't in
        the plugin's allow-list.</td></tr>
<tr><td><code>tag_exists(name)</code></td>
    <td><code>True</code> iff the tag is in the current store
        snapshot.</td></tr>
</table>

<h3>Example</h3>
<pre>reactor_t = get_tag("xmeas_9")
if reactor_t and reactor_t &gt; 130.0:
    set_tag("ctrl.TIC109.SP", 120.0)
OUT1 = reactor_t or 0.0</pre>

<div class='info'>Allow-list patterns the plugin can declare:
<code>script_writable_tags = frozenset({"P-101.cmd_start"})</code>
or <code>script_writable_patterns = ("ctrl.*.SP", "xmv_*")</code>.</div>
"""),

    # ----- Examples -----
    "Examples": ("Examples", """
<h3>Filtered PID input with deadband</h3>
<pre># Smooth the noisy AI, ignore tiny excursions
filtered = ewma(state, 'pv_filt', IN1, alpha=0.3)
OUT1 = deadband(filtered, target=50.0, band=0.5)</pre>

<h3>Time-true trip with first-out latch</h3>
<pre># Trip if IN1 stays above 80 for 5 s; latch until manual reset
tripped = timer_on(state, 'hi_pv', IN1 &gt; 80, dt, delay=5.0)
state.setdefault('latch', False)
if tripped:
    state['latch'] = True
if IN2:                       # operator reset wired to IN2
    state['latch'] = False
OUT1 = 1.0 if state['latch'] else 0.0</pre>

<h3>Tag-driven setpoint hand-off (cross-CM)</h3>
<pre># When override controller is active (IN1 True), drive TIC109's SP
# to the override value (IN2); release otherwise.
if IN1:
    set_tag("ctrl.TIC109.SP", IN2)
OUT1 = get_tag("ctrl.TIC109.PV", 0.0)</pre>

<h3>2-out-of-3 voting for a critical interlock</h3>
<pre>vote = vote_2oo3(IN1 &gt; 0.5, IN2 &gt; 0.5, IN3 &gt; 0.5)
OUT1 = 1.0 if vote else 0.0</pre>

<h3>Square-root flow compensation</h3>
<pre># Differential-pressure flow with low-end linearisation
flow = flow_sq_root(IN1, k=120.0, low_cutoff=0.05)
OUT1 = clamp(flow, 0.0, 1500.0)</pre>
"""),
}


# ───────────────────────────────────────────────────────────────────────
# Curated function categories — match the ribbon's Functions menu
# ───────────────────────────────────────────────────────────────────────
_FN_CATEGORIES: dict[str, list[str]] = {
    "Process Control":   ["clamp", "scale", "deadband", "hysteresis",
                           "lerp", "normalize", "denormalize", "remap"],
    "Signal Processing": ["ewma", "rate_of_change", "integrate",
                           "rate_limit"],
    "PLC Logic":         ["rising_edge", "falling_edge", "sr_latch",
                           "timer_on", "timer_off", "counter"],
    "Alarm":             ["alarm_hi_lo", "alarm_rate", "alarm_dev"],
    "Selection":         ["select_hi", "select_lo", "select_mid",
                           "first_good"],
    "Valve":             ["valve_eq_pct", "valve_quick_open",
                           "valve_installed"],
    "Bit":               ["get_bit", "set_bit", "clear_bit",
                           "toggle_bit", "test_bits"],
    "Unit Conversion":   ["temp_convert", "press_convert",
                           "flow_sq_root"],
    "Timing":            ["pulse", "blink", "ramp"],
    "Data":              ["buffer_push", "peak_detect", "time_avg"],
    "Statistical":       ["avg", "std_dev", "median"],
    "Safety / Voting":   ["vote_2oo3", "vote_1oo2", "watchdog"],
    "Math":              ["poly", "heat_duty"],
}


# Per-function usage hints (one-liner example) — augments docstring
_FN_EXAMPLES: dict[str, str] = {
    "clamp":         "OUT1 = clamp(IN1, 0, 100)",
    "scale":         "v = scale(IN1, 4, 20, 0, 100)   # 4-20mA -> 0-100%",
    "deadband":      "OUT1 = deadband(IN1, target=50, band=0.5)",
    "hysteresis":    "high = hysteresis(state, 'h', IN1, rising=80, falling=70)",
    "lerp":          "OUT1 = lerp(IN1, IN2, t=0.25)",
    "normalize":     "n = normalize(IN1, 0, 100)        # to 0..1",
    "denormalize":   "v = denormalize(IN1, 0, 100)",
    "remap":         "OUT1 = remap(IN1, [0,50,100], [0,80,100])",
    "ewma":          "filtered = ewma(state, 'pv', IN1, alpha=0.2)",
    "rate_of_change": "roc = rate_of_change(state, 'r', IN1, dt)",
    "integrate":     "total = integrate(state, 't', IN1, dt, lo=0, hi=1e6)",
    "rate_limit":    "rl = rate_limit(state, 'rl', IN1, dt, rising_rate=10)",
    "rising_edge":   "pulse = rising_edge(state, 'btn', IN1 &gt; 0.5)",
    "falling_edge":  "pulse = falling_edge(state, 'btn', IN1 &gt; 0.5)",
    "sr_latch":      "out = sr_latch(state, 'L', set_val=IN1, reset_val=IN2)",
    "timer_on":      "trip = timer_on(state, 'ton', IN1 &gt; 80, dt, delay=5.0)",
    "timer_off":     "hold = timer_off(state, 'tof', IN1 &gt; 80, dt, delay=5.0)",
    "counter":       "n = counter(state, 'c', IN1 &gt; 0.5, reset=IN2, preset=10)",
    "alarm_hi_lo":   "hi, lo = alarm_hi_lo(state, 'A', IN1, hi=90, lo=10, db=0.5)",
    "alarm_rate":    "fast = alarm_rate(state, 'r', IN1, dt, limit=2.0)",
    "alarm_dev":     "hi, lo = alarm_dev(IN1, setpoint=50, dev_hi=2, dev_lo=2)",
    "select_hi":     "best = select_hi(IN1, IN2, IN3)",
    "select_lo":     "low = select_lo(IN1, IN2, IN3)",
    "select_mid":    "med = select_mid(IN1, IN2, IN3)",
    "first_good":    "fg = first_good(IN1, IN2, IN3)",
    "valve_eq_pct":  "cv = valve_eq_pct(IN1, rangeability=50)",
    "valve_quick_open": "cv = valve_quick_open(IN1)",
    "valve_installed": "cv = valve_installed(IN1, curve='equal_pct', dp_ratio=0.4)",
    "get_bit":       "b = get_bit(IN1, bit=3)",
    "set_bit":       "w = set_bit(IN1, bit=3)",
    "clear_bit":     "w = clear_bit(IN1, bit=3)",
    "toggle_bit":    "w = toggle_bit(IN1, bit=3)",
    "test_bits":     "ok = test_bits(IN1, mask=0b0110)",
    "temp_convert":  "tF = temp_convert(IN1, 'C', 'F')",
    "press_convert": "psi = press_convert(IN1, 'bar', 'psi')",
    "flow_sq_root":  "flow = flow_sq_root(IN1, k=120.0, low_cutoff=0.05)",
    "pulse":         "p = pulse(state, 'p', IN1 &gt; 0.5, dt, duration=2.0)",
    "blink":         "b = blink(state, 'b', dt, on_time=0.5, off_time=0.5)",
    "ramp":          "y = ramp(state, 'r', target=IN1, dt=dt, rate=5.0)",
    "buffer_push":   "buf = buffer_push(state, 'b', IN1, size=100)",
    "peak_detect":   "mn, mx = peak_detect(state, 'p', IN1, reset=IN2 &gt; 0.5)",
    "time_avg":      "ta = time_avg(state, 'ta', IN1, dt)",
    "avg":           "mean = avg([IN1, IN2, IN3, IN4])",
    "std_dev":       "sd = std_dev([IN1, IN2, IN3, IN4])",
    "median":        "m = median([IN1, IN2, IN3, IN4])",
    "vote_2oo3":     "trip = vote_2oo3(IN1 &gt; 50, IN2 &gt; 50, IN3 &gt; 50)",
    "vote_1oo2":     "trip = vote_1oo2(IN1 &gt; 50, IN2 &gt; 50)",
    "watchdog":      "stale = watchdog(state, 'wd', IN1, dt, timeout=5.0)",
    "poly":          "y = poly(IN1, 1.0, 0.5, 0.02)   # 1 + 0.5x + 0.02x^2",
    "heat_duty":     "Q = heat_duty(flow=IN1, cp=4.18, dt_temp=IN2)",
}


# ───────────────────────────────────────────────────────────────────────
# Per-function HTML renderer (uses live introspection)
# ───────────────────────────────────────────────────────────────────────

def _fn_html(name: str, fn: Callable) -> str:
    try:
        sig = str(inspect.signature(fn))
    except (TypeError, ValueError):
        sig = "(...)"
    doc = inspect.getdoc(fn) or "(no docstring)"
    # First line as summary, rest as details
    lines = doc.split("\n", 1)
    summary = html.escape(lines[0])
    rest = html.escape(lines[1].strip()) if len(lines) > 1 else ""
    example = _FN_EXAMPLES.get(name, "")
    parts = [
        f"<p class='sig'><b>{html.escape(name)}</b>{html.escape(sig)}</p>",
        f"<p>{summary}</p>",
    ]
    if rest:
        parts.append(f"<pre>{rest}</pre>")
    if example:
        parts.append("<h3>Example</h3>")
        parts.append(f"<pre>{example}</pre>")
    return _html(f"{name}()", "\n".join(parts))


# ───────────────────────────────────────────────────────────────────────
# Dialog
# ───────────────────────────────────────────────────────────────────────

class ExpressionHelpDialog(QDialog):
    """Modeless help reference for the expression / script editor.

    Tree on the left (searchable), rich-text browser on the right.
    Function entries are introspected at open time so plugin-registered
    helpers appear without code changes.
    """

    _LAST_GEOMETRY = None   # remember position across opens within a session

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("ACT / Expression Editor — Help")
        self.resize(960, 640)
        if ExpressionHelpDialog._LAST_GEOMETRY is not None:
            self.restoreGeometry(ExpressionHelpDialog._LAST_GEOMETRY)
        self._build()

    def closeEvent(self, ev):
        ExpressionHelpDialog._LAST_GEOMETRY = self.saveGeometry()
        super().closeEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        from azeo_control_trainer.core.presentation.dialog_layout import fit_dialog_to_screen
        fit_dialog_to_screen(self)

    # ----- UI -----
    def _build(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Search by name or description...  (Ctrl+F)")
        self._search.setStyleSheet(
            "QLineEdit { padding: 4px 8px; font-size: 10pt;"
            f" border: 1px solid {UI.border}; border-radius: 3px; }}")
        self._search.textChanged.connect(self._filter)
        search_row.addWidget(self._search, 1)
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(
            f"QPushButton {{ background: {UI.blue}; color: white;"
            " border: none; border-radius: 3px; padding: 6px 18px;"
            " font-weight: bold; }"
            " QPushButton:hover { background: #1A4F8B; }")
        close_btn.clicked.connect(self.close)
        search_row.addWidget(close_btn)
        v.addLayout(search_row)

        split = QSplitter(Qt.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setStyleSheet(
            f"QTreeWidget {{ background: #FAFBFD; border: 1px solid {UI.border};"
            " font-size: 10pt; }"
            " QTreeWidget::item { padding: 3px; }"
            f" QTreeWidget::item:hover {{ background: {UI.selection}; }}"
            f" QTreeWidget::item:selected {{ background: {UI.blue}; color: white; }}")
        self._tree.itemSelectionChanged.connect(self._on_select)
        split.addWidget(self._tree)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setStyleSheet(
            f"QTextBrowser {{ background: white; border: 1px solid {UI.border};"
            " padding: 8px; }")
        # Make the browser respond to <a href='section:Foo'> links
        self._browser.anchorClicked.connect(self._on_anchor)
        self._browser.setOpenLinks(False)
        split.addWidget(self._browser)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([260, 660])
        v.addWidget(split, 1)

        self._populate()
        # Open on Overview by default
        first = self._tree.topLevelItem(0)
        if first is not None:
            self._tree.setCurrentItem(first)

    # ----- Tree population -----
    def _populate(self):
        self._tree.clear()
        # Static sections (top-level)
        for key, (_title, _body) in _SECTIONS.items():
            it = QTreeWidgetItem([key])
            it.setData(0, Qt.UserRole, ("section", key))
            f = QFont(self._tree.font())
            f.setBold(True)
            it.setFont(0, f)
            self._tree.addTopLevelItem(it)

        # Built-in functions as a sub-tree grouped by category
        funcs = self._collect_functions()
        bif_root = QTreeWidgetItem(["Built-in Functions"])
        bif_root.setData(0, Qt.UserRole, ("section", "_builtin_root"))
        f = QFont(self._tree.font())
        f.setBold(True)
        bif_root.setFont(0, f)
        self._tree.addTopLevelItem(bif_root)

        seen: set[str] = set()
        for cat, names in _FN_CATEGORIES.items():
            cat_node = QTreeWidgetItem([cat])
            cat_node.setData(0, Qt.UserRole, ("category", cat))
            for n in names:
                if n in funcs:
                    leaf = QTreeWidgetItem([n])
                    leaf.setData(0, Qt.UserRole, ("function", n))
                    cat_node.addChild(leaf)
                    seen.add(n)
            if cat_node.childCount():
                bif_root.addChild(cat_node)
        # Plugin-registered / other helpers
        leftover = sorted(set(funcs) - seen)
        if leftover:
            other = QTreeWidgetItem(["Other / Plugin-Registered"])
            other.setData(0, Qt.UserRole, ("category", "Other"))
            for n in leftover:
                leaf = QTreeWidgetItem([n])
                leaf.setData(0, Qt.UserRole, ("function", n))
                other.addChild(leaf)
            bif_root.addChild(other)
        bif_root.setExpanded(True)
        self._tree.expandToDepth(0)

    def _collect_functions(self) -> dict[str, Callable]:
        try:
            from azeo_control_trainer.core.strategy.blocks.script_functions import (
                get_script_functions,
            )
            return get_script_functions()
        except Exception:
            return {}

    # ----- Selection / rendering -----
    def _on_select(self):
        items = self._tree.selectedItems()
        if not items:
            return
        kind, payload = items[0].data(0, Qt.UserRole) or ("", "")
        if kind == "section":
            if payload == "_builtin_root":
                self._render_builtin_overview()
            else:
                title, body = _SECTIONS.get(payload, (payload, ""))
                self._browser.setHtml(_html(title, body))
        elif kind == "category":
            self._render_category(payload)
        elif kind == "function":
            funcs = self._collect_functions()
            fn = funcs.get(payload)
            if fn is not None:
                self._browser.setHtml(_fn_html(payload, fn))

    def _render_builtin_overview(self):
        funcs = self._collect_functions()
        rows = []
        for cat, names in _FN_CATEGORIES.items():
            present = [n for n in names if n in funcs]
            if not present:
                continue
            links = ", ".join(
                f"<a href='function:{n}'><code>{n}</code></a>" for n in present)
            rows.append(f"<tr><td><b>{html.escape(cat)}</b></td>"
                          f"<td>{links}</td></tr>")
        body = (f"<p class='lead'>{len(funcs)} helper functions available in "
                  "the editor namespace. Click any name to see its signature, "
                  "docstring, and example.</p>"
                  f"<table>{''.join(rows)}</table>")
        self._browser.setHtml(_html("Built-in Functions", body))

    def _render_category(self, cat: str):
        funcs = self._collect_functions()
        names = _FN_CATEGORIES.get(cat, sorted(funcs))
        items_html = []
        for n in names:
            fn = funcs.get(n)
            if fn is None:
                continue
            try:
                sig = str(inspect.signature(fn))
            except (TypeError, ValueError):
                sig = "(...)"
            doc = (inspect.getdoc(fn) or "").split("\n", 1)[0]
            items_html.append(
                f"<h3><a href='function:{n}'>{html.escape(n)}</a>"
                f"<span class='sig'>{html.escape(sig)}</span></h3>"
                f"<p>{html.escape(doc)}</p>")
        self._browser.setHtml(_html(f"{cat} functions",
                                     "\n".join(items_html)))

    def _on_anchor(self, url):
        s = url.toString()
        if s.startswith("function:"):
            name = s[len("function:"):]
            # Select that leaf in the tree
            for cat_idx in range(self._tree.topLevelItemCount()):
                top = self._tree.topLevelItem(cat_idx)
                for ci in range(top.childCount()):
                    cat = top.child(ci)
                    for li in range(cat.childCount()):
                        leaf = cat.child(li)
                        if leaf.text(0) == name:
                            self._tree.setCurrentItem(leaf)
                            return
            # Fallback: render directly without selecting
            funcs = self._collect_functions()
            fn = funcs.get(name)
            if fn is not None:
                self._browser.setHtml(_fn_html(name, fn))
        elif s.startswith("section:"):
            key = s[len("section:"):]
            for i in range(self._tree.topLevelItemCount()):
                it = self._tree.topLevelItem(i)
                if it.text(0) == key:
                    self._tree.setCurrentItem(it)
                    return

    # ----- Search filter -----
    def _filter(self, text: str):
        needle = text.lower().strip()

        def walk(item):
            visible = False
            label = item.text(0).lower()
            self_match = needle in label
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    visible = True
            visible = visible or self_match or not needle
            item.setHidden(not visible)
            if needle and visible:
                item.setExpanded(True)
            return visible

        for i in range(self._tree.topLevelItemCount()):
            walk(self._tree.topLevelItem(i))


# ───────────────────────────────────────────────────────────────────────
# Convenience launcher used by the ribbon editor
# ───────────────────────────────────────────────────────────────────────

def open_expression_help(parent=None) -> ExpressionHelpDialog:
    """Open (or re-focus) a modeless help dialog. Idempotent: a single
    instance is kept alive across calls so the operator can keep it
    open beside the editor."""
    inst = getattr(open_expression_help, "_instance", None)
    if inst is None:
        inst = ExpressionHelpDialog(parent)
        open_expression_help._instance = inst
        # Drop the reference when the dialog is destroyed so the next
        # call rebuilds with fresh function introspection (catches any
        # plugin-registered additions).
        inst.destroyed.connect(
            lambda *_: setattr(open_expression_help, "_instance", None))
    inst.show()
    inst.raise_()
    inst.activateWindow()
    return inst
