"""Searchable, context-sensitive help for Graphics Designer.

Help is part of the engineering surface, not a link to a repository file.
The center stays modeless so an engineer can follow a procedure while
authoring, and every topic has a stable key so F1 can route from the active
pane without guessing from prose.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)
from azeo_control_trainer.core.presentation.authoring_dialog import (
    add_authoring_dialog_header,
    mark_primary_action,
)
from azeo_control_trainer.core.presentation.product_information import ProductAboutDialog

from .configurator.designer import CONFIGURATOR_HELP
from .studio import WF


@dataclass(frozen=True)
class HelpTopic:
    """One addressable page in the Graphics Designer knowledge base."""

    key: str
    category: str
    title: str
    summary: str
    keywords: tuple[str, ...]
    html: str

    @property
    def search_text(self) -> str:
        body = re.sub(r"<[^>]+>", " ", self.html)
        return " ".join((self.title, self.category, self.summary,
                         *self.keywords, body)).casefold()


def _page(title: str, lead: str, body: str) -> str:
    return f"<h1>{title}</h1><p class='lead'>{lead}</p>{body}"


TOPICS = (
    HelpTopic(
        "machine_pvms", "Reusable engineering", "VFD, turbine and compressor PVMs",
        "Pair a speed loop and run controller with a reusable machine faceplate.",
        ("vfd", "turbine", "compressor", "speed", "governor", "run", "stop", "tapered"),
        _page("Machine PVMs", "Use the machine symbol on the display and open its controls on demand.", """
<p>In the PID PVM library choose <b>VFD Speed</b>, <b>Turbine Speed</b> or
<b>Compressor Speed</b>. Set <b>Path</b> to the speed-controller PID block.
In the instance properties, browse <b>Run control (DEVCTL)</b> to the machine's
device-control block. Both tags must resolve before publication; the two tags
may be in different control modules.</p>
<p>The display shows speed PV/SP and separate run/fault status. Double-click
in the operator display to open the paired faceplate. Its left side contains
the standard loop PV, SP, output, mode and alarm controls; its right side
contains device start/stop, transition timing, run feedback and faults.
Permission checks and controller interlocks still decide whether a command
is allowed. A click never forces feedback or bypasses a protection.</p>
<p>Use the loop Detail action to view or tune the speed controller, and History
to open PV/SP/OUT trends when the host provides those services. For drive-specific
diagnostics, open the DEVCTL detail through its normal status PVM. Turbine trip,
overspeed protection, VFD parameters and hardware drivers remain controller
functions; this faceplate does not create them.</p>
<p>For drawing only, choose <b>Turbine (tapered)</b> or <b>Compressor (tapered)</b>
in Rotating symbols. The compressor mirrors the turbine geometry. Existing
symbols remain available. DEVCTL also provides <b>Tapered Turbine</b> and
<b>Tapered Compressor</b> status PVMs. Connect to visible ports or the symbol
perimeter; mirroring and rotation carry the ports with the artwork.</p>
""")),
    HelpTopic(
        "blocked_pipe_routes", "Release and diagnostics", "Red dashed pipe routes",
        "Identify and correct a route that cannot clear an obstruction.",
        ("pipe", "routing", "red", "dotted", "dashed", "obstacle", "background"),
        _page("Red dashed pipe routes", "A red dashed pipe is a routing diagnostic, not a process alarm.", """
<p>Hover over the pipe to read the reason and obstruction identifiers. The
fallback keeps both connections visible while the normal route is unavailable.</p>
<p>Right-click the pipe and choose <b>Routing → Show blocking objects</b> to
select and reveal its obstacles without changing the route. After moving an
obstacle, use <b>Reroute now</b> to check the result.</p>
<p>Move the obstruction, choose an appropriate equipment port, or adjust the
manual bends. Committed moves and resizes recheck affected routes, including
pipes not connected to the moved obstruction. Text routing uses the lettering
instead of the unused area of a large label box.</p>
<p>Compact equipment routing uses the fitted symbol and its rotation, not
the empty sides of its selection frame. Moving a clear bend beside a small
valve must not create a blocked-route warning. The symbol still requires the
configured pipe clearance; dragging a bend into that area is reported.</p>
<p>For a panel background intended to surround equipment, select the panel and
clear <b>Routing obstacle</b> in its properties. Keep that option enabled for
real equipment. Do not change a red pipe's paint color to hide a blocked route.</p>
""")),
    HelpTopic(
        "engineering_training", "Release and diagnostics", "Assemblies and commissioning",
        "Build repeated equipment arrangements, verify display states, and train against a running controller.",
        ("assembly", "bulk", "mapping", "commissioning", "training", "checklist",
         "quality", "readability", "PA", "procedure", "release", "readiness", "overlap", "clipping"),
        _page("Assemblies and commissioning", "Prepare an HMI for an engineering exercise.", """
<p><b>Insert → Assemblies</b> opens equipment templates and bulk control-block mapping.
Choose a template or the current display, select real target blocks, and use
<b>Preview mapped bindings</b> before applying. Save a selected equipment train
as an assembly to reuse its PVMs and internal connections.</p>
<h2>Equipment packages and compatible tags</h2>
<ol><li>Search the source selector for <b>Pump and VFD</b>, <b>Vessel and outlet</b>,
<b>Compressor and speed control</b>, or <b>Turbine and speed control</b>.
Existing control-loop and saved project assemblies remain available.</li>
<li>Choose each required target. A machine has two independent references:
its PID speed regulator and DEVCTL run sequencer. Selecting the speed tag does
not guess the run-control tag. Repeated uses of the same reference need one choice.</li>
<li>The inline list contains compatible block families. <b>Browse…</b> uses the
shared Control Object Browser: search module, block, equipment description or type.
Expand a block and select a parameter to inspect available units, range, quality
and source write capability. These are source facts; operator authorization is
checked again when an operator issues a command.</li>
<li>Choose <b>Preview mapped bindings</b>. Every failed row identifies its missing
or incompatible target. The preview uses the destination display's L1–L4 level
and can be viewed in each operator theme. It samples when built; use Quick Online
after placement for live faceplates and trends. Changing preview themes never
changes the display or the engineering theme.</li>
<li>Set the X/Y insertion position and choose <b>Place assembly</b>. The class
dimensions, grouping and internal pipe connections are retained. One Undo removes
the placement. The next X position advances by the assembly width plus spacing;
review the position before placing the next package.</li></ol>
<h2>Duplicate or remap existing equipment</h2>
<p>Select an equipment group on the canvas and choose <b>Use selected equipment</b>
to duplicate it with new references. Alternatively choose <b>Selected equipment —
remap in place</b> to update only that selection. The original remains intact
when duplicating; unrelated objects remain intact when remapping a selection.
Internal pipe endpoints and manual bends use the existing clipboard and routing
rules. Reusing a saved assembly follows the same mapping checks.</p>
<p><b>Reload source</b> captures the current selection or display again. Preview
is required after changing mappings. A canvas edit invalidates the reviewed
document, and Apply rechecks current control objects so deleted or changed
modules cannot silently accept an old preview. Save, Verify and Publish remain
separate actions. Templates do not configure controller limits or create tags.</p>
<p><b>Review → Checklist</b> saves expected normal, alarm, bad-quality, manual,
interlocked and communication-loss cases. Choose actual parameter paths,
run the state checks, then preview each case in TEST and record visual evidence.
TEST blocks process writes. Closing the checklist restores earlier preview data.</p>
<p>Save the checklist and display. Verify and Publish report failed checks and
reviews made stale by display edits.</p>
<h2>PA assemblies and procedure revisions</h2>
<ol><li>Create the <a href='help:procedure_hmi'>Procedure HMI blueprint</a> in
PVM Configuration Designer, or use an existing authored PA class. Save the
procedure in PA Designer so it has a project revision.</li>
<li>Open <b>Insert → Assemblies</b> and select <b>PA · your class</b>.
The gallery discovers classes with a typed Procedure Reference interface.
Select a saved YAML revision in each Procedure row; control-block rows remain separate.</li>
<li><b>Refresh references</b> reloads saved revisions. Invalid files are omitted
from the picker and reported in its status tooltip. A new unsaved PA draft is
not a revision and cannot be selected.</li>
<li>Preview and place. The PVM state, procedure commands, conditions, tuning,
trends and history retain their shared ProcedureRef through the paired class.
Apply rechecks the YAML and mapping files; a changed revision requires another preview.</li></ol>
<p>Selection remapping also works for existing PA instances. Free text,
equipment tags, action names and faceplate class names are not procedure references.
PA previews show advisory design state; they do not start or control a procedure.</p>
<h2>Continuous Problems and readability</h2>
<p>Click the check-status button in the status bar, or open <b>View → Problems</b>.
Automatic checks wait for edits to settle and
yield between objects. They pause during pointer gestures and modified text-field
entry. Switching documents cancels the old check. A pending or unavailable status
is not a pass; <b>Check now</b> or <b>Verify</b> checks the current draft explicitly.</p>
<p>Select an operator theme and viewport in the Problems panel. Checks use the
current display's L1–L4 level and authored frame, independently of canvas zoom.
Errors identify broken configuration. Advisory rows identify clipped static labels,
undersized PVMs, foreground readout overlap, text below 7 pt after scaling and text
contrast below the 4.5:1 authoring target. These are Azeo authoring checks, not a
claim of standards certification. Equipment, panels and pipes are excluded from
readout overlap tests. Dynamic colors and complex backgrounds still need visual review.</p>
<p>Double-click a row to locate its object. Right-click a fixable row for
<b>Fit text</b> or <b>Restore standard size</b>. The fix enlarges the object
without shrinking its text, reroutes attached pipes and creates one Undo step.
It rechecks the current object; a deleted or already-corrected item is not changed.
Unlock a locked object first, and edit the master of a reusable class to correct
its member geometry. Review neighboring spacing after resizing.</p>
<p>In <b>Quick Online → Check readability</b>, the same visual checks use the
selected operator theme, viewport and display level. Corrections are made in
Graphics Designer; resend the draft to update its preview. Theme or viewport changes
clear the previous preview report so stale results cannot appear current.</p>
<h2>Complete commissioning and prepare a release</h2>
<ol><li>In Checklist, enter a resolved parameter and choose <b>Add applicable
states</b>, or select equipment on the canvas and choose <b>Add selected equipment</b>.
Repeated additions do not duplicate cases. Manual and interlock cases are offered
only when the source exposes those states.</li>
<li>Run <b>Check all</b>. Preview a case, inspect its appearance, record the
expected appearance and observed evidence, then choose <b>Record review and next</b>.
<b>Next pending case</b> locates the next incomplete case without claiming a review.</li>
<li>Choose <b>Save checklist and draft</b>. Save failures preserve the in-memory
checklist and report the error. Editing the display invalidates earlier evidence.</li>
<li>Open <b>Review → Release readiness</b>. This shows current errors, advisories,
checked cases and visual reviews. It links to the checklist, operator preview and
revision comparison. No saved cases means coverage has not been established.</li>
<li>Resolve errors and complete any saved cases. <b>Save draft and review publication</b>
saves the draft and opens the existing TEST/PROD publication review. The final
publish action reruns verification and uses the existing station retrieval and
Refresh workflow. A readiness report does not grant publication authority.</li></ol>
<p><b>Save readiness report</b> writes a JSON record under the display store's
<b>_commissioning</b> folder, including the document digest, operator context,
findings and checklist counts. It is a snapshot, not approval of later changes.</p>
<p>In <b>Operator Live → Tools</b>, Training sessions uses process snapshots,
input faults and objective evidence. Loop diagnosis compares recorded responses.
Alarm investigation supplies timed shelving and the existing condition faceplates.
Saved session reports retain the observations for later review.</p>
""")),
    HelpTopic(
        "getting_started", "Start here", "Getting Started",
        "The shortest path from a blank display to a verified revision.",
        ("start", "new display", "workflow", "first display"),
        _page("Getting Started",
              "Graphics Designer authors the operator layer over the same "
              "live tag store used by Control Designer.", """
