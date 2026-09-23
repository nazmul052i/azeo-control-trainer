# Azeo Plant Virtual Controller control-strategy basis

## Scope and source of truth

This project is generated from the existing AzeoPlantSimulator engineering
content, the documented control-module basis, the simulator reference strategy,
and the current 612-point catalog copied to
`virtual_io/opcua_tag_catalog.json`. The project-local stabilized version-2
snapshot at `virtual_io/lined_up.snapshot.json` is its reproducible initial
plant state. The catalog is the sole route authority; the project document does
not carry a second signal map that can drift.

`engineering/control_modules.json` owns each regulatory loop's PV identity,
action, normal mode, gain, and reset. `engineering/control_schemes.json` owns
cross-module consumers, handoff ownership, external reset, startup exceptions,
and explicit evidence gaps. The catalog independently owns AI range and unit.
Generation fails instead of guessing when a source is missing, malformed, or
inconsistent with the executable graph.

Generated and online mean that the graphs load and execute against the
simulator. They do not constitute field commissioning, process-safety approval,
or proof of tuning over the full operating envelope.

## Executable inventory

The one `APVC-CTRL-1` PK750 boundary owns all 160 assignments. PK750 is the
smallest supported controller model that can hold the 612 native DSTs: PK300
would be over capacity, while PK750 leaves 198 DSTs available for growth.

| Module family | Count |
| --- | ---: |
| Executable regulatory PID modules | 80 |
| Superseded PID-family monitor (`SIC-7001`) | 1 |
| Calculation modules (including CT1 performance and three evidence-gap monitors) | 12 |
| Device-control modules | 35 |
| Motor modules | 24 |
| SIS modules | 4 |
| MPC integration shell | 1 |
| Optimizer shell | 1 |
| Burner sequence modules | 2 |
| **Total** | **160** |

There are 158 control documents and 2 sequence documents. `TIC-4005` is added
during migration because it is documented but absent from the donor project.
Exactly the 80 executable regulatory PIDs carry the 200 ms module scan rate.
Four project-local Control Module classes create the nine CT1 instances. Their
embedded class snapshots keep runtime independent of the engineering library,
while stable class identities retain reviewed update and deviation tracking.

## Local virtual-I/O architecture

`APVC-VIO-1` is an in-process `LocalAdapter`, not an Ethernet I/O node. Its 612
catalog-derived routes are 184 AI, 64 AO, 211 DI, and 93 DO points. Every
mapping uses its plant tag in `signal`; an OPC node identifier is optional
provenance and is never the LocalAdapter bus address.

Provider construction is entirely project configuration:

- factory: `azeoplant.embedding:create_embedded_plant`;
- invocation: mapping call style selected by the project;
- discovery: the canonical component at `../../AzeoPlantSimulator`, resolved
  beside the project for source/portable runs and through the matching
  immutable bundled project for installed workspaces;
- lifecycle: manual Start, Stop, and Restart from Azeo Explorer;
- resources: `${PROJECT_DIR}/virtual_io/opcua_tag_catalog.json` and
  `${PROJECT_DIR}/virtual_io/lined_up.snapshot.json`;
- source/holder: `APVC-CTRL-1`;
- output claim: required and released with the provider lifecycle;
- input freshness and provider output-staleness watchdog: 2 s each;
- autorun at 1.0x real-time speed;
- process integration and I/O exchange: 100 ms; and
- regulatory execution: 200 ms.

The application schedules provider exchange and controller execution
independently. It seeds output readbacks before controller execution and drains
queued writes between plant steps; it does not pretend they are one synchronous
read/scan/write/step callback.

## Startup and transfer semantics

The project declares `lined_up_hold`. Every PID, AO, and DO downloads in Manual
while retaining its configured `normal_mode`. AO/DO blocks adopt restored
physical readback. PID `out_init` values are engineered by role: direct loops
adopt the as-found final-element demand, cascade masters adopt the first slave's
as-found PV normalized to percent, override constraints start released, and
trim loops adopt the as-found balance. Ordinary SPs adopt snapshot PV, but
constraints keep their design SP/released-output pair; `PIC-5001` keeps 8.5 bar.

`PIC-5001` is an explicit non-bumpless commissioning exception: its controller
starts at the neutral 50% split point instead of reconstructing the snapshot's
fully-open `PCV-5001` leg. Both output blocks remain in Manual, so transfer
requires an operator check rather than an automatic claim of no bump.

