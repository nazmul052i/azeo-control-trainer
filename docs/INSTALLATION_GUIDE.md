# Azeo Installation and Administration Guide

Document ID: AZEO-IG-001 · Edition 1 · September 2026

This guide covers the Windows x64 installer workflow, application selection,
first use, maintenance and offline support. The public source preview does not
include a qualified installer; use the repository README to run from source.
When using a separately qualified package, open **Azeo Help Center** from the Start menu
or an application's Help menu to search this guide alongside the user manual,
PA Designer reference, graphics tutorials and administration guides.
**System information** identifies the actual version, build/source commit,
build time and state, distribution channel, runtime, licence, installed
components and support paths. It can be exported locally and does not send
project contents or environment variables.

The initial installer is a qualification build. Public commercial release
requires publisher signing and the clean-machine acceptance described in the
release engineering guide. A local test on a development workstation does not
establish support for every Windows configuration.

## 1. System requirements and deployment planning

### 1.1 Workstation requirements

| Item | Minimum | Recommended |
| --- | --- | --- |
| Operating system | Windows 10 version 1809 (build 17763) or later, x64 | Windows 11 x64 on current servicing. The 0.3.0 build was qualified on Windows 11 Pro build 26200. Qualify your organization's Windows build before rollout. |
| Processor | Dual-core x64, 2 GHz | Quad-core x64 at 3 GHz or faster: the plant simulator, the controller scan and the Operator Station run at the same time. |
| Memory | 8 GB | 16 GB for the full training plant with several engineering windows open. Measured on the qualification workstation: Explorer with Control Designer over the training area uses about 190 MB, about 330 MB with Graphics Designer and the Operator Station added, before the 612-point virtual plant. |
| Disk | 2 GB free for the installation: Setup is 290 MB and the installed version directory about 930 MB. Allow 4 GB more for extraction, the previous version, project copies and logs. | SSD. Allocate historian, backup and training-archive capacity separately. |
| Display | 1920 × 1080 at 100 % scaling | 1920 × 1080 or larger; 125 % to 200 % scaling is supported, verify it during acceptance together with remote sessions and multi-monitor layouts. |
| Graphics | Any GPU with a current Windows driver; Qt falls back to software rendering without one | A driver-backed GPU for the Operator Station on large displays. |
| Architecture | Intel/AMD x64 only | Windows on ARM emulation, 32-bit Windows, macOS and Linux are not qualified. |
| Permissions | A standard user account. Setup installs for the current user under `%LOCALAPPDATA%\Programs\Azeo` and creates no service, driver or machine-wide firewall rule. | For several Windows users, install for each; workspaces and preferences stay separate. |
| Bundled runtime | Setup includes a private CPython 3.13, Qt 6.10 (PySide6 6.10), the required Python packages, the native plant core and the Visual C++ runtime DLLs it needs. No separate Python, pip, Visual Studio or Qt installation is required. | |

### 1.2 Network ports and security prerequisites

The default training configuration and the Help Center work offline. The
table lists what listens or connects once a project enables it, so a
workstation firewall can be configured deliberately.

| Service | Direction | Port | Default | Authentication |
| --- | --- | --- | --- | --- |
| Controller discovery responder | listens, UDP | 42049 (`controller.discovery_port`) | On, all interfaces. Answers well-formed discovery requests with the controller identity only. | None; read-only identity. |
| Modbus TCP server | listens | 5020 (`controller.modbus_server.port`) | Off. Enabled per project; binds all interfaces unless `host` is set. | None. Modbus has no authentication; restrict it with the firewall. |
| OPC UA server | listens | 4840 (`controller.opcua_server.port`) | Off. Enabled per project; localhost only unless `host` is set. | Anonymous endpoint; keep it on localhost or a controlled network. |
| Modbus TCP and OPC UA field transports | connects | device-defined | Off. Enabled per project. | As configured on the device. |
| Configuration service | listens | 8766 | Off. Started by an administrator with `azeo-configuration serve`; localhost only. | Bearer tokens issued per engineer; database credentials never leave the service host. |
| PostgreSQL configuration database | connects | 5432 | Off. Only with the configuration service. | Database credentials held by the service. |

