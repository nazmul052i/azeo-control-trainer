"""Knowledge Builder — Context-sensitive help linked to specific blocks.

Honeywell-style Knowledge Builder that provides:
  - Block-specific help when right-clicking a block in the canvas
  - Terminal wiring guide showing what connects where
  - Configuration parameter reference with defaults and ranges
  - Common wiring patterns and best practices
  - Links to related blocks and typical usage

Triggered from:
  - Right-click block > "Knowledge Builder..." in canvas context menu
  - Help > "Knowledge Builder..." in menu bar
  - F2 key when a block is selected
"""
from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI

import logging

from azeo_control_trainer.core.presentation.authoring_controls import AuthoringComboBox
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget,
    QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
)

from azeo_control_trainer.core.strategy.model.block_registry import registry

log = logging.getLogger("strategy.knowledge")

_DLG_STYLE = f"""
QDialog {{
    background: {UI.chrome};
}}
QListWidget {{
    background: #FFFFFF;
    color: {UI.text};
    border: 1px solid {UI.border};
    font-size: 9pt;
    outline: none;
}}
QListWidget::item {{
    padding: 4px 8px;
    border-bottom: 1px solid #E8E9EE;
}}
QListWidget::item:selected {{
    background: {UI.blue};
    color: #FFFFFF;
}}
QListWidget::item:hover {{
    background: {UI.hover};
}}
QTextBrowser {{
    background: #FFFFFF;
    border: 1px solid {UI.border};
    font-size: 9pt;
}}
QComboBox {{
    background: #FFFFFF;
    border: 1px solid {UI.border};
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 9pt;
    color: {UI.text};
}}
"""

_HTML_STYLE = f"""
<style>
body {{ font-family: 'Segoe UI', sans-serif; color: {UI.text}; margin: 12px; }}
h1 {{ color: {UI.blue}; font-size: 16pt; border-bottom: 2px solid {UI.blue}; padding-bottom: 6px; }}
h2 {{ color: {UI.blue}; font-size: 12pt; margin-top: 16px; }}
h3 {{ color: {UI.blue}; font-size: 10pt; margin-top: 12px; }}
table {{ border-collapse: collapse; width: 100%; margin: 8px 0; }}
th {{ background: {UI.chrome}; color: {UI.blue}; font-weight: bold; padding: 6px 10px;
     border: 1px solid {UI.border}; text-align: left; font-size: 9pt; }}
td {{ padding: 5px 10px; border: 1px solid {UI.border}; font-size: 9pt; }}
tr:nth-child(even) {{ background: {UI.pane}; }}
code {{ background: {UI.chrome}; padding: 2px 5px; border-radius: 3px;
       font-family: Consolas, monospace; font-size: 9pt; }}
pre {{ background: {UI.pane}; border: 1px solid {UI.border}; border-radius: 4px;
      padding: 10px; font-family: Consolas, monospace; font-size: 9pt;
      overflow-x: auto; }}
.note {{ background: #FFF8E1; border-left: 4px solid #F9A825; padding: 8px 12px;
        margin: 8px 0; border-radius: 0 4px 4px 0; font-size: 9pt; }}
.warning {{ background: #FFEBEE; border-left: 4px solid #C62828; padding: 8px 12px;
           margin: 8px 0; border-radius: 0 4px 4px 0; font-size: 9pt; }}
.tip {{ background: #E8F5E9; border-left: 4px solid #2E7D32; padding: 8px 12px;
       margin: 8px 0; border-radius: 0 4px 4px 0; font-size: 9pt; }}
.terminal-in {{ color: #2E7D32; font-weight: bold; }}
.terminal-out {{ color: {UI.blue}; font-weight: bold; }}
.terminal-bkcal {{ color: #7B1FA2; font-weight: bold; }}
</style>
"""

# ── Block-specific knowledge articles ──
# Maps block_type -> dict with sections
_BLOCK_KNOWLEDGE: dict[str, dict] = {}


def _build_knowledge_db():
    """Build the knowledge database from registered blocks."""
    if _BLOCK_KNOWLEDGE:
        return  # already built

    # Auto-generate knowledge for every registered block
    for block_type, block_cls in registry.all_types().items():
        _BLOCK_KNOWLEDGE[block_type] = _auto_article(block_cls)

    # Add hand-crafted enrichments for key blocks
    _enrich_pid()
    _enrich_ai()
    _enrich_ao()
    _enrich_scaler()
    _enrich_expression()
    _enrich_sfc()
    # Tier 1–4 new block enrichments
    _enrich_filter()
    _enrich_deadtime()
    _enrich_integrator()
    _enrich_derivative()
    _enrich_sqrt()
    _enrich_totalizer()
    _enrich_splitter()
    _enrich_onoff()
    _enrich_ramp_soak()
    _enrich_gain_sched()
    _enrich_alarm()
    _enrich_bias()
    _enrich_transfer()
    _enrich_devctl()
    _enrich_vlvctl()
    _enrich_counter()
    _enrich_pulse()
    _enrich_lookup()
    _enrich_flow_comp()
    _enrich_statistics()
    _enrich_mux_demux()
    _enrich_data_blocks()


