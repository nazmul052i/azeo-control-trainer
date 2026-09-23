# Process History workspace

Process History records the parameters collected by Operator Station and brings
trends, observed events and training comparisons into one workspace. Its controls
use the shared Control Designer blue theme. This guide supplements the user manual
with the historian workspace introduced after manual edition 1.

## Open and arrange a chart

Right-click a numeric tag on the process display and choose **Add to Historian**.
For several tags, hold **Ctrl** and click each PVM or numeric Data Link. Blue
outlines identify the selection. Right-click a selected tag and choose
**Add to Historian (N tags)** to open them together. Ctrl-click again to remove
a tag; **Esc**, a plain click, or leaving the display clears the selection.
Ctrl-click never operates equipment or opens a faceplate.

A PVM contributes its PV or primary numeric value. Its **Other values** submenu
offers individual SP/OUT/readback values and **All displayed values** when present.
A numeric Data Link contributes the exact bound point it displays.

The historian opens in a separate, resizable window. The process display keeps
its width, regardless of **Dock operating tools**. Further additions reuse the
open chart and preserve its review interval. Duplicate tags are added only once;
adding a hidden tag shows it again. Select up to ten tags at a time. If adding
them would exceed the open chart's ten-pen capacity, another window opens with
the complete selection and the existing chart remains available.

You can also open **Trends** in Operator Station or use a faceplate's History
action. These reuse the open historian window. A PID module initially shows PV, SP and
OUT; its transmitter and valve remain available through **Add Pen**. Other module
types open their collected parameters. A chart supports ten pens.

- Select a module or search for a point with **Add Pen**.
- Use the checkboxes under **Pens and values** to show or hide traces. The row
  menu removes or reorders pens. Colors stay attached to their points.
- PV and SP share their engineering scale; SP uses a dashed line. A different
  output unit uses the right axis. More than two engineering units automatically
  select **Compare %**, which uses each point's configured span.
- Drag the divider to give the chart or tables more space. **Statistics** shows
  descriptions and interval minimum, maximum and average.
- Use **Save group** for a named favorite. Groups include pens, visibility,
  colors, comparison mode and window selection. The workspace also remembers
  its size, splitter, statistics visibility and clock display.
- In a narrow historian window, extra commands move into **Tools** and **More**.
  The table scrolls horizontally to preserve readable point names. Closing the
  window releases its timers; station history collection continues.

## Navigate and measure

**Left-drag** inside the plot to draw a zoom rectangle; release to review its
time and value ranges. **Middle-drag** pans, and the **mouse wheel** zooms around
the pointer. Both value axes follow the gesture. **Double-click** fits the
recorded data. **Right-click > Zoom > Zoom back** restores the previous time
and value ranges. Navigation freezes the view while station collection continues;
use **Return to Live** to follow new samples again.

Drag an A/B marker directly to move that cursor without zooming. Tag selection
and Ctrl-selection on the operator display retain their existing behavior.
Archive loads begin after a rectangle or pan finishes, so an older query cannot
replace the view during the gesture.

Right-click inside the trend, or focus the chart and press **Shift+F10**, to open
the historian menu. It replaces the plotting library's configuration menu on
both axes. Menu commands use the time where you clicked:

| Menu | Available work |
| --- | --- |
| Live / Freeze | Resume following or pause the view while collection continues |
| Center / Time range / Zoom | Center on the clicked time, choose interval presets or archive dates, move to adjacent intervals, zoom here, fit data or zoom between A/B |
| Cursors and measurements | Place A/B at the clicked time, show measurements, copy timestamped values with quality, show statistics |
| Selected pen / Pens | Show, hide or isolate traces; set color, solid/dashed style and width; copy a point path; add or remove pens |
| Value scales | Engineering values or configured-span comparison, automatic or configured ranges, explicit primary/secondary limits |
| Chart appearance | Grid, event markers and elapsed/local time labels |
| Review and export actions | Bookmark the clicked time, review events, compare responses, save a group or export history/report/A-B data |

Right-click close to a trace to expose that pen directly. Pen changes update the
value table and persist with module charts and saved groups. Appearance and axis
limit commands affect the current chart; they do not edit controller parameters.
The context menu only includes connected workspace services. Recorded comparisons
offer their comparison report and omit live collection and archive browsing.

**Live** follows the newest samples. **Freeze**, dragging the plot, or reviewing
an older interval stops following without stopping station collection. **Return
to Live** loads the recent archive and resumes following. Window presets extend
from minutes to seven days; **Archive** selects an explicit local date range.
**Previous**, **Next**, zoom and **Fit** provide additional navigation.

The cursor table shows the nearest recorded sample only when it is sufficiently
close to the cursor. Its tooltip includes quality and the actual sample time.
**Current** is the latest station sample, independent of the interval being
reviewed. **Age** is time since collection, not a field device's acquisition age.
The station's controller-freshness indication remains the place to check scan
progress and controller stalls.

Enable **A/B cursors** and drag the two markers. The table shows A, B, change in
value and change per second. Differences require Good values at both markers.
Statistics use finite Good samples only. Uncertain samples remain visible with
their quality; Bad values produce gaps and never become zero or the last Good
value. Run changes also break the trace.

