#!/usr/bin/env python3
"""Check kept layout results against fresh layouts on every fixture.

For each unfragmented fixture, the native adapter lays out the input, applies
a cycle of updates (viewport resizes, restyling an element with its own style,
hiding and showing it, editing a text node), and the final geometry must equal
a fresh layout of the final document. Fixtures only check single computes;
this covers what the LayoutTree keeps between them.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(cli, document):
    completed = subprocess.run([cli], input=json.dumps(document).encode(), capture_output=True, timeout=120)
    return completed.returncode, completed.stdout


def scenarios(document):
    viewport = document.get("viewport", {"width": 800, "height": 600})
    width, height = viewport.get("width", 800), viewport.get("height", 600)
    yield "resize", [{"viewport": {"width": width - 37, "height": height}},
                     {"viewport": {"width": width + 23, "height": height - 19}},
                     {"viewport": {"width": width, "height": height}}]
    elements = [index for index, node in enumerate(document["nodes"]) if node.get("parent") is not None and "style" in node]
    if elements:
        node = elements[len(elements) // 2]
        style = document["nodes"][node]["style"]
        yield "restyle", [{"set": [{"node": node, "style": style}]}]
        hidden = dict(style, display="none")
        yield "hide", [{"set": [{"node": node, "style": hidden}]}, {"set": [{"node": node, "style": style}]}]
    texts = [index for index, node in enumerate(document["nodes"]) if isinstance(node.get("text"), str)]
    if texts:
        node = texts[len(texts) // 2]
        text = document["nodes"][node]["text"]
        yield "retext", [{"set": [{"node": node, "text": text + " more words"}]}]
        yield "retext-word", [{"viewport": {"width": width - 37, "height": height}}, {"set": [{"node": node, "text": "x" + text}]}]


def final(document, cycle):
    result = json.loads(json.dumps(document))
    for step in cycle:
        if "viewport" in step:
            result["viewport"] = step["viewport"]
        for change in step.get("set", []):
            for key in ("style", "text"):
                if key in change:
                    result["nodes"][change["node"]][key] = change[key]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    args = parser.parse_args()
    cli = str(Path(args.cli).resolve())
    checked = 0
    failures = []
    for path in sorted((ROOT / "tests" / "fixtures").glob("*/*.json")):
        document = json.loads(path.read_text()).get("input")
        if not isinstance(document, dict) or "fragmentation" in document or "nodes" not in document:
            continue
        for name, cycle in scenarios(document):
            fresh_code, fresh = run(cli, final(document, cycle))
            if fresh_code != 0:
                continue
            code, incremental = run(cli, dict(document, updates={"iterations": len(cycle), "cycle": cycle}))
            checked += 1
            if code != 0 or json.loads(incremental) != json.loads(fresh):
                failures.append(f"{path.stem} {name}")
    for failure in failures:
        print("differs from a fresh layout:", failure)
    print(f"{checked} update scenarios, {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
