# Configuration Database — engineering, releases and runtime evidence

The configuration service provides a shared PostgreSQL repository and engineering catalog for captured
engineering projects. Open **Explorer → Project Administrator → Configuration
Database**. Select a local project in Project Administrator before opening the
database browser to choose the capture source.

The service supports **file-authoritative snapshots** and **isolated repository
editing pilots**. The current workflow is described below.
Original projects retain their existing stores. An editing pilot owns its
checked-in configuration in the repository and gives each session a separate
recoverable working draft. Release Manager can deploy reviewed releases into a
separate isolated runtime. Original-project and external field-I/O cutover have
not been enabled. A checked-in revision is not evidence of a controller
download or an operator station's accepted revision.

## Using the browser

Project Administrator manages capture/export. The common **Engineering catalog**
is also available from **Explorer or Control Designer → Tag Database → Shared configuration**
and **Graphics Designer → View → Tag Catalog**. The Tag Database's **Live / open modules**
tab retains its live behavior.

All three entry points use one **Configuration** workspace and Control Designer's
shared blue palette, icons and controls. The header owns the service identity,
project and revision set. **Catalog**, **Changes**, **Releases**, **Libraries**,
**Training**, **Recovery** and **Import / export** retain their page state while
navigating. Review dialogs remain separate. Project and connection changes wait
for active requests/reviews; credentials entered here are not persisted.
Background activity uses a thin, smoothly animated blue loading line. The line
stops when its page is hidden or the request finishes; workspace navigation
remains available during a request.
Project, category and runtime selectors use the same flat fields and blue
chevrons, including their open menus and keyboard navigation. Embedded managers
keep tables and local filters usable during refresh; commands stay unavailable
until the request finishes. Separate review dialogs retain their form lock
while a reviewed command is submitted.
The following snapshot operations live in **Import / export**.

The workspace shares the engineering apps' menu bar and titled context menus.
**File** owns connection/project selection and import/export navigation;
**Edit** handles copy and find; **View** refreshes and switches retained pages;
**Object** routes to the current page's existing commands; **Tools** opens the
engineering workflows; **Help** provides local guidance and the user manual.
Table menus select the clicked row, retain the existing command gates, and
refuse stale actions if a background refresh replaces that row. Keyboard
shortcuts are scoped to the visible Configuration workspace, so a hidden shared
catalog tab does not take Control Designer's shortcuts.

1. Confirm the connected service and identity in the header. The local pilot
   connects automatically. Use **Connection** for another service or token.
2. Choose a repository project. **Objects** lists source paths, document kinds,
   revision numbers and SHA-256 prefixes. Select an object for its stable
   repository identity and immutable revision history.
3. Use **Tags** to search module, terminal, field and configuration parameter
   metadata. The case-sensitive namespace is retained, including paths such as
   `LOOP/PID1/CONFIG/lo_lim`. Search itself is case-insensitive. These are
   configured values and schema defaults; this browser does not show live PVs.
4. **Audit** records the authenticated actor, snapshot generation, command and
   commit time. **Previous** and **Next** page through results; **Refresh**
   reloads committed state from the service.
5. Enter the repository project name next to **Capture as**, then select
   **Preview local project**. Review added, changed and removed paths, the
   exclusions, tag count and index limitations in the preview.
6. Select **Import reviewed snapshot** to capture exactly those reviewed bytes.
   Sources remain unchanged. If another engineer imports first, the stale
   generation is refused; run the preview again. A timed-out request retains
   its command identity so retrying does not create a duplicate commit.
7. Use **Export snapshot** to reconstruct the current snapshot in a new
   directory. All hashes are verified before writing. An existing project
   directory is never overwritten. This exports the captured engineering
   files; it is not a backup of the PostgreSQL revision/audit database.

After editing files in a studio, preview and capture again. The browser does
not watch the source directory or imply that a previous snapshot is current.
Concurrent source changes detected while scanning cause the preview to fail.
For a coherent operational baseline, engineering writes must still be paused;
a filesystem scan is not a database transaction over the source files.

## Shared catalog and loop inspection

The shared catalog uses the seat's private `data/configuration/client.json`
profile, created by the pilot setup. A temporary connection entered in Project
Administrator applies only to that administration dialog; it does not replace
the shared catalog profile or another application's credentials.

1. Open the shared catalog in any engineering application. An unambiguous captured
   `project_id` match selects the project automatically. Otherwise choose the
   captured project explicitly. The association is shared by the three applications
   for that local workspace and configuration-service identity.
2. Search a module such as **PIC-2001**, or a full parameter path. Search is local
   after background loading, supports multiple search words, and displays 200 rows
   per page. Filters cover tags, modules, configured I/O, PVM classes and graphics.
3. Select a tag and choose **Inspect loop**. **Properties** identifies its source and
   captured revision; **Parameters** shows the module's configured values. The
   **References** tab follows wires, I/O bindings, controller-store publications,
   controller assignments, placed PVM bindings, class usage and faceplate links.
   **Where used only** filters to consumers. Double-click the Source or Target
   column to follow that side of a reference; **Back** returns to the prior object.
4. Follow a graphic or installed faceplate class and select **Preview**. This uses
   the existing display renderer or measured faceplate widget in a separate window.
   Preview values are not live, and controls cannot issue operator writes.
5. **Coverage** lists unresolved targets, dynamic references and script dependencies
   that cannot be resolved statically. These are review findings, not permission to
   rename or delete a referenced object. Installed faceplate binding templates are
   cataloged as class metadata; this is not an inventory of active runtime subscriptions.

