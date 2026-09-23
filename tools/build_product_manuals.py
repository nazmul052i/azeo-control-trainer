"""Generate and audit six searchable product PDFs without building an installer.

Usage: <configured-python> tools/build_product_manuals.py [--audit]
"""
from __future__ import annotations

import argparse

from build_product_manual_sources import GUIDES, ROOT, main as update_sources
from build_user_manual import audit, build
from document_release import build_release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", help="render and inspect page geometry")
    options = parser.parse_args()
    update_sources()
    for guide in GUIDES:
        source = ROOT / "docs" / guide.filename
        output = ROOT / "output/pdf" / f"{guide.title.replace(' ', '_')}_User_Manual.pdf"
        build_release(
            source, output,
            lambda staged, s=source, g=guide: build(
                staged, source=s, product_title=g.title, document_id=g.document_id),
            audit if options.audit else None,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
