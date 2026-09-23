#!/usr/bin/env python3
"""Regenerate the HMI capability table from its authoritative ledger."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.core.hmi.pvms.capabilities import (  # noqa: E402
    ITEMS,
    counts,
    markdown_inventory,
)


def document() -> str:
    totals = " · ".join(
        f"{number} {status.lower()}" for status, number in counts().items())
    header = f"""# Azeo HMI capability status

This document is generated from
`src/azeo_control_trainer/core/hmi/pvms/capabilities.py`. It records the
implemented product surface, deliberate design decisions, integration-only
features, and unresolved work. `tests/_smoke_operator.py` verifies that each
implementation claim resolves to a real symbol.

**{len(ITEMS)} capabilities · {totals}**

## Status meanings

- **Implemented** — built, reachable from the application, and tested.
- **Model only** — a tested rule exists but no user interface reaches it.
- **Display only** — a control is painted but no runtime service drives it.
- **Missing** — required by the product contract and not implemented.
- **Departure** — an intentional Azeo product decision, with its reason.
- **Not applicable** — requires a site product or integration outside this repo.

The generated inventory is authoritative. Add or change an item in
`capabilities.py`, then run this tool; do not hand-edit the tables.

"""
    return header + markdown_inventory()


def main() -> int:
    path = ROOT / "docs" / "HMI_CAPABILITY_STATUS.md"
    path.write_text(document(), encoding="utf-8")
    print("updated %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
