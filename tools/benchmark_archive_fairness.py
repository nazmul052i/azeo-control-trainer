"""Time real H-01 archive reads alongside accepted writes in a temporary DB."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from azeo_control_trainer.core.hmi.history.archive import HistoryArchive

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    count = 86400 * args.days
    paths = [f"LOOP/P{i}/PV" for i in range(10)]
    results = []
    with tempfile.TemporaryDirectory(prefix="azeo-history-fairness-") as folder:
        archive = HistoryArchive(Path(folder) / "history.sqlite", retention_days=max(1, args.days))
        try:
            with archive.connect() as db:
                for path in paths:
                    db.execute("INSERT INTO history_points VALUES (?,?)", (path, json.dumps({"unit": "bar"})))
                    db.executemany("INSERT INTO history_samples VALUES (?,?,?,?,?,?,?)",
                                   ((path, second, second % 100, "GOOD", archive.origin + second, None, "run")
                                    for second in range(count)))
            print(f"Fixture: {len(paths)} pens x {count} raw samples", flush=True)
            archive._last_prune = time.time()
            read = archive.read
            entered = threading.Event()

            def observed(*args, **kwargs):
                entered.set()
                if not args_namespace.profile:
                    return read(*args, **kwargs)
                import cProfile
                import pstats
                profiler = cProfile.Profile()
                try:
                    return profiler.runcall(read, *args, **kwargs)
                finally:
                    pstats.Stats(profiler).sort_stats("cumtime").print_stats(25)

            args_namespace = args
            archive.read = observed
            for batch in range(1 if args.profile else 3):
                entered.clear()
                start = time.perf_counter()
                query = archive.query(paths, 0, count - 1)
                assert entered.wait(30), "Read did not start"
                write_start = time.perf_counter()
                write = archive.append({}, [(paths[0], count + batch, 42, "GOOD",
                                              archive.origin + count + batch, None, "run")], [])
                write.result(timeout=300)
                write_end = time.perf_counter()
                result = query.result(timeout=300)
                query_end = time.perf_counter()
                results.append(dict(batch=batch, writer_completion_ms=(write_end - write_start) * 1000,
                                    both_completed_ms=(query_end - start) * 1000,
                                    raw_counts=[result["statistics"][path]["count"] for path in paths],
                                    displayed_counts=[len(result["samples"][path]) for path in paths]))
                print(results[-1], flush=True)
            with archive.connect() as db:
                assert db.execute("SELECT count(*) FROM history_samples WHERE time>=?", (count,)).fetchone()[0] == len(results)
        finally:
            archive.close()
    source = ROOT / "src/azeo_control_trainer/core/hmi/history/archive.py"
    report = dict(days=args.days, samples_per_pen=count, results=results,
                  source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