<div class='callout'><b>The normal engineering loop</b><br>
Create or open a display &rarr; place and bind objects &rarr; Verify &rarr;
Test &rarr; Save &rarr; Publish.</div>
<ol>
  <li><b>Create a display.</b> Use File &gt; Display or right-click the
      Displays folder in Graphics Explorer.</li>
  <li><b>Place content.</b> Choose drawing elements from Insert/Palette or
      place a registered PVM from the Control or Library Explorer.</li>
  <li><b>Configure.</b> Select an element and edit the grouped Graphics
      Configuration properties. Bind process values using the binding
      editor rather than typing display-time values into the drawing.</li>
  <li><b>Verify (F8).</b> Resolve every invalid path, expression, action and
      class dependency before the display can be published.</li>
  <li><b>Test (F5).</b> Exercise bindings, navigation, user entries and
      faceplates in the read-only runtime surface. The Test Data dock can
      overlay values, quality, alarm and forced states without writing to the
      controller.</li>
  <li><b>Save (Ctrl+S).</b> Saving updates the engineering draft only.
      <b>Publish</b> creates the revision consumed by stations.</li>
</ol>
<p>Use <a href='help:authoring_workflow'>Authoring Workflow</a> for the
complete lifecycle or <a href='help:keyboard_shortcuts'>Keyboard and Mouse</a>
for the fastest commands.</p>
""")),
    HelpTopic(
        "authoring_workflow", "Start here", "Authoring Workflow",
        "Draft, verify, test, publish, history and workstation assignment.",
        ("draft", "verify", "publish", "revision", "quick online",
         "live", "refresh", "control designer"),
        _page("Authoring Workflow",
              "A display is an engineered document with a controlled "
              "lifecycle, not a loose image file.", """
<table><tr><th>Stage</th><th>Purpose</th><th>Command</th></tr>
<tr><td>Author</td><td>Edit the private working draft.</td><td>Save / Ctrl+S</td></tr>
<tr><td>Preflight</td><td>Check bindings, scripts and dependencies.</td><td>Verify / F8</td></tr>
<tr><td>Exercise</td><td>Run the draft without publishing it.</td><td>Test / F5 or Quick Online</td></tr>
<tr><td>Release</td><td>Create an immutable station revision.</td><td>Publish</td></tr>
<tr><td>Deploy</td><td>Assign layouts and display sets to a workstation.</td><td>Graphics Explorer</td></tr>
<tr><td>Retrieve</td><td>Let Live detect the published revision without replacing an active display.</td><td>Automatic</td></tr>
<tr><td>Accept</td><td>Move a previously viewed display to the waiting revision at an operator-safe time.</td><td>Live Refresh</td></tr>
</table>
<div class='warning'><b>Save is not Publish.</b> A saved draft remains an
engineering work product. Stations use the accepted published revision.</div>
<h2>Which application opens what?</h2>
<ul><li><b>Graphics Designer</b> authors drafts, runs Quick Online and publishes
revisions. Quick Online never changes a station.</li>
<li><b>Live</b> is the dedicated operator runtime and reads only published
configuration assigned to its workstation.</li>
<li>The faceplate engineering icon opens that module in <b>Control
Studio</b>. It does not return to Graphics Designer or bypass publication.</li></ul>
<p>A display Live has never cached opens at the newest assigned revision.
Publishing a display already viewed lights Refresh instead of changing the
screen underneath the operator.</p>
<h2>Before publishing</h2>
<ul><li>No unresolved bindings or missing class dependencies.</li>
<li>Every visible button has a defined action and every alarm list has a
meaningful scope.</li><li>Test navigation at the workstation's intended
resolution and verify the display hierarchy.</li></ul>
""")),
    HelpTopic(
        "display_creation", "Tutorials", "Create and Configure a Display",
        "A complete first-display procedure from project tree to publication.",
        ("display creation", "new display", "configure", "hierarchy",
         "data link", "user entry", "publish"),
        _page("Create and Configure a Display",
              "Build a governed operator display, bind it to real process "
              "data, exercise it, and publish a controlled revision.", """
<h2>1. Create the document</h2>
<ol>
<li>Open <b>Graphics Explorer</b> (Ctrl+1).</li>
<li>Right-click <b>Displays</b> and choose <b>New Display…</b>, or use
File &gt; Display / Ctrl+N.</li>
<li>Give it an engineering name that describes the process scope, such as
<code>L1-Overview</code> or <code>Boiler-Feedwater</code>.</li>
<li>Choose <b>Blank display</b> or <b>From display template</b>. The template
list includes the product templates and project-authored display templates.
The dialog shows the level, asks for the parent display and names the
destination folder and file before you create. The new draft is an
independent copy, and the Graphics Explorer selects it.</li>
</ol>
<div class='callout'><b>New-display default:</b> a blank display starts at
L1 Overview on the standard 1600 × 900 operator page. Change Level only when
the display is intentionally a unit, detail or diagnostic child; then set its
Parent to the display above it.</div>
<h2>2. Define display-level configuration</h2>
<p>Right-click the display in Graphics Explorer and choose
<b>Properties…</b>. The same fields remain available by clicking empty canvas
so Graphics Configuration shows the display. Complete them before drawing:</p>
<table><tr><th>Group</th><th>Field</th><th>Engineering decision</th></tr>
<tr><td>Hierarchy</td><td>Level</td><td>L1 overview, L2 unit control, L3 equipment detail or L4 diagnostic.</td></tr>
<tr><td>Hierarchy</td><td>Parent</td><td>The display above this one in automatic navigation.</td></tr>
<tr><td>Information</td><td>Title / Description</td><td>Operator-facing identity and engineering intent.</td></tr>
<tr><td>Size</td><td>Width / Height</td><td>Target display frame; use Auto only when the assigned layout defines it.</td></tr>
<tr><td>Size</td><td>Fit / View Type</td><td>Whether to preserve scale, fit the frame or scale to it.</td></tr>
<tr><td>Background</td><td>Color</td><td>Prefer a Standard reference so themes remain consistent.</td></tr></table>
<div class='callout'><b>Page boundary:</b> a dotted rectangle marks the exact
configured Width × Height in Edit mode. It is an authoring guide and is never
saved as an element or shown in Test, published graphics or the operator
station. Auto/content-bound canvases intentionally have no fixed boundary.</div>
<h2>3. Enter Edit and compose the static layer</h2>
<ol start='4'>
<li>Choose <b>Edit</b>. The first edit acquires the display's single-writer
lock.</li><li>Place equipment symbols, pipes, shapes and text. Use exact
Geometry fields for repeatable alignment, then Group related parts.</li>
<li>Use the <b>Selection</b> pane (Ctrl+4) to name, lock, hide and reorder
objects. A hidden object remains recoverable there.</li></ol>
<h2>4. Add process data</h2>
<ol start='7'>
<li>Open <b>Control Data</b> (Ctrl+3). Expand a configured module and
function block. Drag a terminal or CONFIG parameter to create a bound Data
Link directly. You can also use Insert &gt; Data Link and browse its path.
Examples: <code>AREA/MODULE/PV</code> and
<code>AREA/MODULE/CONFIG/HI_LIM</code>.</li>
<li>For a reusable process object, drag its function-block row from Control
Data or place a registered class from Function Block / High Performance PVMs /
Process PVMs. Set its Control Tag; do not copy its internal shapes into every
display.</li>
<li>Add charts, alarm lists or tables only after defining their scope,
history pens and bindings.</li></ol>
<h2>5. Add operator interaction</h2>
<ol start='10'>
<li>Use User Entries for writable values. The path must resolve to an
operator-owned input; algorithm outputs remain read-only.</li>
<li>Configure Display Links and button actions on the Interaction tab.
Remove any icon whose runtime service is unavailable.</li>
<li>If behavior needs a script, edit the selected element or display event
and validate it through Script Assistant. Control decisions remain in the
control module.</li></ol>
<h2>6. Verify, exercise and release</h2>
<ol start='13'><li><b>Verify (F8)</b>: resolve every invalid path, missing
class, action and script error.</li><li><b>Test (F5)</b>: check navigation,
Bad quality, alarms, faceplates and user-entry ownership.</li>
<li><b>Save (Ctrl+S)</b> the draft. Save is not release.</li>
<li><b>Publish</b> the accepted revision, then assign its display set and
layout to the intended workstation.</li></ol>
<div class='callout'><b>Completion check:</b> every number has a source,
every status has a producer, every visible control has a handler, and every
operator route has a published target.</div>
""")),
    HelpTopic(
        "l1_l4_hierarchy", "Tutorials",
        "Create an L1-L4 Operator Display Hierarchy",
        "Install, configure, test and release the four Azeo-style levels.",
        ("l1", "l2", "l3", "l4", "hierarchy template", "isa-101",
         "overview", "primary operation", "support display"),
        _page("Create an L1-L4 Operator Display Hierarchy",
              "The four levels answer different operator questions.  The "
              "Hierarchy Pack creates the documents and navigation together; "
              "individual built-in templates extend an existing hierarchy.", """
