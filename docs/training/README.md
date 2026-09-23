# Azeo Process-Control Engineering Fundamentals

**Course ACT-FND-01 | Edition 1.0 | 9 September 2026 | Instructor-led pilot**

A practical foundation course for new process-control engineers using Azeo
Explorer, Control Designer, Graphics Designer, Operator Station and Simulation
Workbench. The course follows a measurement from field acquisition through
control logic, operator presentation, diagnosis and a reviewed engineering release.

## Course pack

This public README records the course scope. Instructor workbooks,
presentations, answer material and generated PDFs are maintained outside the
product repository and are released separately after classroom qualification.

The default delivery is **five days, 40 contact hours**. Breaks and lunch are
additional. This duration is the initial planning assumption; the same material
can be delivered as ten four-hour sessions. Allow one workstation per learner
and ideally one instructor for six learners; pairs may share a workstation if
they alternate engineer/operator duties and submit individual evidence.

## Entry and exit expectations

Learners should be comfortable with basic algebra, percentages, units and a
simple P&ID. No prior Azeo experience is required. Give learners who cannot trace
a sensor-controller-valve path the prerequisite exercise in the workbook before
Day 1. Software installation and native-core builds are instructor preparation,
not classroom learning objectives.

By completion, a learner should be able to:

1. Trace measurement, quality, controller demand and final-element ownership.
2. Create and verify a small function-block module in an isolated project.
3. Explain actual, target and normal modes and make an authorized mode transfer.
4. Use multi-tag history to distinguish a disturbance, a bad measurement and a
   downstream limitation; measure a valid response experiment.
5. Explain cascade, back-calculation, permissives, interlocks and sequence logic.
6. Build and verify a reusable operator graphic and its paired faceplate.
7. Preserve evidence, restore the starting state and release a reviewed change.

Completion demonstrates introductory simulator competence. It does not qualify
a learner to operate a real plant, approve a safety system or commission a
vendor DCS independently. Vendor-specific engineering and site procedures are
outside this course.

## Delivery schedule

| Day / session | Topic | Guided work | Contact time |
| --- | --- | --- | --- |
| 1 / C01 | Plant orientation and signals | L01-L02 | 4 h |
| 1 / C02 | Function-block engineering | L03-L04 | 4 h |
| 2 / C03 | Modes and historian | L05-L06 | 4 h |
| 2 / C04 | Response measurement and tuning | L07-L08 | 4 h |
| 3 / C05 | Cascade and interlocks | L09-L10 | 4 h |
| 3 / C06 | Sequences and alarms | L11-L12 | 4 h |
| 4 / C07 | Diagnosis and repeatable sessions | L13-L14 | 4 h |
| 4 / C08 | Graphics and reusable faceplates | L15-L16 | 4 h |
| 5 / C09 | Commissioning and controlled release | L17-L18 | 4 h |
| 5 / C10 | Integrated capstone | CAP01 | 4 h |

C01-C08 each contain 45 minutes of teaching, 75 and 90 minutes of lab work,
and 30 minutes of review. C09 uses 30/90/90/30 minutes. C10 uses a 20-minute
briefing, 150-minute practical, 40-minute report and 30-minute individual viva.
Knowledge checks and lab scoring fit within the stated review periods.

## Course readiness and scope

Authoring baseline: application commit **9790426**, with the embedded native
plant integration at **02de8e5** and provider improvements at **e0bb958**.
The APVC project contains 149 control and two sequence modules. Some design
metadata specifies CAS as normal while saved PID/AO setup modes are MAN;
learners must read the live actual mode. A normal-mode label does not establish
an operating condition.

This pack is a **pilot edition, not a completed classroom qualification**.
Its workflows and named configuration objects are checked against this repo.
Before delivery, the instructor must execute and sign the qualification cards
for all live exercises on the intended application/build, copied project and
starting snapshots. Process response bounds, tuning trials, alarm challenges
and permitted writes must be recorded there. Blank cards mean the lab is not
released for independent operation. A recorded-data fallback can support theory
but does not earn the corresponding hands-on competency.

Existing automated application checks do not establish model fidelity, a
validated training scenario or full-day workstation reliability. Long-duration
and physical multi-monitor acceptance remain separate product work. The course
does not assume automatic grading, authenticated instructor authority from a
presentation selector, or controller-resolution sequence-of-events recording.

Use only copies/runtimes allocated to the class. Do not edit packaged
`src/strategies/` examples or the master APVC project. The small `azeo_training`
MTR-102 module is used as a copied, offline logic example; its store-driven
inputs do not constitute an integrated dynamic process lesson.

## Assessment and records

The proposed course score is 100 points: practical portfolio 40, capstone 40,
and 20 knowledge questions worth one point each. Pass at 80 points, with every
critical competency independently demonstrated. The instructor guide defines
weights, observable criteria, resits and course completion records. Scores are
entered and reviewed by the instructor; the application does not enforce this
rubric automatically.

Keep each learner's project/release identifiers, snapshot identifiers, lab
records, exports, marked answers and final remediation decisions together. A
screen capture alone is insufficient evidence of a successful control change.

## Maintenance and rebuilding

Authoritative application references: [user manual](../USER_MANUAL.md),
[Workbench guide](../SIMULATION_WORKBENCH_GUIDE.md),
[historian guide](../HISTORIAN_WORKSPACE.md), and
[PVM tutorial](../PVM_FACEPLATE_TUTORIAL.md). These describe the supported product;
this course supplies the teaching sequence and assessment.

Run from the repository root using the configured environment:

```powershell
& .\.venv\Scripts\python.exe tools\validate_training_course.py
& .\.venv\Scripts\python.exe tools\build_training_course.py --audit
```

The validator checks course duration, lab coverage, document links and named
source contracts. It does not run a plant or approve a dynamic exercise. The
builder produces searchable, bookmarked learner and instructor PDFs and page
renders for visual review. Requalify affected lab cards when configuration,
mode behavior, provider, snapshot schema or operator workflows change.
