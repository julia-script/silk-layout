#!/usr/bin/env python3
"""Compare re-layout after small updates with taffy and yoga on the shared workloads.

Every engine keeps one tree and its caches: each timed iteration applies the next step
of a scenario's cycle and lays out again. Scenarios, for every workload:
  leaf       a leaf's padding toggles between 3 and 1
  container  a container halfway down the node list toggles its padding the same way
  resize     the viewport narrows by one pixel per step, so no width repeats
  fluid-resize  the same with the root's width removed, so the resize reaches the boxes
Geometry after the last step is compared on border boxes in absolute coordinates.

  python3 bench/compare/updates.py --silk build/.../release/silk_layout_cli \\
      --taffy bench/compare/taffy/target/release/taffy-harness \\
      --yoga bench/compare/yoga/build/yoga-harness [--iterations 40] [--only NAME,...]
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
from compare import TOLERANCE, mismatches  # noqa: E402


def padding_toggle(document, node):
    style = document["nodes"][node]["style"]
    return [{"set": [{"node": node, "style": dict(style, padding=3)}]},
            {"set": [{"node": node, "style": dict(style, padding=1)}]}]


def scenarios(document):
    nodes = document["nodes"]
    parents = {node["parent"] for node in nodes if node["parent"] is not None}
    leaves = [index for index in range(1, len(nodes)) if index not in parents]
    containers = [index for index in range(1, len(nodes)) if index in parents]
    result = {}
    if leaves:
        result["leaf"] = padding_toggle(document, leaves[len(leaves) // 2])
    if containers:
        result["container"] = padding_toggle(document, containers[len(containers) // 2])
    viewport = document["viewport"]
    result["resize"] = [{"viewport": {"width": viewport["width"] - step, "height": viewport["height"]}} for step in range(1, 41)]
    return result


def fluid(document):
    """The document with an auto-width root, which a resize reaches."""
    nodes = [dict(node) for node in document["nodes"]]
    nodes[0] = dict(nodes[0], style={key: value for key, value in nodes[0]["style"].items() if key != "width"})
    return dict(document, nodes=nodes)


def absolute(document, sources):
    boxes = []
    for node, source in zip(document["nodes"], sources):
        parent = node["parent"]
        px, py = (boxes[parent][0], boxes[parent][1]) if parent is not None else (0, 0)
        boxes.append([px + source["x"], py + source["y"], source["width"], source["height"]])
    return boxes


def run_silk(cli, document, cycle, iterations):
    request = dict(document, updates={"iterations": iterations, "cycle": cycle})
    done = subprocess.run([cli], input=json.dumps(request).encode(), capture_output=True, check=True, timeout=1800)
    samples = json.loads(done.stderr.decode().strip().splitlines()[-1])["layoutNs"]
    return samples, absolute(document, json.loads(done.stdout)["sources"])


def run_harness(binary, document_path, cycle_path, iterations):
    done = subprocess.run([binary, str(document_path), str(iterations), "--dump", "--updates", str(cycle_path)],
                          capture_output=True, check=True, timeout=1800)
    result = json.loads(done.stdout)
    return result["layoutNs"], result["boxes"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--silk", required=True)
    parser.add_argument("--taffy")
    parser.add_argument("--yoga")
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--only", help="comma-separated workload names")
    parser.add_argument("--json", help="write results to this file")
    args = parser.parse_args()
    names = args.only.split(",") if args.only else list(W.WORKLOADS)
    engines = [(n, b) for n, b in (("taffy", args.taffy), ("yoga", args.yoga)) if b]
    results = {}
    ratios = {n: [] for n, _ in engines}
    print(f"{'scenario':<30}{'silk ms':>10}" + "".join(f"{n + ' ms':>11}{'ratio':>8}{'diff':>8}" for n, _ in engines))
    with tempfile.TemporaryDirectory() as tmp:
        for name in names:
            document = W.WORKLOADS[name]()
            document_path = Path(tmp) / f"{name}.json"
            document_path.write_text(json.dumps(document))
            cases = [(scenario, cycle, document, document_path) for scenario, cycle in scenarios(document).items()]
            if "width" in document["nodes"][0]["style"]:
                fluid_document = fluid(document)
                fluid_path = Path(tmp) / f"{name}-fluid.json"
                fluid_path.write_text(json.dumps(fluid_document))
                cases.append(("fluid-resize", scenarios(fluid_document)["resize"], fluid_document, fluid_path))
            for scenario, cycle, document, document_path in cases:
                cycle_path = Path(tmp) / f"{name}-{scenario}.json"
                cycle_path.write_text(json.dumps(cycle))
                silk_ns, silk_boxes = run_silk(args.silk, document, cycle, args.iterations)
                row = {"silk_median_ms": statistics.median(silk_ns) / 1e6}
                line = f"{name + '/' + scenario:<30}{row['silk_median_ms']:>10.3f}"
                for engine, binary in engines:
                    ns, boxes = run_harness(binary, document_path, cycle_path, args.iterations)
                    bad = mismatches(silk_boxes, boxes)
                    ms = statistics.median(ns) / 1e6
                    ratio = row["silk_median_ms"] / ms if ms > 0 else math.inf
                    ratios[engine].append(ratio)
                    row[engine] = {"median_ms": ms, "ratio": ratio, "mismatched_nodes": len(bad)}
                    line += f"{ms:>11.3f}{ratio:>7.2f}x{len(bad):>8}"
                print(line, flush=True)
                results[f"{name}/{scenario}"] = row
    for engine, values in ratios.items():
        finite = [v for v in values if 0 < v < math.inf]
        if finite:
            print(f"geomean silk/{engine}: {math.exp(sum(map(math.log, finite)) / len(finite)):.2f}x")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