<h2>What each level must accomplish</h2>
<table><tr><th>Level</th><th>Operator question</th><th>Content boundary</th></tr>
<tr><td>L1 High Level Overview</td><td>In four seconds, is my domain OK?</td><td>Situational awareness, alarm counts, normalized KPIs and decision-needed indication. No routine control.</td></tr>
<tr><td>L2 Primary Operation</td><td>What do I operate every day?</td><td>Subsystem process path and the controls needed for roughly 80% of routine work, with minimum navigation.</td></tr>
<tr><td>L3 Secondary Operation</td><td>Which major component is not doing what I expect?</td><td>Equipment detail, non-routine operation, diagnostics and the traditional process-flow view.</td></tr>
<tr><td>L4 Support Display</td><td>What failed first, and what do I do now?</td><td>Procedures, permits, interlocks, contextual alarms, faceplates and interactions between loops.</td></tr></table>
<h2>Route A — create a complete linked sample</h2>
<ol><li>Choose File &gt; <b>L1-L4 Hierarchy</b>.</li>
<li>Enter the area, unit or training scope. Studio creates four editable
drafts, one four-level Display Set and one routed operator Layout. Existing
artifacts are never overwritten.</li>
<li>Open Graphics Explorer &gt; Display Sets and inspect the L1 → L2 → L3 →
L4 chain. Open each display and test its Up/Next Display Links.</li></ol>
<h2>Route B — add one level to an existing hierarchy</h2>
<ol><li>Choose <b>New Display</b> and select <b>From display template</b>.
File &gt; <b>From Template</b> opens the same dialog with that choice selected.</li>
<li>Select one <code>ISA-101 - L#</code> template and give the new
display its project name.</li><li>Set Parent, configure its Display Links and
add it to the intended Display Set. A template is copied once; later template
changes do not alter your display.</li></ol>
<h2>Replace the honest placeholders</h2>
<ol><li>Keep the muted panels and process path only when they express the
real plant topology. Delete demonstration equipment that does not.</li>
<li>Replace each labelled PVM slot with a registered Process PVM, then set
its Control Tag and instance configuration.</li><li>Replace sample table rows
with live Data Links or configured table cells. Scope every Alarm List to the
responsible area/module.</li><li>At L1, normalize unlike KPIs before comparing
them. Do not make alarm red the ordinary operating colour.</li></ol>
<h2>Verify, test and deploy</h2>
<ol><li>Run <b>Verify</b> on all four displays. Every path, PVM class, action
and target must resolve.</li><li>Use <b>Quick Online</b> while authoring, then
Test the Display Set: home, up, next level, faceplates, alarm scope, Bad
quality and operator writes.</li><li>Save drafts for review. Publish each
accepted display; Save alone never deploys.</li><li>Assign the generated Layout
and Display Set to the target workstation and use Refresh Configuration in
the operator station.</li></ol>
<div class='callout'><b>Acceptance:</b> L1 detects the need to act, L2 supports
routine action, L3 explains equipment behavior, and L4 supports the recovery
task. If two levels answer the same question, revise the hierarchy.</div>
""")),
    HelpTopic(
        "class_builder_workflow", "Tutorials",
        "Visual PVM and Faceplate Class Builder",
        "The complete paired-class workflow from a blank master to affected-display publication.",
        ("class builder", "public internal", "typed drop target",
         "class master", "affected display", "instance configuration",
         "table alarm trend user entry"),
        _page("Visual PVM and Faceplate Class Builder",
              "Build one reusable visual contract, bind it without plant-"
              "specific literals, and release every display affected by the "
              "accepted class change.", """
<div class='callout'><b>Use this sequence in order.</b> The class master and
its interface are one engineering artifact. A display instance is linked to
that class, while an operator station continues to use its last accepted
published display revision until the affected display is republished.</div>

<h2>1. Create a paired PVM and faceplate</h2>
<ol><li>Open <b>Library Explorer</b> (Ctrl+2).</li>
<li>Right-click <b>Faceplate Classes</b> and choose
<b>New Paired PVM + Faceplate Blueprint...</b>.</li>
<li>Enter a functional class name such as <code>PIDLoop</code>. Studio
creates the faceplate <code>PIDLoop</code>, the compact caller
<code>PIDLoop_PVM</code>, reciprocal pairing, measured class masters, and a
shared starter interface.</li></ol>

<h2>2. Draw on the class-master canvas</h2>
<p>Open either class with <b>Edit Layout...</b>. The tab identifies a PVM or
Faceplate Class, starts in Edit, and shows checkerboard transparency only
inside the dotted class page. That boundary is the instance coordinate
system. Draw, align, group, name, connect, move and resize members with the
ordinary Graphics Designer tools. Set the root Width and Height deliberately;
class Save preserves its internal whitespace and authoritative master size.</p>
<div class='warning'><b>Publish is intentionally disabled on a class
master.</b> Save updates the engineering library. Published operator content
changes only after each affected display is verified and published.</div>

<h2>3. Separate public parameters from internal properties</h2>
<ol><li>With the class master open, choose <b>Edit Class Interface...</b>,
or right-click the class and choose <b>Configure Properties...</b>.</li>
<li>Set <b>Visibility</b> to <b>Public instance parameter</b> for values an
engineer supplies per placement: control tag, paths, range, units, style or
optional-feature selections.</li>
<li>Set it to <b>Internal class property</b> for derived/helper state used
only by member bindings. Internal properties never appear in Configure
instance and cannot be required, writable or a drop target.</li>
<li>Mark a public property <b>Required</b> only when no valid instance can be
created without it. Required labels carry <code>*</code> in the instance
dialog and Verify reports missing values.</li></ol>

<h2>4. Configure the typed primary drop target</h2>
<ol><li>Select one public <b>Control Tag</b> or <b>Function Block
Reference</b> property, normally <code>ControlTag</code>.</li>
<li>Enable <b>Primary target for a dragged control block or tag</b>.</li>
<li>Enter accepted block types, for example
<code>PID, PID_AT, PID_DEADTIME, FLC</code>. Use <code>*</code> only when the
visual is genuinely type-agnostic.</li></ol>
<p>A class may have exactly one primary drop target. It is automatically
Public, Required and Read only. This typed declaration is what makes the
class eligible when a compatible block is dragged from Control Data;
appearance or a coincidentally named string never qualifies a class.</p>

<h2>5. Bind class members</h2>
<table><tr><th>Member</th><th>Class-master configuration</th></tr>
<tr><td>Text or shape</td><td>Select the member, choose <b>Shape binding...</b>,
select <code>text</code>, <code>visible</code>, <code>fill_pct</code>,
<code>opacity</code> or another bindable property, then reference
<code>Pvm.Title</code>, <code>Pvm.UnitName</code> or a typed internal
property.</td></tr>
<tr><td>Data Link</td><td>Use a Parameter Reference such as
<code>Pvm.PVPath</code>. Do not embed a commissioned module path in a reusable
master.</td></tr>
<tr><td>Table</td><td>Use static cells for labels and live descriptors whose
<code>path</code> is <code>Pvm.PVPath</code>, <code>Pvm.SPPath</code> or
another typed reference.</td></tr>
<tr><td>Alarm List</td><td>Set scope/module path to
<code>Pvm.ModulePath</code>; Ack, Param and Help remain live alarm semantics,
not painted sample rows.</td></tr>
<tr><td>Chart / trend</td><td>Define pens with the same class references used
by PV, SP and OUT. Do not create a parallel path vocabulary for history.</td></tr>
<tr><td>User Entry</td><td>Bind only to a public Parameter Reference whose
Direction is <b>Operator write (checked service)</b>, such as
<code>Pvm.SPPath</code>. Algorithm outputs remain read-only.</td></tr></table>

<h2>6. Drag a control block onto a display</h2>
<ol><li>Save and validate the class, then open an ordinary display in Edit.</li>
<li>Open <b>Control Data</b> (Ctrl+3), expand a module, and drag its
function-block row to the canvas.</li>
<li>When more than one native or authored class accepts the type, choose the
visual class. The selection can be remembered per block type on this
display. Cancel creates no placeholder.</li></ol>
<p>The drop fills the primary target and conventional public fields when
present: ModuleName, ModulePath, PVPath, SPPath and OUTPath. A parameter row,
by contrast, creates a bound Data Link.</p>

<h2>7. Configure the instance</h2>
<p>Right-click the grouped instance and choose <b>Configure instance...</b>.
Only public properties appear. Review auto-filled paths, complete required
fields, ranges, units, selections and optional features, then accept. The
master remains linked; instance values are configuration, not copied member
artwork. Reopen the dialog whenever a placement needs a different public
value.</p>

<h2>8. Validate, Save and publish affected displays</h2>
<ol><li>On each class master choose <b>Validate Reusable Class</b> or Verify.
Resolve interface, pairing and dependency errors, then <b>Save</b> the
class.</li>
<li>In Library Explorer, right-click the class and choose
<b>Find Usages...</b>. This is the authoritative affected-display list.</li>
<li>Open every listed display. Verify required instance values and live
bindings, exercise Test/Quick Online, then Save its engineering draft.</li>
<li>Publish each accepted display. Class Save alone never creates a station
revision. The station accepts or refreshes the published revision through
its normal deployment workflow.</li></ol>

<h2>9. Troubleshoot by contract</h2>
<table><tr><th>Symptom</th><th>First check</th></tr>
<tr><td>Authored class is absent from the drop chooser</td><td>Valid saved
interface, one primary drop target, and accepted block type.</td></tr>
<tr><td>Instance dialog exposes helper fields</td><td>Those properties must be
Internal, not Public.</td></tr>
<tr><td>Required field is empty after a drop</td><td>Use the conventional
public name or complete it in Configure instance; then Verify.</td></tr>
<tr><td>Text/table/trend shows blank or Bad</td><td>The member must reference a
declared <code>Pvm.*</code> property whose instance value resolves to a real
terminal or CONFIG path.</td></tr>
<tr><td>User entry is disabled or refused</td><td>The target property must be
Operator write and the resolved process path must be owned and writable.</td></tr>
<tr><td>Faceplate does not open</td><td>Reciprocal pairing and the same
ControlTag/ModulePath instance values on both classes.</td></tr>
<tr><td>Operator station still shows the old visual</td><td>Find Usages,
publish every affected display, then accept/refresh its station revision.</td></tr></table>
<p>Continue with the <a href='help:illustrated_tutorial'>illustrated
walkthrough</a>, <a href='help:bindings'>Bindings and Data</a>, or the general
<a href='help:troubleshooting'>Troubleshooting</a> reference.</p>
""")),
    HelpTopic(
        "pvm_creation", "Tutorials", "Create and Configure a PVM Class",
        "Create a reusable grouped process object and its typed contract.",
        ("pvm creation", "new pvm class", "convert to pvm", "class layout",
         "typed property", "instance configuration"),
        _page("Create and Configure a PVM Class",
              "A PVM is a reusable class: one authored layout plus one typed "
              "configuration contract, placed many times.", """
