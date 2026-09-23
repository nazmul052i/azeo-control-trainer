"""Isolate the release-read work repeated by an Operator Station tick.

The uncached arm reproduces the three existing consumers (alarm membership,
pending revisions, display navigation). It does not simulate full UI latency.
Only a disposable copy of the supplied project's displays is opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from azeo_control_trainer.azeo_operator_station.deployment import PvmDeployment  # noqa: E402
from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore  # noqa: E402
from operator_responsiveness import distribution  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT / "projects/AzeoPlantVirtualController")
    parser.add_argument("--output", type=Path, default=ROOT / "logs/deployment-catalog-benchmark.json")
    args = parser.parse_args()
    report = {"method": "three consumers per pass, 3 x 30 samples per arm; filesystem cache warm; no controller or rendering"}
    with tempfile.TemporaryDirectory(prefix="azeo-catalog-bench-") as directory:
        root = Path(directory) / "pvm"
        shutil.copytree(args.project / "displays/pvm", root,
                        ignore=shutil.ignore_patterns(".lock", ".lock.recover", "_accepted.json"))
        store = DisplayStore(root)
        deployment = PvmDeployment(store)
        targets = deployment.displays()
        for target in targets:
            deployment.open(target)
        report["display_count"] = len(targets)
        reads = Counter()
        original = store.history

        def counted(name):
            reads[name] += 1
            return original(name)
        store.history = counted
        report["arms"] = {"uncached": [], "scoped": []}
        for batch in range(3):
            # Alternate arm order so gradual filesystem warming cannot always
            # favor the optimized arm.
            for arm in (("uncached", "scoped") if batch % 2 == 0 else ("scoped", "uncached")):
                values = []
                reads.clear()
                for _ in range(30):
                    started = time.perf_counter()
                    with deployment.catalog_snapshot() if arm == "scoped" else nullcontext():
                        monitored = deployment.displays()
                        pending = deployment.pending()
                        visible = deployment.displays()
                    values.append((time.perf_counter() - started) * 1000)
                    assert monitored == visible == targets and not pending
                row = distribution(values)
                row["history_reads_per_pass"] = sum(reads.values()) / 30
                report["arms"][arm].append(row)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for arm, batches in report["arms"].items():
        print(arm, "p95 ms:", [round(row["p95_ms"], 2) for row in batches],
              "history reads/pass:", batches[0]["history_reads_per_pass"])


if __name__ == "__main__":
    main()
