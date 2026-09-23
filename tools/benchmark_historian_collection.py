"""Profile collection on the real APVC document inventory, without a running plant.

This isolates Python collection work, not end-to-end UI performance. Graphs are
loaded read-only with their saved initial values/statuses; no controller executes.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import cProfile
import json
import logging
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import azeo_control_trainer.core.strategy.blocks  # noqa: E402,F401
from azeo_control_trainer.core.strategy.serialization.strategy_io import load_strategy  # noqa: E402
from azeo_control_trainer.core.hmi.binding import LiveGraphSource  # noqa: E402
from azeo_control_trainer.core.hmi.history import ContinuousHistorian  # noqa: E402
from operator_responsiveness import distribution  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.WARNING)
    project = ROOT / "projects/AzeoPlantVirtualController"
    graphs = [load_strategy(path, remember=False)[0] for folder in ("control", "sequence")
              for path in sorted((project / folder).glob("*.json"))]
    inventories = 0

    def provider():
        nonlocal inventories
        inventories += 1
        return {graph.name: graph for graph in graphs}

    source = LiveGraphSource(provider)
    historian = ContinuousHistorian(None, resolver=source, period_s=0)
    historian.configure_from(None, graphs)
    report = {"modules": len(graphs), "points": len(historian.TAGS), "batches": {},
              "fixture": "saved APVC graphs; no controller/provider; no archive; original initial values and qualities"}
    for mode in ("direct", "caller_snapshot"):
        batches = []
        for batch in range(3):
            values, inventories = [], 0
            for sample in range(30):
                start = time.perf_counter()
                with source.snapshot() if mode == "caller_snapshot" else nullcontext():
                    count = historian.collect(now=historian.sample_count, force=True)
                values.append((time.perf_counter() - start) * 1000)
                assert count == len(historian.TAGS)
            row = distribution(values)
            row["inventories_per_collection"] = inventories / 30
            batches.append(row)
        report["batches"][mode] = batches
        profile = cProfile.Profile()
        with profile:
            for _ in range(30):
                with source.snapshot() if mode == "caller_snapshot" else nullcontext():
                    historian.collect(now=historian.sample_count, force=True)
        profile.dump_stats(str(args.output_dir / f"{mode}.prof"))
        with (args.output_dir / f"{mode}.txt").open("w", encoding="utf-8") as output:
            pstats.Stats(profile, stream=output).strip_dirs().sort_stats("cumulative").print_stats(25)
        print(mode, "p95:", [round(row["p95_ms"], 2) for row in batches],
              "inventories/collection:", batches[0]["inventories_per_collection"])
    historian.close()
    (args.output_dir / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