<div class='callout'>For the complete paired workflow, including public and
internal properties, typed block drop, instance configuration and release,
use <a href='help:class_builder_workflow'>Visual PVM and Faceplate Class
Builder</a>.</div>
<h2>Choose a creation route</h2>
<table><tr><th>Route</th><th>Use when</th><th>Command</th></tr>
<tr><td>New class</td><td>You are designing a reusable object deliberately.</td><td>Library Explorer &gt; PVM Classes &gt; New PVM Class…</td></tr>
<tr><td>Convert selection</td><td>A tested display composition should become reusable.</td><td>Select members &gt; shortcut menu &gt; Convert to PVM class…</td></tr></table>
<h2>Route A — start from the library</h2>
<ol><li>Open <b>Library Explorer</b> (Ctrl+2), expand Library &gt;
<b>PVM Classes</b>, right-click and choose <b>New PVM Class…</b>.</li>
<li>Name the class by function, not by one plant instance. Use
<code>FeedPumpStatus</code>, not <code>P-101</code>.</li>
<li>Graphics Designer opens the class layout and PVM Configuration Designer.
Keep both open: the layout defines what is drawn; the configurator defines
what each instance supplies.</li></ol>
<h2>Define the typed contract</h2>
<ol start='4'><li>Create a Basic Configuration group and add only the inputs
the class truly varies by instance: Control Tag, Parameter Reference,
Selection, String, Number, Color or Boolean.</li>
<li>Use <code>ModulePath</code>, <code>PVPath</code>, <code>Unit</code> and
named Selection properties rather than embedding a real module name.</li>
<li>Reference project standards as <code>Standard.Name</code>. Reference
class inputs as <code>Pvm.Property</code>.</li>
<li>Use Presence for conditional artwork and Present Online to prevent
unused optional groups from subscribing.</li></ol>
<h2>Author the class layout</h2>
<ol start='8'><li>Place roughly 20–30 purposeful drawing/data elements:
frame, equipment symbol, tag, value, units, mode/status, compact bar and
mini-faceplate action where applicable.</li>
<li>Bind Data Links and dynamic properties to <code>Pvm.*</code> fields. A
class member must never contain the instance name used while testing it.</li>
<li>Name members in Information so later authors can find them. Add named
connection points where pipes should attach.</li>
<li>Group the composition, fit its class frame tightly, and Save the class
layout.</li></ol>
<h2>Route B — convert an existing composition</h2>
<ol><li>Select every intended member, including internal connectors.</li>
<li>Right-click and choose <b>Convert to PVM class…</b>. Studio stores the
class and replaces the selection with one linked instance.</li>
<li>Open Library Explorer &gt; Project, select the new class and choose
<b>Configure Properties…</b>. Replace literal paths with typed properties.</li>
<li>Place a second instance immediately. If changing its configuration
requires editing the class, the contract is incomplete.</li></ol>
<h2>Validate the result</h2>
<ol><li>Right-click the class and choose <b>Validate Class</b>.</li>
<li>Place it on a scratch display, Configure instance, enter Test and force
Good/Bad/alarm/mode states.</li><li>Use <b>Find Usages</b> before changing a
published class. A class edit names every display that must be republished.</li></ol>
<p>Next: <a href='help:pvm_configuration'>PVM Configuration Designer</a> and
<a href='help:faceplate_creation'>Create a Faceplate Class</a>.</p>
""")),
    HelpTopic(
        "procedure_hmi", "Tutorials", "Procedure HMI PVMs and Faceplates",
        "Author and publish advisory procedure graphics using the shared HMI stack.",
        ("procedure", "pilot", "workflow", "high performance", "advisory", "block help"),
        _page("Procedure HMI PVMs and Faceplates", "PA Designer authors logic. Graphics Designer authors the operator HMI.", """
<h2>Create and configure</h2><ol>
<li>Save a procedure revision in PA Designer, under the project's <b>procedures</b> library.</li>
<li>Open PVM Configuration Designer. Open the arrow beside <b>New faceplate</b> and choose
<b>Procedure HMI: PVM, faceplate and detail</b>. Enter a new class name.</li>
<li>The operation creates an editable faceplate, a <b>_PVM</b> caller, graphical workflow <b>_Detail</b>,
and <b>_Conditions</b>, <b>_Tuning</b>, <b>_Trends</b> and <b>_History</b> detail classes.
Use <b>Edit layout</b> for shapes, labels, geometry and ordinary appearance properties.
Find them in Library Explorer under PVM, Faceplate and Detail Display Classes.</li>
<li>Place the caller on a display. Configure <b>ProcedureRef</b> with the relative YAML revision
path beneath <b>procedures/</b>, including revision folders and the filename. Set <b>Title</b>.
Absolute paths and parent-directory traversal are invalid.</li>
<li>Validate, save and Publish. Retrieve/refresh the display in Operator Station through the normal deployment workflow.
The station also needs that project procedure revision and its tag mapping. Graphics publishing does not copy procedure logic.</li></ol>
<h2>Shared High Performance HMI rules</h2>
<p>The blueprint uses the active theme's surfaces, text, outlines and actionable controls.
Ordinary states use text and neutral fills. Procedure prompts do not become process alarms.
Keep the theme-role background when editing; a literal fill intentionally overrides that role.</p>
<h2>Operate and inspect</h2>
<p>Start waits for fresh completed controller scans. Pause is resumable; Held ends the run.
Respond opens the current confirmation, typed entry or comment. Invalid, stale and duplicate responses are refused.
View-only stations cannot issue execution commands. Disabled controls explain why in their tooltip.</p>
<p>Equipment opens the installed process faceplate with its normal write checks. Procedure execution never writes a process parameter.
The workflow draws the live sequence. Conditions shows expressions, observed values, quality, row holds, group hold and timeout.
Page long sequences using Previous/Next; Current step finds the running block. Right-click a workflow row for <b>Block Help</b>, mapped equipment and procedure trends.</p>
<p>Tuning exposes only engineer-selected parameters. In PA Designer's Memory tab configure Operator tuning and units;
in a Wait block expose timer tuning. Live changes restart hold evidence without restarting the overall timeout.
Next run changes are saved once for the same revision and can be cancelled; the active run is unaffected.
Typed limits, station authority, current context and signed change history are enforced by the session and worker.</p>
<p>Trends uses the shared station historian for numeric process and memory values. Quality gaps remain gaps.
Simple numeric comparisons in the active wait appear as labelled current criteria, not historical limits.
Process History View provides pen selection, scales, ranges, archived history, event inspection and export.
History displays current events and recent procedure runs, with access to existing review/compare/export tools.</p>
<p>Closing a faceplate, detail, display or Procedures workspace does not stop execution. Station shutdown ends the run and waits for its final audit.
Use the existing Procedures workspace's Run history for stored-run review, comparison and export.</p>
<h2>Edit bindings and actions</h2>
<p>Read-only state paths use <code>@procedure/{ProcedureRef}/STATE</code>, <code>INSTRUCTION</code>, <code>PROGRESS</code>,
<code>VALUES</code>, <code>STEPS</code>, <code>CONDITIONS</code>, <code>PARAMETERS</code>, <code>HISTORY</code> and <code>EVENTS</code>.
In Configure Data, Live rows source binds a table collection; Presentation = workflow draws STEPS as connected blocks.
Row action = tune opens checked PARAMETERS entries and requires a CONTEXT binding. Set a chart's Shared procedure history
source to <code>@procedure/{ProcedureRef}/TREND</code> with no local pens.</p>
<p>In Configure Interactions, choose <code>procedure_command</code>; Target is start, pause, resume, abort, respond, equipment, tune, trends, history or help.
Set the context source to <code>@procedure/{ProcedureRef}/CONTEXT</code>. Leave Value blank: operator responses are collected at runtime.
Keep the corresponding <code>CAN_*</code> enabled animation and <code>REASON_*</code> tooltip.
Graphics Designer previews are explicitly labelled design previews and do not start procedures.</p>
<p>Unavailable state means the reference, revision, simulator or controller observations need attention.
Check the selected project, copied procedure/mapping files, running modules and completed scans.</p>
""")),
    HelpTopic(
        "faceplate_creation", "Tutorials", "Create a Faceplate Class",
        "Build a paired, reusable operator faceplate with live controls.",
        ("faceplate creation", "paired pvm", "blueprint", "alarm table",
         "trend", "special symbol", "user entry"),
        _page("Create a Faceplate Class",
              "The recommended workflow creates the full faceplate, its "
              "compact calling PVM and compatible typed contracts together.", """
<div class='callout'>This procedure is the faceplate-specific anatomy. Follow
<a href='help:class_builder_workflow'>Visual PVM and Faceplate Class
Builder</a> for the complete class-master, typed-drop, instance and
affected-display release sequence.</div>
<h2>1. Create the paired blueprint</h2>
<p>For advisory procedures, use the specialized <a href='help:procedure_hmi'>Procedure HMI blueprint</a>
from the New faceplate arrow in PVM Configuration Designer.</p>
<ol><li>Open <b>Library Explorer</b> (Ctrl+2).</li>
<li>Right-click <b>Faceplate Classes</b> and choose
<b>New Paired PVM + Faceplate Blueprint…</b>.</li>
<li>Enter a functional class name such as <code>PIDLoop</code>. Studio
creates <code>PIDLoop</code> for the faceplate and
<code>PIDLoop_PVM</code> for its compact caller, pairs them reciprocally,
and opens their editable class documents.</li></ol>
<h2>2. Configure the shared inputs</h2>
<p>The blueprint starts with these typed properties. Rename or extend them
only when the faceplate's real behavior requires it:</p>
<table><tr><th>Property</th><th>Purpose</th><th>Typical instance value</th></tr>
<tr><td>ModuleName</td><td>Header module/block identity</td><td>FIC-101/PID1</td></tr>
<tr><td>Title / Description</td><td>Operator-facing identity</td><td>FLOW CONTROL / Feed flow controller</td></tr>
<tr><td>PVPath</td><td>Process value and PV scale input</td><td>AREA/PID1/PV</td></tr>
<tr><td>SPPath</td><td>Working setpoint and SP write target</td><td>AREA/PID1/SP</td></tr>
<tr><td>OUTPath</td><td>Controller output</td><td>AREA/PID1/OUT</td></tr>
<tr><td>ModulePath</td><td>Alarm and contextual scope</td><td>AREA/PID1</td></tr>
<tr><td>EU0 / EU100</td><td>PV scale endpoints</td><td>0 / 200</td></tr>
<tr><td>UnitName</td><td>Area or unit caption</td><td>Area 1</td></tr></table>
<h2>3. Build the full faceplate layout</h2>
<ol start='4'><li>Keep the blueprint's measured fixed shell. A contextual
faceplate should not expand because one table happens to have more text.</li>
<li>Header: module, title, description, simulation/alarm state and mini or
return action.</li><li>Process body: PV and OUT numerics, PV scale on the
left, light PV channel, centered dark PV bar, working-SP marker, alarm-limit
marks and mode indications.</li><li>Operator inputs: use User Entry controls
bound through <code>Pvm.SPPath</code>, mode/request properties and writable
configuration targets. Do not turn an algorithm output into an editable
field.</li><li>Trend: bind the same PV/SP/OUT sources; do not create a second
path vocabulary for the chart.</li><li>Alarm list/table: scope it to
<code>Pvm.ModulePath</code>, preserve Ack/Param/Help meaning, and test active,
acked and cleared-unacked rows.</li></ol>
<h2>4. Add only serviced Special Symbols</h2>
<table><tr><th>Symbol</th><th>Required runtime service</th></tr>
<tr><td>Module detail</td><td>Registered detail-display class</td></tr>
<tr><td>Primary display</td><td>Published display target and navigation action</td></tr>
<tr><td>Process History</td><td>Historian tags and Process History View</td></tr>
<tr><td>Acknowledge</td><td>Faceplate-scoped visible alarm registry</td></tr>
<tr><td>Control Designer / block faceplate</td><td>Host integration that can open it</td></tr></table>
<div class='warning'>An icon without a service is removed, not left as a
clickable promise. The same rule applies to the compact PVM caller.</div>
<h2>5. Complete the compact caller</h2>
<ol start='10'><li>Open the paired <code>_PVM</code> layout. Keep only the
at-a-glance values needed on the process display: tag, PV, OUT/mode, alarm or
quality state, compact bar and mini-faceplate action.</li>
<li>Bind it through the same typed inputs. Double-click and the mini action
must both open the paired faceplate instance for the same module path.</li></ol>
<h2>6. Validate and exercise</h2>
<ol start='12'><li>Save both layouts and both configuration documents.</li>
<li>Validate each class. Pairing must be reciprocal; every nested dependency
and <code>Pvm.*</code> reference must resolve.</li><li>Place the compact PVM,
choose <b>Configure instance…</b>, fill every path and range, then Test.</li>
<li>Exercise Good, Bad, alarm, simulate, mode ownership, SP write, faceplate
reuse, trend, detail and acknowledgement.</li><li>Publish every display named
by Find Usages after the class is accepted.</li></ol>
<p>Next: <a href='help:pvm_configuration'>PVM Configuration Designer</a> or
the <a href='help:illustrated_tutorial'>full illustrated walkthrough</a>.</p>
""")),
    HelpTopic(
        "pvm_configuration", "Tutorials", "PVM Configuration Designer",
        "Property types, Selection grids, Presence, Present Online and preview.",
        ("pvm configurator", "configuration designer", "selection",
         "presence", "present online", "property group", "standard"),
        _page("PVM Configuration Designer",
              "Define what each instance can configure and how those choices "
              "resolve into class bindings.", CONFIGURATOR_HELP + """
