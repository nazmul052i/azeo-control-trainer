"""The tag database derives itself from the modules, and stays honest.

The point of `strategy/tagdb.py` is that nothing is hand-maintained: the
addressable namespace is walked out of the loaded modules, so it cannot drift
from the configuration. These checks assert that derivation, not a fixture.

Run:  D:\\development\\GitHub\\vpy\\Scripts\\python.exe tests/_smoke_tagdb.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import azeo_control_trainer.core.strategy.blocks  # noqa: F401,E402
from azeo_control_trainer.core.strategy.tagdb import (  # noqa: E402
    EntryKind, TagDatabase,
)

AREA = os.path.join(os.path.dirname(__file__), "..", "src", "strategies",
                    "azeo_training")

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


db = TagDatabase.from_area(AREA)

# ---------------------------------------------------------------- shape
check(f"database built from the area ({len(db)} entries)", len(db) > 500, len(db))
check("area name carried through", db.area_name == "azeo_training", db.area_name)
check("every module is indexed",
      sorted(db.modules()) == ["FIC-102", "LI-101", "LIC-201", "MTR-102",
                               "MTR-203", "XV-101", "XV-OPTION"],
      sorted(db.modules()))
for kind in (EntryKind.FIELD, EntryKind.TERMINAL, EntryKind.PARAMETER):
    check(f"{kind} entries were derived", len(db.of_kind(kind)) > 0,
          len(db.of_kind(kind)))

# ---------------------------------------------------------------- field I/O
fields = db.field_tags()
check("module-referenced field tags resolve to AI/AO/DI/DO blocks",
      all(db.lookup(p).block_type in ("AI", "AO", "DI", "DO")
          for paths in fields.values() for p in paths),
      sorted({db.lookup(p).block_type for ps in fields.values() for p in ps}))

check("an AI's store tag is indexed", "LI-101.PV" in fields, sorted(fields)[:4])
check("a tag read by two modules lists both blocks",
      sorted(fields["LI-101.PV"]) == ["LI-101/AI1", "MTR-102/AI_LI101"],
      fields.get("LI-101.PV"))
check("an AO's store tag is indexed", "FV-102.OUT" in fields)

ai = db.lookup("LI-101/AI1")
check("AI entry faces input and is externally writable",
      ai.direction == "input" and ai.writable,
      f"dir={ai.direction} writable={ai.writable}")
ao = db.lookup("FIC-102/AO1")
check("AO entry faces output and is NOT externally writable",
      ao.direction == "output" and not ao.writable,
      f"dir={ao.direction} writable={ao.writable}")
di = db.lookup("MTR-102/DI_START_CMD")
check("a DI's data type is BOOL", di.data_type == "BOOL", di.data_type)

# A known external server may expose far more channels than the control
# modules currently reference. Catalogue import must keep those channels in
# Explorer without inventing placeholder blocks that would drive outputs.
import json  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from azeo_control_trainer.connectivity.fieldio.opcua_catalog import (  # noqa: E402
    configure_project_from_catalog,
    load_signal_catalog,
)

with tempfile.TemporaryDirectory() as configured_root:
    configured_area = Path(configured_root)
    project_path = configured_area / "_project.json"
    project_path.write_text(json.dumps({
        "areas": [{"name": "CATALOG", "controller": {
            "name": "PK-CATALOG", "model": "PK100"},
            "field_io": {"type": "opcua", "endpoint": "legacy"}}],
    }), encoding="utf-8")
    catalog_path = configured_area / "catalog.json"
    catalog_path.write_text(json.dumps({"tags": [
        {"name": "PT-1", "node_id": "ns=2;s=PLANT.AI.PT-1",
         "kind": "AI", "data_type": "Double", "eu": "bar",
         "description": "Header pressure", "lo": 0, "hi": 20},
        {"name": "XY-1", "node_id": "ns=2;s=PLANT.DO.XY-1",
         "kind": "DO", "data_type": "Boolean", "eu": "",
         "plant_unit": "U001", "unit_description": "Utilities",
         "description": "Start command", "state0": "Stop",
         "state1": "Start"},
    ]}), encoding="utf-8")
    imported_count = configure_project_from_catalog(
        project_path, catalog_path, "opc.tcp://127.0.0.1:4840/plant/",
        eioc_name="EIOC-CATALOG")
    configured = TagDatabase.from_area(configured_area)
    configured_project = json.loads(project_path.read_text(encoding="utf-8"))

check("a signal catalogue configures every OPC UA channel in one operation",
      imported_count == 2
      and set(configured.field_tags()) == {"PT-1", "XY-1"})
configured_ai = configured.lookup("FIELD/PT-1")
configured_do = configured.lookup("FIELD/XY-1")
configured_area_doc = configured_project["areas"][0]
check("configured-only I/O carries engineering metadata into Explorer",
      configured_ai is not None
      and configured_ai.block_type == "AI"
      and configured_ai.direction == "input"
      and configured_ai.writable
      and configured_ai.unit == "bar"
      and configured_ai.eu_range == (0, 20), configured_ai)
check("a configured output remains controller-owned and does not create a block",
      configured_do is not None
      and configured_do.block_type == "DO"
      and configured_do.direction == "output"
      and not configured_do.writable
      and configured_do.unit == ""
      and configured.modules() == [], configured_do)
check("a discrete point's plant area is not mistaken for an engineering unit",
      configured_area_doc["eioc"]["field_io"]["signals"]["XY-1"]["unit"] == ""
      and configured_area_doc["eioc"]["field_io"]["signals"]["XY-1"]
      ["plant_unit"] == "U001")
check("bulk configuration creates a separate EIOC client node",
      configured_area_doc["eioc"]["name"] == "EIOC-CATALOG"
      and configured_area_doc["eioc"]["field_io"]["type"] == "opcua"
      and len(configured_area_doc["eioc"]["field_io"]["signals"]) == 2)
check("EIOC signals do not resize or overwrite the PK controller",
      configured_area_doc["controller"]["model"] == "PK100"
      and "field_io" not in configured_area_doc)

# The public source package ships the current catalogue, not the retired
# archived OPC/EIOC exercise. Keep its surface and NodeId contract covered.
repo_root = Path(__file__).resolve().parent.parent
plant_catalog = load_signal_catalog(
    repo_root / "AzeoPlantSimulator" / "data" / "opcua_tag_catalog.json")
check("current Azeo Plant catalogue carries the complete 612-point surface",
      len(plant_catalog) == 612, len(plant_catalog))
check("Azeo Plant Simulator process points use resolvable bare-tag NodeIds",
      all(spec["node"] == f"ns=2;s={tag}"
          for tag, spec in plant_catalog.items()))

from azeo_control_trainer.connectivity.fieldio.eioc import EthernetIoCard  # noqa: E402

catalog_eioc = EthernetIoCard.from_config(configured_area_doc["eioc"])
check("the EIOC owns and capacity-checks its imported signal inventory",
      catalog_eioc is not None
      and catalog_eioc.signal_count == 2
      and catalog_eioc.read_count == 1
      and catalog_eioc.write_count == 1
      and catalog_eioc.signal_limit == 30_000
      and not catalog_eioc.over_capacity)

# ---------------------------------------------------------------- terminals
term = db.lookup("MTR-102/CND1/OUT_D")
check("a block terminal is addressable Azeo-style", term is not None)
check("terminal carries its data type and direction",
      term and term.data_type == "BOOL" and term.direction == "output",
      term and (term.data_type, term.direction))
check("terminal carries its description",
      term and len(term.description) > 5, term and term.description)

# ---------------------------------------------------------------- parameters
p = db.lookup("MTR-102/CND2/CONFIG/TIME_TRUE")
check("a block parameter is addressable", p is not None)
check("parameter reports its configured value, not the schema default",
      p and abs(float(p.value) - 4.0) < 1e-9, p and p.value)
check("parameter carries its unit from config_units",
      p and p.unit == "s", p and p.unit)
check("parameter carries its description",
      p and "True" in p.description, p and p.description[:50])

# A PID's SP is both an input (the operator writes it) and an output (the
# working setpoint reads back). Azeo treats that as one parameter, so it must
# merge into a single inout point rather than colliding and losing one side.
sp = db.lookup("FIC-102/PID1/SP")
check("a name that is both input and output merges to one point",
      sp is not None and sp.direction == "inout",
      sp and sp.direction)
check("the merged point keeps a description",
      sp and len(sp.description) > 0, sp and sp.description)

enum = db.lookup("MTR-102/DC1/CONFIG/INTERLOCK_OPT")
check("an enumerated parameter offers its choices",
      enum and len(enum.choices) >= 3, enum and enum.choices)

pid = db.lookup("FIC-102/PID1/CONFIG/GAIN")
check("PID tuning is addressable", pid is not None and pid.writable)
check("PID GAIN reports the configured value",
      pid and abs(float(pid.value) - 0.6) < 1e-9, pid and pid.value)

# ---------------------------------------------------------------- queries
hits = db.search("MTR-102")
check("search finds a module's points", len(hits) > 100, len(hits))
check("search can be restricted by kind",
      all(e.kind == EntryKind.FIELD
          for e in db.search("MTR-102", kind=EntryKind.FIELD)))
check("search matches on the store tag too",
      any(e.io_tag == "LI-101.PV" for e in db.search("LI-101.PV")))
check("for_module returns only that module",
      all(e.module == "XV-101" for e in db.for_module("XV-101")))
check("membership test works", "MTR-102/CND2/CONFIG/TIME_TRUE" in db)
check("an unknown path returns None", db.lookup("NOPE/NOPE/NOPE") is None)

# ---------------------------------------------------------------- export
d = db.to_dict()
check("dict export carries counts and entries",
      d["counts"][EntryKind.FIELD] == len(db.of_kind(EntryKind.FIELD))
      and len(d["entries"]) == len(db))

parsed = json.loads(db.to_json())
check("JSON export round-trips", len(parsed["entries"]) == len(db))

csv_text = db.to_csv()
check("CSV export has a header and one row per entry",
      len(csv_text.strip().splitlines()) == len(db) + 1,
      len(csv_text.strip().splitlines()))
check("CSV header names the addressable path first",
      csv_text.splitlines()[0].startswith("path,kind,module"),
      csv_text.splitlines()[0][:40])

# ---------------------------------------------------------------- derivation
# The real contract: change a module, and the database changes with it.
from azeo_control_trainer.core.strategy.serialization.strategy_io import (  # noqa: E402
    load_strategy,
)

graph, _ = load_strategy(os.path.join(AREA, "control", "MTR-102.json"))
one = TagDatabase.from_graphs([graph], area_name="single")
check("a database can be built from graphs in memory",
      len(one) > 0 and one.modules() == ["MTR-102"], one.modules())

before = len(one)
blk = next(b for b in graph.blocks.values() if b.instance_name == "CND1")
graph.remove_block(blk.id)
after = TagDatabase.from_graphs([graph], area_name="single")
check("removing a block removes its points from the database",
      len(after) < before and after.lookup("MTR-102/CND1/OUT_D") is None,
      f"{before} -> {len(after)}")

# ----------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} tag-database check(s) FAILED:")
    for f in failures:
        print("   -", f)
    sys.exit(1)
print(f"All tag-database checks passed ({len(db)} entries derived).")


def test_tagdb():
    """pytest entry point — the checks above run at import."""
    assert not failures, failures
