"""Product help content owned by Azeo Explorer."""

EXPLORER_HELP = """
<h2>Azeo Explorer — the engineering station</h2>
<p>The Explorer is the shell: <b>one configuration, every application
an editor over it</b>. The tag database, alarms and historian all
derive from the loaded modules, so Control Designer, Graphics Designer
and the Operator Station always agree — the Explorer is the tree view
that makes that one system navigable.</p>

<h3>The system tree</h3>
<table border="0" cellspacing="0" cellpadding="4">
<tr><td><b>📚 Library</b></td><td>Every function block (with the
palette's own icons), the categorized <b>Procedure Blocks (PA)</b>,
and every PVM class. Right-click a control block to insert it into the
open module; right-click a PVM class for its Configuration Designer.
Double-click a procedure block to select it in PA Designer, or use
<b>Insert into current procedure</b> to add an undoable step. Procedure
blocks show their version and behavior; search also matches their identity.
<b>Block Help</b> and F1 open the selected procedure block's reference.</td></tr>
<tr><td><b>🗂 Control Strategies</b></td><td>Every area on disk. The
bold one marked <i>(this station)</i> is live; other areas open
engineering-only. Right-click for <b>New Area…</b>.</td></tr>
<tr><td><b>🖧 Physical Network</b></td><td>The Control Network with
the controller node, its Assigned Modules and Assigned I/O (cards and
channels), and Decommissioned Nodes.</td></tr>
<tr><td><b>🔔 Alarms and Events</b></td><td>The area's units and
their alarm-consolidation policy.</td></tr>
<tr><td><b>🗄 Continuous Historian</b></td><td>What is collected —
derived from the modules; right-click a parameter to open its
trend.</td></tr>
</table>

<h3>Status marks — computed, never asserted</h3>
<table border="0" cellspacing="0" cellpadding="4">
<tr><td><b>▲</b></td><td>Not downloaded: the database differs from
the node (the blue triangle).</td></tr>
<tr><td><b>●</b></td><td>On scan, matching the last download.</td></tr>
<tr><td><b>●▲</b></td><td>Running, but <b>changed since download</b> —
a parameter was edited after the last download; download again to
clear it.</td></tr>
<tr><td><b>⛔</b></td><td>Channel disabled — its tag stops updating
and reports Bad, which is what a disabled channel should look
like.</td></tr>
<tr><td><b>⚠</b></td><td>Channel Device Tag that no control module
references — the re-tagging mismatch, marked instead of
hidden.</td></tr>
</table>

<h3>The commission lifecycle (workshop pp.70–85)</h3>
<p>For a network controller, right-click <b>Control Network</b> and choose
<b>Discover Controllers...</b>. Discovery reads the controller's name, model,
serial number, hardware ID, address and OPC UA endpoint; it does not download
or grant control authority.</p>
<ol>
<li><b>Add as Decommissioned</b> associates the discovered hardware identity
with an existing same-name project placeholder (or creates one), then leaves
the physical controller under Decommissioned Nodes.</li>
<li><b>Add &amp; Commission</b> performs the association and commissions the
hardware immediately. A hardware ID already bound to a project node cannot be
silently replaced by a different controller.</li>
<li><b>Decommission</b> (controller menu): refused while the carrier
keylock is LOCKED; otherwise every module goes off scan, a
<i>placeholder</i> keeps the configured name on the Control Network,
and the node reappears under Decommissioned Nodes with the default
name.</li>
<li><b>Identify</b>: flashes the node's light on its row — find the
hardware before you name it.</li>
<li><b>Commission</b>: drag the decommissioned node onto the Control
Network (or use its menu). Name rules: 16 characters or fewer, at
least one letter, only <code>$ - _</code> besides letters and
digits.</li>
<li><b>Auto-sense I/O</b>: derives the cards and channels from what
is actually attached. Decline it and Assigned I/O stays
<i>(not sensed)</i> until you run it.</li>
</ol>

<p>For additional project controllers, Decommission retains the complete
controller record and its module assignments. The Control Network therefore
shows the configuration placeholder while Decommissioned Nodes shows the
physical serial number and last discovered address. Right-click either entry
to commission it again.</p>

<h3>I/O channels (workshop pp.86–91)</h3>
<p>Assigned I/O groups the field map into cards — C01 analog in, C02
analog out, C03 discrete in, C04 discrete out, eight channels each.
Double-click a channel for its Properties (description, type,
address, Device Tag, Enabled). Disabling and re-tagging are live
driver state: a disabled channel's tag goes Bad; a re-tagged channel
feeds the new name and starves the old one.</p>

<h3>Total Download (pp.100–102)</h3>
<p>Right-click the controller (or Control Network) ▸ <b>Total
Download…</b>. The confirm dialog's <i>Verify the configuration</i>
checkbox is real — unchecked, the Verifying stage shows stippled.
The checklist stages map to real steps: download log, upload checks
(unsaved edits reported), pre-download checks (keylock refuses; DST
over-capacity warns), dependency checks, verify, compile, download,
and fingerprint recording — which is what the ▲ diff is computed
against. The audit trail lands in <code>_download.log</code> beside
the area.</p>

<h3>Applications and keys</h3>
<table border="0" cellspacing="0" cellpadding="4">
<tr><td><b>Project Administrator</b></td><td>Creates empty projects or
network-preserving projects, makes independent copies, selects the next
startup project, verifies and migrates metadata, and creates checksum-verified
<code>.azeoproject</code> backups. Deregistration is recoverable and the open
project cannot be renamed or deregistered underneath the session.</td></tr>
<tr><td><b>Control Designer</b></td><td>Double-click any module, or the
toolbar. The Explorer owns the session; editors open from it.</td></tr>
<tr><td><b>Graphics Designer</b></td><td>The type-driven PVM studio;
the PVM Configuration Designer opens from its ribbon (Insert ▸
Library ▸ PVM Config), the Azeo route.</td></tr>
<tr><td><b>Start Plant Simulator</b></td><td>Shown for projects with
configured Local Virtual I/O. It starts the embedded process provider
through the same lifecycle as Tools ▸ Virtual I/O. While running it
becomes <i>Plant Simulator Status…</i>; Stop and Restart remain under
Tools ▸ Virtual I/O.</td></tr>
<tr><td><b>Virtual I/O Simulator</b></td><td>Groups every configured channel
by plant unit and I/O type. AI and DI channels accept static, sawtooth, square,
or sine simulations with Good, Uncertain, or Bad quality. AO and DO remain
read-only because they are controller-owned. Save and load repeatable JSON
commissioning scenarios from the project's
<code>virtual_io/scenarios</code> directory.</td></tr>
<tr><td><b>Simulation Workbench</b></td><td>The system checkout workspace for
projects with Local Virtual I/O. Select the entire control system or one loaded
module, then bulk-enable block simulation, enter Setup/Normal modes, or
initialize dynamic blocks. Its tabs expose live I/O blocks, configured Virtual
I/O references, other block outputs, atomic controller/process snapshots,
deterministic change recording/playback, and provider diagnostics. Pause the
process before stepping it. Snapshot restore can independently include
Operating, Tuning, and Process state; the prior system state is restored if
any selected part fails.</td></tr>
<tr><td><b>Operator Station / Diagnostics / Tag Database</b></td>
<td>Toolbar or the Applications menu.</td></tr>
<tr><td><b>F5</b></td><td>Refresh the tree and recompute every
status mark.</td></tr>
<tr><td><b>F1</b></td><td>This help.</td></tr>
</table>

<p>Everything the Explorer shows is derived from the configuration or
measured from the runtime; if a mark or count looks wrong, it is
telling you something true about the system, not about the
screen.</p>
"""
