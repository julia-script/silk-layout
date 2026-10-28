#!/usr/bin/env python3
"""Compare silk-layout's layout time and geometry with taffy and yoga on shared workloads.

Each harness lays out the same document and reports per-iteration compute time, excluding
process start, JSON parsing and tree construction. Taffy and yoga get a fresh tree for every
iteration so their layout caches never help; silk-layout keeps no cache between computes.
Geometry is compared on border boxes in absolute coordinates.

  python3 bench/compare/compare.py --silk build/.../release/silk_layout_cli \\
      --taffy bench/compare/taffy/target/release/taffy-harness \\
      --yoga bench/compare/yoga/build/yoga-harness [--iterations 15] [--only NAME,...]
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
    args = parser.parse_args()
    names = args.only.split(",") if args.only else list(W.WORKLOADS)
    engines = [("taffy", args.taffy), ("yoga", args.yoga)]
    engines = [(n, b) for n, b in engines if b]
    results = {}
    ratios = {n: [] for n, _ in engines}
    header = f"{'workload':<20}{'silk ms':>10}" + "".join(f"{n + ' ms':>11}{'ratio':>8}{'diff':>8}" for n, _ in engines)
    print(header)
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            document = W.WORKLOADS[name]()
            path = Path(tmp) / f"{name}.json"
            path.write_text(json.dumps(document))
            silk_ns, silk_boxes = run_silk(args.silk, document, args.iterations)
            row = {"nodes": len(document["nodes"]), "silk_ms": min(silk_ns) / 1e6, "silk_median_ms": statistics.median(silk_ns) / 1e6}
            line = f"{name:<20}{row['silk_ms']:>10.3f}"
            for engine, binary in engines:
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
            print(line, flush=True)
            results[name] = row
    for engine, values in ratios.items():
        if values:
            print(f"geomean silk/{engine}: {math.exp(sum(map(math.log, values)) / len(values)):.2f}x")
    for name, row in results.items():
        for engine, _ in engines:
            if row[engine].get("first_mismatch"):
                print(f"{name}: {row[engine]['mismatched_nodes']} boxes differ from {engine}; first {row[engine]['first_mismatch']}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