<h2>Pre-save checklist</h2>
<ul><li>Every property has a unique name and an operator-readable title.</li>
<li>Instance values are Public; derived/helper values are Internal.</li>
<li>Only one public reference property is the primary drop target; it is
Required, Read only and declares compatible block types.</li>
<li>Writable entries reference a public Parameter Reference whose Direction
is Operator write (checked service).</li>
<li>The default Selection names an option that actually exists.</li>
<li>Every <code>Pvm.*</code> reference resolves, including dotted Selection
subproperties.</li><li>Presence references a Boolean value and cannot form a
cycle.</li><li>Optional groups that should cost no runtime subscription have
Present Online cleared.</li><li>Preview shows all intended choices and hides
every Presence-off field.</li></ul>
<p>Return to <a href='help:pvm_creation'>PVM creation</a> or
<a href='help:faceplate_creation'>Faceplate creation</a>.</p>
""")),
    HelpTopic(
        "illustrated_tutorial", "Tutorials",
        "Illustrated PVM and Faceplate Walkthrough",
        "The complete repository tutorial rendered in Help with its screenshots.",
        ("illustrated", "screenshot", "walkthrough", "full tutorial",
         "configuration", "faceplate"),
        _page("Illustrated PVM and Faceplate Walkthrough",
              "When the illustrated documentation is installed, this page "
              "renders it directly inside the Help Center.", """
<p>The complete walkthrough covers paired-class creation, the class-master
canvas, public and internal properties, typed primary drop targets, bindings
for text/tables/alarms/trends/user entries, control-block drag, instance
configuration, validation, class Save, affected-display publishing and
worked troubleshooting examples.</p>
<p>If this development build cannot locate the documentation bundle, use the
detailed <a href='help:pvm_creation'>PVM</a>,
<a href='help:faceplate_creation'>Faceplate</a> and
<a href='help:pvm_configuration'>Configurator</a> topics instead.</p>
""")),
    HelpTopic(
        "workspace", "Workspace", "Canvas and Drawing Tools",
        "Selection, drawing, snapping, zoom, grouping and arrangement.",
        ("canvas", "shape", "line", "draw", "group", "align", "snap"),
        _page("Canvas and Drawing Tools",
              "The canvas combines precise engineering geometry with "
              "direct-manipulation drawing tools.", """
<h2>Select and navigate</h2>
<p>Use the selector at the top of the left sidebar to switch between
<b>Components</b>, <b>Displays</b>, <b>Library</b>, <b>Control Data</b>,
<b>Objects</b> and <b>Layers</b>. Each gets the full available height.
Choose <b>Split view</b> to keep a browser above the component palette.</p>
<ul><li><b>V</b> Select, <b>H</b> Pan, mouse wheel scrolls and Ctrl+wheel
zooms about the pointer.</li><li>Use the Selection pane to recover hidden or
locked objects that cannot be clicked on the canvas.</li>
<li>Use <b>Layers</b> (Ctrl+5) to select, isolate, hide, lock or reassign a
complete equipment, pipe, annotation or navigation layer.</li>
<li>Use the <b>left-edge chevron</b> to hide Graphics Explorer and Palette
together for a larger canvas. Right-click the chevron to show either pane
independently or reset their widths.</li>
<li>The dotted page rectangle is the publishable Width × Height. Keep drawing
and PVM bounds inside it; the inner dashed rectangle is the configurable safe
margin. Both disappear outside Edit mode.</li>
<li>Right-click empty canvas for <b>Display properties…</b>, Fit, grid and
snap controls.</li></ul>
<h2>Context menus</h2>
<p>Right-click the object you are already working with. Display tabs expose
Save, Verify, Publish, Quick Online and tab management. Project-tree folders
expose only the document types they own. Palette sections expose accordion
and pane controls; a palette card can place its item or copy its class name.
PVMs, drawings and pipes expose their complete runtime or authoring commands.
Right-click a Selection row or the Graphics Configuration heading to reach
that same object menu when the object is hidden, locked or covered.</p>
<h2>Draw and arrange</h2>
<p>The Component Palette is a searchable stencil library: type a process
name such as <b>centrifugal pump</b>, <b>vessel</b> or <b>control valve</b> to
filter all PVM and equipment families at once. Click a card, then click the
canvas to place it under the pointer; dragging a card also places it directly.
Choose <b>FLOAT</b> to move the same live palette onto another screen;
<b>DOCK</b> returns it without losing search or section state.</p>
<p>R Rectangle, E Ellipse, L Line, C Connector, P Pencil, A Arc, S Shape,
T Text and X Eraser. Press-drag-release draws shapes at their real size;
clicking without a drag places the standard size. Shift constrains shapes to
a square and lines to 45-degree angles; Alt draws a shape outward from its
centre. Polyline accepts successive vertices and finishes on double-click or
Enter. Enable <b>Repeat</b> above the canvas to keep placing symbols, drawing
shapes or connecting equipment without returning to the palette or ribbon.
Esc or right-click ends the tool; each completed placement has its own undo.</p>
<p>Hold <b>Space</b> and drag to pan temporarily, even with a drawing tool
armed. In Select, <b>Ctrl+drag</b> copies the selection, including connections
between selected objects and manual pipe bends. Each assembly copy gets its
own group. <b>Shift+drag</b> keeps movement horizontal or vertical; Alt bypasses
snapping. A selection moves by one shared offset, preserving its spacing.</p>
<p>The canvas toolbar keeps Select, Pan, Rectangle, Ellipse, Line, Connector,
Text, Repeat, Snap, Guides and Fit within
reach. To reuse drawing appearance, select a styled shape or pipe, choose
<b>Home &gt; Style Brush</b>, then click targets. Repeat keeps the style brush active.
This copies appearance while retaining each target's geometry and bindings.</p>
<p>Palette search stays visible as you scroll. Tab to a component and press
Enter or Space to arm placement. Function-block stencils use the same
engineering glyphs as Control Data; equipment and custom PVMs show their
actual artwork.</p>
<p>The Properties pane separates <b>Design</b> (geometry, fill, stroke and
text), <b>Data</b> (bindings and equipment ports), and <b>Behavior</b> (actions,
scripts and animation). Click a color swatch to pick a color. The alert strip
shows one summary; <b>Details</b> opens the complete live list without
blocking the canvas.</p>
<h2>Smart lines and connectors</h2>
<p>Choose <b>Connector (C)</b> to reveal symbol and PVM connection ports. Selected objects
move their four automatic port handles just outside the resize frame so the
two gestures cannot compete. Drag from any port; a solid teal preview and a
filled destination port confirm the magnetic target before release.
Connectors use rounded orthogonal elbows, avoid equipment, store exact named
endpoints, and re-route when either endpoint moves. New equipment symbols
include named <b>inlet</b>,
<b>outlet</b>, <b>top</b> and <b>bottom</b> ports whose normalized positions
survive resizing. Select a symbol to inspect or refine its port contract in
Graphics Configuration. Open strokes expose exact start/end tips. When lines,
polylines, freeforms, arcs or connectors cross, select the stroke that should
appear on top and choose <b>Format &gt; Line Crossings</b>:</p>
<ul><li><b>Continuous</b> draws normally.</li>
<li><b>Break</b> opens a clean gap at every detected crossing.</li>
<li><b>Jump</b> draws a bridge over every detected crossing.</li></ul>
<p>The Graphics Configuration pane reports the live crossing count. Moving
either stroke recomputes the effect; no intersection coordinates are stored.</p>
<h3>Move a straight pipe section</h3>
<p>With <b>Guides</b> enabled, dragging connected equipment aligns its real
pipe nozzles before nearby frame edges. All selected objects move together.
For a manual route, the adjacent bend supplies the alignment axis. Hold
<b>Alt</b> to bypass snapping or <b>Shift</b> to constrain the drag. The router
retains your chosen ports; it never moves an endpoint to hide misalignment.</p>
<p>In Select mode, hold a straight section and drag it. A horizontal section
moves up or down; a vertical section moves left or right. The cursor shows the
available direction. Equipment ports remain attached and adjacent bends adjust.
Dragging an end section adds short departure legs to preserve the nozzles.
Grid and smart guides help alignment. Release stores a manual route in one Undo
step; Escape or focus loss cancels the gesture. Double-click still inserts a
bend, and endpoint circles still reconnect or detach an end. This applies to
routed pipes and the Line tool's routed connectors; freeform artwork retains
its vertex-editing tools.</p>
<h3>Crossings, branches and overlapping runs</h3>
<p>New routed lines use Jump by default. Existing authored effects remain as
saved. Right-click the stroke and choose <b>Style &gt; Crossover effect</b> to
select Continuous, Break or Jump. At a crossing, exactly one stroke owns the
effect; equal-height strokes follow the drawing stack. Nearby bridges merge
and stay inside the segment. A crossing does not create a process connection.</p>
<p>To add a branch, right-click the desired point on a pipe and choose
<b>Routing &gt; Insert branch junction here</b>. This splits the existing run
into two pipes attached to one visible dot. Connect a third pipe to that dot.
The split preserves outer equipment ports and bends and is one Undo step.
Moving the dot reroutes its attached runs. A junction cannot be inserted within
12 drawing units of an existing endpoint; use the existing port there.</p>
<p>Problems reports overlapping collinear runs and contacts without an attached
junction as advisories. Move a segment to separate independent streams, or
create an explicit branch. These are drawing diagnostics, not controller logic
or fluid-network simulation connections.</p>
<p>Equipment PVMs offer named ports where applicable: suction/discharge for
pumps and compressors, inlet/outlet for flow equipment, and vent/drain for
vessels. The status bar identifies the active target. Inline PVM ports rotate
with their item; compact equipment ports rotate with the glyph while labels
stay upright. Class-defined connection points remain available for custom
equipment.</p>
<p><b>Smart Guides</b> align visible edges and centres while dragging. Alt
temporarily bypasses grid and guide snapping. Make Same Size and Inside Page
complete common multi-selection cleanup without manual coordinate edits.</p>
<h2>Live vessels</h2>
<p>Right-click a vessel symbol and choose <b>Convert to live vessel
(bar + trend)…</b>, then browse to an AI measurement or PID loop. Studio
replaces the static symbol in place with the registered Vessel PVM, preserves
the selected equipment silhouette, and reconnects every attached pipe by its
existing port name. The resulting bar reads the controller range and alarm
limits; the embedded trend uses the shared runtime history. Double-clicking a
PID vessel opens the PID faceplate.</p>
<h2>Fan indicators and the column template</h2>
<p><b>Fan Indicator</b> is an AI PVM in the class library. Its needle and
numeric value read the selected AI's OUT and engineering range. The six muted
sectors divide that range; they are not safe, warning or trip zones. Only bound
limits and a real active alarm produce limit ticks and alarm marks. Missing,
bad, uncertain or nonfinite data removes the needle. A good value without a
valid range reads <b>No range</b>. Double-click for the standard AI faceplate
and its trend/detail actions.</p>
<p><b>Library &gt; Templates &gt; Display templates &gt; L2 - Distillation
Column</b> creates an editable process arrangement with fan measurements,
PID loop PVMs, vessels with level/history, and pump status PVMs. The right-hand
inventory readouts repeat the named equipment measurements; they do not
represent additional tanks. Map each CONFIGURE reference, check equipment
identity and flow paths, then Verify and commission before publication.</p>
<p>Group related elements before saving them as a reusable authored class.</p>
<p>Arrow keys move one display unit. Shift+Arrow resizes symmetrically about
the object's center. Format provides fill, outline, rotation, alignment and
z-order.</p>
""")),
    HelpTopic(
        "graphics_explorer", "Workspace", "Graphics Explorer",
        "Displays, display sets, contextual displays and workstation layouts.",
        ("project tree", "display set", "layout", "navigation", "hierarchy"),
        _page("Graphics Explorer",
              "Graphics Explorer owns environment documents and display "
              "navigation; Library Explorer owns reusable classes.", """