The field I/O picker provides **Shared configuration…** and inserts the actual
store tag (for example `PT-2001`). Graphics Designer's unified binding editor,
including the PVM Configuration Designer, provides **Browse shared configuration…**
and inserts the canonical parameter path with its captured data type. Choosing a
snapshot tag does not download, publish or write to a controller. Verify authored
bindings against the current project before publication.

The status line distinguishes connection state, snapshot generation, capture time
and local-file differences. While visible, the browser checks every 30 seconds;
**Refresh** checks immediately. Matching revision stamps avoid downloading the
full catalog again. Local-file comparison, HTTP, parsing and index construction
run on application-owned workers. Closing a browser does not destroy its worker.

During a service outage, a previously loaded catalog remains available and clearly
reads **OFFLINE — cached snapshot; service currency unknown**. Caches are scoped
to service, credential and project under the ignored seat data directory. A known
access denial removes that identity's cached project; an outage alone cannot
determine whether remote permissions changed. A different credential cannot use
the previous identity's cache. Running revision remains **Unknown** until the
later deployment-integration phase establishes actual runtime evidence.

## Shared drafts, check-in and conflict recovery

Open **Configuration → Changes** from the shared catalog in any of the three
engineering applications. Choose **APVC Shared Editing Pilot** in the catalog's
project selector to use the verified pilot. A service administrator can instead
choose **Create isolated editing pilot…** on a captured project. This creates a
new repository project with its own object identities and recorded source
provenance; it does not convert or overwrite the source project.

1. Select **Start new editing session**. The session opens in its own process
   with an isolated working directory. **Resume selected draft** reopens saved
   work after closing the application or restarting the PC. The same working
   directory cannot be opened by two running session processes.
2. Find a module or graphic and select **Open in Studio**. This reserves it; modules open in
   Control Designer; displays open in Graphics Designer. Other text configuration
   documents use an explicitly labeled advanced document editor. Binary assets
   can be replaced in the working directory and included in the same review.
3. Use the editor's normal **Save**, or **Save drafts** in Shared changes. This
   saves recoverable local work. The shared repository remains at its checked-in
   revision until the next step. Separate sessions never share writable files.
4. Select **Review and check in…**. Open **Changed fields** to compare before and
   after values. Review the reference findings, enter a change description, and
   select **Check in change set**. All changed documents, revisions, derived
   catalog entries and audit evidence commit together. Separate modules can be
   checked in concurrently; a stale document revision is refused.
5. A **New committed revision** or **Conflict: compare** row identifies work
   committed by another session. **Compare / resolve…** displays the draft base,
   your draft and the current shared revision. After reviewing, either reload
   the current revision or retain your reviewed draft against that revision.
   Both actions preserve a dated recovery copy before replacing/rebasing local
   work. Retaining a draft still requires a subsequent checked-in revision.
6. **Retry interrupted check-in** uses the persisted command identity after a
   lost response or process restart. A successful retry returns the original
   receipt; it does not create another revision. A definite rejection preserves
   the draft for another review. An outage does not authorize an offline shared
   commit or an overwrite after reconnecting.

The **Editing** column identifies the authenticated owner and distinguishes
the current session from another window using the same identity. Reservations
last 90 seconds and renew while the session is connected. Shared changes checks
the complete revision/ownership inventory every 20 seconds; **Refresh** checks
immediately. Reconnection compares full inventories, so missed notifications,
renames and deletions are detected. The catalog in other applications checks
every 30 seconds. Local drafts are never silently replaced by those refreshes.

**Release reservations** makes documents available immediately. Normal close
attempts a short background release; after a crash or outage, expiry permits
another session to reserve them. An expired owner must still pass the revision
check and cannot overwrite newer work. Closing the window saves its open drafts;
the working directories and recovery copies remain under the private
`data/configuration/workspaces/` directory. Service credentials are not written
into those drafts, and another service identity cannot resume them through this UI.

### Reviewed module renames

Select a module and choose **Rename module…** after checking in or resolving
existing draft changes. Control Designer's tree Rename also opens this shared
review when editing a repository draft. The preview lists known address changes and unclassified
text/script references that remain unchanged. Review both tabs, supply a reason,
and select **Check in reviewed rename**. The module keeps its repository object
identity and revision history. The module address and known consumers change
together; block instance names and `ctrl.<block>` field publications retain their
existing identities. Block renaming and historical point migration are separate
operations. A concurrent project change invalidates the rename preview so a new
consumer cannot be missed silently.

Historical files and published display history cannot be edited through check-in.
Existing Studio saves target the session's draft tree, and the repository refuses
snapshot imports into an editing pilot. The supported general launcher rejects a
working-draft directory; reopen it through Shared editing. Draft modules cannot go
on scan, and draft displays cannot publish through the existing display store.
Repository release/download support is Phase 4. Original-project cutover, broad
LAN permissions and full workload qualification remain later gates.

## What the first delivery preserves and indexes

| Content | Treatment |
| --- | --- |
| Strategy modules | Original bytes and unknown fields retained; tag catalog derived using the installed block schemas and the existing strategy loader |
| Block/wire IDs, composite references, embedded external identifiers | Retained inside the unchanged source documents |
| PVM drafts, published revisions, layouts and metadata | Retained without converting `PvmDisplay` or creating another publication model |
| Assets and other engineering files | Original bytes retained and hashed |
| Removed files | Tombstone revision retained; earlier bytes remain retrievable through the revision API |
| Restored path | Reuses its repository object ID and adds a revision |
| A changed source path/name | Recorded as removal/addition, except a case-only path change; semantic rename and external identity remapping require the later coordinated-editing stage |
| `.lock`, recovery, accepted-station state, logs, snapshots, local archives | Explicitly excluded and listed in the preview |

