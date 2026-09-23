#!/usr/bin/env python3
"""The definition of done for the adapter: the azeo_control_trainer's own
EIOC driver (``fieldio/opcua_driver.OpcUaLink``) against this simulator's
OPC UA adapter, live on the wire.

Asserts: subscription reads arrive in the trainer's store, a queued write
moves a valve through the bus, and a write to a simulator-owned signal is
refused and does not land. Skips cleanly when the trainer repo is not
present (set AZEO_TRAINER to point at it).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TRAINER_ROOT = Path(os.environ.get("AZEO_TRAINER", str(ROOT.parent))).resolve()
TRAINER = TRAINER_ROOT / "src"

logging.disable(logging.CRITICAL)

ENDPOINT = "opc.tcp://127.0.0.1:48431/plant_sim/"

FAILURES = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAILURES
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  {detail}" if detail else ""))
    if not ok:
        FAILURES += 1


def main() -> int:
    if not TRAINER.exists():
        print("SKIP: azeo_control_trainer not found at", TRAINER)
        return 0
    sys.path.insert(0, str(TRAINER))
    from azeo_control_trainer.core.datastore.shared_data_store import (
        SharedDataStore)
    from azeo_control_trainer.connectivity.fieldio.opcua_driver import OpcUaLink

    from azeoplant.core.engine import SimulationEngine
    from azeoplant.models.flowsheet import Flowsheet, TagDatabase
    from azeoplant.io import VirtualIOBus
    from azeoplant.io.adapters import OpcUaAdapter
    from azeoplant.control.strategy import ControlSystem
    from azeoplant.opc.server import OpcUaServer

    db = TagDatabase()
    fs = Flowsheet(db)
    eng = SimulationEngine(db, fs, dt=0.1)
    snap = ROOT / "snapshots" / "lined_up.json"
    if snap.exists():
        eng.load_snapshot(snap)
    bus = VirtualIOBus(db)
    cs = ControlSystem(db, bus)
    cs.seed_from_plant()
    cs.open_loop()                     # the trainer is the DCS today
    eng.controller = cs
    eng.post_step_hooks.append(bus.tick)
    eng.post_step_hooks.append(cs.step)

    server = OpcUaServer(db, eng, endpoint=ENDPOINT, publish_period=0.2)
    adapter = OpcUaAdapter(bus, server)
    adapter.start()

    def run_for(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            for _ in range(5):
                eng._execute_step()
            time.sleep(0.02)

    # the 612-node address space takes several seconds to build
    for _ in range(40):
        run_for(1.0)
        if server.stats.running:
            break

    store = SharedDataStore()
    link = OpcUaLink(store, ENDPOINT, {
        "FT-1001.PV": {"node": "ns=2;s=FT-1001", "direction": "read"},
        "ZSO-MOV1001A.ST": {"node": "ns=2;s=ZSO-MOV1001A",
                            "direction": "read"},
        "FCV-1001.OUT": {"node": "ns=2;s=FCV-1001", "direction": "write"},
        "LT-1001.BAD": {"node": "ns=2;s=LT-1001", "direction": "write"},
    })
    try:
        connected = link.start(timeout=15.0)
        run_for(3.0)
        check("trainer driver connects to the adapter", connected)

        pv = store.get("FT-1001.PV")
        check("subscription read arrives in the trainer's store",
              pv is not None and abs(float(pv)
                                     - float(db["FT-1001"].value)) < 5.0,
              f"store {pv} vs plant {db['FT-1001'].value:.1f}")
        zso = store.get("ZSO-MOV1001A.ST")
        check("discrete state arrives", zso is not None and bool(zso))

        store.queue_write("FCV-1001.OUT", 41.5)
        run_for(2.5)
        check("queued trainer write moves the valve through the bus",
              abs(float(db["FCV-1001"].value) - 41.5) < 1e-6,
              f"FCV-1001 {db['FCV-1001'].value:.2f}")

        level0 = float(db["LT-1001"].value)
        store.queue_write("LT-1001.BAD", 5.0)
        run_for(2.5)
        check("write to a simulator-owned signal is refused",
              abs(float(db["LT-1001"].value) - 5.0) > 1.0,
              f"LT-1001 {db['LT-1001'].value:.1f} (was {level0:.1f})")

        h = adapter.health()
        check("adapter health reports the session",
              h.received >= 1 and h.connected,
              f"received={h.received} rejected={h.rejected}")
    finally:
        link.stop()
        adapter.stop()

    print(f"\n{FAILURES} failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
