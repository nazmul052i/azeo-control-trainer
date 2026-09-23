# Operating the HMI

The interface follows Azeo HMI and ISA-101 practice: grey-blue displays
that stay quiet, colour reserved for things that are actually abnormal, and
everything on a graphic one click from its faceplate.

## Displays

The process graphics fill the window; the buttons along the bottom
switch displays:

- **Overview** — the whole plant on one PFD-style graphic: live loop boxes,
  valves with the owning loop's mode letters, pumps coloured by their run
  feedback, the recycle return through MOV-1002, and the SIS banner top
  right.
- **Feed & Fuel, Reaction, Fractionation, Utilities** — the section
  displays. Every one of the 80 control modules appears here as a live
  PV / SP / OUT box grouped by unit, so nothing is reachable only through
  a list.
- **D1, C1, H1, R1, T1, T2, B1 P&ID** — detailed unit displays: equipment
  drawn with internals, every control valve carrying its live position,
  every shutdown valve its limit-switch state, and the unit's loop PVMs
  beside the equipment they act on.
The raw tag table lives in its own non-modal window — **Variables**
(F2, or View menu): every point by unit with value, units, quality and
OPC UA node id, with Trend selected and the same right-click menu as
the graphics. It opens beside the displays, not instead of them.

Loop PVMs are drawn with the ISA-5.1 dotted shroud: dashed enclosure
means a DCS software function, solid boxes are field measurements.

Click any loop box, valve, pump, transmitter or vessel to open its
faceplate. The Loops panel (a plain list of all modules) is hidden by
default; bring it back from View, Panels.

## Trends

Trending works like a real historian client: it lives in its own
windows, not in a pane. **Add to trend** on any tag (right-click, the
variables tree's Trend selected, or Ctrl+Enter in the Go-to palette)
opens a trend window if none is up and lands the pen in the one you
last used; **F4** brings it forward, **View > New trend window** opens
more - one per monitor if you like, each fully independent, all live
beside any display.

Inside a window: up to eight pens, each with its own colour
(double-click the swatch) and scale — autoscale, the tag's range, or a
manual span, from the pen's right-click menu. The wheel zooms the time
axis, dragging pans it, and either lets go of **Live**; re-check Live
to ride the right edge again. The cursor line reads every pen out in
engineering units in the legend. **Groups** saves the pen set -
colours, window and all - under a name and recalls it in one click,
like a saved trend display. **CSV** exports the visible window (tag,
time, value), **PNG** the chart image.

## Faceplates

The simulator supplies three faceplate families:

**PID loop (Loop_fp).** PV, SP and OUT with slew buttons, the PV bar with
its alarm limit marks (neutral until that alarm is active), mode buttons
(MAN / AUTO / CAS where a master exists), the condensed trend, and the
alarm list. The detail display (first icon button) opens tuning, limits,
the setpoint filter, PID structure and beta / gamma, per-alarm enable /
out-of-service / shelve, SIMULATE, and the MERROR diagnostics tabs.
SIMULATE substitutes the block's PV while the transmitter keeps reading
the field: the (S) badge shows while it is active.

**Motor / valve (DL_fp).** State commands behind the Accept check
(START / STOP, OPEN / STOP / CLOSE), the real travel or start timer,
Reset for a latched fault, the status icon column (no permit, interlock,
bypass), and the fail alarm with its priority symbol. The detail display
carries the timers, feedback SIMULATE, live I/O bits, and the interlock
list with first-out arrows.

**Analog monitor (AI_fp).** For any measurement that is not a loop PV:
the PV coloured by signal status, the AI bar graph with configured alarm
limits, trend, and a detail display with limits, PV filter, SIMULATE and
the five-row alarm table.

Alarm priority symbols use the simulator's configured presentation: red circle-X
critical, yellow triangle warning, purple diamond advisory, grey bell
when shelved.

## Alarms

The plant-wide alarm system scans the configured schedule (the Alarms
sheet of the design workbook) against every tag - 151 points with the
sheet's setpoints, priorities, deadbands and on-delays - plus a PV BAD
alarm on every scheduled transmitter and one CRITICAL alarm per SIS trip
cause, first-out marked. It runs whether or not any faceplate is open,
and in both loop modes.

**The banner** at the top of the window always shows the most important
unacknowledged alarms, worst and newest first: flashing while active and
unacknowledged, dashed once the condition has cleared but the alarm has
not been acknowledged. Click an entry to open its faceplate; right-click
to acknowledge or shelve it. *Ack shown* acknowledges everything on the
banner; *Silence* stops the horn without acknowledging anything (the
audible horn can be disabled under Simulation > Audible annunciation).

**The alarm summary** (F3, or the *Alarms...* button) is the working
view, three tabs:

- **Active** — everything in alarm or awaiting acknowledgement, filtered
  by priority, unit or text. ● unacknowledged, ○ acknowledged, ↓ cleared
  but unacknowledged. Ack, shelve and out-of-service act on the
  selection; double-click opens the faceplate.
- **Shelved / OOS** — what has been temporarily shelved (it returns by
  itself when the shelf expires) or removed from service (it stays out
  until returned). Both are journalled with who and when.
- **Journal** — the persistent event chronology: every activation,
  return, acknowledgement, shelve, SIS trip and operator action (loop
  open/close, forces, snapshot restores, SIS resets), filterable by
  event type, window and text. It survives restarts in
  `history/events.sqlite`.

Acknowledging is annunciation bookkeeping: it never changes the process,
and a SIS latch still needs its cause cleared and a field reset. In open
loop FT-1003 LO stands: nothing is holding the P-101 minimum-flow
recycle, and the alarm correctly reports that. Closing the loop restores
the 24 m3/h recycle and clears it.

## Open and closed loop

Simulation menu, **Closed loop control** (F9):

- **Checked** — the built-in software DCS holds the plant: 80 modules on a
  200 ms scan, cascades, cross-limits, overrides and the quality masters.
- **Unchecked** — open loop. Every output holds exactly where it was,
  every block drops to MAN and tracks, and the plant is yours: move
  valves by hand from their tag faceplates, or connect an external DCS
  over OPC UA and let it write the outputs. Re-checking adopts the plant
  as found (SP = PV) and closes every loop bumplessly.

The SIS stays armed in both modes; the overview banner shows which mode
you are in and, after a trip, the first-out cause. See
[Control system and ESD](control.md).

## Workspace

- **Ctrl+K** opens the *Go to* palette: type a few characters of any
  tag, module or description; Enter opens its faceplate, Ctrl+Enter adds
  it to the trend.
- **Alt+Left / Alt+Right** walk back and forward through the displays
  you have visited.
- **View > New display window** opens a second, independent operator
  display for another monitor; open as many as you have screens.
- The workspace persists: window size and position, dock layout and
  visibility, speed factor, the variables window and the
  audible-annunciation setting all come back on the next start; trend
  pen sets persist as named Groups in the trend window.

## Running the plant

Run / Freeze / Single step on the toolbar (F5 / F6 / F7), speed factor up
to 20x from the Simulation menu. Snapshots (Ctrl+S / Ctrl+O) capture the
plant, every controller's mode, setpoint and reset, the ESD latches and
the loop mode, bit for bit. Malfunctions and local field operations live
in their panels under View.
