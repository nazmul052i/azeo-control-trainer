"""Import a vendor-neutral OPC UA signal catalogue into an area project.

The online browser is the right tool for discovering an unfamiliar server,
but selecting hundreds of known simulator variables one at a time is not an
engineering workflow. A catalogue import produces the exact same
``eioc.field_io.signals`` contract as the browser, including enough metadata for
the tag database and Explorer to describe unreferenced I/O honestly.
"""
from __future__ import annotations

import json
from pathlib import Path


_INPUT_KINDS = frozenset({"AI", "DI"})
_OUTPUT_KINDS = frozenset({"AO", "DO"})
_KNOWN_KINDS = _INPUT_KINDS | _OUTPUT_KINDS


def load_signal_catalog(path: str | Path) -> dict[str, dict]:
    """Return ``store tag -> field_io specification`` from *path*.

    The accepted document is either a list of records or an object carrying a
    ``tags`` list. Each record needs a unique ``name``, canonical ``node_id``
    and one of AI/AO/DI/DO in ``kind``. Direction is derived from the I/O kind
    so an ambiguous prose field cannot reverse a command and a measurement.
    """
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    records = payload.get("tags") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("OPC UA catalogue must contain a 'tags' list")

    signals: dict[str, dict] = {}
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValueError(f"catalogue row {index} is not an object")
        tag = str(record.get("name") or "").strip()
        node = str(record.get("node_id") or record.get("node") or "").strip()
        kind = str(record.get("kind") or "").upper().strip()
        if not tag or not node or kind not in _KNOWN_KINDS:
            raise ValueError(
                f"catalogue row {index} needs name, node_id and AI/AO/DI/DO kind")
        if tag in signals:
            raise ValueError(f"duplicate catalogue tag: {tag}")

        # ``eu`` may intentionally be an empty string for a discrete point.
        # Testing truthiness here used to replace that empty EU with the plant
        # unit (for example ``U800``), which then appeared as an engineering
        # unit in Explorer.  An explicit EU therefore wins even when blank.
        has_explicit_eu = "eu" in record
        engineering_unit = (record.get("eu") if has_explicit_eu
                            else record.get("engineering_unit")
                            or record.get("unit") or "")
        spec: dict = {
            "node": node,
            "direction": "read" if kind in _INPUT_KINDS else "write",
            "kind": kind,
            "data_type": str(record.get("data_type") or
                             ("Boolean" if kind in {"DI", "DO"}
                              else "Double")),
            "unit": str(engineering_unit or ""),
            "description": str(record.get("description") or ""),
        }
        plant_unit = str(record.get("plant_unit") or
                         (record.get("unit") if has_explicit_eu else "")
                         or "").strip()
        if plant_unit:
            spec["plant_unit"] = plant_unit
        unit_description = str(record.get("unit_description") or "").strip()
        if unit_description:
            spec["plant_unit_description"] = unit_description
        if "lo" in record and "hi" in record:
            spec["range"] = [record["lo"], record["hi"]]
        if "state0" in record or "state1" in record:
            spec["states"] = [str(record.get("state0") or "0"),
                              str(record.get("state1") or "1")]
        signals[tag] = spec
    return signals


def configure_project_from_catalog(
        project_path: str | Path, catalog_path: str | Path, endpoint: str,
        *, eioc_name: str = "EIOC-1", eioc_description: str = "") -> int:
    """Replace one area's OPC UA map with *catalog_path* and return its size.

    This is the non-Qt seam used by project-generation tools. Explorer's EIOC
    OPC UA Client dialog uses :func:`load_signal_catalog` to preview the same
    result before Save. Controller model selection is deliberately unrelated:
    external EIOC signals do not consume PK DST capacity.
    """
    target = Path(project_path)
    project = json.loads(target.read_text(encoding="utf-8"))
    areas = project.get("areas") or []
    if not areas:
        raise ValueError("project has no area to receive OPC UA I/O")
    signals = load_signal_catalog(catalog_path)
    areas[0]["eioc"] = {
        "name": str(eioc_name).strip() or "EIOC-1",
        "description": str(eioc_description).strip()
        or "External OPC UA client",
        "field_io": {
            "type": "opcua",
            "endpoint": str(endpoint).strip(),
            "signals": signals,
        },
    }
    legacy = areas[0].get("field_io") or {}
    if legacy.get("type") == "opcua":
        del areas[0]["field_io"]
    target.write_text(json.dumps(project, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8")
    return len(signals)
