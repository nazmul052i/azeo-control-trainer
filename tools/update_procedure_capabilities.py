#!/usr/bin/env python3
"""Regenerate the Procedure Automation capability document from its ledger."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.procedures.capabilities import (  # noqa: E402
    block_capabilities,
    capability_inventory,
    feature_capabilities,
    markdown_inventory,
)


def _counts(rows) -> str:
    counts = Counter(row.status.value for row in rows)
    order = ("IMPLEMENTED", "COMPOSED", "PARTIAL", "MISSING", "DEPARTURE")
    return " Â· ".join(f"{counts[key]} {key.lower()}" for key in order if counts[key])


def document() -> str:
    blocks = block_capabilities()
    features = feature_capabilities()
    inventory = capability_inventory()
    header = f"""# Procedure automation parity status

This document is generated from
`src/azeo_control_trainer/core/procedures/capabilities.py`. It covers every
block in the procedure comparison catalog plus builder, operation,
monitoring, connectivity, governance, capacity and legacy integration
features. Change the executable ledger, then run this tool; do not hand-edit
the generated matrix.

**{len(blocks)} comparison blocks Â· {_counts(blocks)}**

**{len(features)} wider features Â· {_counts(features)}**

**{len(inventory)} total capabilities Â· {_counts(inventory)}**

## Status meanings

- **Implemented** â€” a tested Azeo caller reaches the behavior.
- **Composed** â€” the outcome is assembled from reviewed Azeo primitives or
  an existing shared subsystem.
- **Partial** â€” a bounded subset is implemented and the omitted behavior is
  stated.
- **Missing** â€” a parity gap remains.
- **Departure** â€” Azeo intentionally uses a different, documented product or
  security boundary.

All 38 comparison blocks are first-class, versioned palette entries and have
executable runtime behavior. Process outputs require explicit operator
authorization, pass through Operator Station's existing checked-write service,
and require a later actual-feedback condition. Procedures never open their own
controller transport. Legacy script, ActiveX and COM blocks use reviewed safe
expressions, allowlisted adapters and the host connector instead of arbitrary
desktop code execution.

The matrix is tested against `consolidated_catalog.yaml`, so adding a catalog
block without classifying and implementing it fails the parity suite.
Commercial feature names identify comparison targets only; this project does
not include or reproduce Yokogawa source code.

Regenerate this document with:

```powershell
$env:PYTHONPATH = "src"
D:\\development\\GitHub\\vpy\\Scripts\\python.exe tools\\update_procedure_capabilities.py
```

See `PROCEDURE_PARITY_GAP_REPORT.md` for the remaining wider-feature gaps and
the rationale for deliberate departures.

"""
    matrix = markdown_inventory()
    return header + matrix[matrix.index("## Blocks"):]


def main() -> int:
    path = ROOT / "docs" / "PROCEDURE_PARITY_STATUS.md"
    path.write_text(document(), encoding="utf-8")
    print(f"updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