Other prerequisites:

- Release builders can audit bundled packages against the PyPI advisory
  database with `tools/audit_dependencies.py`. Review the audit output for
  the exact build being deployed.
- Graphics scripts run in a restricted engine with no file, network or process
  access. Project files are engineering data: a project names the I/O provider
  it loads, so take projects only from a trusted source.
- Real-time antivirus scanning of the workspace slows project loads and
  historian writes; exclude `%LOCALAPPDATA%\Azeo\Workspace` where policy allows.
- An evaluation build carries a time-limited licence; section 16 describes it.

### 1.3 Deployment planning

Choose an installation preset based on the work this workstation performs.
For multiple Windows users, install for each user; their default workspaces and
preferences are separate. This first installer does not provide an all-users
service deployment. Do not run a shared controller project concurrently from
two independent Azeo processes unless the project's deployment explicitly
supports that arrangement.

## 2. Choose your applications

| Preset | Included applications | Typical user |
| --- | --- | --- |
| Full | All six applications, focused Plant Simulator launcher, SDK, shared runtime and Help | Training/integration workstation |
| Engineering | Explorer, Control Designer, Graphics Designer and PA Designer, plus shared runtime and Help | Engineer authoring projects and procedures |
| Operator Station | Operator Station, shared runtime and Help | Operator using a prepared project |
| Custom | Your selected applications, required shared runtime and Help | Workstation with a specific role |

| Component | What it enables | Important dependency |
| --- | --- | --- |
| Explorer | Project navigation, application handoffs and commissioning tools | Shared runtime |
| Control Designer | Module editor, compilation, download and debugging | Shared controller runtime |
| Graphics Designer | Display, PVM and faceplate authoring and publication | Shared display models and bindings |
| PA Designer | Visual PA workflow authoring, tag mapping and revision validation | Shared tag database and PA block library |
| Operator Station | Published displays, faceplates, trends, alarms and operator-guided PA execution | Shared controller/PA runtime; the authoring applications are optional |
| Simulation Workbench | Process clock, disturbances, snapshots and focused Plant Simulator UI | Bundled native simulator and provider |
| C++ SDK | Headers, import library, DLL, CMake integration and examples | A compatible C++ build toolchain is needed only to develop a host application |
| Offline Help | Searchable task guides, block reference and system information | Always installed |

Application selection controls supported launchers and application handoffs.
The current architecture uses a hidden Control Designer host to execute the
controller. Shared implementation files and the native plant provider therefore
remain in the common payload even when their editors are not selected. An
Operator-only installation does not expose the Control Designer faceplate action.
Component selection is not user authorization, licensing or a security sandbox.

The bundled plant can support an Operator Station training session without the
Simulation Workbench editor. An engineer who wants to open Operator Station or
Simulation Workbench should select those applications in Custom or Full.

## 3. Install Azeo

**Before you begin:** obtain the complete Setup EXE and its release information
from your organization's approved distribution location. Confirm the version,
architecture and publisher. A SHA-256 checksum detects file changes only when
compared with a trusted release record; it does not replace publisher signing.

1. Close every Azeo application after saving work. End or abort active training
   procedures deliberately and allow their audit records to finish.
2. Open **Azeo-Setup-x.y.z-x64.exe**. Select the destination and installation
   preset. The default destination is `%LOCALAPPDATA%\Programs\Azeo`.
3. On **Select Components**, select the applications needed here. Shared runtime
   and Help are required. Review the disk-space estimate before continuing.
4. Choose whether to create desktop shortcuts. Start menu entries are created
   for selected applications and Help. The **Azeo > User Guide** folder contains
   the suite manual and all six product-specific PDF manuals, including on an
   Operator Station-only installation.
