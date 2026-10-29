#!/usr/bin/env python3
"""Measure the native JSON adapter, including process startup and JSON I/O.

This is an end-to-end wall-clock baseline, not an instruction-count claim.
Build once before invoking. --record explicitly replaces the local baseline.
"""
import argparse
import json
import platform
from pathlib import Path
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "bench" / "results" / "baseline.json"


def workloads():
    def tree(style, children):
        return {"viewport": {"width": 1000, "height": 800}, "nodes": [{"parent": None, "style": style}] + children}
    block = tree({"width": 600}, [{"parent": 0, "style": {"height": 10}} for _ in range(1000)])
    flex = tree({"display": "flex", "width": 1000}, [{"parent": 0, "style": {"flexGrow": 1, "flexBasis": 0, "height": 20}} for _ in range(1000)])
    wrap = tree({"display": "flex", "width": 500, "flexWrap": "wrap"}, [{"parent": 0, "style": {"width": 30, "height": 10, "flexShrink": 0}} for _ in range(1000)])
    nested = tree({"width": 500}, [{"parent": i, "style": {"display": "block" if i % 2 else "flex", "padding": 1}} for i in range(100)])
    measured = tree({"display": "flex", "width": 1000, "flexWrap": "wrap"}, [{"parent": 0, "style": {}, "measure": {"minWidth": 20, "maxWidth": 80, "height": 20}} for _ in range(500)])
    return {"block-1000": (block, 600, 10000), "flex-grow-1000": (flex, 1000, 20), "flex-wrap-1000": (wrap, 500, 630), "nested-100": (nested, 500, 200), "measured-500": (measured, 1000, 840)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    executable = str(Path(args.cli).resolve())
    results = {}
    for name, (request, expected_width, expected_height) in workloads().items():
        data = json.dumps(request).encode()
        samples = []
        for iteration in range(args.runs + 1):
            start = time.perf_counter_ns()
            completed = subprocess.run([executable], input=data, capture_output=True, check=True, timeout=30)
            duration = (time.perf_counter_ns() - start) / 1e6
            sources = json.loads(completed.stdout)["sources"]
            root = sources[0]
            if len(sources) != len(request["nodes"]) or abs(root["width"] - expected_width) > 0.01 or abs(root["height"] - expected_height) > 0.01:
                raise SystemExit(f"{name}: incorrect root geometry {root}; expected {expected_width} x {expected_height}")
            if iteration:
                samples.append(duration)
        results[name] = {"median_ms": statistics.median(samples), "nodes": len(request["nodes"]), "root": [expected_width, expected_height]}
    old = json.loads(BASELINE.read_text()) if BASELINE.exists() else None
    for name, value in results.items():
        ratio = value["median_ms"] / old["workloads"][name]["median_ms"] if old and name in old["workloads"] else None
        print(f"{name}: {value['median_ms']:.3f} ms" + (f" ({ratio:.3f}x baseline)" if ratio is not None else ""))
    if args.record:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps({"method": "native adapter wall time; one warmup excluded; median", "platform": platform.platform(), "silk": "0.1.0-dev.29+94cf9559bf47", "runs": args.runs, "workloads": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
