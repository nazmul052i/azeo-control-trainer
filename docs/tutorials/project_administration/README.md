# Engineering Project Administration

The **Azeo Project Administrator** manages the whole engineering project, not
an individual control area or display. Open it from **Explorer > File > Project
Administrator…**, from the application toolbar, or from the system-node context
menu.

The registered-project table shows the startup selection, schema revision,
control-module and display counts, last modification time, integrity state, and
canonical directory. Bold text identifies the project used by the current
session. **Active** identifies what a new Trainer process will open.

## Create a project

Choose **New…**, enter a canonical project name and description, then choose
one of two configurations:

- Leave network preservation off for a clean project with empty control,
  sequence, display, Virtual I/O scenario, and documentation folders.
- Enable network preservation and name an existing project to copy its
  controller nodes, assignments, controller/I/O provider declarations, and
  console selection. Control modules and displays are not copied.

The second workflow is useful when the physical or virtual system topology is
already commissioned but a new configuration database is required. A new UUID
is assigned; the source project remains unchanged.

## Copy and rename

Select a project and choose **Copy…** to create an independent complete copy.
Control modules, displays, PVM class revisions, deployment data, I/O catalog,
scenarios, and project documents all follow the copy. The copied project gets a
new UUID and records its source.

**Rename…** changes both the project directory and canonical project name. If
the project is the startup selection, the registry is updated transactionally.
The currently open project cannot be renamed because live editors and runtimes
retain absolute paths for the session.

## Select the startup project

Select a row and choose **Set Active**. This updates the canonical
`projects/_registry.json` atomically. It does not switch the project underneath
open Control Designer, Graphics Designer, or operator windows. Close the current
Trainer session normally and launch again to open the new active project.

## Verify and migrate

**Verify Selected** checks the project document shape and confirms every module
declared by an area exists. It does not compile or download control logic; run
the Control Designer verifier and Total Download workflow for those checks.

**Migrate** previews metadata-only schema changes before writing. The current
migration adds a schema version, stable project UUID, and canonical name to
legacy projects. Existing control, graphics, provider, and deployment data is
not rewritten.

## Back up and restore

Select a project and choose **Backup…**. The result is one
`.azeoproject` archive containing:

- `_project.json` and controller/network assignments;
- control, sequence, and equipment modules;
- HMI drafts, published revisions, PVM classes, and workstation deployment;
- Virtual I/O catalogs, snapshots, and saved commissioning scenarios;
- project-local documentation and settings.

Each file has a SHA-256 checksum in `manifest.json`. The archive is written to
a temporary file, flushed, and atomically renamed only after it closes
successfully.

Choose **Restore…** and select an archive. Restore rejects absolute paths,
parent traversal, a missing/incorrect manifest, duplicate destination names,
and checksum failures. It builds the project in a staging directory and makes
it visible only after full verification. Restoring never overwrites an existing
project; supply a new name when restoring a second copy.

## Register and deregister

**Register…** imports an existing project directory containing
`_project.json` into the canonical projects directory through a staging copy.
The source is not modified.

**Deregister** is intentionally recoverable: it moves the project into
`projects/_deregistered/<name>-<timestamp>`. It does not erase it. The active or
currently open project cannot be deregistered; select/close it first. To recover
one, use **Register…** and select the archived directory.

## Operational checks

After creating, copying, restoring, or registering a project:

1. Run **Verify Selected**.
2. Set the intended project Active and restart the Trainer.
3. Open Control Designer and run the complete verifier.
4. Confirm controller and I/O assignments in Explorer.
5. Start Local Virtual I/O only if the project declares it.
6. Open Graphics Designer and confirm the correct display drafts and revisions.
7. Perform a staged Total Download before placing modules on scan.

Project Administrator actions are written to the application audit log. File
operations do not silently overwrite a registered project, and there is no
permanent-delete button in the application.
