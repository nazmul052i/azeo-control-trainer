"""Offline workflow and block reference derived from the supported Azeo catalog."""
from dataclasses import dataclass
from functools import lru_cache
from html import escape, unescape
import json
import re

from azeo_control_trainer.core.procedures.library import block_library, parameter_specs


@dataclass(frozen=True)
class HelpTopic:
    key: str
    title: str
    category: str
    html: str

    @property
    def search_text(self):
        return unescape(re.sub("<[^>]+>", " ", f"{self.title} {self.category} {self.html}")).casefold()


PARAMETER_HELP = {
    "skip_if": "Idempotent entry check using current mapped observations and memory. True skips this action (or the entire reusable call); False executes it. Bad quality is an error, never permission to skip.",
    "operator_skip_policy": "Never is the default. Reason allows an operator to skip an authored instruction, confirmation or comment before it executes; the actor and required reason enter run evidence. Process outputs and required verification cannot be skipped.",
    "subprocedure_path": "Choose an exact saved revision in this project's procedure library. Saving the parent bundles its dependencies; a running procedure never reloads changed child files.",
    "prefix": "Optional stable prefix for expanded child step identities. Leave blank to derive a collision-free prefix from the call step.",
    "parameters": "JSON mapping from child local variable names to typed literal values, or {\"expression\": \"parent_variable + 1\"}. Child locals reset at each call, including retries. Shared memory cannot be initialized by a call parameter.",
    "tag_aliases": "JSON mapping from a declared child logical tag to a declared parent logical tag. The shared observation binding is reused; types must match.",
    "result_variables": "JSON mapping from child result variables to declared parent variables. Results copy only after successful completion and are checked against parent memory types and limits.",
    "timer_tuning": "Explicitly expose this wait's combined hold, timeout and per-row hold timers to the authored PA Tuning page. None is read-only; next_run queues one change for the next run; live also allows current-run edits that restart qualification.",
    "id": "Unique step identity within this procedure. Rename preserves saved block geometry and connected route references.",
    "description": "Instruction and audit description. State the expected operator action and observable result.",
    "display_text": "Optional operator-facing prompt. When populated it takes precedence over the instruction; clear it to use the fallback wording.",
    "operator_guidance": "Instruction guidance; used after an empty operator prompt and before the general instruction text.",
    "condition": "Combine declared observations with and (ALL) or or (ANY), using parentheses for nested groups. True grants the condition. Timed waits publish each top-level clause to the HMI condition table.",
    "timeout_sec": "Maximum wait in simulation seconds. Choose enough time for the process response and continuous dwell.",
    "poll_sec": "Worker update interval in simulation seconds. Every captured scan is evaluated, including false pulses between updates; faster polling does not accelerate the process.",
    "stable_for_sec": "Required continuous observed True duration in simulation seconds. False, Uncertain, or an interruption by Pause resets the dwell. Zero passes on the first Good/True observation.",
    "on_timeout": "Action when the wait expires: fail, hold or abort. Held finishes this run for review; it is not the Pause command.",
    "tag": "Declared logical tag from Tag mappings, such as LOOP.PV. Its project parameter must resolve and be suitable for this operation.",
    "value": "Requested value. A live run requires explicit operator authorization and applies it through the Operator Station checked-write service; pair it with actual feedback verification.",
    "feedback_tag": "Logical actual-value or actual-mode tag that a later Wait must verify. It may differ from the command/target tag.",
    "start": "First ramp value. A live run requires one authorization for the complete bounded schedule before its first checked write.",
    "end": "Final ramp target. Pair the ramp with a later wait that verifies its actual feedback tag.",
    "rate_per_sec": "Positive change in engineering units per simulation second. The sign is inferred from Start and End.",
    "require_confirmation": "Require an explicit acknowledgement. Live process outputs always require authorization even when this option is cleared.",
    "variable": "Destination memory tag or local variable. Search to select an existing shared Tag DB memory tag or create one immediately. Shared values persist across runs and are also available in Control Designer; legacy local variables reset.",
    "expression": "Restricted calculation using declared tags, variables, numeric literals and supported operators. Use the result variable in later steps.",
    "input_type": "float, int, bool, str or selection. Integer input must be whole; Boolean accepts true/false, yes/no, 1/0 or on/off.",
    "choices": "Allowed selection values, one per line. Values are typed: 1 is numeric, while quoted text remains text. Used by selection input.",
    "min_value": "Optional lower bound for numeric operator input. Leave blank for no lower bound.",
    "max_value": "Optional upper bound for numeric operator input. Leave blank for no upper bound.",
    "comment_prompt": "Request a specific observation. Operator comment requires a nonempty response and records it with the step.",
    "delay_sec": "Delay in simulation seconds. Simulation pause stops progress; elapsed wall time alone does not satisfy it.",
    "event_name": "Stable event name used to identify this observation in the procedure history.",
    "event_payload": "Event detail stored in the audit. Accepts a scalar or JSON value; it does not issue an external event or controller command.",
    "resume_prompt": "Instruction shown while an authored Pause suspends procedure progression. Resume is audited and requires fresh process observations.",
    "hmi_target": "Published display name or mapped MODULE/BLOCK equipment path offered to the operator. The station validates the target before opening it.",
    "monitor_duration_sec": "How long the condition must remain healthy. Author the monitor in a parallel branch when other work must continue concurrently.",
    "adapter_id": "Allowlisted application adapter identity. Dynamic module names and arbitrary executable paths are not accepted.",
    "adapter_version": "Exact reviewed adapter version. A mismatch fails before invoking application code.",
    "adapter_timeout_sec": "Bounded wall-clock time for reviewed adapter execution, from 0.1 to 60 seconds. Timeout fails the step; adapter concurrency is also capped.",
    "adapter_inputs": "Bounded JSON input mapping. A string beginning with '=' is evaluated by the safe expression engine against declared procedure data.",
    "adapter_results": "JSON mapping from adapter result keys to declared PA variables. All results are type/range checked before publication.",
    "integration_operation": "Read or write through an existing logical Tag DB binding, or invoke an allowlisted adapter. Procedure-owned OPC, COM and ActiveX sessions are prohibited.",
    "on_failure": "Action for a step execution error: fail ends Failed, hold ends Held, abort ends Aborted. Explicit Hold and Abort steps have their own terminal outcomes.",
    "section": "Authoring and audit classification. It does not create a branch or change execution order.",
    "equipment": "Equipment context for this step. It is documentation, not a controller-command target.",
    "unit_procedure": "Reusable hierarchy label retained in the executable revision, workflow view and audit evidence.",
    "document_link": "Controlled project-relative SOP, drawing or display reference available from the operator workflow.",
    "video_link": "Controlled project-relative training-video reference available from the operator workflow.",
    "audible": "Request an audible operator notification when the host presentation supports it.",
    "severity": "Procedure message severity. It does not impersonate a controller or SIS priority.",
    "notes": "Engineering notes retained in the saved revision. Use the instruction/prompt fields for operator-facing directions.",
    "condition_rows": "Edit block logic opens up to 32 conditions. Each row has an ID, expression and optional continuous hold time. Hold timers are available on Wait for condition.",
    "condition_logic": "Use row IDs with lowercase and/or and parentheses, for example C1 and (C2 or C3). Include every row. Blank uses the ALL/ANY selection.",
    "calculation_rows": "Ordered expressions assign PA memory top to bottom. Calculate runs once; Wait recalculates on each captured observation before checking conditions. Failed calculations roll back the row batch.",
}


