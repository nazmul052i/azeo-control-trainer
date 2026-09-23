"""Control Designer Help dialog — comprehensive designer reference.

Covers block types, keyboard shortcuts, design patterns, SFC concepts,
equipment modules, strategy compilation, and BKCAL wiring.
"""

from __future__ import annotations

from azeo_control_trainer.core.presentation.brand import UI
from azeo_control_trainer.core.presentation.help_style import (
    HELP_HTML_STYLE as _HTML_STYLE,
    styled_help_html as styled_help_html,  # re-exported for SFC editor help
)

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
)

from azeo_control_trainer.config.paths import project_root

# ── Help topics ───────────────────────────────────────────────────────

_TOPICS = [
    (
        "Getting Started",
        _GETTING_STARTED := """
<h2>Getting Started with Control Designer</h2>

<h3>What is Control Designer?</h3>
<p>Control Designer is a function-block diagram (FBD) strategy designer modeled
for industrial control-module engineering and operator training.
It allows you to design, compile, download, and monitor control strategies
in real time.</p>

<h3>Workflow</h3>
<ol>
<li><b>Create or Open</b> a strategy (File &rarr; New / Open)</li>
<li><b>Drag blocks</b> from the Block Palette onto the canvas</li>
<li><b>Wire blocks</b> by clicking an output terminal then an input terminal</li>
<li><b>Configure blocks</b> by selecting them &mdash; properties appear in the right panel</li>
<li><b>Compile</b> (F7) to validate and determine execution order</li>
<li><b>Download / Go Online</b> (F5) to run the strategy against live process data</li>
<li><b>Monitor</b> live values on block overlays and wire labels</li>
</ol>

<h3>Key Concepts</h3>
<table>
<tr><th>Concept</th><th>Description</th></tr>
<tr><td>Strategy</td><td>A collection of blocks and wires that implements a control algorithm</td></tr>
<tr><td>Block</td><td>A function unit (AI, AO, PID, SCALER, etc.) with input/output terminals</td></tr>
<tr><td>Wire</td><td>A connection from one block's output to another block's input</td></tr>
<tr><td>Compile</td><td>Validates the strategy graph, detects cycles, and computes execution order</td></tr>
<tr><td>Download</td><td>Loads the compiled strategy into the runtime for live execution</td></tr>
<tr><td>BKCAL Wire</td><td>Back-calculation wire for bumpless transfer (PID, SCALER feedback)</td></tr>
</table>

<h3>Project Explorer hierarchy</h3>
<p>Control modules are organized by their process ownership:</p>
<p><b>Project &rarr; Area &rarr; Units &rarr; Unit &rarr; Control Modules</b>.</p>
<p>Create or import a module from a unit's context menu to assign it there.
Right-click an existing module and choose <b>Move to Unit</b> to change that
assignment. A module that is not named in any configured unit remains visible
under <b>Units &rarr; Unassigned &rarr; Control Modules</b>; this does not change
its controller-node assignment or runtime identity.</p>
""",
    ),
    (
        "Engineering Workspace",
        """
<h2>Engineering Workspace</h2>
<p>The lower-left workspace follows the active module. It is not a second
copy of the project: every row and edit is backed by the same graph shown on
the canvas.</p>

<h3>Blocks</h3>
<ul>
<li>Drag a function block onto the canvas, or double-click to place it at the
view centre.</li>
<li>Right-click a block and choose <b>Add to Custom Palette</b> to collect the
standards used by a project or discipline.</li>
<li>Use the palette <b>+</b> menu to create, rename, or remove a custom palette,
and to add a saved multi-block template.</li>
<li>A template dropped on the canvas keeps the pointer coordinate and is one
undoable operation, including all of its internal wires.</li>
</ul>

<h3>Hierarchy</h3>
<p>The hierarchy shows the active module and its blocks, including composite
structure. Activating a row selects the real canvas item, centres it in the
view, and opens its Properties. Use this view to navigate a large module
without searching visually.</p>

<h3>Alarms</h3>
<p>The alarm view lists configured limits and the live alarm state. Activating
a row reveals its owning block. Editable limits are normalized through the
block's configuration schema and recorded on the normal undo stack; the
module becomes unsaved and changed-since-download exactly like a Properties
edit.</p>

<h3>Problems</h3>
<p>Problems validates the active module continuously. Double-click a finding
to reveal its block. <b>Quick Fix</b> offers only actions whose preconditions
still hold; structural fixes and parameter corrections use the normal undo
stack. Tag and required-input findings navigate to the exact block or pin
instead of inventing an unsafe field assignment.</p>

<h3>Module Report</h3>
<p>Choose <b>Module &rarr; Module Report</b> for an engineering record of the
active module. The report includes identity, configured and default values,
live value/unit/quality/limit/force state, hierarchy, alarms, execution order,
scan statistics, and validation results. It can be exported as HTML or PDF.</p>
""",
    ),
    (
        "Fast Diagram Authoring",
        """
<h2>Fast Diagram Authoring</h2>
<h3>Smart wiring and insertion</h3>
<p>Start a wire from any pin. Legal destinations turn green, incompatible or
occupied destinations turn red and explain why, and a nearby legal pin snaps
magnetically. A pin tooltip always includes its data type, units, and known
range. Drop the loose wire on empty canvas to search only blocks that can
complete that connection; the block and wire are one undo operation.</p>

<h3>Command palette and multiple properties</h3>
<p>Press <b>Ctrl+Shift+P</b> to fuzzy-search application commands and all
registered block types. When several blocks are selected, Properties shows
their common parameters. A mixed value is explicit; committing one value
updates the whole selection as one undo step.</p>

<h3>Connection refactoring</h3>
<p>Right-click a wire to insert a compatible block, reconnect either endpoint,
or start a branch from its source. Right-click a simple one-input/one-output
block to remove it and heal the connection. These commands are unavailable
online and are each atomic in Undo.</p>

<h3>Composites, loop wizard, and navigation</h3>
<p>An interior composite tab shows a breadcrumb back to its parent and a
<b>Ports</b> editor. Select an unconnected inner pin to expose it at the
boundary. The command palette's Guided Control Loop Wizard generates expanded
single-PID, cascade, or override patterns with scaling and BKCAL wiring.
Open Diagram Navigator (<b>Ctrl+Alt+M</b>) for a minimap, block list, and named
view bookmarks on large modules.</p>
""",
    ),
    (
        "Deployment and Bulk Engineering",
        """
<h2>Deployment Diff and Bulk Engineering</h2>

<h3>Review deployment impact</h3>
<p>Choose <b>Module &rarr; Deploy Impact</b>, or search for the command with
<b>Ctrl+Shift+P</b>. Each open module is compared with the detached snapshot
captured by its last successful download. The review separates controller
structure, parameter/runtime, drawing-only, and documentation changes. A
structure change explicitly reports that a re-download is required.</p>
<p>The Dependency Impact page walks downstream signal paths from changed blocks.
It also reports other open modules that share a changed field tag. Double-click
an affected row to open and select the real block. A module with no prior
download is shown as a new deployment rather than as an unchanged module.</p>

<h3>Generate modules from a template</h3>
<ol>
<li>Prepare and save a source module. String properties may contain the explicit
tokens <code>{{MODULE}}</code> and <code>{{PREFIX}}</code>.</li>
<li>Choose <b>Tools &rarr; Bulk</b>, select the source and destination area, and
enter CSV rows using <code>module,prefix</code>.</li>
<li>Choose <b>Preview and Validate</b>. Generated modules get new deterministic
block identities, unresolved tokens are refused, and every candidate must
compile.</li>
<li>Choose <b>Apply</b>. Existing paths are never overwritten. All module files
are committed together and registered in the selected project area.</li>
</ol>
<p>A linked class instance is deliberately refused as a bulk-generation source.
Create additional governed instances through <b>Tools &rarr; Classes</b>; cloning
and remapping its block identities would sever valid class comparison.</p>

<h3>Controlled bulk edit</h3>
<p>Use CSV columns <code>module,block,parameter,value</code>. A block may be
addressed by instance name or UUID. Use <code>@module</code> for
<code>description</code> or <code>scan_ms</code>. The preview resolves the real
block configuration schema, converts the value to its declared type, validates
named choices, and compiles every affected module. Unknown fields, duplicate
targets, online modules, and unsaved modules are refused. Apply is transactional:
if any replacement fails, earlier files are restored.</p>
""",
    ),
    (
        "Control Module Classes",
        """
<h2>Class-based Control Modules</h2>
<p>Choose <b>Tools &rarr; Classes</b>, or search for <b>Control Module
Classes</b> with <b>Ctrl+Shift+P</b>. A class is a stable, revisioned source
definition. A linked module embeds the adopted definition and remains a normal,
independently downloadable module; the controller never needs the engineering
class library at runtime.</p>

<h3>Create a class</h3>
<ol>
<li>Open the finished source module and take it offline.</li>
<li>Choose <b>Create Class from Active</b>, name the class, and select only the
properties instances may override. Available properties come from real module
parameters and installed block schemas.</li>
<li>Publish. The source module becomes the first linked instance at revision 1.
The class stores typed defaults and a content digest.</li>
<li>Choose <b>Create Linked Instance</b>. One guided window assigns the module
name, project area and process unit, then exposes the class-approved typed
overrides. Choose <b>Create Linked Module</b> after reviewing the destination.
The new module is compiled, written without overwriting an existing path, and
registered under the selected unit.</li>
</ol>

<h3>Revisions, overrides, and deviations</h3>
<p><b>Instance Properties</b> changes only explicitly public properties. Those
values are retained when a newer class revision is adopted. Direct edits to
other module structure, configuration, runtime settings, or drawing geometry
are reported as instance deviations; they are never disguised as overrides.</p>
<p><b>Publish Active as New Revision</b> validates and compiles a successor but
does not silently rewrite instances. Each instance then reports
<b>STALE</b>. <b>Review / Adopt Update</b> shows class changes, local deviations,
and exact three-way merge conflicts. The engineer may adopt the class and
discard deviations, or preserve non-conflicting deviations. Preserve is
disabled when both the class and instance changed the same property.</p>

<h3>Lifecycle rules</h3>
<ul>
<li>Class revisions are immutable in <code>_module_classes/_history</code>.</li>
<li>A class cannot be deleted from the manager while linked instances exist.</li>
<li><b>Unlink Active</b> retains effective executable logic but ends inheritance.</li>
<li>Class adoption is refused while the active module is online.</li>
</ul>
<p>Open <b>Control Module Class Tutorial</b> in this Help window for the
complete illustrated master-class workflow.</p>
""",
    ),
    (
        "Control Module Class Tutorial",
        """
<h2>Create a Master Control Module Class</h2>
<p>A Control Module Class governs the complete function-block graph, wiring,
configuration, drawing layout and comments of its linked module instances.
The controller still runs an ordinary self-contained module; the class library
is an engineering and revision-control boundary.</p>

<h3>Before starting</h3>
<ol>
<li>Open, compile and save the finished source module.</li>
<li>Take it offline. Class linkage, property changes and adoption are refused
while the active module is online.</li>
<li>Decide which small set of plant-specific values may differ by instance.</li>
</ol>

<h3>1. Create the master</h3>
<p>Choose <b>Tools &rarr; Control Module Classes...</b>, the ribbon
<b>Tools &rarr; Classes</b> command, or search from <b>Ctrl+Shift+P</b>. Choose
<b>Create Class from Active</b>, enter a stable class name and expose only
approved instance properties.</p>
<p><img src="images/control_module_class_tutorial/01_create_master_class.png"
width="760" alt="Create a master Control Module Class"></p>
<p>Each exposed property has a schema-backed path, declared type and captured
default. The source becomes the first linked instance of revision 1.</p>

<h3>2. Create a linked instance</h3>
<p>Select the class and choose <b>Create Linked Instance</b>. In the single
creation window, enter a unique module name, select its project area and process
unit, and review its destination. Checked property rows are explicit typed
overrides; unchecked rows keep inheriting the class default.</p>
<p><img src="images/control_module_class_tutorial/03_linked_instance_properties.png"
width="760" alt="Linked instance properties"></p>
<p>The new module is type-checked and compiled before it is written. Existing
module files are never overwritten.</p>

<h3>3. Publish a successor</h3>
<p>Open a current linked instance, edit and validate the intended master, then
choose <b>Publish Active as New Revision</b>. Publication creates immutable
history but never rewrites other instances; they continue executing their
adopted graph and report <b>STALE</b>.</p>

<h3>4. Review and adopt</h3>
<p>Open a stale instance offline and choose <b>Review / Adopt Update</b>.</p>
<p><img src="images/control_module_class_tutorial/05_review_class_update.png"
width="760" alt="Review a Control Module Class update"></p>
<ul>
<li><b>Class Changes</b> shows what changed in the master.</li>
<li><b>Instance Deviations</b> shows direct local edits outside the public
property interface.</li>
<li><b>Conflicts</b> identifies exact paths changed differently on both sides.</li>
</ul>
<p><b>Adopt Class</b> discards direct deviations and retains declared property
overrides. <b>Preserve Non-conflicting Deviations</b> performs a three-way
merge and is disabled when the same property has conflicting edits.</p>

<h3>5. Verify the effective instance</h3>
<p>After adoption, inspect Class History and Active Deviations, compile the
effective module, run deployment-impact review, and download it through the
normal module release workflow.</p>
<p><img src="images/control_module_class_tutorial/06_adopted_revision_history.png"
width="760" alt="Adopted class revision history"></p>

<h3>Installed cooling-water example</h3>
<p>The <b>AzeoPlantVirtualController</b> project includes four revision-1
classes and nine linked CT1 instances: four regulatory loops, two staged fan
cells, two pump/discharge-valve assemblies and one tower-performance module.
The fan cells include availability, VFD, fault, run-feedback and basin-low-low
protection. Each pump proves its discharge MOV before start, and the standby
pump uses delayed low-header-pressure demand. All 16 CT1 outputs download in
Manual for deliberate commissioning transfer; the embedded simulator remains
open-loop.</p>

<h3>Engineering rules</h3>
<ul>
<li>An override is an approved typed property; a direct edit is a visible
deviation.</li>
<li>Never expose wiring or structure merely to hide an exception.</li>
<li>Unlink only when the module has intentionally become an independent design.</li>
<li>A class cannot be deleted while linked instances exist.</li>
<li>Resolve conflicts explicitly; the product never chooses one edit silently.</li>
</ul>
<p>The maintained offline manual is
<code>docs/CONTROL_MODULE_CLASS_TUTORIAL.md</code>.</p>
""",
    ),
    (
        "Resilience Simulation",
        """
<h2>Redundancy and Communication-Failure Simulation</h2>
<p>Choose <b>Controller &rarr; Redundancy</b>. This is a qualified training model,
not certified redundant or SIS hardware. It operates the real controller
executive and field-data boundary so trends, quality, and output delivery show
the simulated failure.</p>

<h3>Controller pair</h3>
<ul>
<li>The pair exposes primary and standby roles, synchronization generation,
peer-link state, and whether automatic failover is ready.</li>
<li><b>Fail Primary / Auto Failover</b> stops the active scan, promotes only a
synchronized healthy standby, resumes scanning, and reports the measured output
bump and outage time.</li>
<li>A failed standby leaves control running simplex. A failed peer link marks the
standby not synchronized and inhibits automatic failover until recovery and a
new synchronization.</li>
<li>The event chronicle records failure, inhibition, promotion, recovery, and
synchronization events for review.</li>
</ul>

<h3>Field communication faults</h3>
<p>Enter <code>*</code> for every store point or a comma-separated set of exact
tags. An input-link failure holds the last delivered value and publishes
Bad/stale quality. An output-link failure discards subsequent controller demands
before the field provider can receive them and counts every dropped write. Clear
the failure to restore ordinary delivery; no control-module source is modified.</p>
""",
    ),
    (
        "Online Commissioning",
        """
<h2>Online Commissioning and Debugging</h2>

<h3>Compile, Download, and Go Online are different</h3>
<table>
<tr><th>Command</th><th>What it does</th></tr>
<tr><td><b>Compile</b></td><td>Validates types, wires, cycles, composite
interiors, and execution order. A broken composite prevents the outer module
from downloading.</td></tr>
<tr><td><b>Download</b></td><td>Compiles and installs selected modules in the
runtime. It never accepts a silently inert composite.</td></tr>
<tr><td><b>Go Online</b></td><td>Attaches this editor to modules that are
already running; it does not recompile or restart them.</td></tr>
</table>

<h3>Live status</h3>
<p>Online terminals and wires show semantic state: Bad is red, Uncertain is
amber, and a forced input is purple. Hover a terminal for value, quality,
limit, and force details. Forces are deliberately restricted to block inputs;
forcing an output would conceal the algorithm's true result.</p>

<h3>FBD debugger</h3>
<ol>
<li>Download the module, then choose <b>Module &rarr; Debugger</b>.</li>
<li>Use <b>Pause</b> to stop at a safe block boundary while the module remains
online.</li>
<li>Set a breakpoint in the debugger table or from a block's right-click
<b>FBD Debugger</b> menu.</li>
<li><b>Step Block</b> executes one due, non-bypassed block. <b>Run Scan</b>
finishes the current partial scan (or runs one new scan) and stays paused.</li>
<li><b>Run to This Block</b> pauses immediately before that block executes.
The current and next blocks are highlighted on the canvas.</li>
</ol>
<p>Breakpoints are session-only commissioning state. A re-download preserves
them only for block UUIDs that still exist. Going offline or loading a new
compiled module safely abandons any partial scan.</p>

<h3>SFC debugger</h3>
<p>For an <b>SFC_CHART</b>, Open Debugger routes to the chart-level view. It
shows active/completed steps and last transition evaluations and supports
level stop/start/reset, temporary step/action/transition disables, and a
single-scan transition force. These diagnostic changes are not saved into the
engineering chart.</p>
""",
    ),
    (
        "Reusable Composites",
        """
<h2>Reusable Linked Composites</h2>
<p>An embedded composite owns an independent inner graph. A linked composite
instead references a versioned library definition while retaining a complete
last-known effective graph, so a downloaded controller never depends on the
engineering library being online.</p>

<h3>Create and link</h3>
<ol>
<li>Drag a selection around two or more blocks, right-click the selection or
its surrounding canvas, and choose <b>Create Composite from Selection</b>.</li>
<li>Open the composite's <b>Composite Definition...</b> command.</li>
<li>For an embedded composite, declare the public properties that instances
may override. Each property names a block/configuration path, data type,
default, and description.</li>
<li>Choose <b>Publish Current as Definition</b>. The instance becomes linked
to revision 1 with a content digest.</li>
<li>Other composite instances can choose the definition and
<b>Link Selected Definition</b>.</li>
</ol>

<h3>Instance lifecycle</h3>
<table>
<tr><th>State</th><th>Meaning and action</th></tr>
<tr><td><b>Current</b></td><td>The instance revision and digest match the
library.</td></tr>
<tr><td><b>Stale</b></td><td>A newer definition exists. Refresh installs it
atomically and reapplies every still-valid explicit override.</td></tr>
<tr><td><b>Missing</b></td><td>The library record is unavailable. The embedded
last-known graph remains executable; restore the definition before editing
the link.</td></tr>
<tr><td><b>Embedded</b></td><td>No definition identity. Convert to Embedded to
keep the effective graph but stop following future revisions.</td></tr>
</table>
<p>Definition updates use optimistic revision checks so two engineers cannot
silently overwrite each other. Old revisions remain in history, and invalid
or removed public overrides are dropped during an explicit refresh rather
than being applied to the wrong path.</p>
""",
    ),
    (
        "Module Parameters",
        """
<h2>Module Parameter Special Items</h2>
<p>Use the four items in the <b>Special Items</b> palette to make the module's
interface explicit on the function-block diagram. Select an item to configure
its parameter name, data type, initial value, units, and description in
Properties.</p>

<table>
<tr><th>Special item</th><th>Direction inside the module</th><th>Purpose</th></tr>
<tr><td><b>Input Parameter</b></td><td>Source</td><td>Receives a value from the
module's caller or operator interface and supplies it to the diagram.</td></tr>
<tr><td><b>Output Parameter</b></td><td>Sink</td><td>Publishes a calculated
diagram value through the module's external interface.</td></tr>
<tr><td><b>Internal Read Parameter</b></td><td>Source</td><td>Reads a value shared
within this module. Use this for the Azeo-style configurable constant
pattern.</td></tr>
<tr><td><b>Internal Write Parameter</b></td><td>Sink</td><td>Updates a value shared
within this module without exposing it as an external module output.</td></tr>
</table>

<h3>Typical workflow</h3>
<ol>
<li>Drag the required parameter item from <b>Special Items</b> onto the
canvas.</li>
<li>Give it a unique engineering name and select the correct data type.</li>
<li>Wire a source item to consuming block inputs, or wire a calculated value
into a sink item.</li>
<li>Open <b>Module Parameters...</b> from an ACT or CND expression editor to
review all four connection types in one table.</li>
<li>Compile before download; unresolved names and incompatible types are
reported as authoring errors.</li>
</ol>

<p><b>CONSTANT</b> remains available under <b>Azeo Utilities</b> for a quick
literal source. It is intentionally separated from Special Items because it
is not the Azeo module-parameter object; use Internal Read Parameter when
the value belongs to the module interface.</p>
""",
    ),
    (
        "Block Reference",
        """
<h2>Block Type Reference</h2>

<h3>Module Parameter Special Items (4)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>INPUT_PARAMETER</b></td><td>External module input presented as a
source on the diagram</td></tr>
<tr><td><b>OUTPUT_PARAMETER</b></td><td>Diagram sink published as an external
module output</td></tr>
<tr><td><b>INTERNAL_READ_PARAMETER</b></td><td>Reads a module-local parameter;
the Azeo-style configurable constant source</td></tr>
<tr><td><b>INTERNAL_WRITE_PARAMETER</b></td><td>Writes a module-local parameter
without exposing it externally</td></tr>
</table>

<h3>I/O Blocks (9)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>AI</b> (Analog Input)</td><td>Reads a process measurement from the data store, with filtering and 4-level alarms</td></tr>
<tr><td><b>AO</b> (Analog Output)</td><td>Writes a control output to the data store, with BKCAL support and modes</td></tr>
<tr><td><b>DI</b> (Digital Input)</td><td>Reads a boolean signal from the data store</td></tr>
<tr><td><b>DO</b> (Digital Output)</td><td>Writes a boolean signal to the data store</td></tr>
<tr><td><b>SETPOINT</b></td><td>Operator-adjustable setpoint with limits and tracking</td></tr>
<tr><td><b>DEVCTL</b></td><td>Motor/device control with start/stop, interlocks, run feedback, fault detection</td></tr>
<tr><td><b>VLVCTL</b></td><td>On/off valve control with stroke timing, position feedback, fail-safe action</td></tr>
<tr><td><b>DATALOG</b></td><td>Event-triggered data snapshot capture (4 channels)</td></tr>
<tr><td><b>MSG</b></td><td>Operator message block &mdash; sends text to HMI/log on trigger</td></tr>
</table>

<h3>Control Blocks (6)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>PID</b></td><td>ISA PID controller with 8 modes (OOS/IMan/LO/Man/Auto/Cas/RCas/ROut)</td></tr>
<tr><td><b>RATIO</b></td><td>Ratio controller &mdash; maintains fixed ratio between two flows</td></tr>
<tr><td><b>SPLITTER</b></td><td>Split-range output &mdash; 1 PID to 2 valves with configurable breakpoints</td></tr>
<tr><td><b>ONOFF</b></td><td>On/off thermostat controller with deadband hysteresis</td></tr>
<tr><td><b>RAMP_SOAK</b></td><td>Profile controller &mdash; up to 8 ramp-soak segments for kiln/furnace programs</td></tr>
<tr><td><b>GAIN_SCHED</b></td><td>Gain scheduler &mdash; PV-indexed interpolation table for Kp/Ti/Td</td></tr>
</table>

<h3>Signal Processing &amp; Conditioning (14)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>SCALER</b></td><td>Linear scaling with sqrt extraction, clamp, invert, and BKCAL</td></tr>
<tr><td><b>FILTER</b></td><td>First-order exponential filter (noise reduction) with bypass</td></tr>
<tr><td><b>DEADTIME</b></td><td>Transport delay &mdash; ring buffer delay of configurable duration</td></tr>
<tr><td><b>MOVING_AVG</b></td><td>N-sample moving average filter</td></tr>
<tr><td><b>LEAD_LAG</b></td><td>Lead-lag dynamic compensator</td></tr>
<tr><td><b>BIAS</b></td><td>Gain/bias station: OUT = GAIN &times; IN + BIAS, with BKCAL</td></tr>
<tr><td><b>TRANSFER</b></td><td>Bumpless transfer switch between two signal sources</td></tr>
<tr><td><b>ALARM</b></td><td>Standalone process alarm: HI/LO/HH/LL/DEV/ROC with ISA-18.2 shelving</td></tr>
<tr><td><b>LIMITER</b></td><td>High/low output limiting</td></tr>
<tr><td><b>DEADBAND</b></td><td>Deadband filter &mdash; ignores small changes</td></tr>
<tr><td><b>RATE_LIMITER</b></td><td>Rate-of-change limiting (separate up/down rates)</td></tr>
<tr><td><b>RAMP</b></td><td>Rate-limited ramp to target setpoint</td></tr>
<tr><td><b>SIGNAL_CHAR</b></td><td>Piecewise linear signal characterization (up to 10 points)</td></tr>
<tr><td><b>MEM_FLOAT</b></td><td>Analog memory register &mdash; retains value across scans</td></tr>
</table>

<h3>Selector Blocks (8)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>MIN_SELECT</b></td><td>Output = minimum of 3 inputs (override control)</td></tr>
<tr><td><b>MAX_SELECT</b></td><td>Output = maximum of 3 inputs</td></tr>
<tr><td><b>MID_SELECT</b></td><td>Output = median of three inputs (sensor voting)</td></tr>
<tr><td><b>AVG_SELECT</b></td><td>Average of valid inputs with bad-value exclusion</td></tr>
<tr><td><b>SWITCH</b></td><td>Boolean-controlled 2-input signal selector</td></tr>
<tr><td><b>MODE_SWITCH</b></td><td>APC mode switching (AUTO&harr;RCAS, MAN&harr;ROUT)</td></tr>
<tr><td><b>MUX</b></td><td>N-to-1 multiplexer &mdash; index selects one of 8 inputs</td></tr>
<tr><td><b>DEMUX</b></td><td>1-to-N demultiplexer &mdash; index routes input to one of 8 outputs</td></tr>
</table>

<h3>Math Blocks (19)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>SUMMER</b></td><td>Weighted sum: G1&times;IN1 + G2&times;IN2 + G3&times;IN3 + G4&times;IN4 + BIAS</td></tr>
<tr><td><b>MULTIPLIER</b></td><td>Product: IN1 &times; IN2 &times; GAIN</td></tr>
<tr><td><b>DIVIDER</b></td><td>Quotient: IN1 / IN2 (Azeo div-by-zero handling)</td></tr>
<tr><td><b>SUB</b></td><td>Subtraction: IN1 &minus; IN2</td></tr>
<tr><td><b>ABS</b></td><td>Absolute value: |IN|</td></tr>
<tr><td><b>SQRT</b></td><td>Square root with low-cutoff for DP flow measurement</td></tr>
<tr><td><b>INTEGRATOR</b></td><td>Time integration: OUT += IN &times; dt (trapezoidal/rectangular)</td></tr>
<tr><td><b>DERIVATIVE</b></td><td>Rate of change: d(IN)/dt with noise filtering</td></tr>
<tr><td><b>TOTALIZER</b></td><td>Flow totalization: accumulated &Sigma;(flow &times; dt)</td></tr>
<tr><td><b>POLY</b></td><td>Polynomial: a0 + a1&times;x + a2&times;x&sup2; + ... + a5&times;x&#8309;</td></tr>
<tr><td><b>LOG_EXP</b></td><td>Logarithm (ln, log10) and exponential (e^x, 10^x)</td></tr>
<tr><td><b>POWER</b></td><td>Power function: BASE ^ EXPONENT</td></tr>
<tr><td><b>TRIG</b></td><td>Trigonometry: sin, cos, tan, asin, acos, atan, atan2</td></tr>
<tr><td><b>FLOW_COMP</b></td><td>Pressure/temperature compensated flow calculation</td></tr>
<tr><td><b>STATISTICS</b></td><td>Rolling statistics: mean, std dev, min, max over N-sample window</td></tr>
<tr><td><b>LOOKUP</b></td><td>Table lookup with linear interpolation (up to 10 breakpoints)</td></tr>
<tr><td><b>BTU_CALC</b></td><td>Heat duty calculation: Q = Flow &times; Cp &times; &Delta;T</td></tr>
<tr><td><b>ACT</b></td><td>Custom calculation with 60+ DCS functions (expression/script)</td></tr>
<tr><td><b>EXPRESSION</b></td><td>IEC 61131-3 structured text expression evaluator</td></tr>
</table>

<h3>Azeo Utilities (1)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>CONSTANT</b></td><td>Product-specific fixed numeric literal. For
a Azeo-style configurable module value, use Internal Read Parameter.</td></tr>
</table>

<h3>Logic Blocks (21)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>AND</b></td><td>Boolean AND (3 inputs)</td></tr>
<tr><td><b>OR</b></td><td>Boolean OR (3 inputs)</td></tr>
<tr><td><b>NOT</b></td><td>Boolean inverter</td></tr>
<tr><td><b>XOR</b></td><td>Exclusive OR (2 inputs)</td></tr>
<tr><td><b>NAND</b></td><td>NAND gate (3 inputs)</td></tr>
<tr><td><b>NOR</b></td><td>NOR gate (3 inputs)</td></tr>
<tr><td><b>TRUTH_TABLE</b></td><td>Configurable 3-input truth table (8-bit output vector)</td></tr>
<tr><td><b>TIMER_ON</b></td><td>On-delay timer</td></tr>
<tr><td><b>TIMER_OFF</b></td><td>Off-delay timer</td></tr>
<tr><td><b>PULSE</b></td><td>Pulse timer &mdash; fixed-duration output pulse on rising edge</td></tr>
<tr><td><b>COUNTER</b></td><td>Up/down counter with preset, reset, and load</td></tr>
<tr><td><b>COMPARATOR</b></td><td>Compares two signals (GT/LT/GE/LE/EQ) with hysteresis</td></tr>
<tr><td><b>SR_LATCH</b></td><td>Set-dominant flip-flop</td></tr>
<tr><td><b>RS_LATCH</b></td><td>Reset-dominant flip-flop</td></tr>
<tr><td><b>POS_EDGE</b></td><td>Rising edge detector (one-scan pulse)</td></tr>
<tr><td><b>NEG_EDGE</b></td><td>Falling edge detector (one-scan pulse)</td></tr>
<tr><td><b>MEM_BOOL</b></td><td>Boolean memory register</td></tr>
<tr><td><b>BIT_PACK</b></td><td>Pack 16 booleans into integer word</td></tr>
<tr><td><b>BIT_UNPACK</b></td><td>Unpack integer word into 16 booleans</td></tr>
<tr><td><b>SCHEDULE</b></td><td>Time-of-day schedule (on/off by wall clock)</td></tr>
<tr><td><b>SEQ_TIMER</b></td><td>N-step sequence timer with per-step durations</td></tr>
</table>

<h3>Data Handling (4)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>FIFO</b></td><td>First-in first-out queue buffer (edge-triggered push/pop)</td></tr>
<tr><td><b>LIFO</b></td><td>Last-in first-out stack (edge-triggered push/pop)</td></tr>
<tr><td><b>DATALOG</b></td><td>Event-triggered data snapshot capture (4 channels)</td></tr>
<tr><td><b>MSG</b></td><td>Operator message block &mdash; event-triggered text to HMI/log</td></tr>
</table>

<h3>APC Blocks (5)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>APC_CONTROL</b></td><td>Advanced process control (DMC/MPC) interface &mdash; 6 CV/6 MV</td></tr>
<tr><td><b>WATCHDOG</b></td><td>APC watchdog timer &mdash; detects communication failure</td></tr>
<tr><td><b>SHED_LOGIC</b></td><td>APC engagement coordinator (Normal/Partial/Full shed)</td></tr>
<tr><td><b>SP_HANDOFF</b></td><td>Bumpless SP transition between APC and regulatory</td></tr>
<tr><td><b>MV_CLAMP</b></td><td>MV limit enforcement with status feedback</td></tr>
</table>

<h3>Safety &amp; BMS Blocks (9)</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>FOL_LOGIC</b></td><td>Fuel Override Logic with graduated cutback</td></tr>
<tr><td><b>SIS_VOTER</b></td><td>Safety voting (1oo2, 2oo3, 1oo3, 1oo1) with bypass</td></tr>
<tr><td><b>INTERLOCK</b></td><td>Latched shutdown requiring all permissives to reset</td></tr>
<tr><td><b>BURNER_SEQ</b></td><td>NFPA 85/86 burner startup/shutdown sequencer (8-state)</td></tr>
<tr><td><b>FLAME_DET</b></td><td>Flame detector with M-of-N voting</td></tr>
<tr><td><b>FUEL_VALVE</b></td><td>Safety shutoff valve (SSOV) with position feedback</td></tr>
<tr><td><b>BLOWER</b></td><td>FD blower with start delay and draft proving</td></tr>
<tr><td><b>PURGE_TIMER</b></td><td>Pre/post purge timer with airflow proving</td></tr>
<tr><td><b>TRIP_RELAY</b></td><td>Latching trip relay (any trigger, requires reset)</td></tr>
</table>

<h3>SFC Blocks (8) &mdash; IEC 61131-3 Sequential Function Chart</h3>
<table>
<tr><th>Block</th><th>Description</th></tr>
<tr><td><b>INITIAL_STEP</b></td><td>Starting step &mdash; auto-activates on go-online</td></tr>
<tr><td><b>STEP</b></td><td>Sequence step with elapsed timer</td></tr>
<tr><td><b>END_STEP</b></td><td>Terminal step &mdash; signals sequence complete</td></tr>
<tr><td><b>TRANSITION</b></td><td>Condition gate between steps</td></tr>
<tr><td><b>SFC_ACTION</b></td><td>Action with IEC 61131-3 qualifiers (N, P, S, R, L, D)</td></tr>
<tr><td><b>PARALLEL_SPLIT</b></td><td>Simultaneous divergence &mdash; activates all branches</td></tr>
<tr><td><b>PARALLEL_JOIN</b></td><td>Simultaneous convergence &mdash; waits for all branches</td></tr>
<tr><td><b>SELECTOR_BRANCH</b></td><td>Alternative divergence &mdash; first true condition wins</td></tr>
</table>
""",
    ),
    (
        "PID & BKCAL Wiring",
        """
<h2>PID Control Loop Wiring</h2>

<h3>Standard PID Loop Pattern</h3>
<pre>
AI (measurement) &rarr; PID (controller) &rarr; SCALER (0-1 to 0-100) &rarr; AO (valve)
                       &larr;  BKCAL chain (reverse direction)  &larr;
</pre>

<h3>Signal Scaling</h3>
<p>The PID block always outputs in <b>0.0 &ndash; 1.0</b> range.
Valves (AO blocks) expect <b>0 &ndash; 100%</b>.
Use a <b>SCALER</b> block to convert between these ranges.</p>

<table>
<tr><th>Path</th><th>Signal Range</th><th>Description</th></tr>
<tr><td>AI &rarr; PID</td><td>Engineering units</td><td>Process measurement (e.g., 0&ndash;200 &deg;C)</td></tr>
<tr><td>PID &rarr; SCALER</td><td>0.0 &ndash; 1.0</td><td>PID output (normalized)</td></tr>
<tr><td>SCALER &rarr; AO</td><td>0 &ndash; 100%</td><td>Valve position</td></tr>
</table>

<h3>Back-Calculation (BKCAL) Chain</h3>
<p>BKCAL wires propagate the actual valve position <b>backward</b> through
the chain for <b>bumpless transfer</b> (no output bump when switching modes).</p>

<pre>
AO.BKCAL_OUT (0-100%) &rarr; SCALER.BKCAL_IN &rarr; SCALER.BKCAL_OUT (0-1) &rarr; PID.BKCAL_IN
</pre>

<div class="note">
<b>Important:</b> BKCAL wires must be marked as <code>is_bkcal: true</code>
in the wire properties.  The compiler treats BKCAL wires differently &mdash;
they break cycles and execute with a one-scan delay.
</div>

<h3>PID Tuning Parameters</h3>
<table>
<tr><th>Parameter</th><th>Description</th><th>Typical Range</th></tr>
<tr><td>Kp</td><td>Proportional gain (dimensionless)</td><td>0.5 &ndash; 20</td></tr>
<tr><td>Ti</td><td>Integral time (seconds)</td><td>30 &ndash; 3600</td></tr>
<tr><td>Td</td><td>Derivative time (seconds)</td><td>0 (usually disabled)</td></tr>
<tr><td>Action</td><td>Direct (PV&uarr; &rarr; OUT&uarr;) or Reverse (PV&uarr; &rarr; OUT&darr;)</td><td>Depends on process</td></tr>
</table>

<h3>Cascade Control</h3>
<p>In cascade, the primary PID's output becomes the secondary PID's setpoint:</p>
<pre>
AI_primary &rarr; PID_primary &rarr; PID_secondary.CAS_IN
AI_secondary &rarr; PID_secondary &rarr; SCALER &rarr; AO
</pre>
<p>The secondary PID must be in <b>Cascade mode</b> for the primary to control it.</p>
""",
    ),
    (
        "SFC Programming",
        SFC_CHART_HELP := """
<h2>Sequential Function Charts — the SFC_CHART block</h2>

<p>A sequence lives in <b>one SFC_CHART block</b>: the whole chart —
steps, transitions, actions — is the block's configuration, edited in
the chart editor (select the block &rarr; Properties &rarr;
<b>Edit Chart&hellip;</b>, or double-click). The canvas around it stays a
function-block diagram; the sequence itself is authored as a chart, the
way Azeo edits an SFC inside a single composite.</p>

<h3>The block's pins</h3>
<table>
<tr><th>Pin</th><th>Dir</th><th>Meaning</th></tr>
<tr><td><b>EN_D</b></td><td>in</td><td>Enable. Low <b>holds</b> the chart exactly where it is
&mdash; no actions run, no transitions fire, step time stops. Defaults true when unwired.</td></tr>
<tr><td><b>RESET_D</b></td><td>in</td><td>True restarts the chart at its initial step and clears
the completed path. The only way out of DONE &mdash; a finished chart stays finished.</td></tr>
<tr><td><b>ACTIVE</b></td><td>out</td><td>Name of the active step ("" when idle or done).</td></tr>
<tr><td><b>STEP_TIME</b></td><td>out</td><td>Seconds the active step has been active.</td></tr>
<tr><td><b>STEP_NO</b></td><td>out</td><td>1-based index of the active step (0 = none).</td></tr>
<tr><td><b>DONE</b></td><td>out</td><td>Latched true when a termination transition fires.</td></tr>
<tr><td><b>HELD</b></td><td>out</td><td>True while EN_D holds the sequence.</td></tr>
<tr><td><b>FAULT</b></td><td>out</td><td>True when an action or condition raised an error this scan,
or the chart itself fails validation (then ACTIVE also goes Bad).</td></tr>
</table>

<h3>Steps and actions</h3>
<p>A step is a state. While it is active, its <b>actions</b> run — each
action is one expression, with an IEC 61131-3 qualifier:</p>
<table>
<tr><th>Qualifier</th><th>Name</th><th>Behavior</th></tr>
<tr><td><b>P</b></td><td>Pulse</td><td>Runs once, on the scan the step goes active. Use for
one-shot commands: latching a start request, incrementing a counter.</td></tr>
<tr><td><b>N</b></td><td>Non-stored</td><td>Runs <i>every scan</i> while the step is active. Use for
state that must be continuously asserted: holding a valve command, ramping a setpoint
with <code>step_time</code>.</td></tr>
<tr><td><b>S</b></td><td>Stored</td><td>Starts while this step is active and continues every
scan after the chart leaves the step. Give it a stable action Name so an R action can
address it.</td></tr>
<tr><td><b>R</b></td><td>Reset</td><td>Stops the stored action named in <b>Reset target</b>.
The reset action itself does not execute an expression.</td></tr>
<tr><td><b>L</b></td><td>Limited</td><td>Runs while the step is active, for no more than the
configured Time.</td></tr>
<tr><td><b>D</b></td><td>Delayed</td><td>Starts after the configured Time has elapsed and
continues while the step remains active.</td></tr>
</table>
<p>Exactly one step is the <b>initial step</b> (green stripe in the
editor). It activates when the chart first executes and again after
RESET_D. An action that fails at runtime logs a warning and raises
FAULT for that scan — the sequence does not abort.</p>

<h3>Transitions</h3>
<ul>
<li>Each transition has a <b>source step</b>, a <b>target step</b>, and a
<b>condition</b> expression evaluated while the source is active.</li>
<li><b>First true transition wins, in declaration order</b> — list the
abnormal exit (abort, timeout) <i>before</i> the normal advance and it
takes priority.</li>
<li>At most <b>one transition fires per scan</b>; the new step's P
actions run on the next scan.</li>
<li>An <b>empty target</b> ("(terminate)" in the editor, drawn as a
double bar) ends the sequence: DONE latches true and only RESET_D
restarts it.</li>
<li>A timed step needs no timer block: <code>step_time &gt;= 30.0</code>
is the condition.</li>
</ul>

<h3>Online SFC diagnostics</h3>
<p>Debug state is runtime-only and never changes the saved chart. An engineer can
stop/start/reset a chart level, disable an individual step, action, or transition,
and momentarily force a transition that leaves the active step. Disabled
transitions are still evaluated and shown in the transition evaluation tree, but
cannot fire. A force is consumed after one scan and is refused for an inactive or
disabled transition.</p>

<h3>The expression language</h3>
<p>Actions and conditions use the same language as CND/ACT blocks —
arithmetic, comparisons, <code>and/or/not</code>, a conditional, the math
library — plus direct references, so <b>a chart needs no wires to reach
what it commands</b>:</p>
<table>
<tr><th>Function</th><th>Meaning</th></tr>
<tr><td><code>tag('KEY'[, default])</code></td><td>Read a store point. Give a default for
command tags that exist only after their first write &mdash;
<code>tag('SEQ-101.cmd_start', 0)</code> reads 0, not an error, before anyone writes it.</td></tr>
<tr><td><code>set_tag('KEY', value)</code></td><td>Write a store point down the controller's own
output path (the write queue an executive drains) &mdash; the same route a DO block takes.</td></tr>
<tr><td><code>param('BLOCK/TERM')</code></td><td>Read a terminal or parameter of another block
in this module.</td></tr>
<tr><td><code>set_param('BLOCK/TERM', v)</code></td><td>Write an <i>unwired</i> input terminal or
config parameter. Refused, by name, if a wire drives it &mdash; the wire would re-assert it
next scan anyway.</td></tr>
<tr><td><code>write_param('NAME', v)</code></td><td>Assign a module parameter whose Write Access is Writeable.</td></tr>
<tr><td><code>step_time</code></td><td>Seconds the current step has been active &mdash; the ramp
variable: <code>set_tag('VFD-101.speed_sp', min(55.0, max(10.0, step_time * 8.0)))</code>.</td></tr>
</table>
<p>Every expression opens in the expression editor with its tag browser
and <b>Parse</b> button; a condition with a typo is a chart that never
advances and never says why, so parse before you close.</p>

<h3>Commanding devices — the patterns that matter</h3>
<ul>
<li><b>DEVCTL latches.</b> A motor stops on <code>cmd_stop</code>, a valve
closes on <code>cmd_close</code> — <i>not</i> when the start/open request
falls. A stopping step must assert the stop command, not merely drop the
start.</li>
<li><b>Seed commands in the initial step.</b> An N action writing every
command tag to 0 (<code>cmd_start</code>, <code>cmd_stop</code>,
<code>cmd_open</code>, <code>cmd_close</code>&hellip;) keeps the DI blocks
reading them from going Bad, and clears latched requests between runs.</li>
<li><b>Respect permissives.</b> If a start permissive needs a condition
(MTR-101 requires speed reference &gt; 5%), the chart must satisfy it
<i>before</i> commanding the start — set a minimum speed demand in the
start step, then ramp from that floor.</li>
<li><b>Prove, don't assume.</b> Advance on feedback the field reports —
<code>ZSO</code>, <code>run_fb</code>, measured speed — never on your own
elapsed time alone.</li>
</ul>

<h3>Worked example — SEQ-101 (azeo_modbus)</h3>
<table>
<tr><th>Step</th><th>Does (N actions)</th><th>Advances when</th></tr>
<tr><td>IDLE</td><td>All commands to 0</td><td>cmd_start == 1</td></tr>
<tr><td>OPEN_PATH</td><td>cmd_open = 1</td><td>ZSO proves open</td></tr>
<tr><td>START_DRIVE</td><td>speed 10% floor, then cmd_start = 1</td><td>run_fb proves running</td></tr>
<tr><td>RAMP_TO_SPEED</td><td>speed = min(55, max(10, step_time&times;8))</td><td>measured speed_pv &ge; 50</td></tr>
<tr><td>RUNNING</td><td>hold 55%</td><td>cmd_stop == 1</td></tr>
<tr><td>STOPPING</td><td>cmd_start = 0, <b>cmd_stop = 1</b></td><td>run_fb proves stopped</td></tr>
<tr><td>CLOSE_PATH</td><td>cmd_open = 0, <b>cmd_close = 1</b></td><td>ZSC proves closed &rarr; IDLE</td></tr>
</table>

<h3>Authoring in the chart editor</h3>
<ul>
<li><b>Canvas:</b> drag steps to arrange (positions are saved);
double-click a step or a transition line to edit it. Green stripe =
initial step; a double bar below a step is a termination.</li>
<li><b>Structure panel:</b> add/remove steps and transitions;
double-click a list entry to edit. In the step dialog, double-click an
action's expression cell to open the expression editor.</li>
<li><b>Validation</b> runs the chart's own checks — unknown transition
endpoints, conditionless transitions, unreachable steps — and <b>OK is
refused while problems stand</b>, because a broken chart on scan is a
Bad block.</li>
<li>Charts auto-lay out top-to-bottom the first time; after that your
arrangement wins.</li>
</ul>

<h3>Monitoring a running chart</h3>
<p>Watch the block's ACTIVE / STEP_NO / STEP_TIME pins on the canvas, or
wire them to an HMI. The block also keeps the <b>completed path</b> and
per-step visit counts (<code>sequence_snapshot()</code> — the contract an
operator sequence view reads), cleared by RESET_D.</p>

<div class="note">
<b>Legacy block-based SFC:</b> modules built from discrete STEP /
TRANSITION blocks still load, compile and run — the compiler exempts a
cycle only when <i>both</i> ends are SFC-category blocks, so never route
a transition through a logic gate. New sequences should be charts: one
block, one editable picture, no feedback wiring to get wrong.
</div>
""",
    ),
    (
        "Equipment Modules",
        """
<h2>Equipment Modules</h2>

<h3>Azeo / Honeywell Concept</h3>
<p>An <b>Equipment Module</b> is an organizational container that groups
related control elements for a physical piece of equipment. This mirrors
the ISA-88 / ISA-95 equipment hierarchy used in Azeo and Honeywell Experion.</p>

<h3>Equipment Module Contents</h3>
<table>
<tr><th>Element</th><th>Description</th></tr>
<tr><td>Control Modules</td><td>Regulatory FBD strategies (PID loops, interlocks)</td></tr>
<tr><td>SFC Modules</td><td>Sequential procedures (startup, shutdown, transitions)</td></tr>
<tr><td>Parameters</td><td>Equipment-level configuration (design values, limits)</td></tr>
<tr><td>Alarms</td><td>Equipment-level alarm definitions</td></tr>
<tr><td>States</td><td>Equipment operating states (IDLE, RUNNING, FAULTED, etc.)</td></tr>
</table>

<h3>Creating an Equipment Module</h3>
<ol>
<li>Right-click <b>Equipment Modules</b> in the Project Explorer</li>
<li>Select <b>New Equipment Module...</b></li>
<li>Fill in the name, type, and tag prefix</li>
<li>Use the <b>Modules</b> tab to assign Control Modules and SFC Modules</li>
<li>Configure parameters, alarms, and states as needed</li>
<li>Click OK to save</li>
</ol>

<h3>Equipment Types</h3>
<table>
<tr><th>Type</th><th>Examples</th></tr>
<tr><td>REACTOR</td><td>CSTR, batch reactor, tubular reactor</td></tr>
<tr><td>COLUMN</td><td>Distillation column, absorber, stripper</td></tr>
<tr><td>VESSEL</td><td>Separator, flash drum, storage tank</td></tr>
<tr><td>HEAT_EXCHANGER</td><td>Shell-and-tube, air cooler, condenser</td></tr>
<tr><td>FURNACE</td><td>Fired heater, reformer furnace</td></tr>
<tr><td>COMPRESSOR</td><td>Centrifugal compressor, reciprocating compressor</td></tr>
<tr><td>PUMP</td><td>Centrifugal pump, positive displacement pump</td></tr>
<tr><td>VALVE_STATION</td><td>Control valve manifold, safety valve group</td></tr>
</table>

<h3>Relationship to ISA-88 / ISA-95</h3>
<p>Equipment Modules map to the ISA-88 Equipment Module level,
which sits between the Unit (a major process vessel or operation)
and the Control Module (a single loop or device).</p>
""",
    ),
    (
        "Keyboard Shortcuts",
        """
<h2>Keyboard Shortcuts</h2>

<h3>File Operations</h3>
<table>
<tr><th>Shortcut</th><th>Action</th></tr>
<tr><td><b>Ctrl+N</b></td><td>New Strategy</td></tr>
<tr><td><b>Ctrl+O</b></td><td>Open Strategy</td></tr>
<tr><td><b>Ctrl+S</b></td><td>Save Strategy</td></tr>
<tr><td><b>Ctrl+W</b></td><td>Close Window</td></tr>
</table>

<h3>Edit Operations</h3>
<table>
<tr><th>Shortcut</th><th>Action</th></tr>
<tr><td><b>Ctrl+Z</b></td><td>Undo</td></tr>
<tr><td><b>Ctrl+Y</b></td><td>Redo</td></tr>
<tr><td><b>Ctrl+F</b></td><td>Find Block</td></tr>
<tr><td><b>Ctrl+Shift+P</b></td><td>Command palette (commands and blocks)</td></tr>
<tr><td><b>Delete</b></td><td>Delete selected blocks/wires</td></tr>
<tr><td><b>Ctrl+A</b></td><td>Select all blocks</td></tr>
</table>

<h3>Module Operations</h3>
<table>
<tr><th>Shortcut</th><th>Action</th></tr>
<tr><td><b>F7</b></td><td>Compile strategy</td></tr>
<tr><td><b>F5</b></td><td>Download / Go Online</td></tr>
<tr><td><b>Shift+F5</b></td><td>Go Offline</td></tr>
</table>

<h3>View Operations</h3>
<table>
<tr><th>Shortcut</th><th>Action</th></tr>
<tr><td><b>Ctrl+0</b></td><td>Zoom to Fit</td></tr>
<tr><td><b>Ctrl+=</b></td><td>Zoom In</td></tr>
<tr><td><b>Ctrl+-</b></td><td>Zoom Out</td></tr>
<tr><td><b>Ctrl+Alt+M</b></td><td>Diagram Navigator and bookmarks</td></tr>
<tr><td><b>Ctrl+Shift+S</b></td><td>Open Control Designer (from HMI)</td></tr>
<tr><td><b>Ctrl+Shift+O</b></td><td>Open Operator Station</td></tr>
<tr><td><b>F1</b></td><td>Help</td></tr>
</table>

<h3>Canvas Navigation</h3>
<table>
<tr><th>Action</th><th>Description</th></tr>
<tr><td><b>Mouse wheel</b></td><td>Zoom in/out</td></tr>
<tr><td><b>Middle-click + drag</b></td><td>Pan canvas</td></tr>
<tr><td><b>Right-click</b></td><td>Context menu</td></tr>
<tr><td><b>Click output terminal</b></td><td>Start wiring</td></tr>
<tr><td><b>Click input terminal</b></td><td>Complete wire connection</td></tr>
<tr><td><b>Double-click PID block</b></td><td>Open faceplate (when online)</td></tr>
</table>
""",
    ),
    (
        "Design Patterns",
        """
<h2>Common Design Patterns</h2>

<h3>1. Simple Regulatory Loop</h3>
<pre>
AI (xmeas_9) &rarr; PID (TIC109) &rarr; SCALER (0-1 to 0-100) &rarr; AO (xmv_10)
                   BKCAL chain: AO &rarr; SCALER &rarr; PID
</pre>
<p>Every PID loop requires this pattern for proper scaling and bumpless transfer.</p>

<h3>2. Cross-Limiting (Combustion Control)</h3>
<pre>
                    &darr; Air demand
Fuel SP &rarr; MIN_SELECT &rarr; Fuel PID &rarr; Fuel Valve
Air SP  &rarr; MAX_SELECT &rarr; Air PID  &rarr; Air Damper
                    &uarr; Fuel demand
</pre>
<p>Ensures air always leads fuel on increase, fuel leads air on decrease.</p>

<h3>3. Cascade Control</h3>
<pre>
AI_primary &rarr; PID_primary.PV       PID_primary.OUT &rarr; PID_secondary.CAS_IN
AI_secondary &rarr; PID_secondary.PV   PID_secondary.OUT &rarr; SCALER &rarr; AO
</pre>

<h3>4. Override Control (Selector)</h3>
<pre>
Normal_PID.OUT  &rarr; MIN_SELECT &rarr; AO
Safety_PID.OUT  &rarr;
</pre>
<p>The safety controller overrides the normal controller when its output is lower.</p>

<h3>5. SFC Startup Sequence</h3>
<pre>
INITIAL_STEP (IDLE)
   &darr; Transition (operator command)
STEP (PURGE) &mdash; Action: open air damper
   &darr; Transition (30s timer)
STEP (IGNITION) &mdash; Action: open fuel valve
   &darr; Transition (flame detected)
STEP (WARMUP) &mdash; Action: ramp setpoints
   &darr; Transition (COT > target)
END_STEP (RUNNING)
</pre>

<h3>6. APC Engagement</h3>
<pre>
Regulatory PID loops (online, Cascade mode)
   &darr;
WATCHDOG (monitors APC health)
SP_HANDOFF (switches between APC SP and operator SP)
MV_CLAMP (enforces APC output limits)
APC_CONTROL (DMC/MPC interface)
SHED_LOGIC (auto-shed to regulatory on fault)
</pre>
""",
    ),
    (
        "Compilation & Runtime",
        """
<h2>Compilation &amp; Runtime</h2>

<h3>Compilation Process (F7)</h3>
<ol>
<li><b>Validation</b> &mdash; checks all blocks have required connections</li>
<li><b>Cycle Detection</b> &mdash; finds cycles in the block graph (BKCAL wires are excluded)</li>
<li><b>Topological Sort</b> &mdash; determines execution order (upstream blocks first)</li>
<li><b>BKCAL Wire Scheduling</b> &mdash; BKCAL wires execute with one-scan delay</li>
<li><b>Result</b> &mdash; a CompiledStrategy with execution order and wire maps</li>
</ol>

<h3>Runtime Execution (F5)</h3>
<p>Each scan cycle (typically 100ms &ndash; 1s):</p>
<ol>
<li><b>Read Inputs</b> &mdash; AI/DI blocks read from SharedDataStore</li>
<li><b>Propagate Wires</b> &mdash; forward wire values from outputs to inputs</li>
<li><b>Execute Blocks</b> &mdash; in compiled order, each block computes its outputs</li>
<li><b>Propagate BKCAL</b> &mdash; backward wire values (one-scan delay)</li>
<li><b>Write Outputs</b> &mdash; AO/DO blocks write back to SharedDataStore</li>
</ol>

<h3>Bumpless Initialization</h3>
<p>When going online, the bridge performs multi-pass BKCAL propagation
to initialize PID outputs from current valve positions.  This prevents
output bumps on download.</p>

<h3>Live Monitoring</h3>
<p>When online, the designer shows:</p>
<ul>
<li><b>Block overlays</b> &mdash; PV, SP, OUT values on PID blocks</li>
<li><b>Mode badges</b> &mdash; AUTO (blue), MANUAL (amber), CASCADE (green)</li>
<li><b>Wire values</b> &mdash; signal values on wire labels (View &rarr; Show Wire Values)</li>
<li><b>Execution order</b> &mdash; numbered badges on blocks (View &rarr; Show Execution Order)</li>
<li><b>Status bar</b> &mdash; scan count, scan time, error count, online duration</li>
</ul>

<h3>Block Bypass</h3>
<p>Right-click a block &rarr; Bypass to skip it during execution.
Bypassed blocks pass their input directly to their output without
processing.  Useful for debugging.</p>
""",
    ),
    (
        "Strategy Files",
        """
<h2>Strategy File Organization</h2>

<h3>Directory Structure</h3>
<pre>
strategies/
  heater/                    &larr; Fired Heater plugin
    *.json                   &larr; top-level control strategies
    control/                 &larr; regulatory control subfolder
      TIC101_COT_Control.json
      Cross_Limited_Combustion.json
    sequence/                &larr; SFC modules
      Burner_Startup_Sequence.json
    equipment/               &larr; equipment modules
      Fired_Heater_F01.json
    _module_classes/         &larr; revisioned Control Module Classes
    versions/                &larr; auto-saved version history
  te/                        &larr; Tennessee Eastman plugin
    (same structure)
</pre>

<h3>Strategy JSON Format</h3>
<pre>
{
  "name": "Strategy Name",
  "blocks": [
    {"id": "uuid", "block_type": "PID",
     "instance_name": "TIC109",
     "x": 300, "y": 200,
     "config": {"tag": "xmeas_9", "Kp": 8.0, "Ti": 450}}
  ],
  "wires": [
    {"id": "uuid",
     "src_block_id": "...", "src_terminal": "OUT",
     "dst_block_id": "...", "dst_terminal": "IN",
     "is_bkcal": false}
  ]
}
</pre>

<h3>Version History</h3>
<p>Every save creates a timestamped copy in the <code>versions/</code>
subfolder. Up to 20 versions are kept.  Use Tools &rarr; Version History
to browse and restore previous versions.</p>

<h3>Templates</h3>
<p>Templates let you save a group of blocks and wires for reuse.
Use Tools &rarr; Template Manager to create, browse, and place templates.</p>
""",
    ),
    (
        "OPC UA — Server & Client",
        """
<h2>OPC UA — the PK's two faces</h2>

<p>On a real Azeo PK the OPC UA <b>server</b> is embedded in the
controller, while the OPC UA <b>client</b> is the EIOC's job — a
separate node that subscribes third-party servers into the system. The
trainer mirrors that split exactly: the PK node serves your modules, and
an EIOC-style field driver reads external servers.</p>

<h3>Tutorial 1 — Serve your modules (the server)</h3>
<ol>
<li><b>Declare it per area</b> in <code>_project.json</code>, beside the
controller:
<pre>"controller": {
  "name": "PK-CTLR-1", "model": "PK100",
  "opcua_server": { "port": 43300, "host": "0.0.0.0" }
}</pre>
Port 4840 is deliberately <i>not</i> used: by OPC Foundation design it
belongs to the Local Discovery Server (<code>opcualds.exe</code>), and
individual servers take their own port — this trainer uses 43300.
<code>"host": "0.0.0.0"</code> binds every interface for remote clients;
omit it for localhost-only (the endpoint is anonymous).</li>
<li><b>Start the trainer</b> and read the log — it prints the exact
endpoint a remote machine types, e.g.
<code>opc.tcp://172.17.10.88:43300/azeo/pk</code>. For remote access,
allow the port once in an <i>administrator</i> PowerShell:
<pre>New-NetFirewallRule -DisplayName "Azeo PK OPC UA" -Direction Inbound
    -Action Allow -Protocol TCP -LocalPort 43300</pre></li>
<li><b>Connect any UA client</b> (UaExpert, another trainer…) and
browse. The address space <i>is</i> the configuration — derived from
the tag database, never mapped by hand:
<ul>
<li><code>Modules/&lt;MODULE&gt;/&lt;BLOCK&gt;/&lt;TERMINAL&gt;</code>
— every block terminal, live. NodeId = the tag itself:
<code>ns=2;s=FIC-101/PID1/OUT</code>.</li>
<li><code>FieldIO/</code> — the writable field points. NodeId = the
bare store tag: <code>ns=2;s=LI-101.PV</code> — typeable straight
from the tag list, no browsing needed.</li>
</ul></li>
<li><b>Quality travels.</b> A terminal's status maps to the OPC UA
StatusCode — a Bad transmitter reads <i>Bad</i> on the wire, never a
plausible number. This is the one thing the Modbus server structurally
cannot say, and the reason to prefer OPC UA for supervisory reads.</li>
<li><b>Writes follow the tag database's rules.</b> Terminals are
read-only on the wire (a wire re-asserts a written terminal on the next
scan, so accepting the write would offer a control that does nothing).
Only tag-database-writable field points accept writes; a write lands on
the store's queue and becomes real when the executive drains it —
the readback shows the store's truth, which is the honest handshake.</li>
</ol>

<h3>Tutorial 2 — Read a third-party server (the client)</h3>
<ol>
<li>In <b>Azeo Explorer ▸ Physical Network ▸ Control Network</b>,
right-click the EIOC and choose <b>Configure OPC UA Client…</b>. Type
the external server's endpoint and press <b>Connect</b>.</li>
<li><b>Browse online</b>: expand the server's address space in the tree
(folders load as you open them — the EIOC's "online browsing of
signals" flow; offline Nodeset import is deliberately not offered).</li>
<li>Select a variable, pick the <b>store tag</b> it should feed, choose a
<b>direction</b>, and <i>Add signal</i>:
<ul>
<li><b>read</b> — the external server feeds this tag (a
measurement; use a tag the database marks writable-from-outside, i.e. an
input block's tag).</li>
<li><b>write</b> — this controller's output is forwarded to the
external server (a command the controller owns).</li>
</ul></li>
<li><b>Save</b> writes the binding into <code>_project.json</code> under
<code>field_io</code> (<code>type: "opcua"</code>) — the readable
contract, like <code>modbus_map.py</code> is for Modbus. It attaches at
the next launch.</li>
<li><b>Behaviour on the wire</b>: reads are subscriptions (the server
pushes; no polling); a value arriving with a not-Good status is
<i>not published</i>, so the reading block goes Bad the honest way; a
lost session stops publishing and reconnects in the background;
controller writes drain the store's queue out to the server.</li>
</ol>

<h3>The two-trainer exercise</h3>
<p>Trainer A runs the process and serves OPC UA; trainer B binds A's
<code>FieldIO</code> points through the EIOC OPC UA Client dialog. B then
reads A's measurements live and issues supervisory writes that A's
executive makes real — one process, two consoles, a real protocol
boundary between them.</p>
""",
    ),
    (
        "About Control Designer",
        """
<h2>About Control Designer</h2>

<h3>Process Simulator Control Designer</h3>
<p>A function-block diagram (FBD) strategy designer inspired by
professional industrial control engineering tools.</p>

<h3>Features</h3>
<ul>
<li>Visual FBD strategy design with drag-and-drop block placement</li>
<li>60+ function block types across 8 categories</li>
<li>IEC 61131-3 SFC (Sequential Function Chart) programming</li>
<li>Equipment Module organization (ISA-88 / ISA-95)</li>
<li>Real-time compilation with cycle detection</li>
<li>Live online monitoring with block overlays</li>
<li>PID faceplate integration with tuning</li>
<li>Strategy versioning and comparison</li>
<li>Template management for reusable patterns</li>
<li>ISA-101 HMI color conventions</li>
</ul>

<h3>Standards</h3>
<ul>
<li><b>IEC 61131-3</b> &mdash; Programmable controller languages (SFC, FBD)</li>
<li><b>ISA-5.1</b> &mdash; Instrumentation symbols and identification</li>
<li><b>ISA-88</b> &mdash; Batch control (equipment hierarchy)</li>
<li><b>ISA-101</b> &mdash; HMI design and color conventions</li>
<li><b>ISA-18.2</b> &mdash; Alarm management</li>
</ul>

<h3>Technology</h3>
<ul>
<li>Python 3.10+ with PySide6 (Qt 6)</li>
<li>pyqtgraph for real-time trend display</li>
<li>SQLite historian for data recording</li>
</ul>
""",
    ),
]