5. Review the installation summary, then select **Install**. Setup copies its
   bundled dependencies; it does not download Python packages at first launch.
6. Open **Azeo Help Center** from the completion page or Start menu. Review
   **System information** and complete the next section before general use.

**Expected result:** Windows Installed Apps contains Azeo; the Start menu shows
the selected applications and seven PDF links under **Azeo > User Guide**.
The Help Center opens without a controller or network connection. Unselected
application launchers are absent.

**Recovery:** if Setup reports Azeo is running, close its windows normally and
wait for shutdown to finish. Do not force an update over a live controller.
For a failed copy, preserve the Setup log and rerun the same installer after
resolving the reported permission or disk problem. Do not copy individual DLLs
from another release into this installation.

## 4. First launch and acceptance

**Before you begin:** use a disposable training project or a verified project
backup. A displayed value is not evidence of Good quality or a running scan.

1. Open a selected application from the Start menu. On first launch Azeo copies
   its starter projects into `%LOCALAPPDATA%\Azeo\Workspace`.
2. In Help, open **System information**. Confirm the application version,
   components and writable workspace. This should differ from the application
   directory under `Programs\Azeo`.
3. Open Explorer if installed. Confirm the expected project, controller identity,
   modules and published displays. The bundled default is
   `AzeoPlantVirtualController`; project names may differ after local editing.
4. For an Explorer training session, use **Tools > Virtual I/O > Start Simulator**.
   Explorer preserves the project's manual startup policy. Operator Station and
   the focused Plant Simulator launch prepare their configured process/runtime.
5. Confirm controller scan activity and Good quality on a known measurement.
   Open a published overview and then an equipment faceplate. Verify the
   displayed module and tag belong to the intended project.
6. Inspect a live trend. Check timestamps advance and quality is meaningful.
   Do not adjust a setpoint solely to test an unapproved project.
7. Open Help, search `stable_for_sec`, and open a matching PA block topic.
   In an editor, verify F1 and right-click **Block Help** still open the
   application-specific reference.
8. Save a harmless change in a project copy, close all applications, reopen and
   confirm the change persists. Record version, Windows build, selected
   components and results in your rollout acceptance record.

**Expected result:** launch succeeds without development tools; project data
persists outside the install tree; the correct controller drives the graphics.
Bad quality with a stopped or unconfigured provider is an actionable condition,
not a theme defect to hide.

## 5. Find help while working

The Help Center is modeless: keep it beside the application while following a
task. Type several words in the search field to require all of them in a topic.
Use the contents tree for browsing, Back/Forward for recent topics, and **Print
topic** for a local printer or the Windows PDF printer. Clearing search restores
the full contents. Search does not send project information to a server.
For offline reading or printing outside the Help Center, open a product manual
from **Start > Azeo > User Guide**. The links target PDFs installed in the
current version's `output\pdf` directory; no browser or network is required.

| Task | Starting reference | What to verify |
| --- | --- | --- |
| Build/download a control module | User Manual, Control Designer and Virtual I/O chapters | Compile status, module download, scan activity, quality |
| Create a display or faceplate | Graphics tutorials; Graphics Designer F1 | Correct bindings, Test results, publication and station retrieval |
| Operate a prepared unit | User Manual, Operator Live, alarms and trends | Published revision, active unit, mode/ownership and quality |
| Build a PA | PA Designer guide and right-click Block Help | Mapping, block parameters, validation and saved revision |
| Configure shared memory tags | PA Designer tag/memory topics and Control Designer Tag Database | Persistent typed value, range and correct project |
| Test disturbances/snapshots | Simulation Workbench guide | Scope, simulation time, selected snapshot and restoration result |
| Administer shared configuration | Configuration Database guide | Server profile, permissions, checkout/release and backup status |