# Each example describes values to enter in the inspector; project paths are
# deliberately left to the project's existing tag catalog rather than guessed.
BLOCK_DETAILS = {
    "subprocedure": (
        "Save and validate a reusable procedure first. Select its revision, configure typed inputs, optional tag aliases and result mappings.",
        "Runs under the same simulation clock, audit and operator authority as its parent. Workflow step IDs include the call path. A failed/timeout child may select an authored caller outcome path; Hold/Abort stop the whole run.",
        'Input parameters: {"target": {"expression": "requested_target"}}. Returned results: {"verified": "unit_ready"}. Use a Boolean input followed by a Decision for an operator escalation.',
        "Recursive calls, unresolved revisions, incompatible aliases and out-of-range parameters are rejected. Do not confuse a successful human acknowledgement with actual equipment feedback."),
    "instruction": (
        "Write one clear action and acceptance criterion. Add guidance when more explanation is needed; an optional prompt overrides both guidance and instruction.",
        "The operator acknowledges before progression. An acknowledgement records a response; use a Check or Wait block to verify an actual process condition.",
        "Instruction: Review the initial vessel level. Operator guidance: Open the vessel faceplate and confirm the displayed quality is Good.",
        "Unexpected wording usually means Operator prompt or Operator guidance overrides Instruction. Clear the override or edit the field that is displayed."),
    "operator_confirm": (
        "Describe the decision the operator must confirm. Put any longer decision wording in Operator prompt.",
        "Confirmation is recorded before the next step. Decline / abort in the station aborts the run; it does not choose another branch.",
        "Instruction: Confirm the training unit is ready for the setpoint change.",
        "Use Wait for condition for measured acceptance. A human confirmation alone does not verify tag readback."),
    "operator_input": (
        "Declare a result variable, choose the input type, then set numeric bounds or selection choices as appropriate.",
        "The typed response is checked and stored in the variable. It does not write a mapped process parameter.",
        "Result variable: target. Initial variable value: 45. Input type: float. Minimum: 40. Maximum: 60.",
        "Check variable declaration, whole-number requirements for int, selection value types, and minimum/maximum bounds if input is rejected."),
    "operator_comment": (
        "Set Comment prompt to request the exact observation to retain in the run record.",
        "A nonempty response is required and recorded in history before progression.",
        "Comment prompt: Record the stabilized pressure and any visible oscillation.",
        "Comment collection is descriptive evidence. Add a typed input or process condition if the value needs automatic evaluation."),
    "check": (
        "Declare and map each tag in Condition. Select the failure action for a False result or evaluation error.",
        "The condition is evaluated once when reached. True advances; False applies On failure.",
        "Condition: LOOP.PV >= 40 and LOOP.PV <= 60. On failure: fail.",
        "Use Wait for condition when the process needs time to reach the band. Check does not poll until success."),
    "permissive": (
        "Write the condition in granted-permit polarity: True means ready to proceed. Map the Boolean or numeric observations first.",
        "A single prerequisite evaluation gates progression. A False result applies the failure action.",
        "Condition: FEED.PERMIT == True. On failure: hold.",
        "An inverted expression can block a healthy unit. Verify the meaning of the source signal and its Good quality before starting."),
    "watchdog": (
        "Define the acceptable operating envelope and the failure outcome. Place this block at each point where a one-time envelope check is required.",
        "True passes. A violation records a procedure alarm and applies On failure. This block is not continuous background monitoring.",
        "Condition: VESSEL.PRESSURE < 8. On failure: abort.",
        "Do not rely on this as a continuous interlock, controller alarm or SIS. It checks only when execution reaches the block."),
    "wait_until": (
        "Define Condition, timeout, poll interval, continuous dwell and timeout outcome. Map the condition tags before validation.",
        "The runner observes the condition repeatedly using simulation time. False resets accumulated dwell; success advances after continuous observed True time.",
        "Condition: LOOP.PV >= 43 and LOOP.PV <= 47. Timeout: 60 s. Poll interval: 0.1 s. Continuous dwell: 5 s.",
        "If waiting never finishes, inspect the actual mapped value, quality, simulation pause and timeout. A brief excursion outside the band restarts dwell."),
    "delay": (
        "Set the required delay in simulation seconds. Use a wait condition when completion depends on a measured process result.",
        "The delay finishes after simulation time advances by the configured duration.",
        "Delay: 5 simulation seconds. Instruction: Allow the training response to develop.",
        "A paused simulation does not advance this delay. Delay alone does not prove stabilization."),
    "write_tag": (
        "Select a declared logical command tag, requested value and actual feedback tag. Include a later Wait for condition referencing the feedback tag.",
        "A live run asks the operator to authorize the request, then applies it through Operator Station's checked-write service. The later wait verifies actual readback.",
        "Command tag: LOOP.SP. Requested value: 50. Feedback tag: LOOP.SP. Follow with Wait for condition: LOOP.SP == 50.",
        "Validation rejects an output without a subsequent readback wait. Write authority, mode and interlock checks can still reject an authorized request."),
    "ramp_tag": (
        "Select a declared logical tag, start, end, positive rate and update interval. Include a later Wait for condition that verifies the final actual value.",
        "An isolated trial advances internal tags. A live run asks once for authorization, then sends each bounded point through the station's checked-write service.",
        "Logical tag: LOOP.SP. Start: 40. End: 50. Rate: 1 per simulation second. Follow with Wait for condition: LOOP.SP == 50.",
        "Any rejected point fails the ramp. Actual feedback verification remains mandatory after the final accepted write."),
    "read_tag": (
        "Map a readable logical tag and declare a result variable with a suitable initial value.",
        "A Good-quality observation is read into the variable and can be used by subsequent calculations or checks.",
        "Logical tag: LOOP.PV. Result variable: initial_pv. Declare initial_pv = 0.",
        "Check the tag mapping, access and quality. An absent or stale observation cannot be treated as a measured zero."),
    "calculate": (
        "Declare the result variable and all referenced tags or variables. Enter the restricted expression in Expression.",
        "The expression result is stored locally in the procedure variable; it does not issue a controller write.",
        "Expression: LOOP.PV - initial_pv. Result variable: delta_pv. Declare both variables and map LOOP.PV.",
        "Check names, tag mappings, numeric types and division by zero. General Python statements, imports and process access are unavailable."),
    "user_event": (
        "Choose a stable Event name, an informative Instruction and any Event detail needed in history.",
        "The event and payload are recorded, then execution continues.",
        'Event name: stabilization_review. Event detail: {"stage": "before_change"}.',
        "This is a procedure-history entry, not a broadcast to external software or an equipment command."),
    "warning": (
        "Describe the condition the operator should notice. Add a separate confirmation if acknowledgement must stop progression.",
        "A procedure warning is recorded and execution continues.",
        "Instruction: Review the response for oscillation before accepting the exercise result.",
        "A warning does not pause the procedure. Add Operator confirmation for an acknowledgement gate."),
    "alarm": (
        "Describe the procedure alarm. Add a separate confirmation, Hold or Abort when progression must stop.",
        "A procedure alarm is recorded and execution continues. It does not create a controller alarm or trip.",
        "Instruction: The training response requires instructor review. Follow with Operator confirmation when acknowledgement is required.",
        "Do not expect an alarm block to interrupt execution by itself. Its normal result is an alarm record, not an execution error."),
    "hold": (
        "State why this run must be held and what the operator or instructor should review.",
        "This version ends the run with Held status and retains evidence. It does not provide resume-from-held-step. Pause/Resume is a separate runtime control.",
        "Instruction: Hold for instructor review of the unstable response.",
        "Use Operator confirmation for a temporary acknowledgement gate, or the station Pause command for an active-run pause."),
    "abort": (
        "Give an explicit abort reason and any review instructions. Place this where the authored run should end as aborted.",
        "Execution ends with Aborted status and the reason is retained in history.",
        "Instruction: Abort this exercise because its initial condition is not acceptable.",
        "Aborting the procedure does not stop the controller or change equipment state. Operate equipment through the normal station controls."),
    "complete": (
        "Place Complete after acceptance checks and required observations. In a graph its connection leads directly to End.",
        "Successful completion finalizes the audit. A nested completion returns to the caller after its results are checked.",
        "Instruction: Acceptance checks and the operator observation are complete.",
        "Validation rejects steps after Complete. Completion reports procedure outcome; it does not alter the plant."),
}


