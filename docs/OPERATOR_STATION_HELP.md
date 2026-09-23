# Azeo Operator Station User Manual

Edition 1.0 - 23 September 2026
Application baseline: `0.4.0`
Document ID: AZEO-UM-STATION

Published-display operation, checked commands, alarms and process history. This guide uses the current Azeo application names and real
widget captures. Examples use the training system; values and demonstrated
conditions are not operating targets for a real plant.

Section numbers inherited from the [suite user manual](USER_MANUAL.md) are kept
intact so cross-application references remain accurate. Application-specific
commands depend on the active project, selected object and granted authority.

## Start with a task

| Goal | Where to go |
| --- | --- |
| Navigate live displays and open a faceplate | Relevant numbered section below |
| Investigate and acknowledge an alarm | Relevant numbered section below |
| Compare loop history and export evidence | Relevant numbered section below |

**Before changing live state:** use an instructor-approved training copy,
check the selected project and current source quality, and keep Save, Download,
Publish, Refresh and operator commands distinct. The [installation guide](INSTALLATION_GUIDE.md)
covers setup and repair. **Help > System information** gives the actual build,
licence and support paths on the workstation.

## 2. Your first session

### 2.1 Prepare the workstation

For a source checkout, create the environment described in the repository
README, open PowerShell at the repository root, and use:

```powershell
& .\.venv\Scripts\python.exe run.py
```

The command opens Azeo Explorer on the registered default project. On a
managed training workstation, use the installed launcher instead of the
source-checkout command.

For a list of available projects and areas, run:

```powershell
& .\.venv\Scripts\python.exe run.py --list
```

**Expected result:** The project list includes the intended training project, and the startup project is marked as the default. `--list` exits without starting the graphical application.

### 2.2 Open a specific application

Run these options from the repository root using the same interpreter. Replace the project path only when selecting a different prepared project.

| Option after `run.py` | Result |
| --- | --- |
| No option | Open Explorer on the registered default project |
| `projects/AzeoPlantVirtualController` | Open the explicitly named project |
| `--classic` | Open Control Designer as the primary window |
| `--graphics` | Open Graphics Designer as the primary window |
| `--station` | Open the dedicated Operator Station; Control Designer stays hidden until requested from a faceplate |
| `--simulation` | Open Simulation Workbench as the primary window |
| `--blank` | Open an empty scratch area |
| `--online` | Request an area download at startup; use only for a prepared configuration |
| `--no-plant` | Open an engineering session without a driven process source |
| `--help` | Show the launcher's command reference |

For the first commissioning session, use Explorer so you can explicitly inspect the project, provider, and module state. Application windows can also be opened from Explorer's **Applications** menu without launching another independent controller session.

### 2.3 Start the APVC training system

**Before you begin:** Confirm the selected project and use a training copy if you intend to make changes. The default APVC workflow uses the embedded Local Virtual I/O provider. Its manual startup state is deliberate.

1. Launch Explorer and read the project identity in the window.
2. Inspect the controller and Local Virtual I/O nodes under the physical network.
3. Open **Tools > Virtual I/O > Provider Status** if available, or the corresponding Local Virtual I/O node's status command.
4. Choose **Start Simulator** from **Tools > Virtual I/O**.
5. Confirm that the provider reports Running. Refresh the status and check that exchange activity advances.
6. Open the intended module in **Applications > Control Designer**. Compile and download the selected module as described in Chapter 4, or use the reviewed **Total Download** workflow for the prepared project.
7. Confirm that the required modules are online and their scan counters advance.
8. Choose **Applications > Operator Station**.
9. Verify the intended home display, current data quality, and station status.

**Expected result:** The provider is running, required modules are scanning, and bound values appear with credible quality. A source that has just started may need an observation interval before the station can establish freshness.

**Recovery:** If values are Bad, check provider state, online module state, and the tag path before changing a control mode. An open window and a moving clock do not prove that process data is healthy.

### 2.4 Make a read-only orientation pass