<table><tr><th>Folder</th><th>Contains</th></tr>
<tr><td>Displays</td><td>Editable operator graphics organized by hierarchy.</td></tr>
<tr><td>Display Sets</td><td>The displays and four-level navigation available to an operator.</td></tr>
<tr><td>Contextual Displays</td><td>Derived registered faceplates and detail displays.</td></tr>
<tr><td>Layouts</td><td>Screens, frames, geometry and workstation assignment.</td></tr></table>
<p>Right-click a permanent folder to create the document it owns. A
contextual display is derived from a registered class, so it deliberately
has no independent New command. Right-click any display and choose
<b>Properties…</b> to configure its L1–L4 hierarchy, parent, page preset or
custom dimensions, background, fitting, operator scaling, default PVM tag,
authoring guides and Work In Progress status. Right-click the project root to
create a Display, Display Set, Layout or complete linked L1–L4 hierarchy.
Right-click the Explorer tab strip to hide the engineering panes, reset their
widths or open contextual help.</p>
<h2>L1-L4 operator starting templates</h2>
<p>Choose an L1-L4 template from <b>Library &gt; Templates &gt; Display
templates</b>, or use the project-root <b>New L1-L4 hierarchy</b> command to
create four linked, editable drafts with a Display Set and Layout.</p>
<table><tr><th>Level</th><th>Operator task</th><th>Starting content</th></tr>
<tr><td>L1</td><td>Overview of the operator's responsibility</td><td>Read-only KPI bars, unit profiles, domain trends and priority exceptions.</td></tr>
<tr><td>L2</td><td>Routine unit operation</td><td>Process path, configurable loop PVMs, operating envelope, trends and alarms.</td></tr>
<tr><td>L3</td><td>Equipment investigation and non-routine intervention</td><td>Vessel trend, connected equipment, related loops and diagnostic trends.</td></tr>
<tr><td>L4</td><td>Support and diagnostics</td><td>Permit/first-out and instrument tables, procedure assembly area, history and approved guidance.</td></tr></table>
<p>Design the L2 operating task first, summarize its important conditions in L1,
and add L3/L4 detail where the operator needs it. All starting documents inherit
the station theme. Configure every <b>CONFIGURE/...</b> reference using the
existing control browser, establish ranges and limits, and set alarm scope and
navigation. Unconfigured condition tables say <b>Not configured</b>; they do not
claim healthy or granted permits. Alarms initially use station-wide scope, as
the heading states. Embedded charts show recent sampled values; use Process
History View for time-based investigation. New drafts are not published or
assigned to an operator automatically. Preview, Verify and commission before
release. Existing saved displays retain their authored layout.</p>
""")),
    HelpTopic(
        "selection_properties", "Workspace", "Selection and Properties",
        "Information, appearance, geometry, visibility and interaction fields.",
        ("properties", "configuration", "locked", "hidden", "selection"),
        _page("Selection and Properties",
              "The right property pane configures the selected display or "
              "element; the Selection pane is the structural inventory.", """
<ul><li><b>Information</b> names and documents the object.</li>
<li><b>Fill and Line</b> control appearance for drawing elements and selected
PVM placements.</li>
<li><b>Geometry</b> provides exact X, Y, width, height and rotation.</li>
<li><b>Visibility</b> includes hidden/locked state and data-driven rules.</li>
<li><b>Interaction</b> defines operator actions and user-entry behavior.</li>
<li><b>Variables</b> expose typed values to expressions and scripts.</li></ul>
<p>Double-click a text label, process-stream chevron or equipment symbol to
open <b>Format</b> and place the keyboard directly in its Text field. The
same command is available as <b>Format...</b> at the top of the object's
right-click menu. Equipment text stays attached when the symbol is moved,
copied, grouped or resized.</p>
<p>The Selection pane is also the recovery surface: right-click an element
row for its normal canvas shortcut menu, or right-click empty list space to
Select All, Show All, Unlock All or refresh the structural inventory.</p>
<div class='callout'>For a selected PVM, <b>Fill Color</b> and <b>Line Color</b>
override its ordinary panel and equipment material. Leave either field blank
to follow the active theme. These fields never replace alarm, status or
writable-action colors, whose meanings remain governed by the Standard.
Resizing scales the complete PVM. For exact text and number sizing, choose its
named Typography profile in CONFIGURATION; use <b>Edit class typography…</b>
to set family and semantic point sizes once for every linked instance.</div>
""")),
    HelpTopic(
        "bindings", "Data and behavior", "Bindings and Expressions",
        "Connect visual properties to process data, metadata and safe expressions.",
        ("tag", "path", "CONFIG", "PROP", "expression", "quality"),
        _page("Bindings and Expressions",
              "A binding is a live contract to a typed value; Bad quality "
              "must remain visible rather than becoming an invented zero.", """
<h2>Path forms</h2>
<pre>MODULE/BLOCK/PARAMETER
MODULE/BLOCK/CONFIG/NAME
MODULE/BLOCK/PROP/UNITS</pre>
<p>Configured values such as alarm limits use the CONFIG leg. Engineering
metadata such as units and ranges uses PROP. Use the parameter browser to
select real paths and Verify to resolve them against the live graph source.</p>
<h2>Expressions</h2>
<p>Expressions combine bound display values for presentation. They are not
a replacement for controller logic. A Bad input makes the result Bad; it
does not silently reuse a stale numeric value.</p>
<p>See <a href='help:scripting'>TypeScript and Script Assistant</a> for event
behavior.</p>
""")),
    HelpTopic(
        "scripting", "Data and behavior", "TypeScript and Script Assistant",
        "Author display events using the supported Azeo-style object model.",
        ("typescript", "script", "event", "object model", "assistant"),
        _page("TypeScript and Script Assistant",
              "Scripts coordinate display behavior; process control remains "
              "in the controller strategy.", """
<ul><li>Open Script Assistant from Review to browse supported objects,
properties, methods and event templates. Its <b>API</b> tab carries the
TypeScript declarations for the whole surface and its <b>Restrictions</b> tab
states the boundary; completion offers only members the runtime really
has.</li><li>Display open/close events
belong to the display. Click and value-change events belong to the selected
element.</li><li>Use Verify before Test; unknown objects, invalid paths and
unsupported methods are engineering errors. A failing run marks the line it
failed on.</li></ul>
<p><b>Loops are not available.</b> <code>for</code>, <code>while</code> and
<code>do</code> are refused, because a handler that never returns freezes the
operator station and the run watchdog reports an overrun rather than stopping
one. Iterate with the capped array methods instead — <code>forEach</code>,
<code>map</code>, <code>filter</code>, <code>reduce</code>. Dynamic code
constructors and oversized scripts, collections and repeated strings are
also refused. Ordinary text is unaffected: a label may read
&quot;Waiting for start&quot;.</p>
<div class='warning'>Do not implement interlocks, permissives or closed-loop
control in a picture. A display may request and visualize; the control module
must decide.</div>
""")),
    HelpTopic(
        "engineering_library", "Reusable engineering", "Engineering Library",
        "Typed PVM, Faceplate, Detail Display and Special Symbol artifacts.",
        ("library explorer", "class", "ready", "review", "used", "pair"),
        _page("Engineering Library",
              "The Library Explorer is the reusable-asset catalog and "
              "cross-reference surface for this configuration library.", """
<table><tr><th>Artifact</th><th>Role</th></tr>
<tr><td>PVM Class</td><td>Reusable compact object placed on a process display.</td></tr>
<tr><td>Faceplate Class</td><td>Contextual operator popup paired with a PVM.</td></tr>
<tr><td>Detail Display Class</td><td>Function-block diagnostics and configuration surface.</td></tr>
<tr><td>Special Symbol</td><td>Documented faceplate actions, state marks and project SVGs.</td></tr></table>
<p><b>Installed</b> definitions are package-owned and read-only. <b>Project</b>
definitions are authored in this configuration library. State is derived:
Ready, Review or Error. Used is a real cross-reference count, not a label.</p>
<p>Right-click an authored class to edit layout, configure properties, pair
it, validate it or find every usage.</p>
""")),
    HelpTopic(
        "pvm_faceplate", "Reusable engineering", "PVM and Faceplate Tutorial",
        "Build, pair, configure and test a reusable operator object.",
        ("pvm", "faceplate", "builder", "pair", "tutorial", "blueprint"),
        _page("PVM and Faceplate Tutorial",
              "A professional faceplate is a reusable typed composition, "
              "not a screenshot or one-off dialog.", """