GUIDES = {
    "operator_hmi": ("Procedure HMI · operation, tuning and trends", """
<p>In Graphics Designer open PVM Configuration Designer → New faceplate → Procedure HMI. The scaffold creates a paired PVM and faceplate plus Workflow, Conditions, Tuning, Trends and History detail classes. Set ProcedureRef to a saved project revision. Every page is editable through the existing graphics canvas; publish and retrieve it through the normal station workflow.</p>
<p>The PVM opens its faceplate. Workflow shows live step states and authored outgoing paths. Locate step opens the live graph and finds the active call or queued operator response. Previous / Next pages are bounded. Right-click a block for its exact Block Help, mapped equipment faceplate and procedure trends. Closing or pinning a view does not own or stop the run.</p>
<p>Conditions shows each expression, observed values, quality, state, continuous hold and reason. Each row qualifies independently before ALL / ANY / custom group logic earns its combined hold. Pause, lost evidence or a live parameter edit discards continuous qualification. The overall timeout remains visible and continues in active simulation time. No bypass is provided.</p>
<p>Expose parameters in PA Designer: on the Memory tab set Operator tuning to Next run or Live / next run and enter engineering units. Typed limits apply to every edit. In a Wait block's logic worksheet, Operator timer tuning exposes combined hold, overall timeout and individual row holds (0–86400 seconds; timeout must be positive). Unexposed values remain read-only.</p>
<p>Double-click or right-click a tuning row. Next run saves a one-time queued change for this exact revision in signed history. It does not change shared memory while another run is executing. A queued value can be cancelled. Live applies only to an exposed live parameter in the running unpaused procedure; it restarts hold evidence at an observation boundary. The overall timeout does not restart. Shared memory changes persist in the project Tag DB; local variables and timer overrides are run-local. Review the applied event, which records actor, old value, new value and mode.</p>
<p>Trends uses the station historian, with separate engineering scales for numeric process and memory values, quality gaps and procedure-event markers. Open Process History View to choose up to ten pens, adjust ranges and scales, inspect event details, review archived data and export. Collection begins when the points are registered; earlier unrecorded data is not synthesized.</p>
<p>History shows current events and recent runs of this procedure. Review / compare / export opens the existing signed run-report tools. The operator can always open equipment faceplates and work manually. Pause guidance for takeover; Resume rechecks current conditions. Break records an actor and reason before pausing. An authored skippable guidance step asks Continue or Skip before execution; Skip requires a reason. Abort ends guidance without moving equipment. Held is a terminal result, not a resumable pause.</p>"""),
    "getting_started": ("Getting started", """
<p>PA Designer authors manually started advisory procedures, using linear steps or advanced workflows. The editor is offline: opening it does not start a controller or simulator.</p>
<ol><li>Open Explorer → Applications → PA Designer. Create a procedure from Blank, Guided operation, Startup, Shutdown, Equipment changeover, Loop verification or Advanced workflow; or open a saved project revision.</li>
<li>Use the Procedure ribbon to enter identity, version and revision notes.</li><li>Drag a block from Blocks to Workflow. Drop on a wire to insert at that point.</li>
<li>Select each block and configure its Properties. Map logical tags and declare result variables.</li>
<li>Use the live Problems tab to navigate errors and warnings. Validate with F7, then Save revision with Ctrl+S.</li>
<li>In Operator Station, open Tools → Procedures, refresh the library and select the saved revision.</li></ol>
<p>Every starting template is a valid native procedure, not a separate document type. Replace placeholder conditions and wording with specific, reviewable plant evidence.</p>
<p><a href="help:workflow">Visual workflow</a> · <a href="help:engineering">Engineering productivity</a> · <a href="help:blocks">Block reference</a> · <a href="help:running">Operator workflow</a></p>"""),
    "workflow": ("Visual workflow and ribbon", """
<p>The compact ribbon uses the shared Control Designer buttons, group captions, icons and silver theme. Home contains document/edit commands; Procedure contains insertion and definition; View contains canvas tools; Help contains references. Use Collapse ribbon, Ctrl+F1, or double-click a ribbon tab to gain more canvas space; select a tab to expand. Wide pages scroll horizontally.</p>
<p>Workflow blocks use Control Designer's silver body and category-colored header: blue for operator actions, gold for conditions, teal for timing and sequence, green for tag I/O, slate for calculations, steel blue for events, purple for reusable procedures and crimson for safety/run-stop actions. Selection adds a blue outline without replacing the category color.</p>
<p>The status bar shows unsaved changes, block count, selection and actual workflow zoom. Click the zoom percentage to return to 100%, or Fit to see the whole procedure. Hover over a clipped status message to read it in full.</p>
<p>Procedures lists saved revisions in the current project's procedures folder. A new draft appears there only after Save revision. The list refreshes when you return to the window; Refresh beside the library heading reloads it immediately. F5 also reloads the tag catalog. Empty searches and unreadable files are reported in the library; file errors appear in Review.</p>
<ul><li>Drag a library block onto empty canvas to insert after the selected step. Drop onto a wire to insert at that connection.</li>
<li>Drag a block to arrange it. Layout changes do not change execution order.</li><li>Connect one block's output to another block's input to move the target immediately after the source in the real step list.</li>
<li>Drag a wire or its selected handle to change its bends. Double-click the wire or choose Auto Route to reset it.</li>
<li>Use Ctrl+wheel to zoom, middle-drag to pan, and Zoom to Fit for an overview. Escape cancels an active move or connection.</li>
<li>Right-click an unselected block to target it. Right-click within a selection to retain multi-block clipboard/delete operations.</li></ul>
<p>Workflow and Step list edit one document. Undo/Redo restores edits and completed gestures. Copy/Paste and Duplicate preserve block properties and relative geometry with new IDs. A single command creates one undo entry.</p>
<p>Use Steps → Connections to enable branches, parallel execution, bounded retries and transitions. Reusable procedure blocks invoke saved revisions. Complete leads directly to End. See <a href="help:advanced_workflow">Advanced workflow</a> for configuration, bounds and recovery behavior.</p>"""),
    "engineering": ("Engineering productivity and release", """
<h3>Diagnose and navigate</h3><p>Problems updates after editing. Filter errors or warnings, then double-click a finding to open its block property, flow node, connection or tag mapping. F7 remains the authoritative full validation before Save revision.</p>
<h3>Refactor safely</h3><p>Procedure → Rename symbol / Find usages previews every semantic tag, variable or step-ID use. It updates declarations, mappings, direct properties, expressions, logic rows, adapter/call contracts, flow references and retained geometry as one undoable command. Quoted text is not rewritten.</p>
<h3>Compare and reuse</h3><p>Compare revisions uses a saved immutable baseline and the working draft as target. Review workflow, step, tag, memory and mapping changes plus their impact. To reuse a contiguous linear sequence, select its blocks and choose Extract reusable procedure. The child revision is validated and saved first; the parent receives one pinned call. Advanced-flow extraction is refused when branch boundaries would be ambiguous.</p>
<h3>Annotate</h3><p>Add notes, phase bands, rectangles or swimlanes from the Procedure ribbon or canvas background menu. Drag to position; double-click or right-click to edit. Annotations persist as metadata and never execute.</p>
<h3>Review and release</h3><p>Save a draft revision, then choose Review and release. Enter actor and role for Review; use different actors for approval and release and provide a reason. The evidence sidecar is hash-chained and bound to all revision files. Any later byte change produces an integrity error. The local actor name is engineering evidence, not a regulated electronic signature or directory-authenticated identity.</p>
<p><a href="help:validation">Validation and revisions</a> · <a href="help:advanced_workflow">Advanced workflow</a></p>"""),
    "properties": ("Block properties", """
<p>Select a block, double-click it, or choose Properties from its context menu. The inspector shares Control Designer's collapsible property groups: Quick Configuration, Action, Timing, Failure Handling and Documentation.</p>
<p>Filter parameters searches field labels, parameter names and types without changing values. Matching groups expand while searching. Clear the filter to restore collapsed groups. Tooltips identify the stored parameter and type; timing labels show simulation seconds.</p>
<p>Library identity and version are read-only. F2 focuses the step ID for renaming. A step ID must be unique. Instructions and optional operator prompts have different roles: a populated prompt can override instruction wording.</p>
<p>Edits immediately update the draft. Incomplete numeric text remains visible and validation reports it; it is never silently replaced with a default. Use the field's native text shortcuts while typing.</p>
<p>Right-click → Block Help opens the reference for that exact block type, including every exposed property, defaults, examples and common mistakes.</p>"""),
    "tags": ("Tag mappings and variables", """
<ol><li>In a tag or expression field, type part of a path or click the search icon to use the project Tag DB browser. Search matches paths, descriptions and I/O tags. Choose a terminal or configuration parameter; the editor creates and reuses its logical mapping automatically.</li>
<li>Expression insertion preserves the surrounding calculation. Select existing text to replace it. A MODE terminal binds its actual mode. Tag mappings remains available for reviewing or manually editing bindings.</li>
<li>Result fields search memory. New memory tag saves float, int, bool or str, its initial value and optional limits directly in the project Tag DB. Definitions and last values persist in tagdb/memory.sqlite3. Control Designer uses the same tags through the memory_tag property of MEM_FLOAT, MEM_BOOL, MEM_INT and MEM_STRING. New PA runs use the last value.</li>
<li>The Memory tab also retains existing untyped variables (any). Typed input must match its destination memory type; input limits are intersected with memory limits. Calculation and read results must satisfy the destination type and bounds.</li></ol>
<p>Project parameter paths may refer to terminals, module parameters or CONFIG values. Use the catalog rather than guessing case or suffixes. A declared tag that has no valid mapping cannot be validated.</p>
<p>Use Procedure → Rename symbol / Find usages for tags, variables and step IDs. Its preview lists declarations, mappings, direct properties, logic rows, adapter inputs, call contracts and workflow references. Rename applies one undoable AST-aware change and does not replace matching text inside quoted strings. Copying blocks between procedures does not silently add destination tag mappings or variables.</p>
<p>Example: map LOOP.PV to a measured value, declare initial_pv = 0, Read parameter into initial_pv, then Calculate LOOP.PV - initial_pv into delta_pv.</p>"""),
    "timing": ("Timing and conditions", """
<p>Delay, timeout, polling and continuous dwell use simulation time. Simulation pause stops their progression. Procedure Pause also suspends the active procedure. Simulation reset or backward time invalidates the current run.</p>
<p>Conditions support comparisons and Boolean combinations of declared observations, for example <code>LOOP.PV &gt;= 43 and LOOP.PV &lt;= 47</code>. True means satisfied. Prerequisite permit expressions must be True when granted.</p>
<p>Check and Envelope check evaluate once. Wait for condition polls until its condition remains continuously observed True for the configured dwell, or its timeout expires. False resets dwell; zero dwell passes on the first True observation.</p>
<p>Example: timeout 60 s, poll 0.1 s, dwell 5 s. The value must stay inside the specified band across the observations. A five-second delay alone cannot prove that.</p>
<p>Use Edit block logic for multiple rows. Example: C1 checks level &lt; limit for 10 s; C2 checks feed &lt; threshold for 5 s. ALL combines qualified rows; ANY accepts an alternative. Grouped logic such as <code>C1 and (C2 or C3)</code> preserves nesting. Each row qualifies independently. A combined hold starts only after the row logic is satisfied; use zero combined hold when only row timers are needed.</p>
<p>The HMI shows Waiting, Timing, Satisfied or Uncertain, with elapsed/required row hold times and the combined timer. False resets its row timer; an unknown required observation cannot satisfy even an ANY group. Pause or a gap in the retained scan history restarts qualification. The runner consumes every received scan so a brief false pulse between worker polls cannot earn continuous time. A step stops being evaluated once it passes.</p>
<p>Calculations in the same Wait block run top to bottom on each captured observation, before conditions. For example <code>threshold = limit * 0.5</code> can feed a later condition. They assign the selected shared memory tags or legacy local variables through the existing restricted expression evaluator. Use Calculate for a row batch evaluated once. Apply commits the worksheet and its links as one undoable edit. Cancel discards those edits. A memory tag explicitly saved to Tag DB remains in the database even if the worksheet is cancelled; removing a PA link never deletes a shared tag.</p>
<p>Operator takeover: equipment faceplates and checked process commands remain available while guidance runs, pauses or ends. Pause guidance to work manually; End guidance records an abort without moving equipment. Resuming re-evaluates the current wait and starts a fresh dwell. It does not replay process commands.</p>
<p>Uncertain feedback is shown as Uncertain, cannot satisfy a wait, and resets continuous dwell. The timeout continues in simulation time. Start, Resume and operator responses require fresh Good observations. Bad, missing, stale or non-finite feedback stops guidance. All clauses in a condition group use one captured observation.</p>
<p>On timeout and On failure select fail, hold or abort. A generic Hold is a recorded terminal outcome; the catalog Pause Procedure block is a resumable authored suspension. Bad, missing or stale observations require correction rather than assuming a False or zero value.</p>"""),
    "validation": ("Validation and saved revisions", """
<p>The Problems tab updates after edits and separates errors from warnings. Double-click a row to locate its step, workflow node, connection, tag or property. F7 checks the procedure contract, block identities/versions, tag references, result variables, mappings, graph topology, bounded loops, parallel convergence and pinned dependencies. Review lists steps, mappings and warnings. Successful offline validation does not claim live readiness.</p>
<p>Complete must be final. A proposed value requires a later actual-readback wait on the same tag. New Azeo blocks retain their versioned catalog identity; mismatched or unknown identities are rejected.</p>
<p>Save revision creates a new project procedures directory with procedure.yaml and mapping.yaml. Earlier revisions stay intact. A failed save retains the draft and does not expose a partial revision. Changes become a draft revision and do not inherit prior approval signatures.</p>
<p>Compare revisions uses a saved immutable baseline and reports semantic changes and affected execution surfaces. Review and release records Draft → Review → Approved → Released evidence in a hash-chained sidecar bound to the complete revision digest; it does not rewrite the procedure payload. Actor separation is enforced, but this local evidence is not a regulated electronic-signature identity service.</p>
<p>Closing, opening or creating a different procedure prompts for unsaved changes. Immutable repository releases cannot be edited. Existing local referenced documents are retained when saving another revision; controlled-reference and linked-recovery authoring remain outside this editor.</p>"""),
    "running": ("Operator workflow and evidence", """
<ol><li>Use Operator Station → Tools → Procedures. Refresh library and choose the intended saved revision. Its Draft, Review, Approved or Released evidence state is shown beside the revision; state is review context, while station authority and live readiness still control Start.</li>
<li>Check live readiness and mapped observations, then Start procedure.</li><li>Read the active instruction, confirm or enter the required response. Decline / abort ends the run.</li>
<li>Authorize requested process outputs. They use the station's checked-write service; readback waits verify the actual result.</li>
<li>Use Active procedures to switch among up to eight supervised runs. Shared observations are permitted; overlapping output and shared-memory targets are reserved by the first active run.</li>
<li>Pause/Resume controls the selected run. Break with reason is an audited pause. A guidance step that permits operator skip asks Continue or Skip before it executes; Skip needs a reason and cannot bypass a process output or required verification. Abort ends the selected run without stopping the controller or changing equipment.</li>
<li>Review history and export the available run evidence after completion, failure, hold or abort.</li></ol>
<p>Missing/stale observations, simulation rewind and configuration changes can invalidate a run. Closing waits for its audit to finalize; it must not discard evidence. A generic Held run has finished; an authored Pause Procedure remains active and resumes after acknowledgement and a fresh observation.</p>"""),
    "troubleshooting": ("Troubleshooting", """
<table><tr><th>Symptom</th><th>Check</th></tr>
<tr><td>Block or field appears missing</td><td>Clear the block or parameter search, expand its group, and select the intended block. Scroll the ribbon horizontally on a small desktop.</td></tr>
<tr><td>Different instruction appears at runtime</td><td>Inspect Operator prompt and Operator guidance overrides, then save and select the intended revision.</td></tr>
<tr><td>Tag does not validate</td><td>Declare the logical name; choose the actual catalog path; verify terminal versus CONFIG case/access.</td></tr>
<tr><td>Wait never completes</td><td>Check simulation pause, actual value/quality, expression, timeout and continuous dwell resets.</td></tr>
<tr><td>Requested output was rejected</td><td>Check write authority, target mode, interlocks and the audited checked-write result. The procedure never bypasses the station write service.</td></tr>
<tr><td>Alarm did not stop progression</td><td>Procedure alarms log and continue. Add an explicit confirmation, Hold or Abort for the intended behavior.</td></tr>
<tr><td>Held run will not resume</td><td>Generic Hold is terminal. Use Pause Procedure or the operator Pause/Resume control for a resumable suspension.</td></tr>
<tr><td>Revision is absent at the station</td><td>Validate and save successfully, verify the project, then Refresh library at the station.</td></tr>
<tr><td>Paste unavailable or rejected</td><td>Copy procedure blocks first; text-field copy is a text clipboard operation. Invalid or oversized block selections are rejected without changing the draft.</td></tr>
<tr><td>Canvas action is cancelled</td><td>The editor restores document geometry after a UI error. Retain the draft and inspect the application diagnostics before repeating the failing action.</td></tr></table>"""),
    "shortcuts": ("Keyboard shortcuts", """
<table><tr><th>Shortcut</th><th>Action</th></tr>
<tr><td>Ctrl+F1</td><td>Collapse or expand the ribbon</td></tr>
<tr><td>Ctrl+N / Ctrl+O / Ctrl+S</td><td>New procedure / open selected library revision / save a new revision</td></tr>
<tr><td>Ctrl+Shift+R / Ctrl+Shift+X</td><td>Find usages and safe rename / extract selected blocks as a reusable procedure</td></tr>
<tr><td>Ctrl+Shift+D / Ctrl+Shift+G</td><td>Compare revisions / review and release saved revisions</td></tr>
<tr><td>Ctrl+Shift+N</td><td>Add a non-executable engineering annotation</td></tr>
<tr><td>F7 / F5</td><td>Validate / refresh library and tags</td></tr><tr><td>Ctrl+Z / Ctrl+Y</td><td>Undo / Redo</td></tr>
<tr><td>Ctrl+X / Ctrl+C / Ctrl+V / Ctrl+D</td><td>Cut / Copy / Paste / Duplicate selected blocks in Workflow or Step list</td></tr>
<tr><td>Delete / Ctrl+A</td><td>Delete selected blocks / select all workflow blocks</td></tr>
<tr><td>F2 / Alt+Enter</td><td>Rename / Properties</td></tr><tr><td>Ctrl+wheel / middle-drag</td><td>Zoom / pan canvas</td></tr>
<tr><td>Escape</td><td>Cancel the current canvas gesture</td></tr><tr><td>F1</td><td>Contextual help for the focused block or authoring tab</td></tr>
<tr><td>Ctrl+W</td><td>Close the editor, with unsaved-change protection</td></tr></table>
<p>Text editors retain native text selection and clipboard shortcuts. Double-click a ribbon tab to collapse it; click a tab to expand it.</p>"""),
}