1. In Operator Live, select **Displays** and open one assigned unit display.
2. Select a live instrument or loop PVM to open its faceplate.
3. Read the module/block identity, PV, quality, and actual mode.
4. Select **Pin** on the faceplate title bar.
5. Select a second loop and compare the two independent windows.
6. Open **Trends** or the faceplate's history action.
7. Open **Alarms** and inspect the list without acknowledging anything solely to clear the screen.
8. Return to the home display with **Alt+Home**.

**Expected result:** You can locate equipment, retain a comparison faceplate, and reach process history without editing a graphic or changing a process value.

### 2.5 Finish a session

1. Finish an active training session so its report and input-fault cleanup are completed.
2. Review and release exercise-specific input simulations or engineering forces.
3. Save intended engineering changes. Publish only displays that have completed the release checks.
4. Save a project backup or simulation snapshot when the lesson requires one; these serve different purposes.
5. Close tools and application windows through their normal close controls.
6. Wait for application shutdown before restoring files or modifying provider configuration.

Closing a faceplate closes that window. Closing Operator Live is not the same command as pausing the coordinated simulation. Use the explicit Workbench or training-session pause command when you need the process and controller clocks to stop together.

## 10. Operator Live: everyday operation

### 10.1 Read the operating workspace

Operator Live is the operating name used here for Azeo Operator Station. It displays published graphics, not an unsaved Graphics Designer canvas. The station's assignment determines its available displays and layout.

![Figure 10-1. Operator Live presents commands, hierarchical navigation, the process display, alarms and source status.](images/user_manual/operator-overview.png)

| Area | What it tells you or opens |
| --- | --- |
| Search | Find an operating object or display through the supported station search |
| Alarms | Open the alarm summary/investigation context |
| Trends | Open Process History View |
| Training | Open the training workspace when its service is available |
| Tools | Layout, theme, density, diagnosis and training tools |
| Refresh | Retrieve applicable published configuration updates |
| Station menu | Display errors, filtering, utilities, display tags, window mode and supported user/authority controls |
| Navigation row | Back, forward, home, parent, breadcrumb and display chooser |
| Process area | The current published display |
| Operating workspace | Context tools such as trends, diagnosis, alarms and training |
| Alarm banner and status | Alarm attention and observed source state |

Use the process display to understand the plant. Use the source status to judge whether its values are current. The UI clock is not a substitute for either.

### 10.2 Navigate quickly

1. Use **Displays** or **Ctrl+L** to open a known assigned display.
2. Use the breadcrumb and parent/home controls for the display hierarchy.
3. Use **Alt+Left** and **Alt+Right** for navigation history.
4. Use **Alt+Home** for home and **Alt+Up** for the parent display.
5. Use **Search** or **Ctrl+K** when starting from a known tag or object name.
6. Use authored display links for the next operating task.

Returning to a retained display restores its view state, including manual zoom/pan. This helps an operator move between related pages without repeatedly rebuilding the workspace. Hidden retained views do not need to remain visibly open to be recalled.

If a destination is absent, inspect the assigned display set, published availability and hierarchy. Repeated clicking or restarting is not a substitute for correcting a missing assignment.

### 10.3 Zoom and arrange the workspace

| Action | Use |
| --- | --- |
| Ctrl+mouse wheel | Zoom the process graphic |
| Middle-button drag | Pan the process graphic |
| Ctrl+0 | Restore the display's fit behavior |
| Ctrl+= / Ctrl+- | Zoom in/out |
| F11 | Toggle full screen/window mode |
| Tools > Comfortable toolbar | Choose larger labeled commands or compact density |
| Tools > Dock operating tools | Choose the presentation of contextual tools |
| Workspace > Pop out | Open the selected tool in its own tool window |
| Workspace > Hide | Reclaim process viewing space while retaining tool context |

Narrow windows may show command icons with tooltips instead of full text labels. This does not change the command's action. Faceplates and historian charts always use separate windows, independent of the tools-docking preference.

### 10.4 Open, pin and compare faceplates

1. Click the live PVM for the intended loop or equipment.
2. Confirm the identity on the faceplate before using any control.
3. Drag the title bar to place the detached window where it does not obscure important process information.
4. Select **Pin** to retain that faceplate.
5. Click another PVM to open its faceplate in a separate window.
6. Pin additional faceplates only when needed for comparison.
7. Close a faceplate using its own close control when the comparison is complete.