Uninstalled block types, malformed JSON, duplicate file/module identities,
unsafe paths and oversized inputs are refused. Unsupported repository schema
versions are refused at service startup. Capturing a document does not replace
its product's engineering verification or certify its references and logic.
Imports are atomic: no partially indexed project is made available. Import
limits are 10,000 files, 16 MiB per file and 128 MiB of decoded project content.

The tag projection covers modules and unused configured I/O channels. The shared
catalog adds installed PVM/FB PVM and faceplate classes, captured authored
libraries, configured class properties, display placements and static references.
Cross-project references and arbitrary script/runtime indirection remain explicit
coverage limits; an absent usage row is not proof that an object is unused.
Schema defaults representing unbounded numeric limits use JSON objects such as
`{"$number":"Infinity"}` in the catalog; authored file bytes remain unchanged.

## Local pilot setup

The repository's supported interpreter is
`.\.venv\Scripts\python.exe`. PostgreSQL 16 binaries must
already be installed. The optional Python dependencies are declared in the
`configuration` extra in `pyproject.toml`; existing applications do not require
them merely to start.

```powershell
& .\.venv\Scripts\python.exe -m pip install 'psycopg[binary,pool]>=3.2,<4' 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1'
& .\.venv\Scripts\python.exe tools/configuration_pilot.py setup
```

Setup creates an isolated PostgreSQL cluster under ignored
`data/configuration/postgres`, listening on **127.0.0.1:55432**, and a configuration
service on **127.0.0.1:8766**. It does not change the installed Windows service.
It uses generated SCRAM credentials and restricts the Windows pilot directory
ACL to the current account and SYSTEM. Credentials are stored in private local
`server.json` and `client.json` files, never in tracked files, logs, command-line
password arguments or application Qt settings. Setup refuses to overwrite an
existing cluster or client profile.

After restarting the computer:

```powershell
& .\.venv\Scripts\python.exe tools/configuration_pilot.py start
& .\.venv\Scripts\python.exe tools/configuration_pilot.py status
```

The processes start without extra console windows. Logs are under
`logs/configuration`. This is a local pilot launcher, not yet a Windows service
installer. LAN rollout requires the later identity, TLS and service-packaging
qualification gate. The bundled service binds only to loopback; the client
requires HTTPS for a remote address and refuses credential-bearing redirects.

## Service administration

The service CLI is available as `azeo-configuration` in an installed package,
or as `python -m azeo_control_trainer.services.configuration` with `src` on
`PYTHONPATH`. Use `--dsn-file` with a private JSON file containing `dsn`, or
provide `AZEO_CONFIGURATION_DSN` to the service environment.

| Command | Effect |
| --- | --- |
| `migrate` | Apply or verify the schema version and checksum under a database migration lock |
| `serve --port 8766` | Start the loopback HTTP service; also verify the schema |
| `identity NAME --output PRIVATE_FILE [--administrator]` | Create or rotate a named access token; the output file must be new |
| `grant NAME PROJECT_UUID reader` | Allow that identity to browse/export the project |
| `grant NAME PROJECT_UUID importer` | Also permit reviewed source snapshot imports |
| `grant NAME PROJECT_UUID engineer` | Browse and check in a repository-owned editing project |
| `revoke NAME` | Disable the identity immediately for subsequent requests |

Administrators can create new repository projects. Readers cannot import or
check in; importers cannot check in, and engineers cannot import source snapshots.
Identities without a grant cannot read a project by guessing its UUID.
Each API request authenticates again, so revocation takes effect without
waiting for a UI reconnect. Local identity provisioning and grants require
trusted access to database administration credentials. Initial authentication
is named bearer credentials, not Windows-integrated sign-in. Protect exported
credentials as passwords; rotate them if exposed.

All project import/check-in commands and revisions are recorded in one transaction.
Database triggers reject updates/deletes of revision and import-audit rows.
Local identity/grant administration does not yet have a dedicated security
audit view. An actual database owner can perform database administration and
is outside the application's role enforcement boundary.

## Verification and recovery boundary

`tests/test_configuration_database.py` verifies lossless byte export, unknown
field preservation, parameter case, path protection, failed-transaction
rollback, concurrent-import conflicts, idempotent retries, revision retention,
authorization, token revocation, schema drift detection and background Qt I/O.
Its PostgreSQL tests require an explicitly selected test server and create
temporary databases; they never reuse the pilot's project tables.

`tests/test_configuration_catalog.py` adds namespace, spare I/O, library scope,
where-used, conditional refresh, cache isolation/revocation, worker lifetime,
typed picker, preview and full-catalog response performance checks. Run
`tools/verify_configuration_catalog.py --native` for the same PIC-2001 workflow
through Explorer and both studios, related graphic/faceplate previews, and the
existing Operator Station console on a disposable export. Its screenshots and
measurements are written to `logs/configuration-catalog-ui/`.

```powershell
$env:AZEO_CONFIGURATION_TEST_DSN_FILE = 'D:/development/GitHub/azeo_control_trainer/data/configuration/server.json'
& .\.venv\Scripts\python.exe -m pytest tests/test_configuration_database.py -q
& .\.venv\Scripts\python.exe tools/verify_configuration_database.py --native
```