Long archive intervals use extrema-preserving envelopes for plotting. The status
identifies this mode. Interval statistics come from raw samples; while a different
range is loading, measurements remain unavailable rather than averaging the
envelope. Zoom in for cursor measurements and response comparisons. Exports
retain raw samples regardless of the plotted envelope.

## Persistent recording and retention

The normal Operator Station launcher creates a SQLite archive under:

```text
<application data directory>/history/<project-and-console identity>/history.sqlite
```

Project path and console identity scope the archive. It survives application
restart and does not alter shipped display or control-module files. Collection
defaults to one sample per second while the station is running. Closing a trend
view does not stop station recording. Closing the station does.

**Archive** also provides retention settings. Defaults are **one day and 1024 MB**;
the supported settings are 1–365 days and 64–16384 MB. Cleanup expires older
records periodically, using either limit. The storage budget is a soft limit:
the SQLite file, journal and free pages can temporarily exceed it. Reducing a
limit removes older data during the next cleanup. Export evidence to preserve it
beyond retention.

Database reads, writes and full-data exports run in a background worker. Errors
appear in the history status and application log; live memory collection remains
available if archiving fails. Custom callers that do not supply an archive path
use memory history, clearly identified in the status. This is a local station
archive, not a redundant enterprise historian.

Recorded rows contain elapsed archive time, UTC wall time, simulation time when
the provider exposes it, quality and run identity. Switch chart labels between
elapsed and local time. A simulation reset starts a new run; pausing the simulator
does not redefine the archive's wall-time axis. Metadata describes the current
point configuration. For released repository module points, each sample also
retains its recorded address, units, configuration revision and release. Open
**Point configuration** to inspect those recorded contexts and the stable point
identity. A rename retains the same identity; a different object reusing the old
address does not inherit it. Legacy address-based records remain separately
available without invented release provenance. See user manual Section 12.7
and the [configuration identity guide](CONFIGURATION_DATABASE.md#follow-recorded-points-through-a-rename).

## Review events with the process

The **Events** tab and chart markers share the same interval. Filter by category
and select an event to focus on its surrounding response. Available producers
include accepted station commands, alarm-registry transitions, observed mode
changes, training actions, Workbench actions, clock resets and instructor notes.
Workbench history recording does not require command replay to be enabled.

Use **Bookmark / note** to attach an observation at the cursor, or at the latest
time when no cursor has been selected. The current table shows up to 500 events;
the plot limits markers to 120 to remain readable. Full interval exports include
all retained events.

Event times represent application observations. They are not controller scan
ordering or a safety sequence-of-events recorder. Actions performed outside the
connected station/services are only visible when an existing observed source
reports them. No separate plant dependency is introduced into control code.

## Compare process responses

1. Select the loop and a historical interval, optionally bounded by A/B.
2. Open **Compare runs**. Use that interval as the baseline, then select another
   interval and use it as the trial. Alternatively select saved training attempts
   from the same loop. Loop Diagnostics can also send its measured windows here.
3. Choose alignment at selection start, first recorded setpoint change, or fault
   introduction. Missing alignment events are reported explicitly.
4. Set an engineering-unit tolerance. Review PV, SP and OUT overlays, with dashed
   trial traces, alongside observed duration, accumulated error, overshoot,
   settling time and Good/total sample counts.
5. Add instructor observations and export the comparison.

Comparisons require matching loops and engineering units. Changing the loop
clears the previous comparison. Use one simulation run with advancing timestamps.
Missing samples are excluded from arithmetic; a changing setpoint or insufficient
settling observation can make metrics unavailable. These measurements support
instructor review and do not assign an automatic trainee grade.

## Export evidence

**Export** offers the visible interval, A/B interval or all retained history:

| Format | Contents |
| --- | --- |
| HTML report | Chart with pen legend, raw interval statistics, events and notes, plus data files |
| Raw CSV bundle | Samples CSV, events CSV and metadata JSON |
| Chart PNG | Selected interval with a pen legend |
| Comparison export | Aligned chart, measured results and notes, plus aligned CSV and JSON |

Sample CSV includes point identity, units, quality, UTC time, simulation time and
run identity. Bad values remain blank with their quality. All-retained chart
exports query that entire interval, rather than stretching the visible chart.

## Verification

`tests/test_history_workspace.py` exercises cursor work, quality, persistence,
clock resets, envelope extrema and raw statistics, asynchronous selection,
events, A/B values, retention, complete-history exports and response comparison.
Related coverage lives in `test_operator_trending.py`, `test_operator_workspace.py`,
`test_engineering_training.py` and `test_simulation_workbench.py`.

`tools/verify_history_workspace.py` starts the real station against a disposable
project copy, records two short training attempts, captures the workspace and
comparison, exports evidence, and verifies archive reopening. Add `--review` to
leave that disposable station open. Verification does not save course displays.

A controlled ten-pen, 3,600-sample-per-pen benchmark reduced the cursor table
handler from about 164 ms to 0.8 ms (best of three batches). This measures cursor
table work, not end-to-end frame rate or cold application startup.