Opening another faceplate replaces the unpinned one. Pinned windows stay open. Selecting an already open object brings its existing faceplate forward instead of creating an unnecessary duplicate. Faceplates preserve their intended size; on a small logical desktop, the body can scroll while the title controls remain accessible.

![Figure 10-2. A detached PID faceplate retains identity, live values, mode, trend, alarms and contextual actions.](images/user_manual/loop-faceplate.png)

**Recovery:** If a click does not open a faceplate, confirm that the object is an actual bound PVM or has a configured faceplate action. A decorative equipment shape is not necessarily interactive. Check **Station > Display errors** and the display's published revision.

### 10.5 Interpret a measured-loop faceplate

| Indication | Interpretation |
| --- | --- |
| PV | Measured/processed process value with its quality and units |
| SP | Setpoint value appropriate to the faceplate's binding |
| OUT | Controller output or commanded value for that contract |
| Actual mode | Mode currently achieved by the block |
| Target/requested mode | Mode requested by the operator/configuration |
| Mode disagreement | Actual and target are different; inspect tracking, limits and downstream acceptance |
| Scale/bar | Value's position within the configured engineering range |
| Alarm list/marks | Alarm state for the associated object/module |
| Simulation/forced status | Value or behavior has a simulation/engineering intervention |
| Unavailable-value marker | The value is configured but cannot currently be shown as a trustworthy reading |

A requested mode is not proof of an achieved mode. A displayed output is not proof that a valve moved. Confirm downstream mode, acceptance and actual feedback where available.

### 10.6 Change a setpoint or output

**Before you begin:** Confirm the equipment identity, current quality, actual/target mode, operating authority, permitted range, and the instructor's allowed change. The selected field must be writable through the installed faceplate or authored User Entry.

1. Select the intended value/input control.
2. Read the current value and engineering units.
3. Enter the requested value using the supported editor.
4. Review the requested value and any confirmation presented by the application.
5. Submit once.
6. Read **Accepted** or **Rejected** feedback and its reason.
7. Verify the resulting working value, actual mode, output and process response.

Accepted means the existing write service accepted the request. It does not assert actuator movement or completed mode transfer. Rejected feedback remains useful evidence; inspect its reason instead of repeatedly submitting the same command. The UI does not retry rejected writes for you.

### 10.7 Transfer a mode

Mode availability depends on the block family and engineered strategy. Typical labels include MAN for manual operation, AUTO for local automatic control, and CAS for an accepted cascade demand; other modes can be present.

1. Read actual and target mode on the correct faceplate.
2. Check PV quality, SP/OUT agreement, permissives, interlocks, tracking and downstream acceptance.
3. Select the permitted target mode from the supported mode control.
4. Read command feedback.
5. Confirm the actual mode reaches the intended state.
6. Observe the trend for an unexpected output or process discontinuity.

If actual and requested mode remain different, investigate the reason. Do not treat the disagreement as a graphics defect until the runtime conditions have been checked.

### 10.8 Open details, history or engineering

Use the faceplate's contextual actions to open its installed detail view, related controls, history or engineering module. Labels and tooltips identify the action. The engineering action opens **Control Designer** focused on the associated module; it does not open Graphics Designer to edit the display.

Use Graphics Designer through its own application entry point when the task is to edit a display or PVM. That edit still follows Save, Verify, Publish and station Refresh.

### 10.9 Interpret station freshness

| State | Meaning | First check |
| --- | --- | --- |
| LIVE | The station has observed appropriate completed controller scan progress | Confirm the individual value also has suitable quality |
| NO DATA | Freshness cannot yet be established from available scan evidence | Provider/module startup and observable counters |
| PAUSED | Execution is deliberately stopped through the simulation/runtime state | Whether the lesson intentionally paused |
| DISCONNECTED | Required online module/source availability is absent | Module/provider connection and online state |
| STALE | Expected scan progress has not arrived for the relevant timing | Controller scan health and stalled modules |
| PARTLY PAUSED | Some online modules are paused while others progress | Module-specific debugger/pause state |
| SIMULATION indication | The operating environment is simulated | Identify any additional input substitutions/forces |