The native verifier expects a captured project named **APVC Database Pilot**.
It drives the actual Project Administrator entry point, searches tags, checks
revision history, commits an unchanged import and checks for Qt warnings. It
also verifies the full pilot's byte-for-byte export and measures 40 warm HTTP
search requests. Output is in `logs/configuration-ui`; timings describe that
local dataset and do not qualify the proposed 10-client LAN workload.

`tests/test_configuration_editing.py` and `tests/test_configuration_workspace.py`
cover atomic multi-document commits, lease expiry, stale revisions, authenticated
roles, failed transactions, rename impact, private draft recovery and reachable
Studio Save/review actions. `tools/verify_configuration_editing.py` drives two
native engineering processes, conflict resolution, a module rename and a graphic
check-in against the isolated editing pilot. It restores that pilot's source
bytes through another audited change set after verification.

Repository-owned drafts, edit reservations and reviewed module-address renames
are available in the isolated pilot. Display/class/block semantic renames,
release packages, controller/station acknowledgements, stable historian point
migration, trainee baseline cloning and original-project cutover remain later
stages. Shared drafts refuse the legacy display-folder rename because moving
its history would bypass a reviewed repository identity change.

PostgreSQL backup/restore and scheduled recovery also remain later delivery
stages. Do not use a copy of the active PostgreSQL data directory as a backup.
Existing project backup/restore remains the recovery mechanism for the
file-authoritative engineering project; it does not back up repository revisions.

## Verified Phase 1 pilot — 8 September 2026

The captured APVC project contains **256 files**, including **151 modules**,
**16 PVM drafts** and **65 legacy revisions**. Its derived module catalog contains
**33,589 module-derived tags** at the Phase 1 baseline. Export matched every captured file byte for byte, including
unknown JSON properties and assets. The snapshot survived a stop/start of the
isolated PostgreSQL cluster and configuration service.

The final checks passed: **36 focused tests**, all **11 application smoke entry
points**, required Graphics Designer/PVM lint and the new-code lint checks. Operator
Live, Explorer, the HMI checks and the virtual-controller boot ran with the pilot
service/database offline. The virtual-controller check scanned 151 modules,
opened the plant overview and resolved a Good live value. Native browser checks
covered search, revision inspection, import preview, actual import, audit and
window sizing, with no Qt warnings.

On this Windows 11 pilot PC, 40 warm HTTP tag searches measured **90.18 ms median**
and **106.47 ms p95**. An earlier run measured 278.84 ms p95 before client transport
reuse and the generated search projection. These are local pilot measurements;
the proposed shared LAN workload remains unqualified. Evidence is saved under
`logs/configuration-checks/`, `logs/configuration-ui/`,
`logs/database-focused-final.txt` and `logs/database-ui-final.txt`.

## Verified Phase 2 catalog — 8 September 2026

The shared projection contains **33,718 tags**, including 129 additional unused
configured I/O tags, **552 configured I/O channels**, and **158 installed PVM and
faceplate class records**. Reindexing retained snapshot generation **1** and the
original source digest; it did not create engineering-file revisions.

Native verification opened the catalog through Explorer, Control Designer and
Graphics Designer. All three returned identical PIC-2001 parameter metadata,
including the case-sensitive `CONFIG/lo_lim` path, object identity and revision.
The check followed controller, I/O, graphic and faceplate references, opened the
existing Operator Station console, and verified graphic and measured faceplate
previews. No Qt warnings were emitted, all widget fonts had positive point sizes,
and the source project remained byte-for-byte unchanged.

The final checks passed **87 focused tests**, all **11 application smoke entry
points**, and the required Graphics Designer/PVM and new-code lint checks. The
focused tests include a regression that keeps the shared catalog's model/view
widgets unconstructed until its tab is selected.
Eager construction reproduced a native heap fault twice in the long Operator
smoke session; selecting the tab lazily removed that failure in both subsequent
full runs.

On this local pilot, a full catalog HTTP response fell from **8.60 s to 2.69 s**
after removing recursive generic response encoding. Conditional refresh avoids
transferring an unchanged full catalog. Forty alternating native PIC-2001 and
SIC-2001 searches measured **131.52 ms median** and **303.97 ms p95**, including
the UI update. These measurements describe this PC and dataset, not LAN capacity.
Evidence is under `logs/configuration-phase2-final/`,
`logs/configuration-catalog-ui/` and
`logs/configuration-catalog-native-final.txt`.

## Verified Phase 3 editing pilot — 8 September 2026

**APVC Shared Editing Pilot** is an isolated repository-owned copy of the
256-file APVC snapshot. Native verification used two separate Windows processes
and private working directories to edit PIC-2001 and SIC-2001 through Control
Studio, save local drafts and check in both changes. A stale PIC-2001 draft was
refused, compared against the current revision and explicitly reloaded with a
recovery copy. Reviewed renames to PIC-2001-ENG and back preserved the module's
object identity and updated known consumers together. Graphics Designer saved and
checked in the U200 graphic through the same repository command path.

The real session entry point opened its revision inventory and released its
session lock on close. Reopening an already-owned session is refused. Native
editing and launcher checks emitted no Qt warnings. Screenshots of Shared
changes, conflict comparison, rename impact and both studios are under
`logs/configuration-editing-ui/`.