GUIDES["advanced_workflow"] = ("Advanced workflow, reusable procedures and recovery", """
<h3>Build a visual workflow</h3><ol><li>Open Steps → Connections and select Enable advanced workflow. Existing steps and their execution order are retained.</li>
<li>Use Decision pair, Retry pair or Parallel pair for a bounded starting structure, or right-click the Workflow canvas → Add workflow node for an individual Decision, Retry loop, Transition, Merge, Parallel split/join or End.</li>
<li>Select a worksheet row and use the typed node/connection property inspector for transition timing, retry limits, join mode, label, outcome, condition, default marker and priority. The complete worksheets remain available for compact review.</li>
<li>Connect open output/input ports on the canvas. Problems identifies incomplete and ambiguous routes while the graph is being assembled.</li>
<li>Add ordinary blocks from the library, connect them into the graph and Validate. Disconnected blocks, duplicate connections and ambiguous routes are errors.</li></ol>
<h3>Decisions and explicit outcomes</h3><p>A Decision evaluates Good-quality observations and declared memory in ascending priority. Exactly one default path is required. It runs when no condition matches. Use Operator input with Boolean or selection type before a Decision for closed choices; a confirmation only acknowledges an instruction.</p>
<p>Action connections may select passed, failed or timeout. Only an observed rejection or wait timeout selects a recovery path. Storage faults, invalid calculations, missing data and operator Abort stop the run. A process Transition may require a continuous dwell; process_and_operator requires both process evidence and acknowledgement. Confirmation cannot replace a failed process condition.</p>
<h3>Bounded retries</h3><p>A Retry loop has max_iterations (1–1000). Each entry consumes an attempt. When exhausted, it takes its required default exit. Use that exit for an operator decision, a comment, Hold or Abort. Every graph cycle must include a Retry loop; the whole run also has a maximum node-visit budget. A new run resets these counters.</p>
<h3>Parallel observations</h3><p>Parallel split starts each branch. An AND join waits for all branches; an OR join accepts the first arrival and cancels remaining branches. Forks must converge at one matching join. Operator prompts queue in arrival order so none are overwritten. Abort cancels the group, including prompts and timed waits.</p>
<p>Use independent local result variables in each branch. Shared-memory writes, live-tuned result writes and equipment outputs belong before or after the join. Conflicting branch results fail the run. There are at most eight branches per split and 32 branch entries per procedure.</p>
<h3>Reusable modules</h3><p>The Reusable procedure block invokes a saved revision. Select the revision, pass typed local inputs, map optional tag aliases and return child results into parent memory. Save bundles the complete dependency tree. The run stores its document digest and all resolved dependencies. Recursion is prohibited; composition is limited to eight levels, 64 calls, 1000 expanded workflow nodes and 512 variables.</p>
<p>The operator sees the call path with each child step. Locate the pending response or active path in Workflow; equipment remains accessible through the existing faceplates. Child waits retain row holds, combined dwell, quality checks and simulation-time behavior.</p>
<h3>Operator intervention and recovery</h3><p>Pause freezes progression; Resume requires fresh observations and restarts condition qualification. Abort ends guidance without commanding equipment. Hold is a terminal review outcome; investigate the evidence and explicitly start a reviewed procedure. An Already satisfied expression supports safe re-entry checks. The author must verify actual feedback after any proposed action and before successful completion.</p>
<h3>Example pattern</h3><p>Check prerequisites → bounded attempt → request equipment action → wait for actual feedback and stable time → join verification. Route timeout to an operator Boolean decision. Yes may enter the next bounded attempt; No records a comment and ends Held. Verify replacement equipment before guiding removal of the original equipment. This is guidance logic, not an interlock or protection function.</p>
<h3>Troubleshooting</h3><p>No matching decision: review conditions/default. Retry exits early: inspect attempt history and max_iterations. Join blocked: inspect each active branch and queued prompt. Reusable call absent: refresh the library and choose a saved revision. No returned result: the child did not complete successfully. A declined confirmation is not a Yes/No decision; use a typed operator input.</p>
""")


