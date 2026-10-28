#!/usr/bin/env python3
"""Check kept layout results against fresh layouts on random documents.

Each seed builds a random tree of block, flex and inline boxes with text,
applies a random cycle of updates (restyles, text edits, viewport resizes)
through the native adapter's update mode, and the final geometry must equal a
fresh layout of the final document. Modes bias the updates towards the paths
compute takes for them:

  mixed   any style property, some boxes relatively positioned
  inner   changes only the box itself reads (fixed border-box sizes)
  insets  padding and border alone
  text    text edits, some of them one letter in a word, under inline boxes

  python3 scripts/fuzz-updates.py --cli build/.../silk_layout_cli [--seeds 200] [--mode inner,text]
"""
import argparse
import copy
import json
import random
import subprocess
import sys

MODES = {
    "mixed": {"relative": 0.1, "text": 0.2, "inline": 0.1, "typo": False, "kind": "any", "repeat": 1},
    "inner": {"relative": 0.0, "text": 0.3, "inline": 0.0, "typo": True, "kind": "inner", "repeat": 3},
    "insets": {"relative": 0.0, "text": 0.3, "inline": 0.2, "typo": False, "kind": "insets", "repeat": 2},
    "text": {"relative": 0.0, "text": 1.0, "inline": 0.2, "typo": True, "kind": "any", "repeat": 2},
}
WORDS = ["ab", "word", "x", "longer", "q"]


def run(cli, document):
    completed = subprocess.run([cli], input=json.dumps(document).encode(), capture_output=True, timeout=120)
    return completed.returncode, completed.stdout


def random_style(r, mode):
    if r.random() < mode["inline"]:
        return {"display": "inline"}
    kind = r.choice(["block", "flex"])
    style = {"display": kind, "boxSizing": "border-box"}
    if kind == "flex":
        style["flexDirection"] = r.choice(["row", "column"])
        style["flexWrap"] = r.choice(["nowrap", "wrap"])
        style["alignItems"] = r.choice(["stretch", "flex-start", "center", "baseline"])
        if r.random() < .3: style["justifyContent"] = r.choice(["flex-start", "center", "space-between"])
        if r.random() < .5: style["columnGap"] = r.randint(0, 8)
    if r.random() < .5: style["padding"] = r.randint(0, 6)
    if r.random() < .3: style["marginTop"] = r.randint(0, 6)
    if r.random() < .3: style["width"] = r.randint(10, 200)
    if r.random() < .3: style["height"] = r.randint(5, 80)
    if r.random() < .3: style["flexGrow"] = r.randint(0, 2)
    if r.random() < mode["relative"]: style["position"] = "relative"
    if r.random() < .1: style["order"] = r.randint(-1, 2)
    if r.random() < .1: style["alignSelf"] = r.choice(["auto", "center", "baseline"])
    if r.random() < .1: style["minWidth"] = r.randint(0, 50)
    if r.random() < .1: style["border"] = r.randint(0, 3)
    return style


def text_edit(r, mode, nodes, node):
    text = nodes[node]["text"]
    if mode["typo"] and len(text) > 1:
        at = r.randrange(len(text))
        if text[at] != " ":
            return {"node": node, "text": text[:at] + r.choice("abwxl") + text[at + 1:]}
    return {"node": node, "text": " ".join(r.choice(WORDS) for _ in range(r.randint(1, 9)))}


def style_edit(r, mode, nodes, node):
    style = dict(nodes[node]["style"])
    if mode["kind"] == "insets":
        style[r.choice(["padding", "paddingLeft", "paddingTop", "paddingBottom", "border", "borderTop"])] = r.choice([0, 0, 1, 3, 9])
    elif mode["kind"] == "inner":
        # A fixed border-box size from the start, so padding stays inside it.
        for key, value in (("width", r.randint(20, 300)), ("height", r.randint(10, 150)), ("flexShrink", 0)):
            style.setdefault(key, value)
        nodes[node]["style"] = dict(style)
        key = r.choice(["padding", "paddingLeft", "border", "flexDirection", "alignItems", "justifyContent", "columnGap", "flexWrap"])
        choices = {"flexDirection": ["row", "column"], "alignItems": ["stretch", "center", "baseline"], "justifyContent": ["center", "flex-end"], "flexWrap": ["wrap", "nowrap"]}
        style[key] = r.choice(choices[key]) if key in choices else r.randint(0, 9)
    else:
        key = r.choice(["padding", "width", "height", "marginTop", "flexGrow", "flexShrink", "order", "alignSelf", "minWidth", "maxHeight", "border", "insetTop", "paddingLeft"])
        if key == "alignSelf": style[key] = r.choice(["auto", "center", "baseline", "flex-end"])
        elif key in ("width", "height", "minWidth", "maxHeight"): style[key] = r.randint(0, 120)
        else: style[key] = r.randint(0, 6)
    return {"node": node, "style": style}


def case(seed, mode):
    r = random.Random(seed)
    root = {"display": r.choice(["block", "flex"]), "boxSizing": "border-box", "padding": 4}
    # A fixed root width hides viewport resizes from everything below it.
    if r.random() < .5: root["width"] = 800
    nodes = [{"parent": None, "style": root}]
    depth = [0]
    for _ in range(1, r.randint(20, 150)):
        parent = r.choice([index for index in range(len(nodes)) if depth[index] < 7 and "text" not in nodes[index]])
        if r.random() < .15:
            nodes.append({"parent": parent, "text": " ".join(r.choice(WORDS[:4]) for _ in range(r.randint(1, 8)))})
        else:
            nodes.append({"parent": parent, "style": random_style(r, mode)})
        depth.append(depth[parent] + 1)
    document = {"viewport": {"width": 800, "height": 600}, "nodes": nodes}
    texts = [index for index, node in enumerate(nodes) if "text" in node]
    elements = [index for index, node in enumerate(nodes) if "style" in node and index > 0]
    cycle = []
    for _ in range(r.randint(1, 4)):
        changes = []
        for _ in range(r.randint(1, 2)):
            if texts and r.random() < mode["text"]:
                changes.append(text_edit(r, mode, nodes, r.choice(texts)))
            else:
                changes.append(style_edit(r, mode, nodes, r.choice(elements)))
        step = {"set": changes}
        if r.random() < .2: step["viewport"] = {"width": r.randint(500, 900), "height": 600}
        cycle.append(step)
    return document, cycle


def final(document, cycle):
    result = copy.deepcopy(document)
    for step in cycle:
        if "viewport" in step: result["viewport"] = step["viewport"]
        for change in step.get("set", []):
            if "text" in change: result["nodes"][change["node"]]["text"] = change["text"]
            else: result["nodes"][change["node"]]["style"] = change["style"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--seeds", type=int, default=200, help="seeds per mode")
    parser.add_argument("--first", type=int, default=0, help="first seed")
    parser.add_argument("--mode", default=",".join(MODES))
    args = parser.parse_args()
    failures = 0
    total = 0
    for name in args.mode.split(","):
        mode = MODES[name]
        for seed in range(args.first, args.first + args.seeds):
            document, cycle = case(seed, mode)
            fresh_code, fresh = run(args.cli, final(document, cycle))
            if fresh_code != 0: continue
            total += 1
            # Repeating the cycle ends on the same document.
            updates = {"iterations": len(cycle) * mode["repeat"], "cycle": cycle}
            code, kept = run(args.cli, dict(document, updates=updates))
            if code != 0 or json.loads(kept) != json.loads(fresh):
                failures += 1
                print(f"differs from a fresh layout: mode {name} seed {seed}", file=sys.stderr)
    print(f"{total} random update cycles, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