Final verification covered **116 distinct focused tests** across the integration
and hardening runs, **22 existing Graphics Designer document-workflow tests**, all
**11 application smoke entry points**, and the required scoped lint. The final
repository/workspace run passed **56 tests**, including real lock contention,
rollback, permissions, unchanged tag-row retention, stale revisions, interrupted
rename recovery and the native Save/review path. The new overwrite/contention
regressions were first run against the faulty implementations and failed there.
Evidence is in `logs/configuration-phase3-final/`,
`logs/configuration-phase3-commit-green.log`,
`logs/configuration-phase3-hardening-green.log`,
`logs/configuration-phase3-entry-documents.log`,
`logs/configuration-phase3-entry-native.log`,
`logs/configuration-phase3-native-final.log` and
`logs/configuration-phase3-lint-final.log`. The entry-lock assertion initially
failed against the PySide overload; the corrected entry test passed in the
final 56-test run. The initial lint findings were corrected and the final
scoped run is clean.

A native concurrent check-in exposed project-lock contention while the former
writer rebuilt every tag row. Check-in now stages the index and updates only
changed rows; bounded retries handle rolled-back lock contention while retaining
the original command identity. Two original writer measurements were **5.80 s**
and **6.98 s**. Subsequent measurements ranged from **3.78 s to 6.46 s**, depending
on local load. Writes still take seconds on this dataset; this pilot does not
qualify the proposed ten-client workload. Writer measurements are in
`logs/configuration-phase3-commit-before.log`,
`logs/configuration-phase3-commit-after.log` and
`logs/configuration-phase3-commit-profile.log`.

Removing unnecessary catalog-table word wrapping reduced the measured native
search latency to **92.22 ms median / 149.12 ms p95** over forty alternating
PIC-2001/SIC-2001 searches. All three engineering applications still returned
identical metadata, and the existing Operator Station console opened correctly.
These timings describe this PC and its 33,718-tag catalog.

Verification restored the editing pilot's original source bytes through another
audited change set. The original **APVC Database Pilot** remained at generation
**1** with its original digest, and the source project files were unchanged.
The following Phase 4 pilot extends this baseline; original-project cutover and
full deployment qualification have not occurred.

## Phase 4 — reviewed releases and runtime evidence

Open **Configuration → select a repository pilot → Releases**.
The catalog is shared by Explorer, Control Designer and Graphics Designer. The
**Changes** also provides **More → Release Manager**.

### Build and review

1. Save and check in changes through Shared editing. A working draft cannot
   download or publish directly.
2. In **Build release**, select modules and/or graphics. **Validate and review**
   compiles the control modules and runs the actual
   Graphics Designer verifier in an isolated worker process.
3. Review automatically included control dependencies, validation findings, and
   links to displays outside the selected deployment. An Up link does not
   silently request deployment of the whole plant.
4. Enter a reason, acknowledge the review, and choose **Create immutable
   release**. Errors prevent release. A concurrent check-in invalidates the
   review. A release never changes after creation.

The manifest pins every captured file's object UUID, revision and SHA-256,
including module documents, graphics, authored classes, assets and supporting
project context. Only selected deployment objects and required control
dependencies are installed. Installed code classes/resources are pinned by an
application implementation digest, including Python and Qt versions; a different
build must validate a new release. No executable project asset is installed as
an application plug-in.

### Deploy and observe

1. **Create isolated runtime** gives the pilot a private controller tag store,
   publication store and separately authenticated target profile. **Start / resume
   selected runtime** reopens it; the process lock refuses duplicate launches.
2. In **Deploy / recover**, select the release and runtime. Choose control
   download, graphics publication, or both, then **Deploy selected release**.
3. The target verifies the package and uses the existing compiler, `DataBridge`,
   `StrategyRuntime`, `ControllerExecutive`, `DisplayStore` and `PvmDeployment`.
   Unselected modules keep running. A controller keylock refuses download.
4. Open **Operator Live** from the runtime window. First opening an uncached
   graphic adopts its publication. An already opened graphic and its matching
   class/asset directory remain on their accepted revision until operator
   **Refresh**. Publication alone does not redraw the process display.

Delivery jobs have distinct requested, staged, delivered, failed and cancelled
states. **Delivered** confirms target processing, not that an operator opened
the graphic or that a controller completed a scan.

| Comparison | Meaning |
| --- | --- |
| Current | Configured source bytes match the active target; control has completed a scan or the station has accepted the graphic |
| Online changes | The loaded revision matches, but the observed engineering tuning differs from its load snapshot; ordinary mode/SP movement is excluded from this badge |
| Different | The active target reports another source revision |
| Available / inactive | A publication is available but unaccepted, or a loaded module is inactive |
| Not loaded | A connected target has not reported the object |
| Unknown | No fresh target evidence; the heartbeat expires after 30 seconds |

**Configured vs running** uses target reports. An engineer's database save or
deployment request cannot supply an acknowledgment on behalf of a target.
The table initially shows reported objects, including their Unknown state when
evidence expires. Clear **Reported objects only** to include never-loaded
objects, or search by object, target or comparison status. Both engineering and
active revision numbers remain visible even when restored source bytes match.
The catalog's original Live/open-module tab retains its existing local behavior.

### Recover interrupted work

Local command records are written before release, deployment and upload requests.
**Retry pending command** recovers an unconfirmed receipt using the same command
identity. A replay cannot create another revision under that identity.

The runtime stores verified packages and a controller recovery journal locally.
Its receipt is durable before the remote acknowledgment. After a lost response,
the same running module is retained and a publication is found by its original
job identity. Restart reconstructs actual runtimes from verified bytes before
reporting them active. The new boot identity fences late reports from an older
session. Engineering-service outages leave the existing executive and accepted
station graphics operating locally.