<ol><li>Right-click <b>Faceplate Classes</b> and choose the paired PVM +
Faceplate blueprint.</li><li>Author the compact PVM on the class canvas.
Bind only through <code>Pvm.*</code> and <code>Standard.*</code> typed
properties.</li><li>Author the faceplate header, PV/SP/OUT presentation,
mode controls, alarm table, trend and documented Special Symbols.</li>
<li>Open Config to define property groups, selections, presence rules and
online subscription behavior.</li><li>Validate both classes. Pairing must be
reciprocal and every nested dependency must resolve.</li><li>Place the PVM on
a display, configure its module path, enter Test and open its faceplate.</li></ol>
<div class='callout'><b>Recommended composition:</b> 20&ndash;30 drawing and
data elements grouped into one class, with each dynamic property tied to a
typed class property or tag path.</div>
<h2>Detailed procedures</h2>
<ul><li><a href='help:class_builder_workflow'>Complete visual class-builder workflow</a></li>
<li><a href='help:pvm_creation'>Create and Configure a PVM Class</a></li>
<li><a href='help:faceplate_creation'>Create a Faceplate Class</a></li>
<li><a href='help:pvm_configuration'>PVM Configuration Designer</a></li>
<li><a href='help:illustrated_tutorial'>Illustrated walkthrough</a></li>
<li><a href='help:special_symbols'>Faceplate Special Symbols</a></li>
<li><a href='help:validation'>Verify, Test and Publish</a></li></ul>
""")),
    HelpTopic(
        "special_symbols", "Reusable engineering", "Faceplate Special Symbols",
        "Documented module detail, history, alarm, help and navigation symbols.",
        ("icon", "alarm", "history", "acknowledge", "simulate", "detail"),
        _page("Faceplate Special Symbols",
              "Special Symbols preserve the documented operator meaning of "
              "faceplate actions and state indications.", """
<ul><li>Module detail display</li><li>Associated block faceplate</li>
<li>Primary control display</li><li>Control Designer</li>
<li>Process History View</li><li>Acknowledge visible alarms</li>
<li>Return to faceplate</li><li>Alarm and Alarm Help</li>
<li>Simulation active and Mini faceplate</li></ul>
<p>Place them from Insert or the Special Symbols library branch. Configure
the action target where required. If the runtime has no action behind an
icon, remove it rather than presenting an inert control.</p>
""")),
    HelpTopic(
        "preview_themes", "Release and diagnostics", "Operator theme and faceplate preview",
        "Check the current draft at the operator's theme and viewport size.",
        ("quick online", "preview", "theme", "dark", "resolution", "faceplate", "recovery"),
        _page("Operator theme and faceplate preview", "Preview the latest draft before publication.", """
<ol><li>Open the display and choose <b>Quick Online</b> from the ribbon or its tab menu.</li>
<li>Choose the operator theme. The display and every open faceplate update together.
Graphics Designer's engineering workspace retains its own appearance.</li>
<li>Click a built-in PVM to open its faceplate; use its Detail action for the associated
detail view. Authored PVM faceplate/detail links retain their instance parameters,
including procedure references. Close a faceplate using its title-bar close control.</li>
<li>Choose <b>Display viewport</b> to inspect 1366 × 768, 1920 × 1080 or 2560 × 1440.
These are logical pixels for the process display, excluding station chrome. Scroll the
preview when the chosen viewport exceeds your monitor. Fit window restores resizing.</li>
<li>Use <b>Export image</b> to save the current display viewport as a PNG for review.</li>
<li>After editing, send <b>Quick Online</b> again. It replaces that draft's old preview
and closes the old faceplates, so the next faceplate shows the new configuration.</li></ol>
<p>Live values continue to update. Operator writes, procedure commands and scripts are
not executed in this preview. Verify operating behavior using the existing commissioning
and controlled runtime workflow. Navigation links can open other drafts already added
to this preview; add the target from Studio first.</p>
<p>A fixed authored background remains fixed. Clear it in Display Properties to follow
the operator theme. New displays follow the theme by default. Changing a preview theme
does not save a display, publish a revision, or change a station's preferences.</p>
<h2>Recovery status</h2><p>The status bar reports the last successful recovery checkpoint.
Edits schedule recovery after two quiet seconds, or after at most thirty seconds of
continuous editing while the event loop is responsive. A checkpoint is separate from
Save and Publish. If recovery fails, <b>Recovery unavailable</b> remains visible; hover
to read the reason and click to retry. A failed attempt also retries after thirty seconds.
Ctrl+S saves the draft. A failed Save keeps the unsaved document open.</p>
""")),
    HelpTopic(
        "validation", "Release and diagnostics", "Verify, Test and Publish",
        "Preflight bindings and dependencies, exercise runtime behavior, then release.",
        ("verify", "error", "unresolved", "test", "publish", "diagnostic"),
        _page("Verify, Test and Publish",
              "A display is complete only when its data drives it and every "
              "operator action has a handler.", """
<h2>Verify (F8)</h2><p>Checks binding paths, expressions, missing classes,
invalid actions and authored-class contracts. Resolve errors before release.</p>
<h2>Test (F5)</h2><p>Uses the draft in a read-only runtime surface. Check Bad
quality, alarms, navigation, write ownership, faceplate reuse and user-entry
validation. Test Data scenarios are render-only overlays and automatically
turn off when Test mode exits.</p>
<h2>Publish</h2><p>Creates the accepted revision used by assigned stations.
Publication history is append-only and supports controlled revert.</p>
<div class='warning'>Drawn is not driven. A visual indicator with no source
and a button with no handler are verification defects, even if both paint
correctly.</div>
""")),
    HelpTopic(
        "keyboard_shortcuts", "Reference", "Keyboard and Mouse",
        "Complete Graphics Designer command reference.",
        ("shortcut", "keyboard", "mouse", "F1", "hotkey"),
        _page("Keyboard and Mouse", "Fast, repeatable engineering commands.", """
<table><tr><th>Command</th><th>Shortcut</th></tr>
<tr><td>Context help</td><td>F1</td></tr><tr><td>Save draft</td><td>Ctrl+S</td></tr>
<tr><td>Publish</td><td>Ctrl+Shift+P</td></tr><tr><td>New display</td><td>Ctrl+N</td></tr>
<tr><td>Test / exit test</td><td>F5</td></tr><tr><td>Verify</td><td>F8</td></tr>
<tr><td>Edit mode</td><td>Ctrl+E</td></tr><tr><td>Explorer tabs</td><td>Ctrl+1 / 2 / 3 / 4 / 5</td></tr>
<tr><td>Focus Canvas</td><td>F11</td></tr>
<tr><td>Next document</td><td>Ctrl+Tab</td></tr><tr><td>Cancel tool</td><td>Esc</td></tr>
<tr><td>Select / Pan</td><td>V / H</td></tr><tr><td>Rectangle / Ellipse</td><td>R / E</td></tr>
<tr><td>Line / Connector</td><td>L / C</td></tr><tr><td>Pencil / Arc</td><td>P / A</td></tr>
<tr><td>Shape / Eraser / Text</td><td>S / X / T</td></tr>
<tr><td>Move selection</td><td>Arrow</td></tr><tr><td>Resize about center</td><td>Shift+Arrow</td></tr>
<tr><td>Zoom about pointer</td><td>Ctrl+mouse wheel</td></tr></table>
<p><b>Space+drag</b> temporarily pans without dropping the current tool.
<b>Ctrl+drag</b> copies a selection; <b>Shift+drag</b> constrains movement to an
axis; <b>Alt+drag</b> bypasses snapping. Enable <b>Repeat</b> above the canvas
for successive placements or strokes. <b>Esc / right-click</b> stops drawing.</p>
""")),
    HelpTopic(
        "troubleshooting", "Release and diagnostics", "Troubleshooting",
        "Resolve Bad quality, missing objects, validation errors and publish failures.",
        ("bad", "missing", "error", "cannot publish", "diagnostics"),
        _page("Troubleshooting",
              "Start with the first violated contract; later symptoms are "
              "often consequences of it.", """
<table><tr><th>Symptom</th><th>Check</th></tr>
<tr><td>Value is blank or shows Bad</td><td>Verify path, source quality, module execution and CONFIG/PROP leg.</td></tr>
<tr><td>PVM cannot be placed from Control Data</td><td>Save and validate the class interface; it needs one public required read-only reference as Primary Drop Target and the dragged block type in Accepted block types.</td></tr>
<tr><td>Configure instance shows the wrong fields</td><td>Only per-placement inputs should be Public. Move derived/helper state to Internal and save the interface.</td></tr>
<tr><td>A required property remains empty after block drop</td><td>The drop always fills the primary target. Conventional ModuleName, ModulePath, PVPath, SPPath and OUTPath fields auto-fill when declared; configure custom required names explicitly.</td></tr>
<tr><td>Faceplate does not open</td><td>Reciprocal pairing, module path and registered faceplate role.</td></tr>
<tr><td>User Entry is read-only</td><td>Use a public Parameter Reference with Operator write direction and resolve it to a checked writable process target.</td></tr>
<tr><td>Button does nothing</td><td>Interaction action and runtime handler; remove unsupported actions.</td></tr>
<tr><td>Publish is disabled on the class master</td><td>This is intentional. Save the class, Find Usages, then verify and publish every affected display.</td></tr>
<tr><td>Display publish is refused</td><td>Verify results, single-writer lock, unresolved dependencies and permissions.</td></tr>
<tr><td>Station shows an older display</td><td>Accepted revision, workstation layout and deployment assignment.</td></tr></table>
<p>Use Review &gt; Registration Log for class-registration diagnostics and
Review &gt; Verify for display-specific preflight.</p>
""")),
)

TOPIC_BY_KEY = {topic.key: topic for topic in TOPICS}


def illustrated_tutorial_path() -> Path | None:
    """Locate the optional illustrated source-tree documentation bundle."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "PVM_FACEPLATE_TUTORIAL.md"
        if candidate.exists():
            return candidate
    return None


HELP_STYLE = f"""
<style>
body {{ color: {WF['tx']}; font-family: 'Segoe UI', sans-serif;
       font-size: 10pt; line-height: 1.45; }}
h1 {{ color: {WF['navy']}; font-size: 20pt; margin: 4px 0 2px 0; }}
h2 {{ color: {WF['navy']}; font-size: 13pt; margin-top: 18px; }}
.lead {{ color: {WF['tx2']}; font-size: 11pt; margin-bottom: 16px; }}
a {{ color: {WF['lapis']}; text-decoration: none; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th {{ background: {WF['chrome']}; color: {WF['navy']}; text-align: left; }}
th, td {{ border: 1px solid {WF['bd']}; padding: 6px 8px; }}
pre, code {{ font-family: Consolas, monospace; background: {WF['chrome']}; }}
pre {{ border: 1px solid {WF['bd']}; padding: 9px; }}
.callout {{ background: {WF['sel']}; border-left: 4px solid {WF['lapis']};
            padding: 10px 12px; margin: 12px 0; }}
.warning {{ background: #FFF4DE; border-left: 4px solid {WF['warn']};
            padding: 10px 12px; margin: 12px 0; }}
li {{ margin: 4px 0; }}
</style>
"""

