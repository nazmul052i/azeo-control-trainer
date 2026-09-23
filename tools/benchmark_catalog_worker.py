"""Compare recurring catalog discovery on the caller and its owned worker."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore  # noqa: E402
from operator_responsiveness import distribution  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    deployment = PvmDeployment(DisplayStore(ROOT / "projects/AzeoPlantVirtualController/displays/pvm"))
    counts = Counter()
    owner = threading.get_ident()
    original = DisplayStore.history

    def counted(store, name):
        counts["owner" if threading.get_ident() == owner else "worker"] += 1
        return original(store, name)
    DisplayStore.history = counted
    rows = {"synchronous": [], "background": []}
    try:
        with deployment.catalog_snapshot():
            expected = deployment.displays()
        for batch in range(3):
            order = ("synchronous", "background") if batch % 2 == 0 else ("background", "synchronous")
            for mode in order:
                times, owner_reads, worker_reads = [], [], []
                for _ in range(30):
                    worker = deployment._catalog_worker
                    if worker is not None and worker._future is not None:
                        worker._future.result(timeout=10)  # outside the timed handler
                    counts.clear()
                    scope = (deployment.catalog_snapshot if mode == "synchronous"
                             else deployment.background_catalog_snapshot)
                    started = time.perf_counter()
                    with scope():
                        assert deployment.displays() == expected
                        deployment.pending()
                        assert deployment.displays() == expected
                    times.append((time.perf_counter() - started) * 1000)
                    owner_reads.append(counts["owner"])
                    worker = deployment._catalog_worker
                    if worker is not None and worker._future is not None:
                        worker._future.result(timeout=10)
                    worker_reads.append(counts["worker"])
                row = {**distribution(times), "owner_reads": owner_reads, "worker_reads": worker_reads}
                rows[mode].append(row)
                print(mode, batch + 1, "p95", round(row["p95_ms"], 3), "owner reads", sorted(set(owner_reads)), flush=True)
        (args.output_dir / "results.json").write_text(json.dumps({
            "display_count": len(expected), "batches": rows,
            "fixture": "read-only APVC releases; worker completion outside timed caller; alternating batch order",
        }, indent=2), encoding="utf-8")
    finally:
        deployment.close()
        DisplayStore.history = original


if __name__ == "__main__":
    main()
