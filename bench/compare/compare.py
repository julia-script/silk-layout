#!/usr/bin/env python3
"""Compare silk-layout's layout time and geometry with taffy and yoga on shared workloads.

Each harness lays out the same document and reports per-iteration compute time, excluding
process start, JSON parsing and tree construction. Taffy and yoga get a fresh tree for every
iteration so their layout caches never help; silk-layout keeps no cache between computes.
Geometry is compared on border boxes in absolute coordinates.
Grid workloads are taffy-only comparisons: Yoga is explicitly skipped and excluded
from timing-ratio and geometry summaries.

  python3 bench/compare/compare.py --silk build/.../release/silk_layout_cli \\
      --taffy bench/compare/taffy/target/release/taffy-harness \\
      --yoga bench/compare/yoga/build/yoga-harness [--iterations 15] [--only NAME,...]

--docs DIR runs the documents in DIR instead of the built-in workloads, for example the
trees from taffy's README benchmark table (bench/compare/taffy-readme exports them).
"""
import argparse
import json
import math
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import workloads as W  # noqa: E402

TOLERANCE = 0.05


def run_silk(cli, document, iterations):
    request = dict(document, benchmark={"iterations": iterations})
    done = subprocess.run([cli], input=json.dumps(request).encode(), capture_output=True, check=True, timeout=1800)
    samples = json.loads(done.stderr.decode().strip().splitlines()[-1])["layoutNs"]
    # silk-layout reports parent-relative locations; the harnesses report absolute ones.
    sources = json.loads(done.stdout)["sources"]
    boxes = []
    for node, source in zip(document["nodes"], sources):
        parent = node["parent"]
        px, py = (boxes[parent][0], boxes[parent][1]) if parent is not None else (0, 0)
        boxes.append([px + source["x"], py + source["y"], source["width"], source["height"]])
    return samples, boxes


def run_harness(binary, path, iterations):
    done = subprocess.run([binary, str(path), str(iterations), "--dump"], capture_output=True, check=True, timeout=1800)
    result = json.loads(done.stdout)
    return result["layoutNs"], result["boxes"]


def mismatches(a, b):
    bad = [i for i, (x, y) in enumerate(zip(a, b)) if any(abs(p - q) > TOLERANCE for p, q in zip(x, y))]
    return bad


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--silk", required=True)
    parser.add_argument("--taffy")
    parser.add_argument("--yoga")
    parser.add_argument("--iterations", type=int, default=15)
    parser.add_argument("--only", help="comma-separated workload names")
    parser.add_argument("--json", help="write results to this file")
    parser.add_argument("--docs", help="directory of documents to run instead of the built-in workloads")
    args = parser.parse_args()
    workloads = W.WORKLOADS
    if args.docs:
        workloads = {p.stem: (lambda p=p: json.loads(p.read_text())) for p in sorted(Path(args.docs).glob("*.json"))}
    names = args.only.split(",") if args.only else list(workloads)
    engines = [("taffy", args.taffy), ("yoga", args.yoga)]
    engines = [(n, b) for n, b in engines if b]
    results = {}
    ratios = {n: [] for n, _ in engines}
    header = f"{'workload':<20}{'silk ms':>10}" + "".join(f"{n + ' ms':>11}{'ratio':>8}{'diff':>8}" for n, _ in engines)
    print(header)
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            document = workloads[name]()
            is_grid = any(node.get("style", {}).get("display") == "grid" for node in document["nodes"])
            path = Path(tmp) / f"{name}.json"
            path.write_text(json.dumps(document))
            silk_ns, silk_boxes = run_silk(args.silk, document, args.iterations)
            row = {"nodes": len(document["nodes"]), "silk_ms": min(silk_ns) / 1e6, "silk_median_ms": statistics.median(silk_ns) / 1e6}
            if is_grid:
                row["comparison"] = "taffy-only (grid; Yoga unsupported)"
            line = f"{name:<20}{row['silk_ms']:>10.3f}"
            for engine, binary in engines:
                if is_grid and engine == "yoga":
                    row[engine] = {"skipped": "Yoga does not support CSS Grid"}
                    line += f"{'skip':>11}{'-':>8}{'-':>8}"
                    continue
                ns, boxes = run_harness(binary, path, args.iterations)
                bad = mismatches(silk_boxes, boxes)
                ms = min(ns) / 1e6
                ratio = row["silk_ms"] / ms
                ratios[engine].append(ratio)
                row[engine] = {"ms": ms, "median_ms": statistics.median(ns) / 1e6, "ratio": ratio, "mismatched_nodes": len(bad)}
                if bad:
                    i = bad[0]
                    row[engine]["first_mismatch"] = {"node": i, "silk": silk_boxes[i], engine: boxes[i]}
                line += f"{ms:>11.3f}{ratio:>7.2f}x{len(bad):>8}"
            if is_grid:
                line += "  [taffy-only grid; Yoga unsupported]"
            print(line, flush=True)
            results[name] = row
    for engine, values in ratios.items():
        if values:
            print(f"geomean silk/{engine}: {math.exp(sum(map(math.log, values)) / len(values)):.2f}x")
        skipped = sum(bool(row[engine].get("skipped")) for row in results.values())
        if skipped:
            print(f"{engine}: {len(values)} workloads compared; {skipped} grids skipped (not included in ratios or geometry)")
    for name, row in results.items():
        for engine, _ in engines:
            if row[engine].get("skipped"):
                continue
            if row[engine].get("first_mismatch"):
                print(f"{name}: {row[engine]['mismatched_nodes']} boxes differ from {engine}; first {row[engine]['first_mismatch']}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