Freshness follows observed controller counters and module periods. A healthy slow module cannot prove a stalled fast module is current. Individual Bad/Uncertain values still need investigation even when the station has observed scanning elsewhere.

### 10.10 Retrieve published updates

1. Complete the immediate operating/training action.
2. Read the **Refresh** indication and planned configuration context.
3. Choose **Refresh** or press **F5** in Operator Live.
4. Confirm the intended display revision/content.
5. Test any changed navigation or faceplate action.

Refresh does not save a Studio draft or download a controller module. An update count relates to applicable station updates; it is not a tally of all engineering files in the project.

## 11. Alarms and condition investigation

### 11.1 Distinguish state from acknowledgement

An alarm describes an abnormal condition and its acknowledgement state. Returning to normal does not necessarily mean that the operator has acknowledged the event. Acknowledging an active alarm does not remove its cause.

| Condition/response | Meaning |
| --- | --- |
| Active, unacknowledged | Abnormal condition requiring attention and acknowledgement according to the exercise |
| Active, acknowledged | Condition remains abnormal; operator response is recorded |
| Returned, unacknowledged | Condition has cleared, but acknowledgement remains outstanding |
| Shelved | Temporarily removed from normal annunciation under a recorded reason/duration |
| Suppressed | Separate configured/runtime suppression state; do not confuse with timed shelving |
| Horn silenced | Audible attention has been silenced; alarm acknowledgement is a separate action |

Read the text, symbols and priority, not color alone. Displayed alarm-limit regions can be neutral scale information; a colored graphic is not by itself evidence of a live alarm record.

### 11.2 Investigate before acknowledging

1. Open **Alarms** or press **Ctrl+Shift+A**.
2. Check the current filter and scope.
3. Select the intended alarm and read its object, condition, priority and state.
4. Open its faceplate/detail or **Tools > Alarm investigation**.
5. Inspect related values, conditions and trend context.
6. Follow the exercise's response guidance.
7. Acknowledge the selected alarm using the provided action.
8. Verify the acknowledgement state and whether the condition is still active.

The alarm list preserves a selected alarm by its identity as updates arrive. If that alarm disappears, selection is cleared; select the intended target again rather than assuming that the next visible row is the same alarm.

### 11.3 Filter the alarm view

Use **Station > Alarm filter** for the supported priority/state/scope controls. Review the active filter whenever a banner and list seem to disagree. A filtered-out alarm can still be present elsewhere in the station's alarm context.

Filtering changes what you are viewing; it does not clear an alarm, acknowledge it, or remove its process cause. Restore the intended operating filter before handing over the workstation.

### 11.4 Use Alarm investigation

1. Choose **Tools > Alarm investigation**.
2. Select the alarm of interest.
3. Read the response guidance and related module context.
4. Inspect **Observed events** for transitions seen by the station.
5. Open **Conditions** or the condition faceplate to inspect reported permissives/interlocks and available controller first-out outputs.
6. Open **Trend** to compare the condition with process behavior.

Events arriving within one station poll are observations, not a proven causal order. A controller-reported first-out value is different evidence from sorting timestamps sampled by the user interface.

For a permit-style signal, True means granted. A false permit is not granted; an absent binding is unverified. Check the installed block's polarity and the actual binding before interpreting a condition. Do not paint a permanent healthy indication for a missing source.

![Figure 11-1. Alarm investigation with a demonstration alarm and example guidance in an isolated documentation session.](images/user_manual/alarm-investigation.png)

### 11.5 Shelve and unshelve

**Before you begin:** Confirm that temporary shelving is part of the instructor's intended workflow and that the alarm identity is correct.

1. Select the alarm in Alarm investigation.
2. Enter a specific reason.
3. Choose a duration within the UI's supported range of one minute to 24 hours.
4. Choose **Shelve**.
5. Verify the shelf state and record why monitoring attention is being handled differently.
6. Choose **Unshelve** to end the shelf early, or verify behavior when it expires.

When a shelf expires, an active condition becomes unacknowledged again. Shelving is not indefinite suppression and is not a repair. Include active shelves in the session handover and cleanup review.

