"""Keep simulator connection points aligned with the shared vector library.

Only the hidden port group is copied. The simulator owns its palette and
hand-authored symbols; matching vector geometry prevents accidentally applying
coordinates from another extraction of the same named equipment.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "src/azeo_control_trainer/core/hmi/assets/symbols/symbols"
SIMULATOR = ROOT / "AzeoPlantSimulator/azeoplant/ui/symbols"
HAND_AUTHORED = {"fittings/pid_splitter.svg", "vessels/pid_boiler.svg"}
PORT_GROUP = re.compile(r'<g class="connection-points"[^>]*>.*?</g>', re.DOTALL)


def _node_signature(node: ET.Element) -> tuple:
    return (
        node.tag,
        tuple(sorted(node.attrib.items())),
        (node.text or "").strip(),
        tuple(_node_signature(child) for child in node),
    )


def geometry_signature(path: Path) -> tuple:
    root = ET.parse(path).getroot()
    return (
        root.attrib.get("viewBox"),
        tuple(
            _node_signature(child)
            for child in root
            if child.tag.endswith("}g") and child.attrib.get("data-role") != "connection-points"
        ),
    )


def matching_symbols() -> list[tuple[Path, Path]]:
    result = []
    for target in sorted(SIMULATOR.rglob("*.svg")):
        if target.relative_to(SIMULATOR).as_posix() in HAND_AUTHORED:
            continue
        signature = geometry_signature(target)
        candidates = [p for p in SHARED.rglob(target.name) if geometry_signature(p) == signature]
        if len(candidates) != 1:
            raise ValueError(
                f"{target}: expected one identical source geometry, found {len(candidates)}"
            )
        result.append((target, candidates[0]))
    return result


def synchronized_text(target: Path, source: Path) -> str:
    target_text, source_text = (
        target.read_text(encoding="utf-8"),
        source.read_text(encoding="utf-8"),
    )
    group = PORT_GROUP.search(source_text)
    if group is None or len(PORT_GROUP.findall(target_text)) != 1:
        raise ValueError(f"Missing or duplicate port metadata: {target}, {source}")
    return PORT_GROUP.sub(lambda _: group.group(), target_text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without writing files")
    args = parser.parse_args()
    pairs = matching_symbols()
    changed = []
    for target, source in pairs:
        updated = synchronized_text(target, source)
        if updated == target.read_text(encoding="utf-8"):
            continue
        changed.append(target)
        if not args.check:
            target.write_text(updated, encoding="utf-8", newline="\n")
    print(
        f"Checked {len(pairs)} shared symbols; {len(changed)} {'out of sync' if args.check else 'updated'}."
    )
    for path in changed:
        print(path.relative_to(ROOT).as_posix())
    return int(args.check and bool(changed))


if __name__ == "__main__":
    raise SystemExit(main())