Context help describes the selected block's properties, examples, timing and
failure behavior. The suite manual supplements it with end-to-end tasks.
An external reference link may open the browser only when selected; core help
and illustrations are installed locally.

## 6. Procedure automation and operator control

PA Designer authors a reviewed sequence; Graphics Designer authors its HMI
PVM and faceplate. Operator Station runs the procedure against the current tag
store. Installing PA Designer is not necessary merely to use an already
configured PA faceplate in Operator Station.

**Authoring workflow:** open the project, create a procedure, insert blocks,
map logical tags using Tag DB search, configure typed memory/input values,
validate, save a revision, bind a PA PVM and its faceplate in Graphics Designer,
test, publish, and retrieve it at the intended station.

**Multiple conditions:** give each row a clear identity. ALL requires every
row; ANY allows one qualifying row. A grouped expression such as
`C1 and (C2 or C3)` states a mixed requirement. Use `Wait for condition` for
continuous timed qualification. Configure row hold times and the combined hold
time deliberately; they are different requirements.

**Worked training example:** C1 checks a mapped pressure is below a reviewed
training limit; C2 checks pump-stopped feedback. Select ALL and require the
combined result to remain true for 5 simulation seconds. A false observation,
unusable quality or pause interruption resets qualification. A timeout is the
maximum wait, not the hold duration. Use the block reference for the exact
on-timeout and on-failure behavior. The numerical limit must come from the
training project's operating criteria, not from this example.

**Memory values:** a shared memory tag is stored in the project's database and
can be used by PA and Control Designer. Operator Input validates its type, choices
and range before persisting a result. A local legacy variable resets for the
run. Do not use the two as interchangeable storage.

**Operator workflow:** open the PA faceplate; inspect revision, step, conditions,
timers and relevant trends before starting. Respond to instructions and verify
readback in the equipment faceplates. Pause/resume affect guidance. Abort ends
the advisory procedure; it does not shut down equipment. Operators retain their
normal equipment controls at all times. A `set tag` PA step records a proposal;
this runtime does not automatically execute that process write.

**Tuning:** only parameters explicitly exposed by the authored faceplate are
tunable. Next-run changes apply to a later run; permitted live timer changes
restart qualification. Review the live condition/timer table after tuning.
Held, Failed, Aborted and Completed are distinct terminal outcomes. A Held
outcome requires review; it is not the same as an active run's Pause state.

## 7. Data locations and moving from a repository or ZIP

| Location | Purpose | Update/uninstall behavior |
| --- | --- | --- |
| `%LOCALAPPDATA%\Programs\Azeo\versions\x.y.z` | Versioned code, private runtime, immutable starter content and manuals | Setup owns these files |
| `%LOCALAPPDATA%\Azeo\Workspace\projects` | Working projects, memory databases, displays and procedure records | Preserved |
| `Workspace\src\strategies` | Writable copies of shipped training areas | Preserved; updates do not merge over local edits |
| `Workspace\data` and `Workspace\logs` | Runtime data and journals | Preserved |
| Windows user preferences under Azeo | Window and station preferences | Preserved |
| Project paths explicitly opened elsewhere | User-owned engineering data | Outside Setup's ownership |

`AZEO_WORKSPACE_DIR` can select another local writable workspace. Configure it
before launch. Keep it outside `Programs\Azeo` and any version directory.
Changing it selects another workspace; it does not migrate existing data.

To move from a source checkout or portable ZIP, close the old application and
back up the complete project, including its databases and runtime evidence.
Use Explorer's Project Administrator to copy/restore it into the new workspace
or open the existing project by its full path. Verify the selected project and
provider paths before going online. Setup never guesses which repository or ZIP
contains the newest work and never imports your old projects automatically.
Keep the original until acceptance succeeds.

## 8. Update and repair