### 11.6 Maintain response guidance

Where the tool provides editing, enter guidance for the selected alarm key and choose **Save response guidance**. Guidance should explain the condition, useful confirming observations, the instructor-approved response, and the result that establishes recovery.

Guidance is stored with the project's display configuration. It is authored information and requires project review. It is not generated proof that a response is correct for every operating state.

## 12. Trends and loop diagnosis

### 12.1 Open the relevant history

1. Select the loop/equipment context or open its faceplate.
2. Choose its history action, **Trends**, or **Ctrl+Shift+H**.
3. Confirm the selected module and the available pens.
4. Review the time window, axis units, pen labels and quality gaps.
5. Move or resize the separate historian window while navigating to related process displays.

To start from a displayed value, right-click its tag and choose **Add to Historian**. To chart several tags together, hold **Ctrl** and click each PVM or numeric Data Link, then right-click a selected tag and choose **Add to Historian (N tags)**. Blue outlines mark the selection. A PVM contributes its PV or primary value; **Other values** offers additional displayed parameters such as SP and OUT. Ctrl-click again to deselect a tag; **Esc** clears the selection. Normal PVM clicks still open faceplates.

New tags join the open chart without duplicates. Each chart supports ten pens; if the selection would exceed that limit, it opens in another historian window. See [Process History workspace](HISTORIAN_WORKSPACE.md) for archive browsing, measurements, saved groups and exports.

The Process History View uses the shared history service. The amount of history available depends on what has been collected and retained. An empty period before recording began is not evidence that the process was constant.

### 12.2 Read a trend correctly

| Check | Why it matters |
| --- | --- |
| Tag/pen identity | Similar names can refer to PV, field value, SP or output |
| Units and range | A percentage output and engineering-unit PV may use different scales |
| Time range | A short response and a slow drift require different windows |
| Quality | Bad/missing observations should not be interpreted as valid continuous data |
| Mode | Manual, automatic, cascade and tracking responses have different causes |
| Intervention timing | A setpoint change, fault, tuning edit or restore changes the interpretation |

Use the cursor and available trend controls to inspect an event at a particular time. Add only the pens needed to answer the current question; too many similarly colored traces make diagnosis harder.

### 12.3 Open Loop diagnosis

1. Choose **Tools > Loop diagnosis**, or **Investigate loop** in Training.
2. Select the intended loop.
3. Inspect PV/SP/OUT, actual/target/normal modes, configured output limits, tracking and back-calculation where reported.
4. Add an optional valve-feedback parameter path when a suitable value exists.
5. Open the faceplate for supported operating/tuning controls.
6. Compare the numerical state with the shared trend.

The diagnosis tool records measurements made during an active training session in its timeline. Its metrics support engineering judgement; they do not automatically grade a trainee or choose safe tuning values.

![Figure 12-1. Loop diagnosis combines history, operating state and measured baseline/trial comparison.](images/user_manual/loop-diagnosis.png)

### 12.4 Measure a baseline and trial

**Before you begin:** Prepare an instructor-approved response test with a known step and positive settling tolerance in engineering units. Use the same starting snapshot, allowed input, mode, tolerance and observation window for baseline and trial. Record any limit, tracking, simulation or additional disturbance that makes the responses different.

1. Select the loop and tolerance.
2. Establish the agreed starting condition and carry out the allowed change.
3. Choose **Begin baseline** to record the baseline response window according to the exercise timing.
4. Wait long enough to observe the response, then choose **End measurement**.
5. Record the actual mode, limits and conditions during the baseline.
6. Apply only the planned trial change and restore a comparable starting condition.
7. Choose **Begin trial**, observe the response, and choose **End measurement**.
8. Compare the metrics together with the plotted signals and quality.

| Metric | Meaning | Interpretation limit |
| --- | --- | --- |
| Accumulated absolute error | Integral of absolute SP-PV error, in EU seconds | Does not bridge Bad/missing observations |
| Overshoot | Excursion beyond the selected step target, in engineering units | Single-step interpretation requires a stable target |
| Settling time | Time to remain inside the chosen tolerance | Requires five continuous seconds inside tolerance at the end of the window |

