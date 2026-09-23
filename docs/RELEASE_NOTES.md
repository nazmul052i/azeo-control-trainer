# Azeo release notes

## Public source preview

This repository is a source preview of the Azeo Control Trainer suite. The
Python package currently reports version 0.4.0. This repository does not
publish a qualified installer, signed executable, or production support claim.
The [Installation Guide](INSTALLATION_GUIDE.md) describes the Windows packaging
and administration workflow; use the [README](../README.md) to run from source.

The suite includes Explorer, Control Designer, Graphics Designer, Operator
Station, Simulation Workbench and PA Designer. The sample plant and authored
projects are intended for training and simulation, not a live safety or
industrial-control deployment.

### Notable work in this source preview

- Control Designer supports reusable module classes and linked instances,
  compilation, download, online monitoring, and a simulated controller.
- Graphics Designer supports display templates, PVM classes, faceplates,
  operator bindings, scripts through a restricted runtime, and publication to
  Operator Station.
- PA Designer supports versioned procedure blocks, workflow authoring,
  validation, tag mappings, and operator-facing procedure graphics.
- Simulation Workbench coordinates the plant simulator, controller, snapshots,
  and training playback.
- Help includes product guides, tutorials, and system information.

### Release qualification

An installer should not be represented as a production release until the exact
build has passed dependency and redistribution review, publisher signing,
clean-machine installation, upgrade and rollback checks, and application
acceptance tests. Those checks are build-specific; this source snapshot does
not assert that a future installer has passed them.

Workspace format is 1. Back up a workspace before using it with a newer build;
newer display metadata may not be editable by an older build without loss.