**Updates are versioned offline update installers of two kinds.** A full Setup
(`Azeo-Setup-<version>-x64.exe`) installs any newer version and keeps the
selected applications and the workspace. A patch Setup
(`Azeo-Patch-<new>-from-<base>-x64.exe`, a few megabytes) upgrades exactly the
base version it names: it copies the unchanged files from the installed base
into the new version folder, installs the changed files, verifies the whole
folder against the release manifest before switching the shortcuts and the
version record, and keeps the base folder for rollback. A patch keeps the
installed location and application selection (use the full Setup's Modify
to change them) and refuses, leaving the installation unchanged, when a
different version is installed, a base file is damaged or an Azeo process
is running; the refusal names the full Setup to use instead. Neither kind
requires uninstalling or copying dependencies by hand. Automatic online
checks are not provided in this edition; obtain updates through the approved
release channel.

**Before updating:** read the target release notes and supported upgrade range;
save work; complete/abort training runs; close every Azeo window; make and verify
a workspace backup. Take the shared configuration database's own backup if that
service is in use. A copy of local projects is not a server database backup.

1. Run the newer Setup EXE, full or patch. Review the retained destination
   and selections (a patch shows neither; it keeps both).
2. Add/remove applications if needed, then install. Setup writes the new code
   under its version directory and updates the current Start menu shortcuts.
3. Launch Help and verify the new version. Run the first-launch acceptance steps
   against a project copy before returning the workstation to training use.
4. Check memory values, published revisions, PA configuration, history and
   provider operation. Retain the pre-update backup and previous Setup package.

**Repair:** rerun the same version's Setup. It restores packaged files and
recreates selected shortcuts. It does not reset user projects or databases.
This is also the way to add or remove applications after installation: choose
Custom and change the selection. Removing an application removes its current
entry point; shared dependencies remain because another application may need
them. Older version payloads are retained until uninstall.

Setup refuses an ordinary downgrade. Version-directory retention alone is not
an automatic rollback guarantee, and launching an old EXE against newer data
is not a safe rollback procedure. Follow the next section.

## 9. Backup, recovery and rollback

**Backup procedure:** close all processes using the project, including shared
database tools. Copy the complete workspace and any externally located projects
to a dated backup outside the install directory. Include SQLite companion files
if they remain after shutdown. Preserve registry-based Azeo preferences if your
organization requires them. Back up a remote PostgreSQL service with its own
supported backup tool. Record application version and the backup location.

**Verify:** open a disposable restored copy; confirm modules, memory tags,
display revisions and PA records are present. A backup that has never been
restored has not passed recovery acceptance.

**Failed update:** keep the Setup log. If installation did not finish, rerun the
same target version after correcting the failure. Do not delete the workspace
to resolve an application-file error. If the application fails after a completed
update, preserve the diagnostic logs before repair or rollback.

**Controlled rollback:** close all Azeo windows; preserve a copy of the current
workspace for investigation. Restore the verified pre-update workspace to a
separate path, configure `AZEO_WORKSPACE_DIR` to that restored location, and run
the previous trusted Setup with `/ALLOWDOWNGRADE=1`. This explicit maintenance
override allows replacement of the current shortcuts with that previous version.
Validate the restored workspace before use. Do not reuse a database migrated by
a newer release unless its compatibility is explicitly documented.

The workspace carries a format marker. This release accepts format 1 and refuses
unknown formats instead of silently rewriting them. It introduces no automatic
project/schema migration. Future incompatible migrations require a separately
qualified backup/upgrade/restore procedure in their release notes.

## 10. Uninstall and remove data deliberately

Close Azeo and use **Windows Settings > Apps > Installed apps > Azeo > Uninstall**.
Setup-owned version files and shortcuts are removed. User workspaces, external
projects, history and Windows preferences are retained for reinstall or recovery.

To remove user data as well, first verify and archive anything you must retain.
Then remove the specific workspace and user preferences through your normal
administration process. There is no automatic “delete all projects” step in
Uninstall. Reinstallation reconnects to the retained default workspace.

## 11. Troubleshooting by symptom