**Retry selected failed deployment** retries its exact release after a transient
problem is corrected. A failed older job blocks later jobs. If the package must
be replaced, **Cancel selected failed deployment**, then deploy the corrected
release. Cancellation is audited; it does not undo successful partial target
changes or falsely mark the job delivered. Active downloads cannot be cancelled.
Their results and any subsequent correction remain in **Audit** and runtime
comparison. Package integrity failures are reported as failed verification;
changed local files are never silently trusted.

### Keep selected online tuning

1. Select the runtime and choose **Compare selected runtime's online tuning**.
2. Review the observation time and individual configured/online differences.
   Check only the fields to keep and enter a reason.
3. **Check in selected tuning** writes a new engineering revision through the
   existing check-in/lease service, preserving unselected parameters and unknown
   document fields. It does not automatically download that revision.

Changed selected values, a restarted/offline target, conflicting edit ownership,
or a newer engineering revision require another review. Normal movement in an
unselected online parameter does not invalidate the selected tuning. The module
and graphics must subsequently pass the usual release and deployment gates.
Comparison includes effective schema defaults and canonical PID enum values;
an omitted default or a friendly runtime label does not become a tuning change.

### Pilot scope and verification

`tools/verify_configuration_releases.py --prepare` creates a fresh isolated
APVC release pilot. Running it without arguments drives the native Release
Manager, its review and upload dialogs, the existing controller executive, and
Operator Live through the PIC-2001/U200 workflow. Private target/workspace details
are kept in `data/configuration/release_acceptance.json`; screenshots and the
acceptance result are under `logs/configuration-release-ui/`.

The isolated runtime intentionally has **no external field-I/O connection**.
Absent signals remain Bad, and ordinary scan/alarm warnings are expected. This
does not migrate or replace the existing virtual controller or attach a second
controller to its output channels. Production/LAN installation, workload
qualification, package-retention administration, coordinated controller/station
cutover and full backup/restore orchestration remain later phases. Selective
library adoption is described below.

Native acceptance on **8 September 2026** used **APVC Release Pilot
20260908-120233** (`7d44f0ed-4add-472e-91c4-9c1e13885446`). Seven control modules
and U200 were released through the actual review and deployment windows. The
run recovered a deliberately lost delivery acknowledgment without resetting
the module or duplicating publication, kept scanning through an injected
engineering-service outage, and uploaded only PIC-2001 GAIN from **1.0 to
1.125**. A second publication retained the open operator view and its pinned
resources until explicit Refresh. Restart recovered the released modules and
accepted station revision from verified local packages. The process exited
successfully with **zero Qt warnings**.

The source project digest remained
`0cd63b68627f96ed400418325779ecb3d8b1d281f85b06c02097e7382fcb4687`.
The reviewed status table showed all eight reported objects Current after
ordinary scans; online upload excluded unchanged defaults and equivalent PID
enum labels. This is isolated integration evidence, not workload or external
field-I/O qualification.

Regression verification covered **83 configuration checks**, the affected
Control Designer and Graphics Designer editor tests, all **eleven application
smokes**, and scoped Ruff checks. The false online-change badge, background-panel
routing obstruction and accepted-display retention regressions were reproduced
against their previous behavior before confirming the fixes. No shipped
course artifacts changed.

## Libraries, instance adoption and engineering history

Open **Configuration → Libraries** in Explorer, Control Designer or
Graphics Designer, then select a repository-owned project. The same shared dialog
lists authored PVMs, paired faceplates, installed PVM configuration and control
composite definitions. Filter by class or instance/display name. Selecting an
instance shows its retained class defaults, effective property values and
explicit engineer overrides.

### Update a shared graphics class

1. Before changing an existing shared class, select its unpinned instances and
   choose **Review initial pins**. Review the instances and enter a reason, then
   check in. This retains the current drawing and captures its class files,
   paired faceplates, nested classes, standards and supporting functions.
2. Edit the class in a private Graphics Designer draft and check in the change.
   An affected unpinned instance prevents the class change from being checked
   in; unrelated new classes can still be added. Repository drafts do not
   automatically refresh every linked instance when a class is saved.
3. Refresh Library Manager. **Update available** identifies consumers whose
   retained contract differs from the current class. Select only the instances
   that should adopt it and choose **Review selected adoption**.
4. Review **Selected instances** and **Class changes**, including the actual
   definition/configuration differences. Enter a reason and check in the review.
   Unselected instances keep their existing document content and retained class
   files. A paired faceplate opens against the version retained by its source
   PVM, including when different versions are open simultaneously.
5. Use **Release Manager** to validate, release and deploy the changed display.
   Check-in alone does not replace an operator's accepted display.

Instance choices and declared shape overrides survive adoption. External pipe
attachments retain their stable source-element identities. An update that
removes an attached element, invalidates an explicit choice or changes a control
composite boundary is refused for review. Unlinked graphics have independent
geometry and must be explicitly relinked before class adoption. Explicitly
detached nested graphics keep their frozen geometry when their parent adopts a
new revision. Older positional overrides are resolved against the placed member
identities before a reordered class is applied.

**Source revision set** defaults to the latest checked-in classes. Select an
earlier project generation to review adoption of that historical class contract.
This creates a new engineering revision; historical evidence is never modified.
Composite instances use their existing embedded definition snapshots and public
parameter overrides. A linked composite nested inside another linked composite
is governed by its owning class, rather than edited independently behind that
class's contract.