This distinction is deliberate. Setting every constraint SP to snapshot PV or
every selector output to 50% would arm an override at an arbitrary operating
point and create a first-transfer bump.

Each of the 19 slave SPs has exactly one `SP_HANDOFF` owner. Handoffs read the
slave working SP before engagement and are armed at startup, but the engineered
slave mode remains the sole ownership action. An inactive handoff does not
enqueue a write; entering `CAS` captures the live working SP before transfer.

Seventeen distinct cascade masters use external reset. A typed
`REMOTE_ANALOG` reads the first slave's `BKCAL_OUT` value, quality, and limit;
an explicit scaler converts slave EU to 0..100 percent before the master's
`BKCAL_IN`. Bad reset quality disables external-reset use. Each slave returns
working PV through `BKCAL_OUT`. Feedforward is wired only to `PID.FF_VAL` and
never included in the reset measurement.

The full 19-path table and exact algorithms are generated in
[CONTROL_TOPOLOGY.md](CONTROL_TOPOLOGY.md).

## Control and safety ownership

The provider starts with `open_loop: true`, disabling the simulator BPCS while
leaving its independent SIS active. APVC therefore owns BPCS output commands;
the simulator may still apply protective action. Claim/release around the
provider session is the output-holder arbitration boundary.

The 173 writable routes are exhaustively and disjointly partitioned:

- 157 have one named APVC AO/DO strategy owner;
- 6 (`XY-9001` through `XY-9006`) belong to
  `azeoplant.control.esd:SISMatrix`; and
- 10 are reserved/inactive because the current APVC engineering basis does not
  assign them a producer.

The 16 CT1 commands are owned by linked, project-local APVC class instances;
no upstream software-DCS controller was imported with the open-loop physics.
The remaining reserved outputs have physical model effects but no evidenced
strategy owner and retain restored values. `FIC-7003` owns `SC-7001`;
`SIC-7001` remains a non-writing
monitor so it cannot become a second owner.

## Evidence boundary

Neither `engineering/Control_Modules.md` nor the simulator reference strategy
defines equations for `FY-0101`, `FY-2001`, or `PY-0101`. Their misleading
identity calculations and all write paths are removed. They retain source
measurements with `MONITOR_ONLY_UNSPECIFIED` status. This is an explicit gap,
not an implied implementation. `SIC-7001` is separately marked
`MONITOR_ONLY_SUPERSEDED`.

## Optional interoperability profile

The optional OPC UA profile is disabled and has no network address. Local
operation needs neither a separate OPC server nor an EIOC. Per-tag OPC node IDs
remain engineering provenance only. Enabling a network profile is a deployment
change and must not alter the local ownership contract.

## Stop, status, and fault evidence

A normal close stops the worker and embedded session, releases the holder, and
marks cached field values stale. Driver health exposes running state, the
612-route inventory, validated startup readbacks, enqueued demands, actual
output readbacks, failures, last error, and provider health. Provider readback
remains authoritative if a plant step clamps or rejects a demand. Use Explorer
for live state and the system/error logs for persistent evidence.

## Acceptance contract

Qualification verifies:

- distinct APVC identity, one controller, and all 160 assignments;
- configuration-driven Local Virtual-I/O with project-relative resources;
- the stabilized v2 snapshot and exactly 612 catalog-derived signal routes;
- exactly 157 APVC-owned, 6 SIS-owned, and 10 reserved writable routes;
- exactly 80 executable PIDs at 200 ms and no PV/action/mode/tuning/range
  mismatch against engineering data;
- actual PV graph topology, including U200 `UY-2001`, `TIC-2002` on `TT-2002`,
  and the documented three high-select PV paths;
- all 19 unique forward handoff owners and 17 typed external-reset contracts;
- feedforward separation from external reset and quality/limit propagation;
- constraint SP exceptions, role-specific `out_init`, selector release,
  split-range boundaries, and exact scheme calculations;
- all PID and 157 strategy AO/DO blocks in the declared Manual hold;
- monitor-only evidence gaps have no control-writing path;
- deterministic 30-s startup without unexplained deviation, Bad/non-finite
  controller inputs, runtime errors, or plant-engine errors before transfer;
- deterministic regeneration and byte-preservation of the donor project; and
- documentation that describes the generated topology rather than donor
  placeholders.

Run the APVC smoke and qualification tests after any catalog, snapshot, graph,
provider, timing, ownership, or documentation change.
