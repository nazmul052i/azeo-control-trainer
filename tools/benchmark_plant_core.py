"""Compare the same embedded plant on C++ and Python, outside UI workloads."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def measure(steps: int) -> dict:
    sys.path.insert(0, str(ROOT / "AzeoPlantSimulator"))
    logging.disable(logging.CRITICAL)
    from azeoplant.embedding import create_embedded_plant

    batches = []
    for _ in range(3):
        runtime = create_embedded_plant({
            "source": "CORE-BENCHMARK", "dt": .1, "autorun": False,
            "snapshot": "snapshots/lined_up.json", "catalog": "data/opcua_tag_catalog.json",
        })
        try:
            runtime.start()
            runtime.step(20)
            started = time.perf_counter()
            runtime.step(steps)
            batches.append((time.perf_counter() - started) * 1000 / steps)
            assert runtime.engine.stats.errors == 0
            health = runtime.health().detail
            assert not health["bpcs_enabled"]
        finally:
            runtime.stop()
    return {"core": health["core"], "native_units": health["native_units"],
            "signals": health["tag_count"], "steps_per_batch": steps,
            "milliseconds_per_step": batches, "best_ms": min(batches),
            "median_ms": statistics.median(batches)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=300)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps must be positive")
    if args.worker:
        print(json.dumps(measure(args.steps)))
        return 0
    rows = []
    for requested in ("0", "1"):
        result = subprocess.check_output([
            sys.executable, __file__, "--worker", "--steps", str(args.steps),
        ], env=dict(os.environ, AZEO_NATIVE=requested), text=True, timeout=180)
        row = json.loads(result)
        assert row["core"] == ("cpp" if requested == "1" else "python")
        rows.append(row)
    print(json.dumps({"python": rows[0], "cpp": rows[1],
                      "speedup": rows[0]["best_ms"] / rows[1]["best_ms"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
