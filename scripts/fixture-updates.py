#!/usr/bin/env python3
"""Check kept layout results against fresh layouts on every fixture.

For each unfragmented fixture, the native adapter lays out the input, applies
a cycle of updates (viewport resizes, restyling an element with its own style or with changed style facts,
hiding and showing it, editing a text node, scrolling a scroll container), and
the final geometry must equal a fresh layout of the final document. Fixtures
only check single computes; this covers what the LayoutTree keeps between them.
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
        # Style facts kept across computes: relative positioning, a negative
        # margin, a length width, each on a box and then on its parent.
        for name, target in (("restyle-facts", node), ("restyle-facts-parent", document["nodes"][node]["parent"])):
            original = document["nodes"][target].get("style")
            if original is None:
                continue
            moved = dict(original, position="relative", insetTop=3, marginLeft=-4, width=57)
            yield name, [{"set": [{"node": target, "style": moved}]}]
            yield name + "-back", [{"set": [{"node": target, "style": moved}]}, {"set": [{"node": target, "style": original}]}]
    scrollers = [index for index, node in enumerate(document["nodes"])
                 if "style" in node and ({node["style"].get("overflowX"), node["style"].get("overflowY"), node["style"].get("overflow")} & {"scroll", "auto", "hidden"})]
    if scrollers:
        node = scrollers[len(scrollers) // 2]
        offset = document["nodes"][node].get("scrollOffset", {"x": 0, "y": 0})
        yield "scroll", [{"viewport": {"width": width - 37, "height": height}},
                         {"set": [{"node": node, "scrollOffset": {"x": offset.get("x", 0) + 7, "y": offset.get("y", 0) + 5}}]}]
    texts = [index for index, node in enumerate(document["nodes"]) if isinstance(node.get("text"), str)]
    if texts:
        node = texts[len(texts) // 2]
        text = document["nodes"][node]["text"]
        yield "retext", [{"set": [{"node": node, "text": text + " more words"}]}]
        yield "retext-word", [{"viewport": {"width": width - 37, "height": height}}, {"set": [{"node": node, "text": "x" + text}]}]
    grids = [index for index, node in enumerate(document["nodes"])
             if node.get("style", {}).get("display") in ("grid", "block grid", "inline-grid", "inline grid")]
    if grids:
        owner = grids[0]
        original = document["nodes"][owner]["style"]
        flow = "row" if original.get("gridAutoFlow") == "column dense" else "column dense"
        variants = (
            ("grid-template", dict(original, gridTemplateColumns=[35, {"minmax": [0, {"fr": 1}]}],
                                   gridTemplateRows=[17, "auto"])),
            ("grid-areas", dict(original, gridTemplateAreas=[["a", "a"], ["b", "c"]])),
            ("grid-flow", dict(original, gridAutoFlow=flow)),
            ("grid-implicit", dict(original, gridAutoColumns=[11, 23], gridAutoRows=[7, 19])),
            ("grid-gaps", dict(original, columnGap=7, rowGap=5)),
        )
        for name, changed in variants:
            forward = {"set": [{"node": owner, "style": changed}]}
            yield name, [forward]
            yield name + "-back", [forward, {"set": [{"node": owner, "style": original}]}]
        children = [index for index, node in enumerate(document["nodes"])
                    if node.get("parent") == owner and "style" in node]
        if children:
            child = children[0]
            original = document["nodes"][child]["style"]
            placed = dict(original, gridColumnStart=2, gridColumnEnd={"span": 1},
                          gridRowStart=1, gridRowEnd={"span": 1})
            forward = {"set": [{"node": child, "style": placed}]}
            yield "grid-placement", [forward]
            yield "grid-placement-back", [forward, {"set": [{"node": child, "style": original}]}]
        inflow = [child for child in children
                  if document["nodes"][child]["style"].get("position") not in ("absolute", "fixed")]
        if inflow:
            child = inflow[0]
            original = document["nodes"][child]["style"]
            ordered = dict(original, order=original.get("order", 0) + 3)
            forward = {"set": [{"node": child, "style": ordered}]}
            yield "grid-order", [forward]
            yield "grid-order-back", [forward, {"set": [{"node": child, "style": original}]}]
    subgrids = [index for index in grids
                if any(document["nodes"][index]["style"].get(axis) == "subgrid"
                       or isinstance(document["nodes"][index]["style"].get(axis), dict)
                       and "subgrid" in document["nodes"][index]["style"][axis]
                       for axis in ("gridTemplateColumns", "gridTemplateRows"))]
    for child in subgrids:
        owner = document["nodes"][child].get("parent")
        if owner is None or owner not in grids:
            continue
        original = document["nodes"][owner]["style"]
        # Equal total extent must not alias different inherited track geometry.
        for axis in ("gridTemplateColumns", "gridTemplateRows"):
            before = dict(original, **{axis: [30, 70]})
            after = dict(original, **{axis: [60, 40]})
            yield f"subgrid-{child}-{axis}-redistribution", [
                {"set": [{"node": owner, "style": before}]},
                {"set": [{"node": owner, "style": after}]}]
        # Name changes can move descendants without changing a single track size.
        named = dict(original, gridTemplateColumns=[
            {"names": ["before"]}, 30, {"names": ["after"]}, 70])
        renamed = dict(original, gridTemplateColumns=[
            {"names": ["after"]}, 30, {"names": ["before"]}, 70])
        yield f"subgrid-{child}-ancestor-names", [
            {"set": [{"node": owner, "style": named}]},
            {"set": [{"node": owner, "style": renamed}]}]
        child_style = document["nodes"][child]["style"]
        for gap in ("columnGap", "rowGap"):
            yield f"subgrid-{child}-{gap}-inheritance", [
                {"set": [{"node": child, "style": dict(child_style, **{gap: 0})}]},
                {"set": [{"node": owner, "style": dict(original, **{gap: 13})},
                         {"node": child, "style": dict(child_style, **{gap: "normal"})}]}]


def final(document, cycle):
    result = json.loads(json.dumps(document))
    for step in cycle:
        if "viewport" in step:
            result["viewport"] = step["viewport"]
        for change in step.get("set", []):
            for key in ("style", "text", "scrollOffset"):
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
                if name.startswith(("grid-", "subgrid-")):
                    checked += 1
                    failures.append(f"{path.stem} {name}: fresh layout failed, exit {fresh_code}")
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
