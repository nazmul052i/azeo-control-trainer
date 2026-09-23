# P&ID symbol asset provenance

The artwork and extractor in this directory originated in the browser-based
HMI Graphics Drawing project. Only the reusable asset subsystem was promoted
into Azeo's core HMI; the separate editor/runtime implementation was excluded.

| | |
|---|---|
| Upstream | HMI Graphics Drawing asset project |
| Commit | `009f6512d8303488e4a3506e2f647bdf68bb1af9` |
| Upstream date | 2026-08-14 |
| Promoted to core HMI | 2026-08-28 |
| Licence | see `LICENSE` |

## Included

- standalone ISA and P&ID SVGs;
- editable source artwork;
- the deterministic extraction/manifest generator;
- connection and embedded-data manifests;
- the upstream licence and symbol-specific documentation.

Generated SVGs should stay reproducible from `source/`. Make artwork or
extractor changes in the recorded upstream project when practical, copy the
same asset subset, update the commit above, regenerate, and run
`tests/_smoke_hmi_core.py`.

The core application reads these files only through
`core/hmi/pvms/symbols.py`. No editor JavaScript or alternate display document
format is required at runtime.

## Local extractor corrections — 2026-09-13

The retained extractor now locates gate-valve inlet/outlet ports on the bow-tie
flow axis, including actuator variants. Centrifugal suction mouths use their
centers, round exchangers share exact axes, and kettle-reboiler head/shell ports
use their respective centers. Both generated SVG sets and manifests are rebuilt
from this source. The source artwork and licence are unchanged. Validate these
corrections with `tests/test_symbol_process_centerlines.py` and the HMI core smoke.