@lru_cache(maxsize=1)
def help_topics():
    topics = {key: HelpTopic(key, title, "Authoring", body) for key, (title, body) in GUIDES.items()}
    catalog = []
    for block in block_library():
        kind = block.step_type
        configure, outcome, example, mistakes = BLOCK_DETAILS[kind]
        template = block.instantiate("example_step")
        rows = []
        for key, label, editor_type, fallback in parameter_specs(kind, block):
            value = template.get(key, fallback)
            default = "blank" if value is None or value == "" else json.dumps(value, ensure_ascii=False)
            if key == "id":
                default = "generated unique ID"
            value_type = ", ".join(editor_type) if isinstance(editor_type, tuple) else editor_type.replace("_", " ")
            rows.append(f"<tr><td>{escape(label)}<br><code>{key}</code></td><td>{escape(value_type)}<br>Default: {escape(default)}</td>"
                        f"<td>{escape(PARAMETER_HELP[key])}</td></tr>")
        extra = (["condition_rows", "condition_logic"] if kind in {"check", "permissive", "watchdog", "wait_until"} else [])
        if kind in {"wait_until", "calculate"}:
            extra.append("calculation_rows")
        for key in extra:
            rows.append(f"<tr><td>Edit block logic<br><code>{key}</code></td><td>Worksheet<br>Default: empty</td>"
                        f"<td>{escape(PARAMETER_HELP[key])}</td></tr>")
        body = (f"<p>{escape(block.description)}</p><p><b>Library:</b> {block.block_id} · {block.version}<br>"
                f"<b>Source identity:</b> {block.source_id} · {block.source_version}</p>"
                f"<h3>Configure</h3><p>{escape(configure)}</p><h3>When reached</h3><p>{escape(outcome)}</p>"
                f"<h3>Example</h3><p>{escape(example)}</p><h3>Properties</h3><table>"
                "<tr><th>Property</th><th>Type and default</th><th>Meaning</th></tr>" + "".join(rows) + "</table>"
                f"<h3>Common mistakes</h3><p>{escape(mistakes)}</p>"
                '<p><a href="help:tags">Tag mappings and variables</a> · <a href="help:timing">Timing</a> · '
                '<a href="help:validation">Validation</a></p>')
        topics[block.help_key] = HelpTopic(block.help_key, block.label, "Blocks · " + block.category, body)
        catalog.append(f'<li><a href="help:{block.help_key}">{escape(block.label)}</a> — {escape(block.description)}</li>')
    topics["blocks"] = HelpTopic("blocks", "Block reference", "Authoring",
        f"<p>The {len(block_library())} supported Azeo blocks use a versioned advisory profile. Choose a block below, "
        "right-click its instance or palette entry for Block Help, or press F1 with the block focused.</p><ul>" +
        "".join(catalog) + "</ul><p>Library provenance is not release approval. Unsupported upstream block types are not offered.</p>")
    return topics