| Symptom | Check first | Corrective action and acceptance |
| --- | --- | --- |
| Setup says Azeo is running | Open Azeo windows; pending PA audit shutdown | Close normally, wait, retry. Confirm no active runtime before updating. |
| Application missing from Start menu | System information/component selection | Rerun Setup, select Custom and the application. Verify its launcher appears. |
| Control Designer action absent on an operator faceplate | Control Designer component not selected | Expected for Operator-only. Add the editor only if this workstation needs it. |
| Missing DLL, bridge import error or bad image | Full installation/version/architecture | Repair using the matching x64 Setup. Never rename a cp313 bridge for another Python ABI. |
| A window fails before Help opens | `Workspace\logs\startup.log`; `%LOCALAPPDATA%\Azeo\Logs\startup-error.txt` | Preserve original error, repair if files are missing, verify workspace permissions. |
| Mixed dark/silver panels or blank text | Actual application version; Windows scaling/theme | Restart on the current build, record screenshot and DPI settings. Do not patch Qt with another release's DLLs. |
| Tags show Bad or values stop advancing | Provider state, controller scans, connection logs | Start/configure the intended provider. Verify Good quality and advancing timestamps. |
| Empty procedure library | Selected project and saved PA revisions | Open the correct project or save a validated procedure. A new project need not have any procedures. |
| PA condition never qualifies | Tag mapping, quality, each row, ALL/ANY expression, hold/timeout and simulation clock | Inspect the faceplate condition table and per-block help. Correct the reason instead of bypassing it. |
| PA timer resets | False/Uncertain observations, pause/resume or live tuning | Continuous True is required; confirm observation history and stable process conditions. |
| Memory tag appears to revert | Project/workspace identity; shared vs local variable | Verify the shared database and persist action. Reopen that same project. |
| Graphics edits do not appear at station | Saved draft vs published and retrieved revision | Publish/validate, retrieve for the station and refresh. Saving alone does not deploy. |
| Database busy | Another process/editor/backup holding the database | Complete or stop the competing operation normally, then retry. Runtime reports Bad rather than waiting indefinitely. |
| Network feature unavailable | External service/profile/certificate/endpoint | Configure the required service; installing Azeo does not provision it. |
| Update opens an unexpected project | Workspace override and project registry | Check System information and correct the selected workspace/project. Existing projects are never overwritten by new starter content. |
| Disk fills over time | Process history, audit, backups and retained versions | Apply your retention policy, archive required evidence and verify a backup before deleting data. |

## 12. Collect support evidence

An installed launcher that fails before the workspace is available writes its
last startup failure to `%LOCALAPPDATA%\Azeo\Logs\startup-error.txt`. Normal
startup and product logs remain under the selected workspace's `logs` directory.

1. Record what you were doing, the expected result and the actual result.
   Include the local time/time zone and exact error text.
2. Open Help > **System information** and **Export system information**.
   Review the JSON before sharing; it includes local installation/workspace
   paths, OS, Python, Qt, version and selected components.
3. Copy relevant application and Setup logs. A minimal project copy or screenshot
   can help reproduce an issue; remove secrets and unrelated operating data.
4. Include whether you ran from an installed application, source checkout or ZIP,
   and whether the issue survives a restart or occurs only after a specific action.

Nothing is uploaded automatically. The system-information report does not
contain project files, connection credentials or process values. Logs may
contain tag names, paths and operational events; review them before sharing.
Your organization supplies the support contact and release channel.

## 13. Unattended installation

Administrators can use Setup's silent mode after interactive acceptance:

```powershell
.\Azeo-Setup-0.4.0-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /TYPE=operator /LOG="C:\DeployLogs\azeo-install.log"
```

The 0.4.0 feature release uses a full Setup because its private-runtime
dependency set changed. Setup retains the prior component selection during an
upgrade unless the administrator deliberately chooses another selection.