MARKDOWN_STYLE = f"""
body {{ color: {WF['tx']}; font-family: 'Segoe UI', sans-serif;
       font-size: 10pt; }}
h1, h2, h3 {{ color: {WF['navy']}; }}
a {{ color: {WF['lapis']}; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid {WF['bd']}; padding: 5px 7px; }}
code, pre {{ font-family: Consolas, monospace; background: {WF['chrome']}; }}
"""


def topic_matches(topic: HelpTopic, query: str) -> bool:
    """Token-and search used by both the UI and headless regression tests."""
    words = tuple(word for word in query.casefold().split() if word)
    return all(word in topic.search_text for word in words)


class GraphicsDesignerHelpCenter(QDialog):
    """Modeless searchable reference with stable deep links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Graphics Designer Help Center")
        self.setMinimumSize(860, 560)
        self.resize(1040, 680)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setStyleSheet(f"""
QDialog {{ background: {WF['page']}; }}
QLineEdit, QTreeWidget, QTextBrowser {{ background: {WF['pane']};
  border: 1px solid {WF['bd']}; color: {WF['tx']}; }}
QLineEdit {{ padding: 7px 9px; font-size: 10pt; }}
QTreeWidget {{ font-size: 9pt; }}
QTreeWidget::item {{ padding: 5px 4px; }}
QTreeWidget::item:selected {{ background: {WF['sel']}; color: {WF['navy']}; }}
QPushButton {{ padding: 5px 12px; }}
""")
        self._topic_items: dict[str, QTreeWidgetItem] = {}
        self.last_render_source = "topic"
        self._build_ui()
        self.show_topic("getting_started")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        add_authoring_dialog_header(
            self,
            layout,
            "Graphics Designer Help Center",
            "Search authoring workflows, commands, bindings, PVMs and "
            "diagnostic guidance.",
        )
        header = QHBoxLayout()
        header.addStretch(1)
        self.result_label = QLabel("")
        self.result_label.setStyleSheet(f"color: {WF['tx3']};")
        header.addWidget(self.result_label)
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setObjectName("help_search")
        self.search.setPlaceholderText(
            "Search workflows, commands, bindings, PVMs, errors or shortcuts")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_topics)
        layout.addWidget(self.search)

        splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setObjectName("help_topics")
        self.tree.setHeaderHidden(True)
        # Long engineering topic names must remain names, not ellipses —
        # "Illustrated PVM and Faceplate Walkthrough" is a different page
        # from the shorter overview beside it.
        self.tree.setMinimumWidth(310)
        categories: dict[str, QTreeWidgetItem] = {}
        for topic in TOPICS:
            category = categories.get(topic.category)
            if category is None:
                category = QTreeWidgetItem(self.tree, [topic.category])
                category.setFlags(category.flags() & ~Qt.ItemIsSelectable)
                font = QFont(self.tree.font())
                font.setBold(True)
                category.setFont(0, font)
                categories[topic.category] = category
            item = QTreeWidgetItem(category, [topic.title])
            item.setData(0, Qt.UserRole, topic.key)
            item.setToolTip(0, topic.summary)
            self._topic_items[topic.key] = item
        self.tree.expandAll()
        self.tree.currentItemChanged.connect(self._topic_changed)
        splitter.addWidget(self.tree)

        self.browser = QTextBrowser()
        self.browser.setObjectName("help_content")
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        splitter.addWidget(self.browser)
        splitter.setSizes([320, 700])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        hint = QLabel("F1 opens help for the active Graphics Designer pane")
        hint.setStyleSheet(f"color: {WF['tx3']};")
        footer.addWidget(hint)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)

    @property
    def current_topic_key(self) -> str:
        item = self.tree.currentItem()
        return str(item.data(0, Qt.UserRole) or "") if item else ""

    def show_topic(self, key: str) -> bool:
        item = self._topic_items.get(key)
        if item is None:
            return False
        if item.isHidden() or item.parent().isHidden():
            self.search.clear()
        if self.tree.currentItem() is item:
            self._render(key)
        else:
            # currentItemChanged renders the page. Rendering again here
            # rebuilt QTextDocument twice for every navigation and could
            # destabilise Qt after a long graphics-engineering session.
            self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        return True

    def filter_topics(self, query: str) -> None:
        visible = []
        for topic in TOPICS:
            item = self._topic_items[topic.key]
            match = topic_matches(topic, query)
            item.setHidden(not match)
            if match:
                visible.append(topic.key)
        for index in range(self.tree.topLevelItemCount()):
            category = self.tree.topLevelItem(index)
            category.setHidden(all(category.child(i).isHidden()
                                   for i in range(category.childCount())))
        self.result_label.setText(
            f"{len(visible)} topic{'s' if len(visible) != 1 else ''}")
        if visible and self.current_topic_key not in visible:
            self.show_topic(visible[0])
        elif not visible:
            self.browser.setHtml(
                HELP_STYLE + "<h1>No matching topic</h1>"
                "<p>Try a process term, command name, object type or error "
                "message.</p>")

    def visible_topic_keys(self) -> tuple[str, ...]:
        return tuple(topic.key for topic in TOPICS
                     if not self._topic_items[topic.key].isHidden())

    def _topic_changed(self, current, _previous) -> None:
        if current is None:
            return
        key = current.data(0, Qt.UserRole)
        if key:
            self._render(str(key))

    def _render(self, key: str) -> None:
        topic = TOPIC_BY_KEY.get(key)
        if topic is not None:
            tutorial = illustrated_tutorial_path() \
                if key == "illustrated_tutorial" else None
            if tutorial is not None:
                self.browser.document().setBaseUrl(QUrl.fromLocalFile(
                    str(tutorial.parent) + "/"))
                self.browser.document().setDefaultStyleSheet(
                    MARKDOWN_STYLE)
                self.browser.setMarkdown(
                    tutorial.read_text(encoding="utf-8"))
                self.last_render_source = "illustrated_markdown"
            else:
                self.browser.document().setBaseUrl(QUrl())
                self.browser.setHtml(HELP_STYLE + topic.html)
                self.last_render_source = "topic"
            self.browser.verticalScrollBar().setValue(0)

    def _open_link(self, url: QUrl) -> None:
        if url.scheme() == "help":
            self.show_topic(url.path() or url.host())


TOUR_STEPS = (
    ("Welcome", "getting_started", """
Graphics Designer turns live control data into governed operator displays.
This tour points out the surfaces used in the normal create, verify, test and
publish workflow."""),
    ("Graphics Explorer", "graphics_explorer", """
Create displays, navigation sets and workstation layouts here. Permanent
folders own New commands; contextual displays are derived from classes."""),
    ("Engineering Library", "engineering_library", """
Reusable PVM, Faceplate and Detail Display classes live here with derived
validation state, pairing and real cross-reference counts."""),
    ("Visual Class Builder", "class_builder_workflow", """
Create the paired PVM and faceplate, draw each class master, separate Public
instance parameters from Internal helpers, and declare one typed primary drop
target before placing the class from Control Data."""),
    ("Control Data", "bindings", """
Browse configured modules, function blocks, terminals and CONFIG parameters.
Drag a block to place a compatible PVM; drag a parameter to create a bound
Data Link without retyping its path."""),
    ("Palette and Canvas", "workspace", """
Place data elements, user entries, Special Symbols and drawing primitives,
then use exact geometry, grouping, alignment and snapping on the canvas."""),
    ("Properties and Bindings", "selection_properties", """
Select an object to configure appearance, geometry, interaction and typed
bindings. Use the Selection pane to reach hidden or locked objects."""),
    ("Verify, Test and Publish", "validation", """
Verify first, exercise the draft in Test, save engineering work, and publish
only the accepted revision intended for operator stations."""),
)


class GraphicsDesignerTour(QDialog):
    """Small modeless guided tour that keeps the Studio usable beside it."""

    def __init__(self, step_callback=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Graphics Designer Guided Tour")
        self.setWindowFlag(Qt.Tool, True)
        self.resize(520, 300)
        self._step_callback = step_callback
        self._step = 0

        layout = QVBoxLayout(self)
        add_authoring_dialog_header(
            self,
            layout,
            "Guided tour",
            "Follow the normal create, bind, verify, test and publish workflow.",
        )
        self.progress = QLabel()
        self.progress.setStyleSheet(
            f"color: {WF['lapis']}; font-weight: 600;")
        layout.addWidget(self.progress)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        layout.addWidget(line)
        self.title = QLabel()
        self.title.setFont(QFont("Segoe UI", 15, QFont.Bold))
        self.title.setStyleSheet(f"color: {WF['navy']};")
        layout.addWidget(self.title)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setAlignment(Qt.AlignTop)
        self.body.setStyleSheet(f"color: {WF['tx2']}; font-size: 10pt;")
        layout.addWidget(self.body, 1)

        buttons = QHBoxLayout()
        help_button = QPushButton("Open this help topic")
        help_button.clicked.connect(self._open_topic)
        buttons.addWidget(help_button)
        buttons.addStretch(1)
        self.back = QPushButton("Back")
        self.back.clicked.connect(lambda: self.set_step(self._step - 1))
        self.next = QPushButton("Next")
        mark_primary_action(self.next)
        self.next.clicked.connect(self._advance)
        buttons.addWidget(self.back)
        buttons.addWidget(self.next)
        layout.addLayout(buttons)
        self.set_step(0)

    @property
    def step_index(self) -> int:
        return self._step

    @property
    def topic_key(self) -> str:
        return TOUR_STEPS[self._step][1]

    def set_step(self, index: int) -> None:
        self._step = max(0, min(index, len(TOUR_STEPS) - 1))
        title, key, body = TOUR_STEPS[self._step]
        self.progress.setText(
            f"STEP {self._step + 1} OF {len(TOUR_STEPS)}")
        self.title.setText(title)
        self.body.setText(" ".join(body.split()))
        self.back.setEnabled(self._step > 0)
        self.next.setText(
            "Finish" if self._step == len(TOUR_STEPS) - 1 else "Next")
        if self._step_callback is not None:
            self._step_callback(key)

    def _open_topic(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "open_help"):
            parent.open_help(self.topic_key)

    def _advance(self) -> None:
        if self._step == len(TOUR_STEPS) - 1:
            self.close()
        else:
            self.set_step(self._step + 1)


class GraphicsDesignerAboutDialog(ProductAboutDialog):
    """Product/build identity plus the active graphics configuration."""

    def __init__(self, area_name: str, library_name: str,
                 display_root: Path, parent=None):
        super().__init__(
            "graphics_designer",
            parent,
            context_rows=(
                ("Area", area_name),
                ("Configuration library", library_name),
                ("Display store", str(display_root)),
                ("Document model", "Published PVM display revisions"),
            ),
        )