Changing setpoints or Bad/missing observations can invalidate single-step response metrics. A response that has not settled by the end of the window should not receive a fabricated settling time. Record invalid results and the reason instead of treating them as zero error.

### 12.5 Navigate history and use the trend context menu

Right-click inside the chart, or focus it and press **Shift+F10**, for historian
commands. Clicking near a trace selects its pen context; time-based commands use
the clicked time.

| Task | Command or control |
| --- | --- |
| Follow current data | Live / Return to Live |
| Inspect an earlier period | Freeze, drag the chart, or choose Archive dates |
| Move through time | Previous, Next, Center, Zoom or Fit |
| Measure a response | Place A/B cursors; inspect A, B, change and rate |
| Arrange traces | Pens and values checkboxes; pen menu for order, style, color or removal |
| Compare different scales | Engineering values or Compare % of configured span |
| Save a useful chart | Save group; reopen the named group later |

Freezing the view does not stop collection. **Current** shows the latest station
sample even while an older interval is displayed. **Age** measures time since
collection, not the field device's acquisition age. Cursor values include the
actual nearby sample time and quality; a distant sample is not presented as an
exact measurement at the cursor.

Bad values break traces. A/B differences require Good values at both markers,
and statistics use finite Good samples. Long intervals may display an envelope
that preserves extrema; zoom in for cursor or response measurements. Statistics
and exports use raw samples. Chart scales do not change controller ranges.

### 12.6 Review events, compare runs and export evidence

1. Choose the required interval and pens. Open **Events**, filter the category,
   and select an event to focus on its response.
2. Use **Bookmark / note** to record an observation at the cursor or latest time.
3. Open **Compare runs** to select baseline and trial intervals, or saved training
   attempts from the same loop. Choose the alignment and engineering-unit tolerance.
4. Review PV/SP/OUT overlays, quality coverage, duration, error, overshoot and
   settling results. Missing alignment events or unsuitable data remain explicit.
5. Use **Export** for the visible interval, A/B interval or all retained history.
   Choose an HTML report, raw CSV bundle or chart PNG; comparisons have their own
   report and aligned data export.

Match loop identity, units and test conditions before comparing responses.
Recorded events are station observations and do not establish controller-scan
ordering. Measurements support instructor judgement without assigning a grade.

The normal station records locally while it runs; closing a trend window leaves
collection active, but closing the station stops it. **Archive** exposes retention
settings. The default is one day and 1024 MB; retain exports before reducing limits
or allowing evidence to expire. The status identifies an archive failure or a
memory-only view. This is a local station historian, not a redundant archive service.

### 12.7 Inspect point identity and recorded configuration

Select a pen and open **Point configuration**, also available from the pen-table
context menu. Review its identity, current configuration and recorded paths,
units/ranges, revisions and releases in the selected interval.

For released repository modules, the same object retains history through a rename;
a different object reusing its old name does not inherit that history. Open charts
and saved groups follow the point identity. Older address-based records remain
separately available without invented repository or release information.

If a historical interval contains different engineering units, combined
measurements and that mixed-unit trace are suppressed. Select a single
configuration interval; the historian does not infer unit conversion. A live
**Current** cell with different units displays its own unit. Exports retain each
sample's recorded path, units, identity and release context.

![Figure 12-2. Point configuration follows one demonstration loop through a rename while preserving the recorded paths, revisions and releases.](images/user_manual/point-configuration.png)

## 17. Keyboard and gesture reference

### 17.1 Shortcuts depend on the active application

**F5 has four different meanings:** refresh Explorer, download a module in Control Designer, enter TEST in Graphics Designer, and retrieve configuration in Operator Live. Confirm window focus before using it. A text editor or modal dialog can also consume a shortcut.

### 17.2 Explorer and Control Designer