A class review is tied to the reviewed project generation and engineer.
Intervening changes require a fresh review. Check-in uses the existing edit
reservations, revision checks and audit writer. An unconfirmed command is kept
locally; **Release Manager → Retry pending command** recovers the original
receipt. Retained class files cannot be edited through ordinary draft check-in.

### Inspect properties, usage and revisions

The shared Engineering catalog provides **Properties**, **References**,
**History** and **Revisions**. Properties include the repository object identity,
captured revision and, for module terminals/configuration parameters, the stable
historian point identity. History is loaded on a background worker. The latest
500 object revisions include the engineer, time, change reason and path at that
revision; double-click a revision to inspect its immutable document. The
paginated service API remains available for older revision evidence.

Display previews retrieve the captured generation and retained class files.
They do not silently render an older instance using today's class contract.
Running state remains a separate observation in Release Manager.

### Follow recorded points through a rename

Released control graphs carry their repository module identity and release
context. The historian derives each module point identity from the project,
module object, block identity, member and member kind. Renaming a module or block
therefore keeps that identity; a different object reusing the address does not.
The default collected terminals and additional numeric module points selected
from Operator Live follow this rule. Bare field aliases without a repository
module identity remain address-based.

Open **Process History → Point configuration** (also available from the pen-table
context menu) to inspect a point's identity, current configuration and the
recorded paths, units/ranges, revisions and releases in the selected interval.
The first/last timestamps are observed samples for each recorded configuration,
not a claim that a continuous alias interval was recorded between them.

Open charts and saved chart groups retain point identity across rename and name
reuse. Retired identities remain available for historical queries. Each archived
sample retains its original address and configuration context. CSV/report
exports include point identity, recorded path, configuration revision and release;
units come from the recorded sample context. A historical interval containing
different units suppresses combined measurements and hides that mixed-unit trace:
select a single configuration interval to review it. No unit conversion is
inferred.

The pen table's **Current** value remains live during historical review. If its
current units differ from the selected historical configuration, the current
cell includes its own unit so the historical **Unit** column cannot relabel it.

The SQLite extension is additive: original raw sample records, timestamps,
quality and run information remain intact. When an address-based archive meets a
new repository identity, its old samples remain accessible as separate retired
address history. Existing saved groups keep that legacy history. Old samples
are not assigned a fabricated object identity or release. High-rate sample
collection continues through the existing asynchronous archive, outside
configuration database transactions.

### Phase 5 verification