# ── Dialog ────────────────────────────────────────────────────────────

_DLG_STYLE_SHEET = f"""
QDialog {{
    background: {UI.pane};
}}
QSplitter::handle {{
    background: {UI.border};
    width: 1px;
}}
QListWidget {{
    background: #F7F8FA;
    border: 1px solid {UI.border};
    border-radius: 3px;
    font-size: 9pt;
    color: {UI.blue};
    outline: none;
}}
QListWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid #E8EAF0;
}}
QListWidget::item:selected {{
    background: {UI.blue};
    color: #FFFFFF;
}}
QListWidget::item:hover {{
    background: {UI.hover};
}}
QTextBrowser {{
    background: #F7F8FA;
    border: 1px solid {UI.border};
    border-radius: 3px;
}}
"""


class ControlDesignerHelpDialog(QDialog):
    """Comprehensive help dialog for Control Designer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from azeo_control_trainer.core.presentation.app_icon import get_app_icon
        self.setWindowIcon(get_app_icon(application_id="control_designer"))
        self.setWindowTitle("Control Designer Help")
        self.setMinimumSize(800, 520)
        self.resize(960, 640)
        self.setStyleSheet(_DLG_STYLE_SHEET)

        self._build_ui()
        # Select first topic
        self._topic_list.setCurrentRow(0)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        # Header
        hdr = QLabel("Control Designer Help")
        hdr.setFont(QFont("Segoe UI", 12, QFont.Bold))
        hdr.setStyleSheet(f"color: {UI.blue}; padding: 6px 8px; background: transparent;")
        layout.addWidget(hdr)

        # Splitter: topic list (left) | content (right)
        splitter = QSplitter(Qt.Horizontal)

        self._topic_list = QListWidget()
        for title, _ in _TOPICS:
            item = QListWidgetItem(title)
            self._topic_list.addItem(item)
        self._topic_list.setFixedWidth(200)
        self._topic_list.currentRowChanged.connect(self._on_topic_changed)
        splitter.addWidget(self._topic_list)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        docs = project_root() / "docs"
        self._browser.document().setBaseUrl(
            QUrl.fromLocalFile(str(docs) + "/"))
        self._browser.setSearchPaths([str(docs)])
        splitter.addWidget(self._browser)

        splitter.setSizes([200, 700])
        layout.addWidget(splitter, 1)

    def _on_topic_changed(self, row: int):
        if 0 <= row < len(_TOPICS):
            title, html = _TOPICS[row]
            if title == "About Control Designer":
                from azeo_control_trainer.core.presentation.product_information import (
                    product_information_html,
                )
                html += product_information_html("control_designer")
            self._browser.setHtml(_HTML_STYLE + html)

    def show_topic(self, title: str) -> bool:
        """Select a topic by its list title; False if no such topic."""
        for row, (topic_title, _) in enumerate(_TOPICS):
            if topic_title == title:
                self._topic_list.setCurrentRow(row)
                return True
        return False