| Application | Shortcut | Action |
| --- | --- | --- |
| Explorer | F5 | Refresh the project view |
| Explorer | Ctrl+F | Search the system |
| Explorer | F1 | Explorer Help |
| Control Designer | Ctrl+N / Ctrl+O | New/open strategy |
| Control Designer | Ctrl+S / Ctrl+Shift+S | Save/Save As |
| Control Designer | Ctrl+F4 | Close module |
| Control Designer | Ctrl+Z / Ctrl+Y | Undo/Redo |
| Control Designer | Ctrl+F | Find block |
| Control Designer | F7 | Compile |
| Control Designer | F5 | Download |
| Control Designer | Shift+F5 | Take all online modules open in Control Designer offline |
| Control Designer | Ctrl+K | Compare Parameters |
| Control Designer | Ctrl+0 | Zoom to Fit |
| Control Designer | Ctrl+Shift+G | Graphics Designer |
| Control Designer | Ctrl+Shift+O | Operator Station |
| Control Designer | Ctrl+T | Tag Database |
| Control Designer | Ctrl+M | Monitoring Tab |
| Control Designer | Ctrl+Shift+W | Watch Window |
| Control Designer | Ctrl+Shift+M | Controller Simulator |
| Control Designer | F1 | Control Designer Help |

### 17.3 Graphics Designer and PVM Configuration Designer

| Shortcut/gesture | Action |
| --- | --- |
| Ctrl+N | New display |
| Ctrl+K | Search Graphics Designer commands and recent components |
| Ctrl+S | Save display/class |
| Ctrl+Shift+P | Publish an eligible display |
| Ctrl+E | Toggle/request Edit mode |
| F5 | TEST mode |
| F8 | Verify |
| F11 | Focus-view toggle |
| Ctrl+2 | Library Explorer |
| Ctrl+3 | Control Data |
| Ctrl+Tab | Next open tab |
| V / H | Select / Pan |
| R / E / L | Rectangle / Ellipse / Line |
| C / P / A | Connector / Pencil / Arc |
| S / X / T | Polygon/shape tool / Eraser / Text |
| Esc | Cancel the active gesture/tool |
| Space+drag | Temporary pan |
| Ctrl+drag | Copy the selection |
| Shift+drag | Axis-constrained movement |
| Alt+drag | Bypass snapping |
| Arrow keys | Nudge selection in Edit mode |
| Shift+Right / Shift+Left | Grow/shrink width about the center |
| Shift+Up / Shift+Down | Grow/shrink height about the center |
| Shift+F | Zoom to selection |
| F1 | Contextual Help Center |
| Designer: Ctrl+Z / Ctrl+Y | Undo/Redo the current class configuration |

Single-letter drawing shortcuts are intended for the authoring canvas. When editing a text/property field, use the field's normal text-entry behavior and then return focus to the canvas.

### 17.4 Operator Live

| Shortcut/gesture | Action |
| --- | --- |
| Alt+Left / Alt+Right | Previous/next navigation history |
| Alt+Home / Alt+Up | Home / parent display |
| Ctrl+L | Display chooser |
| Ctrl+K | Search |
| Ctrl+Shift+A | Alarm list |
| Ctrl+Shift+H | Process History View |
| F5 | Retrieve published configuration updates |
| F11 | Full screen/window |
| Ctrl+0 | Fit process display |
| Ctrl+= / Ctrl+- | Zoom in/out |
| Ctrl+mouse wheel | Zoom the process graphic |
| Middle-button drag | Pan the process graphic |
| Faceplate title drag | Move the detached faceplate |
| Faceplate Pin | Retain that window when another object is opened |
| Ctrl+click PVM/numeric Data Link | Select tags together for Add to Historian |
| Shift+F10 in historian chart | Open the historian context menu |

### Related Azeo help

- [Complete suite user manual](USER_MANUAL.md) - connected workflow and glossary.
- [Installation and administration](INSTALLATION_GUIDE.md) - setup, update and repair.
- [PA Designer authoring guide](PA_DESIGNER.md) - block and procedure details.
- [Control Module Class tutorial](CONTROL_MODULE_CLASS_TUTORIAL.md) - reusable control engineering.
- [PVM and faceplate tutorial](PVM_FACEPLATE_TUTORIAL.md) - reusable graphics engineering.

Use the application's F1 help for the installed block or selected tool. A screenshot
documents the interface, not live readiness or permission to perform a command.