The native verification tool **tools/verify_configuration_libraries.py** creates
a new isolated class/history project and private runtime. It drives Library
Manager and adoption review, releases two independently retained PVM/faceplate
versions, opens both detached faceplates in Operator Live, then renames and
downloads a recorded control loop. It checks the open chart, archived release
provenance, shared revision inspector and captured display preview. Screenshots
and a receipt are written under **logs/configuration-library-ui/**.

The final database, editing, release/runtime, library, historian and authoring
regression run passed **156 tests**. The follow-up library/history/linked-PVM
run passed **40 checks**, including the final compatibility fixes; these sets
overlap. All eleven product smoke entry points passed, as did the required
Graphics Designer/PVM lint and scoped configuration/history lint. Detached-child,
legacy-override and historical/current-unit regressions were first observed
failing and then confirmed passing.

Native acceptance completed on 8 September 2026 in
**logs/configuration-library-ui/20260908-131231/** with no captured Qt warnings.
Its isolated project is **e1ce6e11-69ce-42f2-8f12-43db6a167dba**; the receipt
records both deployed releases and the unchanged historian point identity.
The original APVC source digest remained unchanged. The captured class manager,
detached faceplates, renamed trend, recorded releases and engineering preview
were inspected visually.

This phase does not cut over the original APVC project or connect the private
test controller to external plant outputs.

## Training baselines and recovery (Phase 6)

### Capture an immutable baseline

Open **Configuration → Training → New baseline** on a
repository editing project. Select a validated release, name the baseline and
record its purpose. The release must include every control module and display
in the project. Its pinned files retain the exact project, classes, assets and
dependencies even after later engineering changes.

A configuration-only baseline is useful for an engineering exercise. To make
a repeatable process exercise, first use Operator Live's **Training → Instructor
setup and review** to capture a starting condition and save an exercise. Attach
that saved exercise to the baseline review. Its process/controller snapshot is
retained independently of mutable project files, with a checksum. The service
checks that the captured controller modules and block identities match the
selected release. Baselines cannot be edited; create another baseline to change
the exercise or starting condition.

### Create and run trainee copies

1. Select the baseline, enter a trainee project name and choose **Create isolated
   trainee copy**. This administrator action creates new repository project and
   object identities from the baseline's released files. Repeat for each trainee.
   Changes in one copy do not change another copy or its source baseline. Grant
   each trainee access to their own project using the existing identity commands.
2. Select the trainee project in Engineering Catalog. Use **Release Manager** to
   validate a complete release and deploy it to a new isolated runtime.
3. In the runtime, choose **Start isolated training process**, then **Open Operator
   Live**. Close an already open station before attaching the training process.
   This opt-in path uses the bundled in-process Azeo provider and that runtime's
   private tag store. It starts at the provider's default state; it does not
   silently restore a mutable snapshot excluded from the release.
4. Open **Training → Instructor setup and review → Load inherited baseline**.
   The inherited definition and its snapshot are checked and installed locally.
   Selecting them does not reset the process. Choose **Start** explicitly to
   restore the exercise's starting condition and begin recording.
5. Introduce an input fault, investigate the live display and trend, record
   objective evidence, clear faults and finish the session. The existing trainee
   workspace and saved-session report remain the exercise UI.

Before a baseline exercise resets the process, the runner checks its snapshot,
definition, trainee project, released module identities/digests and captured
provider build. A changed trainee configuration must be reconciled or captured
as a new baseline. A configuration clone or database restore never replays
operator writes or restores process physics by itself.

Training events and samples retain the session identity, baseline provenance,
wall-clock/simulation times and observed release configuration. Numeric module
samples use the same stable point identities as Process History. Configuration
changes observed during a session are recorded as events. Two clones can use
the same human-readable tag names while retaining separate historical identities.
The historian and session archives remain local SQLite stores; collection does
not use configuration-service requests inside control scans.

### Create and verify a recoverable backup

Open **Project Administrator → Database recovery**, or **Configuration → Recovery**.
Recovery requires a service administrator. The
service host must have its matching PostgreSQL `pg_dump` and `pg_restore`
utilities configured; the local pilot setup supplies their directory.

1. Name the backup. Optionally add each seat's **History**, **Training** and
   **Journal** archives, selecting the appropriate type for each file. Released
   runtimes store these at `history.sqlite3`, `training/sessions.sqlite` and
   `simulation/operator_changes.sqlite` below their runtime directory.
2. Choose **Create consistent backup**. Each selected SQLite archive is captured
   through its online backup API, including committed WAL contents. The service
   then captures PostgreSQL with one exported transaction snapshot. Its manifest
   inventories every configuration table, project, asset and related archive.
   Every release and baseline referenced by the frozen evidence must exist in
   that database snapshot or the backup is refused.
3. Select the completed backup and choose **Rehearse selected restore**. The
   service creates a new `azeo_rehearsal_…` database, restores the dump, and checks
   all table counts/digests plus the captured archive hashes and inventories.
   A verified receipt names that isolated database. The original database stays
   in service and no controller or simulator starts.
4. Use **Export selected backup** to retain a portable ZIP containing the dump,
   evidence and manifest. **Import exported backup** verifies its exact file
   inventory and checksums before accepting it on a recovery service. Rehearse
   the imported backup to verify actual recoverability.

Use **Retry pending command** after a lost response, then refresh the receipts.
Repeated commands reuse their original identity. An interrupted restore retains
its generated destination and resumes verification there. Incomplete rehearsals
remain visible for diagnosis. A failed or interrupted download does not replace
an existing backup or leave a completed-looking ZIP.

The consistency boundary is explicit: independently frozen SQLite archives
precede the PostgreSQL snapshot. This is a coherent evidence set linked to
immutable releases, not a claim that every workstation was stopped at the same
instant. Backups include all configuration projects, revision/audit history,
project access records and token hashes; they require administrator-controlled
storage. PostgreSQL server roles, service credentials/settings and separately
installed application/provider binaries are not included. Retain those through
the deployment's administration procedure. The pilot accepts at most 20 evidence
archives per backup and bounds archive uploads/imported contents at 2 GB.

Existing address-based historian archives remain readable and retain their
original timestamps, quality and paths. They are not assigned invented release
or point identities. A restored database is an isolated rehearsal destination;
switching service ownership, production rollback, scheduled verification, PITR
and shared-workload qualification remain Phase 7 work.

### Staged migration remains explicit

Continue importing file-owned projects as read-only shadows, reviewing their
lossless exports and then creating isolated repository editing copies. Training
clones use the same transaction and identity machinery. The original APVC
project and shipped course modules are not converted in place. Before any
future cutover, retain a verified backup/reverse export and qualify every
authoring writer and target deployment path against the new owner.

### Phase 6 verification

`tools/verify_configuration_training.py` drives the native baseline review,
creates two trainee copies, downloads separate releases, runs inherited exercises
against private in-process providers and records input-fault/recovery evidence.
It also drives the backup and restore dialogs, executes real PostgreSQL restore,
and exports/reimports the portable backup. Screenshots and the receipt are
retained below `logs/configuration-training-ui/`.

Native acceptance completed on 8 September 2026 in
`logs/configuration-training-ui/20260908-142851/`. Its instructor project is
`b55c4b53-ae22-4295-979e-a2236ba8176c`; the receipt records both trainee projects,
separate point/session identities, six evidence archives and the verified
isolated restore. Portable export/import and clean shutdown passed with no
captured Qt warnings. The original APVC source digest remained unchanged.

The configuration, library, runtime, historian, training, workbench and Graphics
Studio regression run passed **209 tests**. A separate shutdown regression
verifies that closing an active runtime saves the session while its process
clock is still available. Restart with automatically discovered first-out
signals and interrupted backup downloads were observed failing against the
previous behavior, then confirmed passing after their fixes.

All eleven product smoke entry points passed, including the APVC boot with 151
scanning modules and healthy Virtual I/O. Required Graphics Designer/PVM lint and
the scoped configuration/training/recovery lint passed. Shipped strategy files
have no changes from this verification.

A final native `--review-station` run revalidated the unchanged trainee
configuration with the final application build, recorded recovered PID quality,
and checked closing an active exercise before detaching its process clock. The
recovered Operator Live view was inspected visually; the run exited cleanly
with no Qt warnings. Its screenshot is `trainee-b-operator-recovered.png` in the
same acceptance directory. Expected controller feedback-cycle advisories and
the deliberately introduced process alarms/Bad-quality messages remain in the
native log; these are separate from application/Qt errors.
