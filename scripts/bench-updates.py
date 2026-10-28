#!/usr/bin/env python3
"""Time re-layout after small updates, in process, with the native JSON adapter.

Each scenario applies one step of its cycle to the source tree and then times
one compute. The first, untimed compute lays out the initial document. After
the run, the adapter's final geometry must equal a fresh layout of the same
final document, so caches cannot change results unnoticed.

Build the release adapter first: silk build --package silk_layout_cli --release
"""
import argparse
import copy
import json
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench" / "updates"))
import workloads  # noqa: E402


def final_document(document, cycle, iterations):
    final = copy.deepcopy(document)
    for iteration in range(iterations):
        step = cycle[iteration % len(cycle)]
        if "viewport" in step:
            final["viewport"] = step["viewport"]
        for change in step.get("set", []):
            node = final["nodes"][change["node"]]
            for key in ("style", "text"):
                if key in change:
                    node[key] = change[key]
    return final


def run(cli, document):
    completed = subprocess.run([cli], input=json.dumps(document).encode(), capture_output=True, timeout=600)
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.decode())
    return completed.stdout, completed.stderr.decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--only", help="substring filter on workload/scenario")
    parser.add_argument("--json", help="write results to this file")
    parser.add_argument("--small", action="store_true", help="smaller scenes, for checking debug builds")
    args = parser.parse_args()
    cli = str(Path(args.cli).resolve())
    results = {}
    print(f"{'scenario':32} {'median ms':>10} {'full':>6} {'probes':>7} {'hits':>6} {'reused':>7} {'analyze':>8} {'shape':>7}")
    for workload, (document, scenarios) in workloads.all(args.small).items():
        for scenario, cycle in scenarios.items():
            name = f"{workload}/{scenario}"
            if args.only and args.only not in name:
                continue
            request = dict(document, updates={"iterations": args.iterations, "cycle": cycle})
            incremental, report = run(cli, request)
            fresh, _ = run(cli, final_document(document, cycle, args.iterations))
            if json.loads(incremental) != json.loads(fresh):
                raise SystemExit(f"{name}: geometry after updates differs from a fresh layout")
            stats = json.loads(report.strip().splitlines()[-1])
            samples = stats.pop("layoutNs")
            stats["median_ms"] = statistics.median(samples) / 1e6
            results[name] = stats
            print(f"{name:32} {stats['median_ms']:10.3f} {stats['fullLayouts']:6} {stats['sizeProbes']:7} {stats['cacheHits']:6} {stats['reusedLayouts']:7} {stats['analyzeCalls']:8} {stats['shapeCalls']:7}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
