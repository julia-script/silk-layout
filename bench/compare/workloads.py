#!/usr/bin/env python3
"""Generate the cross-engine layout workloads shared by silk-layout, taffy and yoga.

Each workload is a silk-layout CLI document (viewport + flat node list). Every style
property the harnesses translate is written explicitly, so engine defaults (for example
yoga's column direction and zero flex-shrink) do not change the layout being measured.
Supported properties: display (block | flex), boxSizing (always border-box, the taffy and yoga default), flexDirection, flexWrap, flexGrow,
flexShrink, flexBasis, width, height, minWidth, minHeight, maxWidth, maxHeight,
padding, margin, border, rowGap, columnGap, alignItems, justifyContent, plus leaf
"measure" fixtures {minWidth, maxWidth, height}: width is the available width clamped
to [minWidth, maxWidth] (min-content = minWidth, max-content = maxWidth), height fixed.
Dimensions are numbers (px), "auto", or {"percent": fraction}.
"""
import json
import random
import sys
from pathlib import Path


def flex(**extra):
    style = {"display": "flex", "boxSizing": "border-box", "flexDirection": "row", "flexWrap": "nowrap", "flexShrink": 1}
    style.update(extra)
    return style


def block(**extra):
    style = {"display": "block", "boxSizing": "border-box"}
    style.update(extra)
    return style


def document(nodes, width=1000, height=800):
    return {"viewport": {"width": width, "height": height}, "nodes": nodes}


def random_dimension(rng):
    roll = rng.random()
    if roll < 0.2:
        return "auto"
    if roll < 0.8:
        return round(rng.uniform(0, 500), 2)
    return {"percent": round(rng.random(), 4)}


def random_tree(count, fanout, seed):
    """Taffy's "deep random" shape: flex containers with random length/percent/auto sizes."""
    rng = random.Random(seed)
    nodes = [{"parent": None, "style": flex(width=random_dimension(rng), height=random_dimension(rng))}]
    queue = [0]
    while len(nodes) < count:
        parent = queue.pop(0)
        for _ in range(fanout):
            if len(nodes) >= count:
                break
            nodes.append({"parent": parent, "style": flex(width=random_dimension(rng), height=random_dimension(rng))})
            queue.append(len(nodes) - 1)
    return document(nodes)


def block_stack(count):
    nodes = [{"parent": None, "style": block(width=600)}]
    nodes += [{"parent": 0, "style": block(height=10 + i % 7, padding=1)} for i in range(count)]
    return document(nodes)


def flex_grow(count):
    nodes = [{"parent": None, "style": flex(width=1000)}]
    nodes += [{"parent": 0, "style": flex(flexGrow=1 + i % 3, flexBasis=0, height=20, minWidth=0)} for i in range(count)]
    return document(nodes)


def flex_wrap(count):
    nodes = [{"parent": None, "style": flex(width=500, flexWrap="wrap", rowGap=2, columnGap=3)}]
    nodes += [{"parent": 0, "style": flex(width=20 + i % 17, height=10 + i % 5, flexShrink=0)} for i in range(count)]
    return document(nodes)


def nested_chain(depth):
    nodes = [{"parent": None, "style": block(width=800)}]
    for i in range(1, depth):
        style = block(padding=1) if i % 2 else flex(flexDirection="column", padding=1)
        nodes.append({"parent": i - 1, "style": style})
    nodes.append({"parent": depth - 1, "style": block(height=10)})
    return document(nodes)


def measured_wrap(count):
    nodes = [{"parent": None, "style": flex(width=1000, flexWrap="wrap")}]
    nodes += [{"parent": 0, "style": flex(), "measure": {"minWidth": 20 + i % 11, "maxWidth": 60 + i % 37, "height": 20}} for i in range(count)]
    return document(nodes)


def app_cards(cards):
    """A feed of cards: block page, flex rows of cards, each card a column with a header row,
    measured body text and a footer of fixed buttons."""
    nodes = [{"parent": None, "style": block(width=1200, padding=8)}]

    def add(parent, style, measure=None):
        node = {"parent": parent, "style": style}
        if measure:
            node["measure"] = measure
        nodes.append(node)
        return len(nodes) - 1

    row = None
    for card in range(cards):
        if card % 4 == 0:
            row = add(0, flex(flexWrap="nowrap", columnGap=8, marginBottom=8, alignItems="stretch"))
        body = add(row, flex(flexDirection="column", flexGrow=1, flexBasis=0, minWidth=0, padding=6, border=1, rowGap=4))
        header = add(body, flex(alignItems="center", columnGap=6))
        add(header, flex(width=32, height=32, flexShrink=0))
        add(header, flex(flexGrow=1), {"minWidth": 40, "maxWidth": 160 + card % 50, "height": 16})
        add(body, flex(), {"minWidth": 60, "maxWidth": 900 + card % 200, "height": 48})
        footer = add(body, flex(justifyContent="flex-end", columnGap=4))
        for _ in range(3):
            add(footer, flex(width=48, height=24, flexShrink=0))
    return document(nodes, 1200, 800)


WORKLOADS = {
    "deep-random-1k": lambda: random_tree(1_000, 10, 1),
    "deep-random-10k": lambda: random_tree(10_000, 10, 2),
    "deep-random-100k": lambda: random_tree(100_000, 10, 3),
    "block-stack-10k": lambda: block_stack(10_000),
    "flex-grow-1k": lambda: flex_grow(1_000),
    "flex-wrap-10k": lambda: flex_wrap(10_000),
    "nested-chain-200": lambda: nested_chain(200),
    "measured-wrap-1k": lambda: measured_wrap(1_000),
    "app-cards-500": lambda: app_cards(500),
}


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "workloads")
    out.mkdir(parents=True, exist_ok=True)
    for name, build in WORKLOADS.items():
        (out / f"{name}.json").write_text(json.dumps(build(), separators=(",", ":")))
        print(out / f"{name}.json")


if __name__ == "__main__":
    main()