def _auto_article(block_cls) -> dict:
    """Auto-generate a knowledge article from block metadata."""
    try:
        temp = block_cls("_kb")
    except Exception:
        return {"overview": f"<p>{block_cls.description}</p>"}

    # Build terminal tables
    in_html = ""
    if temp.inputs:
        in_html = _terminal_table(temp.inputs, "Input")

    out_html = ""
    if temp.outputs:
        out_html = _terminal_table(temp.outputs, "Output")

    # Build config table
    cfg_html = ""
    schema = temp.get_config_schema()
    if schema:
        cfg_html = "<h2>Configuration Parameters</h2>"
        cfg_html += "<table><tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr>"
        for name, (ptype, default, desc) in schema.items():
            tname = ptype.__name__ if hasattr(ptype, '__name__') else str(ptype)
            cfg_html += f"<tr><td><code>{name}</code></td><td>{tname}</td><td>{default}</td><td>{desc}</td></tr>"
        cfg_html += "</table>"

    overview = f"<p>{block_cls.description}</p>"
    overview += f"<p><b>Category:</b> {block_cls.category.value}</p>"
    overview += f"<p><b>Block Type:</b> <code>{block_cls.block_type}</code></p>"

    return {
        "overview": overview,
        "inputs": in_html,
        "outputs": out_html,
        "config": cfg_html,
    }


def _terminal_table(terminals: dict, direction: str) -> str:
    css_class = "terminal-in" if direction == "Input" else "terminal-out"
    html = f"<h2>{direction} Terminals</h2>"
    html += "<table><tr><th>Name</th><th>Type</th><th>Default</th><th>BKCAL</th><th>Description</th></tr>"
    for name, term in terminals.items():
        bkcal = '<span class="terminal-bkcal">Yes</span>' if term.is_bkcal else ""
        html += (f'<tr><td><span class="{css_class}">{name}</span></td>'
                 f"<td>{term.data_type.value}</td>"
                 f"<td>{term.default_value}</td>"
                 f"<td>{bkcal}</td>"
                 f"<td>{term.description}</td></tr>")
    html += "</table>"
    return html