Create the log directory first. Exit code 0 means Setup completed; it is not a
runtime acceptance result. Nonzero means failure or an interrupted installation;
retain the log. The installer does not automatically start applications in silent
mode and does not force-close a running controller.

For a custom selection, use `/TYPE=custom` and a comma-separated component list,
for example `/COMPONENTS="explorer,pa_designer,operator_station"`.
Required runtime/help components remain selected. The stable application names
are `explorer`, `control_designer`, `graphics_designer`, `operator_station`,
`pa_designer`, `simulation_workbench`; the integration component is `sdk`.
Use `/DIR="D:\Applications\Azeo"` to choose the installation root. This changes
the program location, not the workspace. Deploy in the intended user's context.

## 14. Native integration and compatibility

Select the SDK component to install the C++ headers, import library, matching
`azeocore.dll`, runtime dependencies and examples under the version's `sdk`
directory. Use the [Native Plant Core guide](NATIVE_PLANT_CORE.md) and SDK README
for API contracts, ownership, threading and build examples.

A C++ host needs the matching architecture and exported interface. A Python host
also needs a bridge built for its interpreter ABI: `_azeocore.cp313-win_amd64.pyd`
means CPython 3.13 on Windows x64. The bundled applications already include their
matching interpreter. Copying the bridge into a different Python version is not
a supported deployment method. Keep the DLL, bridge and dependencies from one
qualified build together; do not place them into System32.

Upgrading Azeo does not automatically update external host applications or their
copies of the SDK. Rebuild/test the host against the intended SDK and redeploy
through that application's release process.

## 15. Release status and limits

This edition provides optional applications, bundled runtime, local Help,
per-user workspace separation, repair, uninstall and offline versioned updates.
It does not provide automatic online updates, binary-delta patching, an
all-users Windows service installer, user licensing or an activation server
(the evaluation licence in section 16 is a time gate only), a production SIS,
or a hard-real-time runtime guarantee. See [Release notes](RELEASE_NOTES.md)
for the particular build's verification and remaining release gates.

Project configuration releases are separate from software updates: publishing
a display or releasing controller configuration does not update installed code.
Keep software version, controller configuration and published display revision
in your workstation acceptance record.

## 16. Evaluation licences

An evaluation build ships a signed licence file, `license\azeo.lic`, beside
`installation.ini` in the version directory (`versions\0.4.0\license\azeo.lic`,
or the root of a portable copy). Builds made without a licence file are
unrestricted. A build that carries one refuses to start any application
without a valid licence; the Help Center always opens.

- **Validity.** The licence names its licensee, an issue date, a last valid
  date and an evaluation length (seven days for the standard evaluation). The
  evaluation runs for that length from the first launch on the workstation and
  never past the last valid date. **System information** in the Help Center
  shows the licence, its expiry and the days remaining; the application log
  records the same line at every start.
- **Warnings.** Two days before expiry each application shows a reminder at
  start. After expiry a dialog names the licence and the reason, and the
  process exits with code 3, so a script can tell a licence stop from a crash.
  A scripted run that must never wait on a dialog sets `AZEO_UNATTENDED=1`;
  the reason is then logged and the exit code is the only signal.
- **Clock rules.** The first launch and the last use are recorded in
  `%LOCALAPPDATA%\Azeo\Workspace\license\state.json` (beside the licence for
  a portable copy), keyed to the licence, the workstation and the Windows
  user. A clock set back behind the last use refuses to start until it is
  corrected. Deleting the record restarts the count only up to the last valid
  date.
- **Renewal.** Obtain a new `azeo.lic` from your Azeo contact and copy it over
  the old one, or install the build that carries it; a licence-only change
  needs no reinstall. A licence bound to a version runs only that version.
- **Limits.** This is an evaluation time gate, not copy protection or user
  licensing: there is no activation server, no user role and no feature
  switch, and it does not replace publisher signing, redistribution review
  or clean-machine acceptance for the exact build being deployed.
