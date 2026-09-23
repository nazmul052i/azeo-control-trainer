/** Maintain the ACT-FND-01 teaching deck and its source-linked speaker notes.
 * Uses the installed PowerPoint document interface when the optional Artifact
 * Tool runtime is unavailable. Never opens a trainer or changes a course project.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TMP = path.join(ROOT, 'tmp', 'training_slides');
const OUTPUT = path.join(ROOT, 'output', 'presentations');
const workbook = await fs.readFile(path.join(ROOT, 'docs/training/LEARNER_WORKBOOK.md'), 'utf8');
const guide = await fs.readFile(path.join(ROOT, 'docs/training/INSTRUCTOR_GUIDE.md'), 'utf8');
const manifest = JSON.parse(await fs.readFile(path.join(ROOT, 'docs/training/course_manifest.json'), 'utf8'));
const slides = [];
let day = 0;
let session = 'Introduction';

function section(text, prefix) {
  const lines = text.split('\n');
  const start = lines.findIndex(line => line.startsWith(prefix));
  if (start < 0) throw new Error(`Missing source heading ${prefix}`);
  const depth = lines[start].match(/^#+/)[0].length;
  let end = start + 1;
  while (end < lines.length && !new RegExp(`^#{1,${depth}} `).test(lines[end])) end++;
  return lines.slice(start, end).join('\n').replace(/<!--.*?-->/g, '').trim();
}
function plain(text) {
  return text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*`#]/g, '')
    .replace(/\n{3,}/g, '\n\n').trim();
}
function add(type, title, data = {}, notes = '', source = '') {
  const refs = source || `docs/training/LEARNER_WORKBOOK.md (${session}); docs/training/INSTRUCTOR_GUIDE.md (Day ${day || 'course setup'})`;
  slides.push({ type, title, day, session, ...data,
    notes: `${notes}\n\nTeaching context: ${session}. Use the assigned isolated project and the qualified exercise card for live interventions.\n\nSources: ${refs}\nCourse ACT-FND-01, edition ${manifest.edition}. Application authoring baseline ${manifest.application_baseline}.`.trim() });
}
const text = (title, lines, notes = '') => add('text', title, { lines }, notes);
const compare = (title, leftTitle, left, rightTitle, right, notes = '') =>
  add('compare', title, { leftTitle, left, rightTitle, right }, notes);
const table = (title, columns, rows, note = '', notes = '') =>
  add('table', title, { columns, rows, note }, notes);
const picture = (title, file, caption, notes = '', side = null) =>
  add('image', title, { image: `docs/images/user_manual/${file}`, caption, side },
      `${notes}\n\nImage source: docs/images/user_manual/${file}. Product reference capture. Its displayed process numbers describe the captured example, not this class's qualified starting condition.`);
function startDay(number, title, agenda) {
  day = number; session = `Day ${day}`;
  add('section', `Day ${day}`, { subtitle: title, lines: agenda },
      `Eight contact hours. Breaks and lunch are additional. Review the previous day's cleanup and remediation before live work. ${agenda.join(' ')}`);
}
function startSession(id, title, outcomes) {
  session = id;
  const item = manifest.sessions.find(s => s.id === id);
  add('session', `${id}  ${title}`, { lines: outcomes,
    timing: `${item.teaching_minutes} min teaching / ${item.labs.map(l => `${l.id} ${l.minutes} min`).join(' / ')} / ${item.review_minutes} min review` },
    `Session outcomes: ${outcomes.join(' ')}\n\nAllocate the published time across concept slides, demonstrations and practical work. Detailed procedures and qualifying conditions follow in the lab slides and notes.`);
}
function lab(id, title, setup, steps, evidence, pass, cleanup, prompt) {
  const labSource = plain(section(workbook, `### ${id} - `));
  const duration = manifest.sessions.flatMap(s => s.labs).find(l => l.id === id)?.minutes;
  add('lab', `${id}  ${title}`, { setup, lines: steps, duration },
      `Instructor preparation: ${setup}\n\nFULL LEARNER PROCEDURE\n${labSource}\n\nFacilitation: ask for the learner's prediction before the first intervention. Record assistance and distinguish a software problem from a control misunderstanding.`);
  add('debrief', `${id}  Evidence and debrief`, { evidence, pass, cleanup, prompt },
      `Evidence required: ${evidence}\nAcceptance: ${pass}\nRestoration: ${cleanup}\nDiscussion: ${prompt}\n\nReview against the complete learner procedure and the instructor portfolio rubric. Incomplete live qualification permits analysis only. Record any hands-on outcome still outstanding.\n\n${labSource}`);
}
function quiz(first, last, label) {
  const questions = [];
  const answers = [];
  for (let n = first; n <= last; n++) {
    const id = `Q${String(n).padStart(2, '0')}`;
    const match = workbook.match(new RegExp(`(?:^|\\n)${id}\\. ([\\s\\S]*?)(?=\\n\\n|$)`));
    if (!match) throw new Error(`Missing ${id}`);
    questions.push(`${id}  ${plain(match[1]).replace(/\n/g, ' ')}`);
    const answer = guide.split('\n').find(line => line.startsWith(`| ${id} |`));
    answers.push(answer?.split('|')[2]?.trim() || '');
  }
  add('questions', label, { lines: questions },
      `Give learners time to answer independently before discussion. Each question is worth one point under the course rubric.\n\nANSWER KEY\n${answers.map((a, i) => `Q${String(first + i).padStart(2, '0')}: ${a}`).join('\n\n')}`);
}

add('cover', 'Process-Control\nEngineering Fundamentals', {
  subtitle: 'Five-day instructor presentation', lines: ['ACT-FND-01', '40 contact hours', 'Edition 1.0']
}, 'Welcome learners. This deck accompanies the 26-page learner workbook and 24-page instructor guide. The course develops introductory simulator competence. Explain that the class works in isolated copies and uses qualified live-lab conditions. Speaker notes contain instructor answers and detailed lab procedures.');
text('Course outcomes', [
  'Trace a measurement through acquisition, control and operator presentation.',
  'Explain modes and constraints using live values, quality and history.',
  'Build and verify a small module and reusable graphic.',
  'Recover the agreed state and hand over a reviewed engineering change.'
], 'Ask each learner about their current role and experience with P&IDs, controllers and process trends. Relate the four outcomes to evidence they will submit. The course assesses explanations and verified results rather than speed of clicking.');
table('Five-day learning route', ['Day', 'First session', 'Second session'], [
  ['1', 'C01  Orientation and signals', 'C02  Function-block engineering'],
  ['2', 'C03  Modes and historian', 'C04  Response and tuning'],
  ['3', 'C05  Cascade and interlocks', 'C06  Sequences and alarms'],
  ['4', 'C07  Diagnosis and sessions', 'C08  Graphics and PVMs'],
  ['5', 'C09  Checkout and release', 'C10  Integrated capstone']
], 'Each session lasts four contact hours. Breaks and lunch are additional.', 'Explain the progression and the lab IDs. The same content can run as ten half-day sessions. Reserve evidence review and cleanup time. Avoid recovering lost teaching time by skipping restoration.');
compare('Classroom scope', 'Assigned training environment', [
  'One identified project and runtime', 'Qualified starting snapshot and allowed changes', 'Instructor-approved recovery procedure'
], 'Competence demonstrated', [
  'Introductory simulator engineering', 'Evidence-based diagnosis and verification', 'A reviewed change with traceable results'
], 'This pilot requires instructor qualification before live exercises. Real-plant operation, vendor certification, SIS approval and burner commissioning are outside the course. The Instructor/Trainee presentation selector does not grant service permissions. Explain these limits once clearly rather than repeating generic warnings on every slide.');
text('The engineering evidence record', [
  'Question and prediction before intervention',
  'Project, runtime, tag path, units, quality and actual mode',
  'Action, observed result and competing explanation',
  'Restoration proof and an evidence file another engineer can inspect'
], 'Introduce W1 through W4 in the learner workbook. A screenshot can illustrate a result, but source identity, raw data, change receipts and cleanup observations establish what happened. Keep incorrect predictions and explain what changed the conclusion.');

startDay(1, 'Orientation and function-block engineering', ['C01  Plant orientation and signals', 'C02  Function-block engineering']);
startSession('C01', 'Plant orientation and signals', ['Locate the source and consumer of a signal.', 'Separate numeric value from quality.', 'Compare the same loop across the applications.']);
table('Five applications, distinct responsibilities', ['Application', 'Responsibility'], [
  ['Explorer', 'Project navigation, configuration and application entry'],
  ['Control Designer', 'Function-block engineering and controller execution'],
  ['Graphics Designer', 'Display and faceplate authoring'],
  ['Operator Station', 'Published displays, operating actions and history'],
  ['Simulation Workbench', 'Process state, input simulation and shared clock']
], '', 'Ask what would change if the same controller read from a different I/O provider. The control half communicates through SharedDataStore and the configured I/O boundary. Graphics do not implement the PID algorithm.');
picture('Explorer and project identity', 'explorer.png', 'Reference interface. Confirm the allocated project and controller before saving or running.', 'Demonstrate project identity, controller/provider navigation and application entry on the current class build. Explain that opening a similarly named project in another session does not share its live tag store.');
table('Measurements and demands', ['Signal', 'Meaning', 'Ownership'], [
  ['AI', 'Analog measurement', 'Acquired input'], ['DI', 'Discrete measurement or status', 'Acquired input'],
  ['AO', 'Analog demand', 'Controller output'], ['DO', 'Discrete demand', 'Controller output']
], 'A demand can differ from actual position or process response.', 'Examples include flow measurement, valve proof, valve demand and a start command. Plant-side input simulation supports AI/DI. AO/DO remain controller-owned in that surface. Ask which independent feedback would establish that a valve moved.');
compare('Value and quality', 'Numeric value', ['Magnitude with an engineering unit', 'May remain plausible during an input fault', 'Needs a known source and time context'], 'Quality', ['Trust or status associated with the value', 'Can be Bad while the number looks normal', 'Can affect consumer status or actual mode'], 'Hold a finite input number constant and compare Good versus Bad quality during the qualified demonstration. Do not teach that Bad must display as zero. Explain any observed consumer behavior using the installed block contract.');
table('Three input and display test layers', ['Layer', 'What changes', 'Evidence to inspect'], [
  ['Block simulation', 'Input inside a controller block', 'Sim state and consuming block'],
  ['Virtual I/O simulation', 'Acquisition before the controller', 'Provider input and downstream path'],
  ['Graphics TEST', 'Isolated rendering overlay', 'Appearance and blocked writes']
], 'Provider process disturbances use a separate model-supported mechanism.', 'A signal substitution does not establish mechanical damage. The focused simulator Disturbances tab exposes model-supported interventions through the provider catalog. Keep original settings so restoration is precise.');
table('Linear scaling worked example', ['Input fraction', 'Calculation', 'Result'], [
  ['0%', '0 + 0.00 x 300', '0 m3/h'], ['25%', '0 + 0.25 x 300', '75 m3/h'],
  ['50%', '0 + 0.50 x 300', '150 m3/h'], ['100%', '0 + 1.00 x 300', '300 m3/h']
], 'Arithmetic example only. These values do not define approved plant operating points.', 'General relation: EU = low + fraction x (high - low). Ask learners to distinguish percentage points from EU. Ten percentage points of this span equals 30 m3/h. Numeric scaling does not establish Good quality.');
picture('Published plant overview', 'operator-overview.png', 'The overview provides navigation and process context. Detailed diagnosis begins at the bound loop.', 'Show the live overview and navigate to the qualified U100 loop. Identify the display hierarchy and demonstrate returning to the previous display. The captured numbers are illustrative of the reference session.');
lab('L01', 'Orient, locate and compare', 'Assigned APVC copy. Provider Running. Inspection only.', [
  'Confirm project, controller and provider identity.', 'Locate FIC-1001 and record PV, SP, OUT, quality and modes.',
  'Pin its detached faceplate and compare a second loop.', 'Open the associated engineering module, then return to the overview.'
], 'W1, navigation route and two-loop observation table.', 'Correct identity and complete observations with units and modes.', 'Close extra windows and preserve the assigned runtime.', 'Which application owns the algorithm and which displays its result?');
lab('L02', 'Trace a signal and its quality', 'Signed input-test card with exact layer, finite value and recovery.', [
  'Trace FT-1001 acquisition to the AI and PID.', 'Compare the original input with an approved Good substitution.',
  'At the same number, apply Bad quality and observe consumers.', 'Restore the prior settings and prove the original source resumed.'
], 'Original/Good/Bad table, source path and scaling calculation.', 'Correct layer and quality interpretation with recovery evidence.', 'Remove only this exercise intervention and verify live quality/mode.', 'What would distinguish a plausible Bad value from a valid measurement?');
startSession('C02', 'Function-block engineering', ['Explain an existing measured loop.', 'Create a module without competing plant outputs.', 'Distinguish saved source from running configuration.']);
picture('The function-block workspace', 'control-designer.png', 'Reference example: forward values and downstream back-calculation appear on separate connections.', 'Point out the palette, properties, compile/download controls and execution view. This reference screenshot uses a different teaching module. The live L03 reference is the assigned copy of FIC-1001.');
table('FIC-1001 forward signal path', ['Block', 'Input or function', 'Next consumer'], [
  ['FT-1001  AI', 'Flow acquisition', 'AI OUT feeds PID IN'],
  ['FIC-1001  PID', 'Regulation', 'PID OUT feeds scaler IN'],
  ['OUT_SCALE', 'Configured range conversion', 'Scaler OUT feeds AO CAS_IN'],
  ['FCV-1001  AO', 'Final-element demand', 'Configured field output']
], 'Read the wires and ranges in the actual learner copy.', 'This path comes from projects/AzeoPlantVirtualController/control/FIC-1001.json. The AI range is 0-300 m3/h at the authoring baseline. Forward numeric compatibility alone cannot establish downstream acceptance.');
table('Downstream acceptance and BKCAL', ['Return step', 'Connection'], [
  ['Final element to scaler', 'FCV-1001.BKCAL_OUT to OUT_SCALE.BKCAL_IN'],
  ['Scaler to PID', 'OUT_SCALE.BKCAL_OUT to FIC-1001.BKCAL_IN'],
  ['Engineering check', 'Verify return units and downstream status']
], '', 'Have learners trace this independently. The output scaler is currently 0-100 to 0-100 in the reference module. Its unit-slope forward conversion does not make the acceptance return path irrelevant.');
compare('Terminals and configuration parameters', 'Terminals', ['Carry values and associated status', 'Connect block outputs and inputs', 'Resolve the installed block contract'], 'Configuration', ['Defines ranges, action, limits and tuning', 'Uses MODULE/BLOCK/CONFIG/name', 'Retains exact parameter case'], 'Examples include FIC-1001/FIC-1001/CONFIG/GAIN and CONFIG/out_lo. Do not guess that an alarm limit is a terminal because its caption looks like one. Use a real binding resolver or catalog.');
table('Engineering state changes', ['Action', 'What it establishes'], [
  ['Save', 'Preserved engineering source'], ['Compile / F7', 'Structural and execution diagnostics'],
  ['Download', 'Selected runtime module inventory'], ['Online view', 'Attachment to running values'],
  ['Re-Download row action', 'Changed source applied to an already online module']
], 'Review the complete Download selection. Deselected running modules go offline.', 'The global Go Off Line command affects open online modules. Use Controller Status per-row actions for scoped work. A saved edit or an open editor tab does not prove new logic is scanning.');
text('A controlled module test', [
  'A unique practice module has no mapped plant AO or DO.',
  'Known input points test the intended scale conversion.',
  'A Bad-quality case checks honest propagation.',
  'Before/after inventory confirms unrelated modules remain active.'
], 'Introduce COURSE_AI_<learner>, AI1 DIRECT 0-100 and SCALE1 0-100 to 0-300. The new input tag is a fixture allocated for the class. Confirm the test method in the qualification card before class.');
lab('L03', 'Explain the existing loop', 'FIC-1001 open in the assigned copy. Inspection only.', [
  'Trace the forward demand path and BKCAL return.', 'Record ranges, action, form, tuning and limits.',
  'Compare configured modes with live actual/target modes.', 'Write a one-page control narrative and have a peer trace it.'
], 'Annotated loop sketch and narrative with parameter paths.', 'Correct sources, consumers and ranges with live/configured state separated.', 'Leave engineering source unchanged.', 'What does the static diagram leave unknown about actual operation?');
lab('L04', 'Build a measurement module', 'COURSE_AI_<learner> with AI1 and SCALE1. No plant outputs.', [
  'Create AI DIRECT 0-100 and scaler 0-100 to 0-300.', 'Save and compile, then use the qualified scoped test method.',
  'Test Good inputs 0, 25, 50 and 100, followed by Bad quality.', 'Compare predicted outputs and restore the test state.'
], 'Module report, four-point result and runtime inventory.', '0/75/150/300 EU for valid points with correct quality behavior.', 'Restore simulation and take only the practice module offline if directed.', 'Why can a numeric wire compile and still be wrong?');
quiz(1, 2, 'Day 1 knowledge check: source and scaling');
quiz(3, 4, 'Day 1 knowledge check: quality and download');

startDay(2, 'Modes, history and loop response', ['C03  Modes and historian', 'C04  Response measurement and tuning']);
startSession('C03', 'Modes and historian', ['Explain achieved and requested modes.', 'Check SP and OUT markers on their own scales.', 'Use multi-tag history without losing quality or time context.']);
table('Actual, target and normal mode', ['Mode field', 'Meaning', 'Question it answers'], [
  ['Actual', 'Achieved live state', 'What is the block doing now?'],
  ['Target', 'Requested state', 'What has been requested?'],
  ['Normal', 'Configured intended mode', 'What is the intended operating mode?']
], 'FIC-1001 saved setup mode is MAN and normal mode is CAS at the source baseline.', 'Neither source setting establishes the current runtime state. Ask learners to explain a target/actual mismatch through tracking, quality, downstream acceptance or initialization status.');
table('Sources of SP and output demand', ['Actual mode', 'Typical operating source', 'Checks before interpretation'], [
  ['MAN', 'Operator output demand', 'Tracking, output bounds and acceptance'],
  ['AUTO', 'Regulation to local setpoint', 'PV quality, action and limits'],
  ['CAS', 'Supported upstream setpoint path', 'Upstream validity and downstream conditions']
], 'Read the installed block contract and live status for the selected loop.', 'These are introductory meanings. A visible mode choice may be unavailable under current conditions. Do not repeat writes or bypass conditions to make actual mode match the target.');
compare('Mode-transfer evidence', 'Before the request', ['Identify the target and permitted sequence', 'Record PV/SP/OUT and actual mode', 'Inspect limits, tracking and BKCAL'], 'After the request', ['Confirm the achieved actual mode', 'Measure the observed output change', 'Compare with the qualified transfer bound'], 'Use the signed L05 card. Establish output-change acceptance from instructor rehearsals. A controlled refusal can demonstrate status reasoning, but the course also requires a successfully demonstrated authorized transfer.');
table('Output-marker position', ['OUT value', 'Range', 'Normalized position'], [
  ['20%', '20-100%', '(20-20)/(100-20) = 0.00'],
  ['60%', '20-100%', '(60-20)/(100-20) = 0.50'],
  ['100%', '20-100%', '(100-20)/(100-20) = 1.00']
], 'Position fraction = (value - low) / (high - low).', 'These are arithmetic positions, not plant setpoints. The output triangle should track the output bar on this range. Dividing by high alone gives the wrong answer for a nonzero low limit.');
text('SP-marker interpretation', [
  'The SP marker represents setpoint on the vertical engineering range.',
  'SP 150 on a 0-300 range lies halfway up the scale.',
  'A changing PV with fixed SP should leave the SP marker fixed.',
  'Scale-end clipping does not establish that an out-of-range value is valid.'
], 'Relate this to the previously reported UI defect. Check the numerical value, source path, range endpoints and geometry together. SP and OUT are different signals and need not move together.');
picture('Display-defect exercise', 'loop-faceplate.png', 'Archived defect example. OUT reads 68.9%, while the lower triangle remains near zero.',
  'This archived screenshot intentionally shows the earlier marker defect. Ask learners to identify the inconsistency and calculate the expected normalized position on the displayed 0-100 scale. Expected answer: approximately 0.689 of the usable range. The screenshot does not establish that its MAN indication is wrong because no contrary live mode evidence is supplied. Do not present this capture as the current corrected faceplate.',
  ['Which visible value conflicts with the marker?', 'What evidence would test the mode indication?', 'Which tag, scale and geometry belong in a defect report?']);
text('Historian selection workflow', [
  'Right-click a numeric PVM or data link and choose Add to Historian.',
  'Add SP and OUT through the available other-values choices.',
  'Ctrl-select related objects for a detached multi-tag view.',
  'A chart supports up to ten pens before additional windows.'
], 'Demonstrate from the current operator graphic rather than an unrelated chart tool. Require full tag paths and units so each series has an unambiguous meaning. A local numeric caption may not reveal the source identity.');
table('Historian mouse interactions', ['Gesture', 'Result'], [
  ['Left-drag rectangle', 'Zoom the selected region'], ['Middle-drag', 'Pan the view'],
  ['Mouse wheel', 'Zoom near the pointer'], ['Double-click', 'Fit the view'],
  ['Right-click, Zoom, Zoom Back', 'Return to the preceding zoom view']
], 'A/B cursor manipulation remains distinct from rectangle zoom.', 'Use the trainer historian context menu. Practice recovery from an overly narrow view. The earlier default pyqtgraph context menu is not the intended operator workflow.');
compare('Historical review and process time', 'Historian review', ['Freeze or inspect a past interval', 'Collection continues', 'A/B measures observed differences'], 'Coordinated process control', ['Workbench or session Pause/Resume', 'Provider and controller share the clock', 'Speed affects pacing of the exercise'], 'Always identify wall time versus simulation time. A paused plot is not evidence that the process stopped. Do not compare a fast simulation trial with a baseline measured under different sampling/pacing assumptions.');
text('Evidence in a trend', [
  'Related PV, SP and OUT expose sequence and constraints.',
  'Quality gaps require explicit treatment in the interpretation.',
  'Raw exports preserve detail that a long-range plot may reduce.',
  'Same-poll observations leave fine causal order unresolved.'
], 'Downsampled peak envelopes help detect excursions but cannot replace raw samples for detailed transient measurement. Ask what independent evidence would distinguish a setpoint step from an input fault.');
lab('L05', 'Explain a mode transfer', 'Signed mode card with entry state, sequence, bounds and recovery.', [
  'Record PV/SP/OUT, actual/target modes and downstream status.', 'Predict SP and OUT ownership after each allowed transfer.',
  'Perform the sequence and confirm actual mode and output change.', 'Check SP/OUT markers and restore the approved condition.'
], 'Transfer table with predictions, status and marker calculations.', 'Confirmed authorized transfer and correct actual-mode interpretation.', 'Restore values, modes and temporary simulations.', 'When is a target/actual mismatch meaningful rather than a display defect?');
lab('L06', 'Build a historian investigation', 'Qualified loop with no new process intervention.', [
  'Open related PV/SP/OUT pens with correct units.', 'Practice rectangle zoom, pan, Fit and Zoom Back.',
  'Use A/B and event context to interpret a selected change.', 'Export raw data and return to a useful live view.'
], 'Trend, raw export, A/B result and supported interpretation.', 'Correct sources, time basis and quality treatment.', 'Preserve the process state.', 'What conclusion would require more precise sampling than this record?');
startSession('C04', 'Response measurement and tuning', ['Run a valid baseline experiment.', 'Compare one qualified tuning change fairly.', 'Explain metric validity before recommending a change.']);
table('PID terms and interpretation', ['Term', 'Introductory meaning', 'Implementation detail to record'], [
  ['Proportional', 'Responds to current error', 'Gain and controller action'],
  ['Integral', 'Accumulates error over time', 'Reset convention and time unit'],
  ['Derivative', 'Responds to change', 'Selected form and filtering/structure']
], 'A tuning number from another implementation may use a different convention.', 'FIC-1001 source uses standard form with its configured structure and action. RESET and RATE are time values in seconds in the teaching source. Do not relabel reset time as repeats per minute.');
compare('Control behavior and constraints', 'Feedback response', ['Error relates SP to PV', 'Action must match the process sign', 'Tuning affects response characteristics'], 'Conditions that can dominate', ['Output saturation or tracking', 'Downstream mode and acceptance', 'Bad or substituted measurement'], 'Establish the signal path and actual mode before tuning. A gain change cannot repair an incorrect source or a downstream refusal. Use the qualified trial instead of searching for sustained oscillation in the plant model.');
add('chart', 'Illustrative step-response comparison', {
  chartKind: 'line', categories: ['0','5','10','15','20','25','30','35','40','45','50','55','60'],
  series: [
    { name: 'SP', values: [50,50,60,60,60,60,60,60,60,60,60,60,60] },
    { name: 'Response A', values: [50,50,50,52,55,58,61,62,61,60.4,60.1,60,60] },
    { name: 'Response B', values: [50,50,50,54,60,65,64,61,58.5,59,60.8,60.4,60] }
  ], xTitle: 'Time (s)', yTitle: 'Illustrative PV / SP (EU)',
  note: 'Synthetic teaching data. These curves are not APVC test results.'
}, 'Ask learners to compare rise, overshoot and oscillation before seeing any summary metric. Both responses use the same illustrative reference, but a faster first movement does not establish the better acceptable response. The actual course trial uses QF1 measured acceptance bounds and raw records.');
table('A comparable baseline and trial', ['Keep the same', 'Record explicitly'], [
  ['Starting condition', 'Compatible snapshot, modes and drift window'],
  ['Intervention', 'One approved SP step'], ['Measurement', 'Start delay, duration and tolerance'],
  ['Execution', 'Clock speed, quality and output constraints'],
  ['Changed factor', 'Exactly one approved tuning parameter']
], '', 'The instructor qualifies the baseline and the selected one-parameter trial before class. Restore operating/tuning/input scope, not merely process state. Reject comparisons with unexplained differences.');
picture('Loop diagnosis and response measurement', 'loop-diagnosis.png', 'The diagnosis surface combines loop status, history and baseline/trial measurements.', 'Explain the correct loop selection, optional feedback, tolerance and Begin baseline / Begin trial / End measurement controls. The reference capture has no completed measurements. Its process values are not the L07 baseline.');
table('Response metrics', ['Metric', 'Unit', 'Interpretation'], [
  ['Accumulated absolute error', 'EU*s', 'Magnitude of error accumulated over the window'],
  ['Overshoot', 'EU', 'Excursion beyond the target under the measurement contract'],
  ['Settling time', 's or unavailable', 'Accepted time inside the selected tolerance']
], 'The application requires five continuous seconds inside tolerance at the end of the window.', 'A QF1 card may require a longer stable period. Missing or Bad samples and a changing SP invalidate single-step metrics. Never substitute zero for an unavailable settling result.');
table('Absolute-error integral example', ['Interval (s)', 'Endpoint errors (EU)', 'Trapezoid area (EU*s)'], [
  ['0-1', '4 and 3', '3.5'], ['1-2', '3 and 2', '2.5'],
  ['2-3', '2 and 1', '1.5'], ['3-4', '1 and 0', '0.5'], ['Total', 'Four seconds', '8.0']
], 'Worked arithmetic. A four-second record cannot prove five seconds of final settling.', 'For each interval use average absolute error times elapsed time. Sum 3.5+2.5+1.5+0.5=8 EU*s. This calculation is independent of any process trial and must not be reported as an observed Loop diagnosis result.');
text('Invalid response measurements', [
  'A second SP change breaks the single-step comparison.',
  'Missing or Bad samples leave an unmeasured interval.',
  'Different entry conditions or start delays bias the comparison.',
  'An unsettled response requires an unavailable settling result.'
], 'Have learners reject an intentionally invalid record and explain the failure before collecting another. The error integral does not bridge a bad interval as if samples were valid. A mathematically plausible score is insufficient without the validity conditions.');
compare('Tuning recommendation', 'Evidence to compare', ['Metric validity and equal trial conditions', 'Error, overshoot and settling', 'Output constraints and oscillation'], 'Defensible decisions', ['Retain the approved original', 'Recommend the trial for review', 'Repeat an inconclusive comparison'], 'A trial can reduce error while exceeding an allowed overshoot or output constraint. Reject it under the card even if one metric improves. Confirm live tuning values and record the restoration decision.');
lab('L07', 'Measure a baseline response', 'Signed card with snapshot, SP step, tolerance, window and recovery.', [
  'Record a prediction and confirm entry stability.', 'Restore the complete condition and open Loop diagnosis.',
  'Apply one step, then start baseline measurement promptly.', 'Inspect validity, export evidence and restore the condition.'
], 'W2 plan, raw trend and valid metric interpretation.', 'Comparable timing, correct units and explicit validity checks.', 'Restore the complete approved starting condition.', 'Which observation would invalidate your response measurement?');
lab('L08', 'Compare one tuning trial', 'Same experiment with one instructor-qualified parameter change.', [
  'Restore the L07 starting condition.', 'Apply the approved parameter and verify the running value.',
  'Repeat the same step and timing using Begin trial.', 'Compare constraints and recommend a decision.'
], 'Baseline/trial comparison and one-factor change log.', 'Fair comparison and a constraint-aware recommendation.', 'Restore original tuning unless the instructor approves retention.', 'Can a smaller error integral accompany an unacceptable response?');
quiz(5, 6, 'Day 2 knowledge check: modes and markers');
quiz(7, 8, 'Day 2 knowledge check: time and validity');

startDay(3, 'Cascade, interlocks and alarm investigation', ['C05  Cascade and interlocks', 'C06  Sequences and alarms']);
startSession('C05', 'Cascade and interlocks', ['Trace demand and acceptance through two loops.', 'Explain permit polarity and timed conditions.', 'Separate reported first-out from display order.']);
table('Cascade roles in the reference plant', ['Role', 'Teaching object', 'Purpose'], [
  ['Primary', 'TIC-3001', 'Temperature regulation supplies a downstream target'],
  ['Secondary', 'FIC-3001', 'Fuel-flow regulation accepts supported cascade demand'],
  ['Final element', 'FCV-3001', 'Configured fuel-flow demand interface']
], 'Read-only analysis of the installed U300 configuration.', 'The workbook does not authorize heater startup, fuel changes or arbitrary temperature steps. Any dynamic cascade demonstration needs its own signed card. A recorded case earns analysis evidence rather than live transfer evidence.');
compare('Cascade demand and acceptance', 'Forward path', ['Primary output requests secondary demand', 'Scaling reconciles percent and EU', 'Secondary actual mode affects acceptance'], 'Return path', ['Downstream BKCAL reports acceptance', 'Return scaling preserves meaning', 'Constraints can propagate upstream'], 'The reference metadata describes FIC-3001 BKCAL returned to TIC-3001 through 0-3000 EU to 0-100% conversion. Inspect the actual scaler and wires. A changing primary OUT cannot prove that the secondary followed.');
text('A cascade limitation investigation', [
  'Both faceplates show actual and target mode.',
  'Related history shows primary OUT and secondary SP/PV/OUT.',
  'Quality, tracking and limits explain demand acceptance.',
  'The return path connects downstream constraints to upstream behavior.'
], 'Use a prequalified case with a known bounded condition or a recorded trace. Require learners to identify the difference between requested demand, achieved secondary SP and process response. Do not retune the primary to compensate for a secondary mode problem.');
compare('Permissives and interlocks', 'Permission to act', ['A permissive grants the required permission', 'DEVCTL INTERLOCK True means permit granted', 'Configuration defines the response'], 'Evidence of a condition', ['Abnormal CND output can assert True', 'OR and NOT can convert abnormal to permit', 'An absent binding leaves the condition unknown'], 'Read the True=permit convention carefully. It prevents the common inversion that lights a no-permit indication on a healthy device. Explain why an unknown condition cannot simply be treated as False.');
table('Interlock truth table', ['CND1', 'CND2', 'OR_ILK', 'NOT_ILK / permit'], [
  ['False', 'False', 'False', 'True'], ['True', 'False', 'True', 'False'],
  ['False', 'True', 'True', 'False'], ['True', 'True', 'True', 'False']
], 'Copied MTR-102 logic example. True on the final input grants permission.', 'Ask learners to fill this table before showing the completed form. The instructor may hide the answer table until debrief. Explain the CND to OR to NOT to DEVCTL scan chain and allow enough scans for settled observations.');
table('Threshold and duration', ['Input case', 'Condition rule', 'Expected condition'], [
  ['Valve position 1', 'CND1: IN1 == 0', 'False'],
  ['Valve position 0', 'CND1: IN1 == 0', 'True'],
  ['Level 10 for 2 s', 'CND2: IN1 < 50 for 4 s', 'Timer incomplete from reset'],
  ['Level 10 for 5 s', 'Same rule', 'True after required duration'],
  ['Level exactly 50', 'Strict less-than threshold', 'False']
], 'The level fixture uses gallons. Observed timing includes scan quantization.', 'These are isolated test inputs from the copied teaching module. They do not imply a dynamic motor/plant fixture. Reset the timer between trials. Observe the block contract for reset and first-out behavior.');
text('First-out and reset evidence', [
  'A controller-reported latch can identify an initiating condition.',
  'The first visible alarm row may reflect polling or sorting.',
  'Clearing the cause may leave a reset-required device state.',
  'Bypassing a condition does not establish a repaired cause.'
], 'MTR-102 DC1 has RESET_REQUIRED true at the source baseline. Inspect BFI latch/reset behavior from the actual block contract or qualified trace. Never fabricate first-out where the controller supplies none.');
lab('L09', 'Trace the cascade', 'TIC-3001 and FIC-3001 read-only. Optional live case needs QF1.', [
  'Pin both loop faceplates and record their states.', 'Trace primary demand to secondary and final element.',
  'Trace BKCAL and its range conversion.', 'Explain a qualified or recorded acceptance limitation.'
], 'Two-way signal map and modes/status table.', 'Correct roles, units and demand-acceptance reasoning.', 'Instructor restores any separately qualified live demonstration.', 'What proves that the secondary accepted the primary demand?');
lab('L10', 'Prove interlock polarity and delay', 'Offline copied MTR-102 fixture disconnected from APVC outputs.', [
  'Locate CND1, CND2, OR, NOT, BFI and DC1.', 'Predict the truth table before applying inputs.',
  'Compare short/long low-level durations and the exact threshold.', 'Inspect first-out/reset and remove the test inputs.'
], 'Truth table, timed observations and reset explanation.', 'Correct permit polarity and threshold/duration reasoning.', 'Clear the fixture, resets and any bypass state.', 'Why is the strict threshold boundary a separate test?');
startSession('C06', 'Sequences and alarms', ['Read conditions and actions behind a sequence state.', 'Distinguish alarm condition from acknowledgment.', 'Investigate a cause and verify recovery.']);
text('Sequence engineering record', [
  'State and entry conditions identify the current step.',
  'Actions have actual consumers and output ownership.',
  'Transitions combine proofs, conditions and timers.',
  'Failure paths include timeout, interruption and reset behavior.'
], 'A state label alone is weak evidence. Ask what sets each condition and what handles each action. Keep the BMS-3001 lesson read-only and use a qualified isolated or recorded execution trace.');
table('Transition test design', ['Test', 'Input condition', 'Evidence required'], [
  ['Successful progression', 'Required proof and time present', 'Correct transition and action'],
  ['Withheld condition', 'One prerequisite absent', 'No unauthorized progression'],
  ['Interrupted progression', 'Approved interruption in the fixture', 'Configured failure/recovery behavior']
], 'The exact state names and timer values come from the installed block contract.', 'BMS-3001 describes purge, pilot, prove, main and release at a high level. Learners must verify the executable state definitions. Do not force live field outputs to create an assessment trace.');
compare('Alarm condition and acknowledgment', 'Condition', ['The configured abnormal state exists', 'Activation may include delay/deadband', 'Recovery depends on the source condition'], 'Acknowledgment', ['The operator records awareness', 'The active condition may remain', 'Evidence still needs a cause and response'], 'Demonstrate acknowledgment while the qualified alarm remains active. Ask learners what changed and what did not. The physical/model cause is independent of whether the alarm row is acknowledged.');
table('Alarm handling choices', ['Action', 'Purpose', 'Required follow-up'], [
  ['Investigate', 'Understand cause and consequence', 'Relevant history and source evidence'],
  ['Acknowledge', 'Record operator awareness', 'Confirm condition status'],
  ['Shelve', 'Temporary presentation management', 'Reason, duration and unshelving/expiry'],
  ['Recover source', 'Remove the actual exercise trigger', 'Verify return and cleanup']
], 'Supported shelving duration: one minute to 24 hours. Use the qualified card duration.', 'Suppression is a separate operation. Neither shelf nor acknowledgment repairs the source. Missing authored guidance is a configuration gap to record. Do not invent a plant operating procedure to fill it.');
picture('Alarm investigation workspace', 'alarm-investigation.png', 'Transitions, related conditions and authored guidance support the investigation.', 'Demonstrate Tools > Alarm investigation and the relevant condition faceplate. Ask what evidence supports any first-out claim. Same-poll observations remain unresolved for finer causal order.');
lab('L11', 'Read a sequence', 'BMS-3001 read-only with current block reference and an isolated or recorded trace.', [
  'Trace the source of each relevant condition and action.', 'Describe two transitions with timers and proofs.',
  'Design success, withheld-proof and interruption tests.', 'Compare the trace and identify unexecuted tests.'
], 'State diagram, transition tables and three test cases.', 'Traceable conditions/actions with failure and recovery behavior.', 'Close the review copy without field writes.', 'What would make a drawn state an unsupported implementation claim?');
lab('L12', 'Investigate one alarm', 'Qualified key, trigger, delay/deadband, allowed response and recovery.', [
  'Record normal state and observe the approved trigger.', 'Acknowledge and inspect whether the condition remains.',
  'Investigate transitions and perform the authorized shelf/unshelve.', 'Remove the source trigger and verify recovery.'
], 'Alarm timeline, cause/response note and cleanup evidence.', 'Correct lifecycle interpretation and reasoned temporary shelving.', 'Restore the trigger and remove this lab shelf.', 'What proof would you need before calling the initiating cause resolved?');
quiz(9, 10, 'Day 3 knowledge check: cascade and permits');
quiz(11, 12, 'Day 3 knowledge check: delay and first-out');

startDay(4, 'Diagnosis and reusable operator graphics', ['C07  Diagnosis and repeatable sessions', 'C08  Graphics and paired faceplates']);
startSession('C07', 'Diagnosis and repeatable sessions', ['Discriminate competing causes using evidence.', 'Restore the source responsible for the symptom.', 'Produce a repeatable attempt and reviewable report.']);
table('A flat PV has several explanations', ['Hypothesis', 'Discriminating observation'], [
  ['Steady process', 'Independent measurements agree with steady conditions'],
  ['Static input substitution', 'Simulation state and unrelated process variation'],
  ['Bad acquisition', 'Source/consumer quality and status'],
  ['Stale display', 'Source progresses while this view does not']
], 'A flat trend alone cannot identify the cause.', 'Have learners propose observations before writing to anything. Good quality on a deliberately frozen substitute does not prove that the measurement reflects the changing process.');
text('A focused diagnostic sequence', [
  'Confirm runtime identity, provider progress and live time.',
  'Compare source input, block value, PID state and final demand.',
  'Test the hypothesis with the smallest allowed observation or action.',
  'Confirm recovery at the source and downstream consumers.'
], 'For each proposed intervention, require target, expectation and recovery. Asking for missing information is acceptable engineering behavior. Do not reward a confident unsupported diagnosis.');
compare('Input substitution and process disturbance', 'Input substitution', ['Changes acquisition or block input', 'Can control value and quality', 'Restores the prior simulation state'], 'Provider process disturbance', ['Uses an installed model-supported fault', 'Has a catalog identity and limits', 'Requires observed process recovery'], 'A simulated input fault does not justify a mechanical-equipment conclusion. A provider disturbance must use its actual catalog ID, value semantics, bounds and duration. Model parameters in the focused UI are read-only reference values.');
picture('Coordinated Simulation Workbench', 'simulation-workbench.png', 'Scope and shared clock controls determine what a checkout action affects.', 'Point out the selected system/module scope, I/O Blocks and snapshots. Focused simulator snapshots default to process state. Use full coordinated scope when a course reset requires operating values, tuning or input profiles.');
table('Training session lifecycle', ['Command', 'Result'], [
  ['Capture starting condition', 'Create a real compatible starting snapshot'],
  ['Save exercise', 'Store definition and observable objectives'],
  ['Start', 'Restore the captured condition and begin recording'],
  ['Restart', 'Save current attempt and begin again from baseline'],
  ['Finish', 'Clear tool-introduced faults and save the report']
], '', 'Finish does not guarantee removal of separate Virtual I/O overrides, block simulations, forces, shelves or model disturbances. Record all interventions independently.');
picture('Exercise definition and objectives', 'training-exercise.png', 'Objectives need observable completion criteria and supporting evidence.', 'Demonstrate choosing the exact PID and AI/DI input, writing an observable objective, capturing a condition and saving the exercise. A snapshot name in the slide is never a substitute for a captured compatible snapshot.');
text('Objective and evidence example', [
  'Objective: identify the named substituted input and its layer.',
  'Evidence: source/consumer quality, simulation state and a full tag path.',
  'Recovery: restore prior settings and prove live acquisition resumes.',
  'Review: instructor records completion with evidence notes.'
], 'Contrast this observable objective with a vague objective such as understand the plant. The current application supplies explicit objective review, not automatic grading under this course rubric.');
picture('Session timeline and review', 'session-timeline.png', 'Recorded samples and events support an attempt review. Report review does not replay the plant.', 'Session samples occur when simulation time advances by at least one second, subject to station polling. Explain that changing software/configuration/provider can prevent an identical snapshot restore. Reopen the actual saved attempt and confirm its identifiers.');
text('The intervention inventory', [
  'Session faults and block/input simulation',
  'Virtual I/O profiles and controller forces',
  'Alarm shelves and provider process disturbances',
  'Tuning, operating values and the final restored condition'
], 'The learner owns cleanup evidence for their interventions. The instructor privately records fault details and original settings. Clear the responsible layer rather than hiding an alarm or substituting Good quality.');
lab('L13', 'Diagnose an unknown input problem', 'One qualified concealed fault. Learner receives symptom, scope and allowed actions.', [
  'Confirm identity, time and operating condition.', 'Write competing hypotheses and discriminating observations.',
  'Trace source, quality, modes and downstream behavior.', 'Explain the correction, apply it if authorized and prove recovery.'
], 'Hypothesis table, before/after evidence and incident note.', 'Correct responsible layer with a supported explanation.', 'Remove the fault at its original layer and verify the agreed state.', 'Which observation ruled out your strongest alternative explanation?');
lab('L14', 'Run a repeatable attempt', 'Qualified loop/input with supported snapshots and recorded identities.', [
  'Write objectives, capture a condition and save the exercise.', 'Start explicitly, introduce the approved fault and clear it.',
  'Finish, review objectives and export HTML plus JSON.', 'Reopen the record and verify a compatible repeat attempt.'
], 'Exercise/session IDs, reviewed objectives and complete exports.', 'Explicit restore and traceable evidence across attempts.', 'Check interventions outside the session tool as well.', 'Why does loading an exercise differ from starting it?');
startSession('C08', 'Graphics and paired faceplates', ['Design around an operator decision.', 'Use valid typed bindings and reusable instances.', 'Verify actions in the assigned Operator Station.']);
text('The operator question', [
  'The display names the process condition an operator needs to judge.',
  'Hierarchy and navigation preserve useful context.',
  'Units, quality, actual mode and abnormal states remain legible.',
  'Each visible action has an implemented, meaningful behavior.'
], 'Ask learners to state the intended decision before choosing symbols. Blue engineering chrome is consistent across apps. Process state still uses semantic theme roles, so normal/alarm/quality cannot be reduced to one cosmetic color.');
picture('Graphics Designer authoring', 'graphics-designer.png', 'Layout, bindings and navigation need verification at the intended station size.', 'Demonstrate the current assigned project and avoid editing packaged course displays. Inspect clipping, readability and property-panel width on the actual workstation. A visually attractive static display is not commissioned behavior.');
picture('Assembly mapping preview', 'assemblies.png', 'Mapped paths and compatible block types determine whether an assembly is usable.', 'Demonstrate Insert > Assemblies with actual compatible targets. Preview mapped bindings before applying. Record unresolved references rather than accepting plausible dummy values.');
text('Geometry and reuse', [
  'Snap, alignment and guides reduce repetitive placement.',
  'Connections attach to visible symbol geometry and ports.',
  'Groups include related symbols, labels and pipes.',
  'Movement and resize tests check that authored relationships persist.'
], 'Show a symbol move and group move during the live demonstration. The course uses the single PvmDisplay document stack. Do not import an archived renderer or a parallel faceplate format.');
table('Binding contract', ['Binding element', 'Engineering check'], [
  ['Primary target', 'Accepted block type and full path'],
  ['PV, SP, OUT', 'Correct terminal meaning and units'],
  ['Mode and quality', 'Actual values and status producers'],
  ['Configured limit', 'Exact CONFIG path and case'],
  ['Write action', 'Checked operator path and correct target']
], '', 'Ask what sets each displayed field and what handles each action. One working instance can conceal hardcoded target names. Prepare two distinct compatible targets before the lab.');
picture('PVM Configuration Designer', 'pvm-designer.png', 'The typed interface connects a reusable design to its primary block target.', 'Explain primary drop target, accepted block types, internal helper properties and validation. The reference image shows a tutorial class. The class must remain reusable when mapped to the second learner target.');
compare('PVM and paired faceplate', 'Compact PVM', ['Identity and decision-relevant values', 'Quality and mode indications', 'Entry to the detailed view'], 'Paired faceplate', ['Same primary target', 'Detailed values and supported actions', 'Detached windows with pinning'], 'The faceplate should expose real supported actions through checked writes. Teach clear target identification and size/readability rather than crowding controls into a decorative panel.');
table('Two-instance acceptance test', ['Test step', 'Required observation'], [
  ['Map instance A', 'A reads target A'], ['Map instance B', 'B reads target B'],
  ['Pin A, then open B', 'Two detached faceplates remain identifiable'],
  ['Apply an allowed action to A', 'A responds and B remains unaffected'],
  ['Inspect mode/quality/markers', 'Each reflects its own live target']
], 'The second target provides a negative control for hardcoded bindings.', 'Runtime action tests require the isolated test station. Quick Online and TEST previews are read-only. Publish the preliminary local-file revision and retrieve it at the test station before the action test.');
table('Preview and deployment contexts', ['Context', 'Use'], [
  ['Graphics TEST', 'Isolated appearance review with writes blocked'],
  ['Quick Online', 'Read-only engineering preview'],
  ['Local-file Publish', 'Create a station-consumable revision'],
  ['Repository draft', 'Private edit pending check-in and release'],
  ['Operator Refresh', 'Accept the delivered display and matching assets']
], 'Private repository drafts cannot run or publish directly.', 'This distinction matters in L18. The early local project supports preliminary test-station publication. The later repository editing pilot requires release deployment.');
lab('L15', 'Build an operator display', 'New display in the learner copy, two compatible loops and a valid destination.', [
  'State the operator question and hierarchy level.', 'Place and map assemblies/components with valid paths.',
  'Align, connect and group related objects.', 'Review live bindings, layout and navigation in preview.'
], 'Draft, binding checks, layout rationale and defect list.', 'Readable state and functional navigation with valid targets.', 'End preview and retain only the intended learner draft.', 'Which element helps the operator make the intended decision?');
lab('L16', 'Create a reusable pair', 'L15 draft with PVM Configuration Designer and two distinct PID targets.', [
  'Define the typed primary target and paired bindings.', 'Validate and place two independently mapped instances.',
  'Publish to the isolated test station for action testing.', 'Pin both faceplates and prove target isolation.'
], 'Pair/interface record, mapping table and action evidence.', 'Independent targets and functioning detached/pinned faceplates.', 'Restore live values and clear TEST overrides.', 'What would a hardcoded target look like in the two-instance test?');
quiz(13, 14, 'Day 4 knowledge check: faults and cleanup');
quiz(15, 16, 'Day 4 knowledge check: reusable targets');

startDay(5, 'Commissioning, release and capstone', ['C09  Checkout and controlled release', 'C10  Integrated capstone']);
startSession('C09', 'Commissioning and controlled release', ['Review every applicable display state.', 'Trace a change through check-in and deployment.', 'Prove acceptance and recovery on the assigned runtime.']);
table('Six-state display review', ['Case', 'Visible interpretation to verify'], [
  ['Normal', 'Valid values and expected operating state'], ['Alarm', 'Meaningful abnormal indication'],
  ['Bad quality', 'Untrustworthy data clearly identified'], ['Manual', 'Actual mode correctly represented'],
  ['Interlocked', 'Driven condition, or documented not applicable'], ['Communication loss', 'Expected loss-of-data appearance']
], 'A missing interlock binding remains unknown rather than a healthy False condition.', 'The course requires all applicable reviews even when the product would present some missing/stale review states as warnings. Remove an inapplicable interlock case with a recorded reason.');
picture('Commissioning checklist', 'commissioning.png', 'Preview Case checks the actual canvas with isolated TEST data and records visual review.', 'Demonstrate binding validation, Preview Case, observed evidence and Record visual review. Restore preview at the end. A TEST loss-of-communications appearance does not qualify real provider reconnect behavior.');
text('Review currency after a change', [
  'The checklist records evidence against the reviewed display state.',
  'A relevant edit makes earlier review evidence stale.',
  'Failed cases need correction and another run.',
  'The course requires completed applicable reviews before acceptance.'
], 'Current product distinction: failed cases are publish errors, while missing or stale visual reviews are warnings. Explain the course acceptance rule without claiming the application enforces every review as a hard block.');
compare('Local engineering and repository drafts', 'Local working project', ['Save modifies local engineering files', 'Scoped modules can run in its assigned runtime', 'Local graphics use the Publish path'], 'Repository editing pilot', ['Save preserves a private draft', 'Review and check-in create shared revisions', 'Release deployment updates the isolated runtime'], 'At L18 capture the reviewed learner copy, create its isolated editing pilot and perform a small documented draft improvement. Capturing files does not redirect local Save or download a controller.');
table('Repository change sequence', ['Stage', 'Evidence'], [
  ['Capture reviewed snapshot', 'Correct objects and source identity'],
  ['Start editing session', 'Assigned private draft and reservation'],
  ['Review and check in', 'Changed fields, reason and shared revision'],
  ['Validate and create release', 'Complete scope and dependencies'],
  ['Deploy and accept', 'Runtime receipt, scan/graphic acceptance']
], '', 'Use Configuration > Import / export, Changes and Releases. Have the administrator provision permissions before the lesson. A learner can explain and verify administrator-only actions without receiving unrestricted permissions.');
picture('Configured versus running evidence', 'configured-running.png', 'Deployment receipts and fresh target observations establish different facts.', 'Delivered confirms target processing, not a controller scan or operator acceptance. Inspect Configured vs running and Audit, then verify the intended graphic and module behavior in the target session.');
table('Runtime comparison meanings', ['Status', 'What to investigate'], [
  ['Current', 'Matching source plus scan or graphic acceptance'],
  ['Online changes', 'Observed tuning differs from loaded engineering values'],
  ['Different', 'Target uses another revision'],
  ['Available / inactive', 'Graphic unaccepted or module inactive'],
  ['Unknown / not loaded', 'Missing fresh evidence or object not reported']
], '', 'Clear Reported objects only when checking items never loaded. Do not call a change complete because the release manager returned a receipt while the operator still views an older display.');
compare('Snapshot scope', 'Focused process snapshot', ['Process state and active disturbances', 'Controller operating/tuning options unselected by default', 'Useful for process-only recovery'], 'Complete course reset', ['Qualified operating and tuning state as needed', 'Relevant input profiles and identities', 'Compatible exercise and explicit Start restore'], 'A tuning comparison may require operating values, tuning and input state in addition to process state. Record the exact contract and entry run state. Requalify after relevant model/program/provider changes.');
picture('Released training baseline', 'released-baseline.png', 'An immutable baseline ties a release to its teaching purpose and optional exercise snapshot.', 'Explain Create isolated trainee copy, deploy to its runtime, Start isolated training process, Open Operator Live, Load inherited baseline and Start. Loading the exercise definition alone does not reset the process.');
text('Engineering handover record', [
  'Problem, cause and evidence supporting the change',
  'Source, checked-in, release and runtime identities',
  'Observed station behavior and remaining limitations',
  'Recovery baseline, scope, procedure and cleanup proof'
], 'Use W4. The receiving peer should be able to determine what runs without relying on another engineer\'s open editor tabs. Preserve raw exports and receipts with the interpretation.');
lab('L17', 'Complete the state checkout', 'L15/L16 draft with valid targets and original preview state recorded.', [
  'Add the six available cases and resolve applicability.', 'Validate bindings and preview each actual canvas state.',
  'Record evidence and repeat stale or failed reviews.', 'Restore prior preview mode and overrides.'
], 'W3 matrix, checklist review evidence and corrected defect log.', 'All applicable states supported by real bindings and review.', 'No TEST override remains active.', 'Which condition cannot be inferred when a binding is absent?');
lab('L18', 'Release and hand over', 'Provisioned service identity, editing pilot and isolated target runtime.', [
  'Verify captured course objects and make a small draft improvement.', 'Review/check in the change and create a complete validated release.',
  'Deploy control/graphics scope and verify fresh running evidence.', 'Confirm station acceptance and demonstrate approved recovery.'
], 'Change/release receipts, station acceptance and W4 handover.', 'Traceable source-to-runtime behavior with correct scope.', 'Restore the instructor-specified end condition.', 'What extra evidence is needed after a Delivered receipt?');
quiz(17, 18, 'Day 5 knowledge check: deployment and scope');
quiz(19, 20, 'Day 5 knowledge check: restore and review');
startSession('C10', 'Integrated capstone', ['Diagnose one qualified loop problem.', 'Recover the approved condition.', 'Improve, verify and release an operator graphic.']);
text('CAP01 scenario', [
  'The assigned unit has a reported loop problem.',
  'The instructor supplies one qualified fault and allowed actions.',
  'The learner diagnoses the responsible layer and proves recovery.',
  'The existing course graphic receives one useful, verified improvement.'
], plain(section(workbook, '### CAP01 - ')));
table('Capstone time plan', ['Activity', 'Minutes'], [
  ['Briefing and scope', '20'], ['Practical work', '150'],
  ['Engineering report', '40'], ['Individual viva', '30'], ['Total', '240']
], 'The practical includes diagnosis, correction, graphics verification and release.', 'Suggested practical split: 20 scope/baseline, 45 evidence/diagnosis, 25 correction/recovery, 40 graphic/change, 20 acceptance/cleanup. Pause timing for confirmed product or infrastructure failures and preserve evidence.');
text('CAP01 entry conditions', [
  'Correct isolated project, runtime, release and snapshot',
  'Known operating bounds, allowed intervention and stop condition',
  'Reusable course graphic with compatible targets',
  'Evidence folder and instructor recovery contact'
], 'The private instructor fault card holds the exact intervention, prior settings and expected recovery. Do not add an unqualified second disturbance during assessment. Retuning requires explicit inclusion in the card.');
text('CAP01 diagnostic evidence', [
  'PV/SP/OUT, actual/target modes, quality and limits',
  'Input source and available downstream acceptance or feedback',
  'Competing hypotheses and a useful raw historian record',
  'A correction that addresses the identified responsible layer'
], 'Observe without steering unless a critical scope, output-ownership or interlock error requires intervention. Log prompts. Require the learner to explain their proposed action and prediction before applying it within the authorized scope.');
text('CAP01 graphic and release evidence', [
  'A clear improvement for diagnosing this class of problem',
  'Valid paths and independent behavior of two instances',
  'Applicable state reviews with no stale evidence',
  'Reviewed release plus observed station acceptance'
], 'Use the existing course artifact to keep the capstone achievable. A better title, clearer quality/mode indication or corrected read binding can be sufficient if the operational purpose and verification are strong. Repository drafts require release deployment.');
table('Course assessment', ['Component', 'Points'], [
  ['Practical portfolio', '40'], ['Integrated capstone', '40'], ['Knowledge checks', '20'], ['Total', '100']
], 'Pass at 80/100 with all required hands-on outcomes and critical competencies demonstrated.', 'Scores come from instructor review. The application does not automatically enforce this rubric. A recorded-data substitute can earn analysis evidence while leaving the live practical competency outstanding.');
table('Capstone rubric', ['Area', 'Points'], [
  ['Scope and plan', '4'], ['Evidence collection', '8'], ['Diagnosis', '8'],
  ['Correction and recovery', '6'], ['Graphic improvement', '6'], ['Controlled release', '4'], ['Handover and viva', '4']
], 'Maximum 40 points. Detailed descriptors are in the instructor guide.', 'Full credit requires correct independent execution and evidence. Use the guide\'s 75/50/0 percent descriptors for assistance or incomplete work, rounding to half points. Do not penalize administrator-only steps or confirmed software failures when required reasoning and verification are demonstrated.');
text('Critical competencies', [
  'Correct isolated scope and controller output ownership',
  'Honest quality handling and actual-mode reasoning',
  'True=permit polarity with no bypass-as-repair',
  'Verified cleanup and restoration of the required end condition'
], 'The guide lists six independent critical competencies: scope, ownership, quality, mode transfer, interlock polarity, and restoration. A high total cannot compensate for an undemonstrated critical item. Record both the original attempt and a later reassessment.');
text('Individual viva', [
  'Explain a target/actual mode difference in your case.',
  'Identify the observation that rejected a competing hypothesis.',
  'State the quality and time limits of your historian conclusion.',
  'Show how another engineer verifies and recovers your released change.'
], 'Each learner answers individually even in paired workstation delivery. Use equivalent prompts across the cohort. Ask for evidence before accepting a confident narrative.');
text('Course completion and next practice', [
  'Retain raw evidence, reviewed objectives and release receipts.',
  'Complete any outstanding live outcome through a qualified reassessment.',
  'Practice the weakest competency with a fresh equivalent scenario.',
  'Use the workbook and product guides for further supervised work.'
], 'Completion establishes introductory simulator competence within the assessed scope. Record the final decision and any practical outcome still pending. Real-plant and vendor-specific authority remain separate.');

day = 0; session = 'Instructor appendix';
add('section', 'Instructor reference', { subtitle: 'Qualification, fault cards and delivery support', lines: ['Use alongside the instructor guide', 'Speaker notes include procedures and answer keys'] }, 'This appendix supports preparation and remediation. It does not add hours to the 40-hour learner schedule.');
text('Live-lab qualification card', [
  'Build, provider, project, release, runtime and snapshot identity',
  'Entry values, quality, modes, drift band and observation duration',
  'Exact intervention, permitted bounds and recovery acceptance',
  'Three recorded rehearsals with independent review'
], plain(section(guide, '### Qualify a dynamic baseline before assigning it')) + '\n\n' + plain(section(guide, '### QF1 - ')));
compare('F01 and F02 input fault cards', 'F01  Bad measurement', ['Qualified finite value with Bad quality', 'Source/consumer quality evidence', 'Restore prior input settings'], 'F02  Frozen Good input', ['Qualified static input and duration', 'Independent process/source evidence', 'Prove acquisition resumes'], plain(section(guide, '### F01 - ')) + '\n\n' + plain(section(guide, '### F02 - ')));
text('F03 downstream limitation', [
  'A separately qualified mode or acceptance condition',
  'Known output bounds and complete operating snapshot',
  'Evidence from both demand and acceptance paths',
  'A tested mode/recovery sequence with achieved-state proof'
], plain(section(guide, '### F03 - ')));
text('F04 provider process disturbance', [
  'Actual catalog identity, units and allowed values',
  'Qualified duration and expected process response',
  'Active state confirmed in the selected unit',
  'Clear selected followed by process-recovery evidence'
], plain(section(guide, '### F04 - ')));
compare('F05 and F06 engineering fault cards', 'F05  Wrong read binding', ['Copied isolated display', 'Two compatible known targets', 'Correct source path and retest both instances'], 'F06  Wrong scale', ['Practice module without plant outputs', 'Predicted versus observed point tests', 'Correct source and repeat all points'], plain(section(guide, '### F05 - ')) + '\n\n' + plain(section(guide, '### F06 - ')));
text('Product failure during class', [
  'Preserve the project/runtime identity and available logs.',
  'Pause the assessment clock for a confirmed software failure.',
  'Recover a compatible known state before resuming.',
  'Repeat evidence that lost continuity and record the limitation.'
], 'Do not penalize learners for a product defect. The pack does not claim full-shift or physical-monitor qualification. Rehearse a full teaching block on the actual workstation arrangement before class. Keep software defects separate from teaching misunderstandings.');
text('Source and companion material', [
  'ACT-FND-01 Learner Workbook and Instructor Guide',
  'User Manual and Engineering Training Workflows',
  'Simulation Workbench and Historian Workspace guides',
  'PVM / Faceplate Tutorial and Configuration Database guide'
], 'Repository sources: docs/training/LEARNER_WORKBOOK.md; docs/training/INSTRUCTOR_GUIDE.md; docs/USER_MANUAL.md; docs/ENGINEERING_TRAINING.md; docs/SIMULATION_WORKBENCH_GUIDE.md; docs/HISTORIAN_WORKSPACE.md; docs/PVM_FACEPLATE_TUTORIAL.md; docs/CONFIGURATION_DATABASE.md. Images come from docs/images/user_manual. No external vendor manual supplies course claims.');

await fs.mkdir(TMP, { recursive: true });
await fs.mkdir(OUTPUT, { recursive: true });
const deck = {
  title: 'Azeo Process-Control Engineering Fundamentals',
  edition: manifest.edition,
  width: 960, height: 540, font: 'Segoe UI',
  sourceRoot: ROOT, buildDir: TMP,
  output: path.join(OUTPUT, 'Azeo_Process_Control_Training_Detailed.pptx'),
  slides
};
const specPath = path.join(TMP, 'deck.json');
await fs.writeFile(specPath, JSON.stringify(deck, null, 2), 'utf8');
await fs.writeFile(path.join(TMP, 'slide_index.md'), slides.map((s, i) =>
  `${String(i + 1).padStart(3, '0')}  ${s.day ? `Day ${s.day}` : s.session}  ${s.title.replace(/\n/g, ' ')}`).join('\n'), 'utf8');
console.log(`Prepared ${slides.length} slides with source-linked speaker notes.`);
if (!process.argv.includes('--content-only')) {
  const chart = spawnSync('D:\\development\\GitHub\\vpy\\Scripts\\python.exe',
    [path.join(ROOT, 'tools', 'prepare_training_chart.py')],
    { stdio: 'inherit', windowsHide: true });
  if (chart.status !== 0) process.exit(chart.status ?? 1);
  const result = spawnSync('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
    path.join(ROOT, 'tools', 'export_training_slides.ps1'), '-SpecPath', specPath],
    { stdio: 'inherit', windowsHide: true, timeout: 900000 });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