def _enrich_pid():
    """Add detailed PID knowledge."""
    if "PID" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["PID"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Basic Regulatory Loop</h3>
    <pre>
    AI (sensor) ──OUT──> PID.IN (PV)
    PID.OUT ──> SCALER.IN (0-1 → 0-100%)
    SCALER.OUT ──> AO.IN (valve)

    AO.BKCAL_OUT ──> SCALER.BKCAL_IN  (reverse)
    SCALER.BKCAL_OUT ──> PID.BKCAL_IN  (reverse)
    </pre>
    <div class="note"><b>Note:</b> BKCAL wires must be marked as <code>is_bkcal=True</code>
    in the strategy editor. They provide bumpless transfer and anti-windup.</div>

    <h3>Cascade Control</h3>
    <pre>
    Primary PID.OUT ──> Secondary PID.CAS_IN
    Secondary PID.BKCAL_OUT ──> Primary PID.BKCAL_IN
    </pre>

    <h3>Split Range</h3>
    <pre>
    PID.OUT ──> SCALER_1.IN  (0-0.5 → 100-0% heating)
    PID.OUT ──> SCALER_2.IN  (0.5-1.0 → 0-100% cooling)
    </pre>
    """
    kb["tuning"] = """
    <h2>Tuning Guide</h2>
    <table>
    <tr><th>Parameter</th><th>Effect of Increase</th><th>Typical Range</th></tr>
    <tr><td><code>GAIN (Kp)</code></td><td>Faster response, more oscillation</td><td>0.5 - 20</td></tr>
    <tr><td><code>RESET (Ti)</code></td><td>Slower integral action, less overshoot</td><td>30 - 3600 s</td></tr>
    <tr><td><code>RATE (Td)</code></td><td>More derivative kick, noise sensitive</td><td>0 - 60 s</td></tr>
    <tr><td><code>alpha</code></td><td>Derivative filter: smaller = more filtering</td><td>0.05 - 0.5</td></tr>
    </table>

    <div class="tip"><b>Tip:</b> Start with P-only control (Ti=9999, Td=0), then add integral.
    Use the Step Test tab to identify process gain and time constant.</div>

    <h3>GAIN Normalization</h3>
    <p>This system uses <b>absolute gain</b> (not percent gain):</p>
    <pre>gain_a = Kp * (PV_span / OUT_span)</pre>
    <p>Since OUT_span = 1.0 (PID outputs 0-1), the effective gain is:</p>
    <pre>Kp = |Kc_percent / 100| / PV_span</pre>

    <h3>Action</h3>
    <p><b>Direct acting:</b> PV increases → OUT increases (e.g., level control with drain valve)</p>
    <p><b>Reverse acting:</b> PV increases → OUT decreases (e.g., temperature with cooling valve)</p>
    """
    kb["best_practices"] = """
    <h2>Best Practices</h2>
    <ul>
    <li>Always wire BKCAL for bumpless initialization and anti-windup</li>
    <li>Use SCALER blocks between PID (0-1) and AO (0-100%)</li>
    <li>Set appropriate SP limits to prevent operator errors</li>
    <li>Enable <code>sp_pv_track_man</code> for smooth Manual→Auto transitions</li>
    <li>Use <code>pv_ftime</code> (PV filter) to reduce noise, not derivative</li>
    <li>Monitor <code>ARWStatus</code> — if always wound up, check valve sizing</li>
    </ul>
    """


def _enrich_ai():
    if "AI" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["AI"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <p>AI blocks read a process variable from the SharedDataStore tag.</p>
    <pre>
    Store tag (e.g. xmeas_9) ──→ AI.OUT
    AI.OUT ──→ PID.IN (PV input)
    AI.OUT ──→ SCALER.IN (if range conversion needed)
    </pre>
    <div class="note"><b>Tag:</b> Configure the <code>tag</code> parameter to match
    the process variable name in the data store (e.g., <code>xmeas_9</code> for reactor temperature).</div>

    <h3>Alarm Configuration</h3>
    <p>AI blocks support 4-level alarming (ISA-18.2):</p>
    <table>
    <tr><th>Alarm</th><th>Parameter</th><th>Priority</th></tr>
    <tr><td>High-High</td><td><code>HI_HI_LIM</code></td><td>Critical</td></tr>
    <tr><td>High</td><td><code>HI_LIM</code></td><td>High</td></tr>
    <tr><td>Low</td><td><code>LO_LIM</code></td><td>High</td></tr>
    <tr><td>Low-Low</td><td><code>LO_LO_LIM</code></td><td>Critical</td></tr>
    </table>
    """


def _enrich_ao():
    if "AO" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["AO"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <p>AO blocks write a control output to the SharedDataStore tag (typically a valve).</p>
    <pre>
    SCALER.OUT ──→ AO.IN (SP input, 0-100%)
    AO.BKCAL_OUT ──→ SCALER.BKCAL_IN (reverse, for bumpless init)
    </pre>

    <div class="warning"><b>Critical:</b> AO output range depends on plugin.
    Heater AO uses 0-1 (out_hi=1.0). TE AO uses 0-100 (out_hi=100). Check
    your <code>out_lo</code> and <code>out_hi</code> configuration.</div>

    <h3>SP Tracking</h3>
    <p>When <code>sp_pv_track_man</code> is enabled, the AO SP tracks the PV
    (actual valve position) in Manual mode for bumpless transfer to Auto.</p>
    """


def _enrich_scaler():
    if "SCALER" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["SCALER"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <p>SCALER converts between PID range (0-1) and valve range (0-100%).</p>

    <h3>Forward Path (PID → Valve)</h3>
    <pre>
    PID.OUT (0-1) ──→ SCALER.IN
    Config: in_lo=0, in_hi=1, out_lo=0, out_hi=100
    SCALER.OUT (0-100) ──→ AO.IN
    </pre>

    <h3>BKCAL Path (Valve → PID)</h3>
    <pre>
    AO.BKCAL_OUT (0-100) ──→ SCALER.BKCAL_IN
    SCALER performs inverse: (value - out_lo)/(out_hi - out_lo) * (in_hi - in_lo) + in_lo
    SCALER.BKCAL_OUT (0-1) ──→ PID.BKCAL_IN
    </pre>

    <div class="note"><b>Key:</b> The SCALER's BKCAL path must be wired for the
    3-pass BKCAL initialization in bridge.py to work correctly.</div>
    """


def _enrich_expression():
    if "EXPRESSION" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["EXPRESSION"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <p>Wire process values to IN1-IN8 inputs, and digital signals to BIN1-BIN4.</p>
    <pre>
    AI_1.OUT ──→ EXPRESSION.IN1
    AI_2.OUT ──→ EXPRESSION.IN2
    DI_1.OUT ──→ EXPRESSION.BIN1
    EXPRESSION.OUT ──→ next block
    EXPRESSION.BOUT ──→ logic block
    </pre>
    """
    kb["examples"] = """
    <h2>Expression Examples</h2>
    <table>
    <tr><th>Expression</th><th>Description</th></tr>
    <tr><td><code>IN1 * 2.5 + IN2</code></td><td>Linear combination</td></tr>
    <tr><td><code>max(IN1, IN2, IN3)</code></td><td>Maximum selector</td></tr>
    <tr><td><code>sqrt(IN1**2 + IN2**2)</code></td><td>Vector magnitude</td></tr>
    <tr><td><code>IN1 if BIN1 else IN2</code></td><td>Conditional selection</td></tr>
    <tr><td><code>clamp(IN1, 0, 100)</code></td><td>Output clamping</td></tr>
    <tr><td><code>abs(IN1 - IN2) > 5.0</code></td><td>Deviation alarm (BOUT)</td></tr>
    <tr><td><code>PREV + IN1 * dt</code></td><td>Integrator / accumulator</td></tr>
    <tr><td><code>lerp(IN1, IN2, 0.5)</code></td><td>Linear interpolation</td></tr>
    <tr><td><code>deadband(IN1 - IN2, 0.5)</code></td><td>Deadband filter</td></tr>
    </table>

    <h3>Available Functions</h3>
    <p><code>abs, min, max, round, sqrt, sin, cos, tan, asin, acos, atan, atan2,
    exp, log, log10, pow, ceil, floor, sign, clamp, deadband, lerp, ramp_limit</code></p>

    <h3>Available Constants</h3>
    <p><code>PI</code> (3.14159...), <code>E</code> (2.71828...), <code>TRUE</code>, <code>FALSE</code></p>

    <h3>Special Variables</h3>
    <p><code>dt</code> — scan period in seconds, <code>PREV</code> — previous OUT value</p>
    """


def _enrich_sfc():
    for bt in ("STEP", "INITIAL_STEP", "END_STEP", "TRANSITION"):
        if bt not in _BLOCK_KNOWLEDGE:
            continue
        kb = _BLOCK_KNOWLEDGE[bt]
        kb["wiring_guide"] = """
        <h2>SFC Wiring Guide</h2>
        <h3>Basic Sequence</h3>
        <pre>
        INITIAL_STEP ──DONE──→ TRANSITION_1.IN
        TRANSITION_1.OUT ──→ STEP_1.ACTIVATE
        STEP_1.DONE ──→ TRANSITION_2.IN
        TRANSITION_2.OUT ──→ STEP_2.ACTIVATE
        ...
        STEP_N.DONE ──→ END_STEP.ACTIVATE
        </pre>

        <h3>Step Deactivation (BKCAL)</h3>
        <pre>
        TRANSITION.OUT ──BKCAL──→ previous STEP.DEACTIVATE
        </pre>
        <div class="note">BKCAL wires from transitions deactivate the previous step
        when the transition fires.</div>
        """


def _enrich_filter():
    if "FILTER" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["FILTER"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI.OUT ──→ FILTER.IN
    FILTER.OUT ──→ PID.IN (filtered PV)
    </pre>
    <div class="tip"><b>Tip:</b> Use FILTER between the AI and PID blocks to reduce
    measurement noise. A TAU of 3–10 seconds is typical for temperature loops.
    Shorter TAU for fast loops (flow, pressure).</div>
    """
    kb["best_practices"] = """
    <h2>Best Practices</h2>
    <ul>
    <li>TAU should be &lt; 10% of the process time constant to avoid sluggish control</li>
    <li>Use BYPASS input to disable filtering during step tests or commissioning</li>
    <li>For very noisy signals, cascade two FILTER blocks rather than using extreme TAU</li>
    <li>The AI block has a built-in <code>PV_FTIME</code> filter — consider using that instead of a separate FILTER block for simple cases</li>
    </ul>
    """


def _enrich_deadtime():
    if "DEADTIME" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["DEADTIME"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Model-Based Control</h3>
    <pre>
    Process model:
    AI.OUT ──→ DEADTIME.IN
    DEADTIME.OUT ──→ (use as delayed PV in model comparison)
    </pre>
    <h3>Smith Predictor</h3>
    <pre>
    PID.OUT ──→ DEADTIME.IN (model dead time)
    DEADTIME.OUT ──→ SUMMER.IN2 (subtract from PV)
    </pre>
    <div class="note"><b>Note:</b> DEADTIME uses a ring buffer internally. Memory usage
    scales with DELAY/scan_period. A 60s delay at 100ms scan = 600 samples.</div>
    """


def _enrich_integrator():
    if "INTEGRATOR" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["INTEGRATOR"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Flow Totalizer (simple)</h3>
    <pre>
    AI (flow rate) ──→ INTEGRATOR.IN
    INTEGRATOR.OUT ──→ (accumulated volume)
    </pre>
    <h3>Custom Ramp Generator</h3>
    <pre>
    CONSTANT (slope) ──→ INTEGRATOR.IN
    INTEGRATOR.OUT ──→ PID.SP (ramping setpoint)
    </pre>
    <div class="tip"><b>Tip:</b> Set HI_LIMIT and LO_LIMIT to prevent runaway accumulation.
    Use HOLD input to freeze the integrator during abnormal conditions.
    METHOD=TRAP (trapezoidal) is more accurate than RECT (rectangular).</div>
    """


def _enrich_derivative():
    if "DERIVATIVE" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["DERIVATIVE"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI.OUT ──→ DERIVATIVE.IN
    DERIVATIVE.OUT ──→ ALARM.PV (rate-of-change monitoring)
    </pre>
    <div class="warning"><b>Warning:</b> Raw derivatives amplify noise. Always set
    TAU &gt; 0 to filter the output. A TAU of 1–5 seconds is typical.</div>
    """


def _enrich_sqrt():
    if "SQRT" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["SQRT"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>DP Flow Transmitter</h3>
    <pre>
    AI (dP) ──→ SQRT.IN
    SQRT.OUT ──→ PID.IN (linearized flow)
    </pre>
    <div class="note"><b>Low-Cutoff:</b> Below LOW_CUTOFF (default 1%), the output is
    forced to zero. This prevents noise amplification at very low flows where
    √(small number) creates erratic readings.</div>

    <h3>Alternative</h3>
    <p>The SCALER block has a built-in <code>sqrt_enable</code> option that combines
    square root extraction with range conversion in a single block.</p>
    """


def _enrich_totalizer():
    if "TOTALIZER" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["TOTALIZER"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI (flow rate, e.g. m³/hr) ──→ TOTALIZER.IN
    Config: FACTOR = 1/3600  (convert hr⁻¹ to s⁻¹)
    TOTALIZER.OUT ──→ (accumulated volume in m³)
    </pre>
    <div class="tip"><b>Tip:</b> FACTOR converts the flow rate time base to per-second.
    For flow in units/hr, use FACTOR = 1/3600. For units/min, use 1/60.
    HI_LIMIT causes rollover (like an odometer) — set to batch size for batch totals.</div>
    """


def _enrich_splitter():
    if "SPLITTER" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["SPLITTER"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Split-Range Heating/Cooling</h3>
    <pre>
    PID.OUT (0-1) ──→ SCALER.IN (0-1 → 0-100%)
    SCALER.OUT ──→ SPLITTER.IN

    SPLITTER.OUT1 ──→ AO_HEAT.IN  (100% at IN=0, 0% at IN=50)
    SPLITTER.OUT2 ──→ AO_COOL.IN  (0% at IN=50, 100% at IN=100)
    </pre>
    <div class="note"><b>Default Config:</b> SPLIT_PT=50% with REVERSE_1=True gives
    standard heating/cooling split range. OUT1 decreases as input rises (heating
    valve closes), OUT2 increases above split point (cooling valve opens).</div>

    <h3>Configurable Breakpoints</h3>
    <table>
    <tr><th>Parameter</th><th>Default</th><th>Description</th></tr>
    <tr><td>SPLIT_PT</td><td>50%</td><td>Input value where control transitions from OUT1 to OUT2</td></tr>
    <tr><td>REVERSE_1</td><td>True</td><td>OUT1 decreases as input increases (typical for heating)</td></tr>
    <tr><td>REVERSE_2</td><td>False</td><td>OUT2 increases as input increases (typical for cooling)</td></tr>
    </table>
    """


def _enrich_onoff():
    if "ONOFF" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["ONOFF"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI.OUT ──→ ONOFF.PV
    SETPOINT.OUT ──→ ONOFF.SP
    ONOFF.OUT ──→ DO.IN (discrete output)
    ONOFF.OUT_FLOAT ──→ AO.IN (analog output: 0 or 100%)
    </pre>
    <div class="tip"><b>Tip:</b> Use DEADBAND to prevent rapid cycling.
    A deadband of 1-2% of span is typical. Set REVERSE=True for cooling applications
    (output ON when PV > SP).</div>
    """


def _enrich_ramp_soak():
    if "RAMP_SOAK" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["RAMP_SOAK"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Furnace Temperature Profile</h3>
    <pre>
    RAMP_SOAK.OUT ──→ PID.SP (setpoint from profile)
    DI (start button) ──→ RAMP_SOAK.START
    DI (stop button) ──→ RAMP_SOAK.STOP
    DI (reset button) ──→ RAMP_SOAK.RESET
    </pre>
    <div class="note"><b>Profile Example (3 segments):</b><br>
    Ramp 1: 25°C → 100°C at 2°C/min, then soak 60s<br>
    Ramp 2: 100°C → 200°C at 5°C/min, then soak 120s<br>
    Ramp 3: 200°C → 25°C at 3°C/min (cool-down), soak 60s</div>
    """
    kb["best_practices"] = """
    <h2>Best Practices</h2>
    <ul>
    <li>Set START_VALUE to match the current process temperature before starting</li>
    <li>Use RAMP_RATE in <b>units/min</b> (not units/sec)</li>
    <li>Monitor RUNNING and COMPLETE outputs for HMI status display</li>
    <li>Wire STOP to an interlock for safety shutdown</li>
    <li>Up to 8 ramp-soak pairs — set N_SEGMENTS accordingly</li>
    </ul>
    """


def _enrich_gain_sched():
    if "GAIN_SCHED" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["GAIN_SCHED"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>PV-Based Gain Scheduling</h3>
    <pre>
    AI (PV) ──→ GAIN_SCHED.SCHED_VAR
    GAIN_SCHED.KP ──→ (write to PID gain via store)
    GAIN_SCHED.TI ──→ (write to PID Ti via store)
    </pre>
    <div class="note"><b>Usage:</b> Configure 2-5 breakpoints mapping a scheduling
    variable (e.g., load, temperature, flow) to PID tuning parameters. The block
    linearly interpolates between breakpoints.</div>

    <h3>Typical Application</h3>
    <p>pH control where process gain varies dramatically across the pH range.
    Schedule aggressive tuning near pH 7 and conservative tuning at extremes.</p>
    """


def _enrich_alarm():
    if "ALARM" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["ALARM"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI.OUT ──→ ALARM.PV
    SETPOINT.OUT ──→ ALARM.SP (for deviation alarms)
    DI (operator shelve) ──→ ALARM.SHELVE
    ALARM.ANY_ALARM ──→ DO (alarm annunciator)
    </pre>

    <h3>Alarm Types</h3>
    <table>
    <tr><th>Output</th><th>Condition</th><th>Enable</th></tr>
    <tr><td>HI_HI</td><td>PV ≥ HI_HI_LIM</td><td>HH_EN</td></tr>
    <tr><td>HI</td><td>PV ≥ HI_LIM</td><td>HI_EN</td></tr>
    <tr><td>LO</td><td>PV ≤ LO_LIM</td><td>LO_EN</td></tr>
    <tr><td>LO_LO</td><td>PV ≤ LO_LO_LIM</td><td>LL_EN</td></tr>
    <tr><td>DEV_HI</td><td>PV - SP &gt; DEV_HI</td><td>DEV_EN</td></tr>
    <tr><td>DEV_LO</td><td>SP - PV &gt; DEV_LO</td><td>DEV_EN</td></tr>
    <tr><td>ROC</td><td>|dPV/dt| &gt; ROC_LIM</td><td>ROC_EN</td></tr>
    </table>

    <div class="tip"><b>ISA-18.2:</b> All alarms use deadband for return-to-normal.
    The SHELVE input suppresses all alarm outputs (for maintenance).</div>
    """


def _enrich_bias():
    if "BIAS" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["BIAS"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Feedforward Bias</h3>
    <pre>
    PID.OUT ──→ BIAS.IN
    Feedforward calc ──→ BIAS.BIAS_IN
    BIAS.OUT ──→ SCALER.IN (or AO)
    </pre>
    <div class="tip"><b>Tip:</b> BIAS has a BKCAL_OUT terminal for cascade initialization.
    Wire it to maintain the BKCAL chain when inserting a BIAS block between PID and AO.</div>
    """


def _enrich_transfer():
    if "TRANSFER" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["TRANSFER"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <h3>Redundant Controller Switchover</h3>
    <pre>
    PID_Primary.OUT ──→ TRANSFER.IN1
    PID_Backup.OUT ──→ TRANSFER.IN2
    DI (switchover) ──→ TRANSFER.SELECT
    TRANSFER.OUT ──→ SCALER.IN
    </pre>
    <div class="note"><b>Bumpless:</b> RAMP_RATE controls how fast the output transitions
    between sources. Set to 0 for instant switchover (e.g., safety applications).
    TRACKING output indicates transfer is in progress.</div>
    """


def _enrich_devctl():
    if "DEVCTL" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["DEVCTL"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    DI (start button) ──→ DEVCTL.START_CMD
    DI (stop button) ──→ DEVCTL.STOP_CMD
    DI (interlock OK) ──→ DEVCTL.INTERLOCK
    DI (motor running FB) ──→ DEVCTL.RUN_FB
    DI (fault reset) ──→ DEVCTL.RESET
    DEVCTL.DO_START ──→ DO (motor contactor)
    </pre>

    <h3>State Machine</h3>
    <table>
    <tr><th>State</th><th>Value</th><th>Description</th></tr>
    <tr><td>STOPPED</td><td>0</td><td>Motor off, ready to start</td></tr>
    <tr><td>STARTING</td><td>1</td><td>Waiting for RUN_FB (timeout → FAULTED)</td></tr>
    <tr><td>RUNNING</td><td>2</td><td>Motor confirmed running</td></tr>
    <tr><td>STOPPING</td><td>3</td><td>Waiting for RUN_FB to drop (timeout → FAULTED)</td></tr>
    <tr><td>FAULTED</td><td>4</td><td>Fault — requires RESET</td></tr>
    </table>

    <div class="warning"><b>Interlock:</b> If INTERLOCK goes false while running or starting,
    the device immediately transitions to STOPPING. This is a safety-critical input.</div>
    """


def _enrich_vlvctl():
    if "VLVCTL" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["VLVCTL"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    DI (open cmd) ──→ VLVCTL.OPEN_CMD
    DI (close cmd) ──→ VLVCTL.CLOSE_CMD
    DI (interlock) ──→ VLVCTL.INTERLOCK
    AI (position FB) ──→ VLVCTL.POS_FB  (set to -1 for simulated position)
    VLVCTL.DO_OPEN ──→ DO (open solenoid)
    VLVCTL.DO_CLOSE ──→ DO (close solenoid)
    </pre>

    <div class="note"><b>Simulated Position:</b> When POS_FB = -1 (no field feedback),
    the block simulates valve travel based on STROKE_TIME.
    FAIL_ACTION (CLOSE or OPEN) determines valve position on interlock trip.</div>
    """


def _enrich_counter():
    if "COUNTER" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["COUNTER"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    POS_EDGE.OUT ──→ COUNTER.CU (count rising edges)
    DI (reset) ──→ COUNTER.RESET
    COUNTER.QU ──→ (reached preset count)
    COUNTER.CV ──→ (current count value)
    </pre>
    <div class="tip"><b>Tip:</b> Use POS_EDGE or NEG_EDGE blocks upstream to convert
    continuous signals into edge triggers. The counter only increments on
    rising edges of CU/CD inputs.</div>
    """


def _enrich_pulse():
    if "PULSE" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["PULSE"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    DI (trigger) ──→ PULSE.IN
    PULSE.OUT ──→ DO (solenoid pulse) or TIMER input
    </pre>
    <div class="note"><b>Behavior:</b> On rising edge of IN, OUT goes true for PT seconds,
    then returns false. Re-triggering during an active pulse is ignored.
    Use for timed solenoid pulses, batch additions, or alarm acknowledge signals.</div>
    """


def _enrich_lookup():
    if "LOOKUP" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["LOOKUP"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI.OUT ──→ LOOKUP.IN (e.g., thermocouple mV)
    LOOKUP.OUT ──→ (linearized temperature)
    </pre>
    <h3>Typical Uses</h3>
    <ul>
    <li>Thermocouple linearization (mV → °C)</li>
    <li>Valve characteristic correction (% → Cv)</li>
    <li>Non-linear sensor calibration</li>
    <li>Custom gain scheduling curves</li>
    </ul>
    <div class="tip"><b>Tip:</b> For simple linear curves, use SIGNAL_CHAR instead.
    LOOKUP supports up to 10 (X,Y) breakpoints with linear interpolation.
    Values outside the table range are clamped to the nearest endpoint.</div>
    """


def _enrich_flow_comp():
    if "FLOW_COMP" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["FLOW_COMP"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI (raw flow) ──→ FLOW_COMP.FLOW
    AI (pressure) ──→ FLOW_COMP.PRESSURE
    AI (temperature) ──→ FLOW_COMP.TEMPERATURE
    FLOW_COMP.OUT ──→ PID.IN (compensated flow)
    </pre>

    <h3>Compensation Formula</h3>
    <pre>FLOW_COMP = FLOW × √((P_actual / P_design) × (T_design / T_actual))</pre>

    <div class="note"><b>Units:</b> Temperature must be in absolute units (Kelvin or Rankine).
    If your AI reads in °C, add 273.15 using a SUMMER block before FLOW_COMP.
    COMP_TYPE can be "P" (pressure only), "T" (temperature only), or "PT" (both).</div>
    """


def _enrich_statistics():
    if "STATISTICS" not in _BLOCK_KNOWLEDGE:
        return
    kb = _BLOCK_KNOWLEDGE["STATISTICS"]
    kb["wiring_guide"] = """
    <h2>Wiring Guide</h2>
    <pre>
    AI.OUT ──→ STATISTICS.IN
    STATISTICS.MEAN ──→ (average value display)
    STATISTICS.STD_DEV ──→ COMPARATOR.IN1 (variability alarm)
    STATISTICS.MIN/MAX ──→ (range monitoring)
    </pre>
    <div class="tip"><b>Tip:</b> Use N (window size) × scan_period to determine
    the time window. E.g., N=100 at 1s scan = 100 second rolling window.
    STD_DEV uses Bessel's correction (N-1 denominator) for unbiased estimation.</div>
    """


def _enrich_mux_demux():
    for bt in ("MUX", "DEMUX"):
        if bt not in _BLOCK_KNOWLEDGE:
            continue
        kb = _BLOCK_KNOWLEDGE[bt]
        if bt == "MUX":
            kb["wiring_guide"] = """
            <h2>Wiring Guide</h2>
            <pre>
            AI_1.OUT ──→ MUX.IN1
            AI_2.OUT ──→ MUX.IN2
            AI_3.OUT ──→ MUX.IN3
            COUNTER.CV ──→ MUX.INDEX (1-based)
            MUX.OUT ──→ (selected AI value)
            </pre>
            <div class="note"><b>Index:</b> 1-based (1 selects IN1, 2 selects IN2, etc.).
            Out-of-range index sets VALID=False, output holds last value.</div>
            """
        else:
            kb["wiring_guide"] = """
            <h2>Wiring Guide</h2>
            <pre>
            PID.OUT ──→ DEMUX.IN
            SEQ_TIMER.STEP ──→ DEMUX.INDEX (1-based)
            DEMUX.OUT1 ──→ AO_1.IN
            DEMUX.OUT2 ──→ AO_2.IN
            </pre>
            <div class="note"><b>Behavior:</b> The selected output receives the input value.
            Non-selected outputs retain their last value. Use CLEAR to zero all outputs.</div>
            """


def _enrich_data_blocks():
    for bt in ("FIFO", "LIFO"):
        if bt not in _BLOCK_KNOWLEDGE:
            continue
        kb = _BLOCK_KNOWLEDGE[bt]
        order = "oldest first (queue)" if bt == "FIFO" else "newest first (stack)"
        kb["wiring_guide"] = f"""
        <h2>Wiring Guide</h2>
        <pre>
        AI.OUT ──→ {bt}.IN
        POS_EDGE.OUT ──→ {bt}.PUSH (rising edge adds value)
        POS_EDGE.OUT ──→ {bt}.POP  (rising edge removes value)
        {bt}.OUT ──→ (read {order})
        </pre>
        <div class="note"><b>Edge-Triggered:</b> PUSH and POP operate on rising edges only.
        DEPTH configures maximum buffer size. FULL/EMPTY outputs indicate buffer state.</div>
        """

    if "DATALOG" in _BLOCK_KNOWLEDGE:
        kb = _BLOCK_KNOWLEDGE["DATALOG"]
        kb["wiring_guide"] = """
        <h2>Wiring Guide</h2>
        <pre>
        COMPARATOR.OUT ──→ DATALOG.TRIGGER (log on alarm)
        AI_1.OUT ──→ DATALOG.IN1
        AI_2.OUT ──→ DATALOG.IN2
        PID.OUT ──→ DATALOG.IN3
        DATALOG.SNAP_COUNT ──→ (total snapshots taken)
        </pre>
        <div class="tip"><b>Tip:</b> Wire TRIGGER to an alarm condition or periodic
        timer to capture process snapshots. Last captured values are available
        on LAST_1 through LAST_4 outputs.</div>
        """

    if "BIT_PACK" in _BLOCK_KNOWLEDGE:
        kb = _BLOCK_KNOWLEDGE["BIT_PACK"]
        kb["wiring_guide"] = """
        <h2>Wiring Guide</h2>
        <pre>
        DI_0.OUT ──→ BIT_PACK.BIT_0
        DI_1.OUT ──→ BIT_PACK.BIT_1
        ...
        BIT_PACK.WORD ──→ (16-bit packed integer)
        </pre>
        <div class="note"><b>Bit Order:</b> BIT_0 is the least significant bit (value 1),
        BIT_15 is the most significant (value 32768). Use with BIT_UNPACK for
        packing/unpacking status words for communication blocks.</div>
        """

    if "MSG" in _BLOCK_KNOWLEDGE:
        kb = _BLOCK_KNOWLEDGE["MSG"]
        kb["wiring_guide"] = """
        <h2>Wiring Guide</h2>
        <pre>
        ALARM.HI ──→ MSG.TRIGGER
        AI.OUT ──→ MSG.VALUE
        Config: TEXT = "High temperature alarm"
        </pre>
        <div class="note"><b>Edge-Triggered:</b> Message is sent on rising edge of TRIGGER only.
        Configure TEXT for the message template. If INCLUDE_VALUE is true, the current
        VALUE is appended to the message text. Messages are logged at INFO level.</div>
        """


class KnowledgeBuilderDialog(QDialog):
    """Context-sensitive block help dialog."""

    def __init__(self, block_type: str | None = None, parent=None):
        super().__init__(parent)
        self._current_type = block_type
        self.setWindowTitle("Knowledge Builder")
        self.setMinimumSize(850, 550)
        self.resize(1000, 650)
        self.setStyleSheet(_DLG_STYLE)

        _build_knowledge_db()
        self._build_ui()

        if block_type:
            self._select_block_type(block_type)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        hdr.setContentsMargins(8, 4, 8, 4)

        title = QLabel("Knowledge Builder")
        title.setStyleSheet(
            f"font-size: 11pt; font-weight: bold; color: {UI.blue};")
        hdr.addWidget(title)
        hdr.addStretch()

        # Block type selector
        hdr.addWidget(QLabel("Block:"))
        self._cmb_block = AuthoringComboBox()
        self._cmb_block.setFixedWidth(200)
        all_types = sorted(registry.all_types().keys())
        self._cmb_block.addItems(all_types)
        self._cmb_block.currentTextChanged.connect(self._on_block_changed)
        hdr.addWidget(self._cmb_block)

        layout.addLayout(hdr)

        # Splitter: section list | content
        splitter = QSplitter(Qt.Horizontal)

        # Section list
        self._section_list = QListWidget()
        self._section_list.setFixedWidth(180)
        self._section_list.currentRowChanged.connect(self._on_section_changed)
        splitter.addWidget(self._section_list)

        # Content browser
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        splitter.addWidget(self._browser)

        splitter.setSizes([180, 800])
        layout.addWidget(splitter, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(
            f"QPushButton {{ background: {UI.chrome}; color: {UI.blue}; "
            f"border: 1px solid {UI.border}; border-radius: 3px; "
            "font-size: 9pt; padding: 5px 16px; font-weight: bold; }"
            f"QPushButton:hover {{ background: {UI.chrome_alt}; }}")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _select_block_type(self, block_type: str):
        idx = self._cmb_block.findText(block_type)
        if idx >= 0:
            self._cmb_block.setCurrentIndex(idx)

    def _on_block_changed(self, block_type: str):
        self._current_type = block_type
        kb = _BLOCK_KNOWLEDGE.get(block_type, {})

        # Populate section list
        self._section_list.clear()
        self._sections = []

        # Standard sections
        section_order = [
            ("overview", "Overview"),
            ("inputs", "Input Terminals"),
            ("outputs", "Output Terminals"),
            ("config", "Configuration"),
            ("wiring_guide", "Wiring Guide"),
            ("tuning", "Tuning Guide"),
            ("examples", "Examples"),
            ("best_practices", "Best Practices"),
        ]
        for key, label in section_order:
            if key in kb and kb[key]:
                self._section_list.addItem(label)
                self._sections.append(kb[key])

        if self._section_list.count() > 0:
            self._section_list.setCurrentRow(0)

    def _on_section_changed(self, row: int):
        if 0 <= row < len(self._sections):
            block_cls = registry.get(self._current_type)
            title = block_cls.display_name if block_cls else self._current_type
            html = f"{_HTML_STYLE}<h1>{title}</h1>{self._sections[row]}"
            self._browser.setHtml(html)
