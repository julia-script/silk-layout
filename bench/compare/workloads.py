#!/usr/bin/env python3
"""Generate the cross-engine layout workloads shared by silk-layout, taffy and yoga.

Each workload is a silk-layout CLI document (viewport + flat node list). Every style
property the harnesses translate is written explicitly, so engine defaults (for example
yoga's column direction and zero flex-shrink) do not change the layout being measured.
Supported properties: display (block | flex | grid), boxSizing (always border-box),
flexDirection, flexWrap, flexGrow, flexShrink, flexBasis, width, height, minWidth,
minHeight, maxWidth, maxHeight, padding, margin, border, rowGap, columnGap,
alignItems, justifyContent, plus numeric/named grid placement, named areas,
count/auto repeats, fixed/percent/fr/minmax, intrinsic and fit-content tracks,
cycling implicit tracks, all four auto-flows, and item/self alignment.
Grid workloads compare only with taffy; nonzero order is not supported.
Leaf "measure" fixtures {minWidth, maxWidth, height, wrap?} clamp available width
to [minWidth, maxWidth]; wrap=true multiplies height by the synthetic line count.
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


def grid_fixed(count=10_000):
    """Ten fixed columns and fixed 160px rows, with explicitly placed wrapping leaves."""
    columns = [64, 96, 128, 160, 192] * 2
    width = sum(columns) + 6 * (len(columns) - 1)
    nodes = [{"parent": None, "style": {
        "display": "grid", "boxSizing": "border-box", "width": width,
        "gridTemplateColumns": columns,
        "gridTemplateRows": [160] * ((count + len(columns) - 1) // len(columns)),
        "gridAutoRows": 160, "gridAutoFlow": "row",
        "columnGap": 6, "rowGap": 4,
        "justifyItems": "stretch", "alignItems": "stretch",
    }}]
    for i in range(count):
        row, column = divmod(i, len(columns))
        nodes.append({"parent": 0, "style": block(
            minWidth=0,
            gridColumnStart=column + 1, gridColumnEnd={"span": 1},
            gridRowStart=row + 1, gridRowEnd=row + 2,
            alignSelf=("start", "end", "center", "stretch")[i % 4],
        ), "measure": {"minWidth": 16, "maxWidth": 420 + i % 181, "height": 12, "wrap": True}})
    return document(nodes, width, 800)


def grid_fr(count=10_000):
    """Ten 40/80/120/160/200px columns (twice), with wrapping text sizing auto rows."""
    columns = [{"minmax": [0, {"fr": 1}]}, {"fr": 2}, {"fr": 3}, {"fr": 4}, {"fr": 5}] * 2
    width = 1254  # (1254 - nine 6px gaps) / 30fr = 40px/fr.
    nodes = [{"parent": None, "style": {
        "display": "grid", "boxSizing": "border-box", "width": width,
        "gridTemplateColumns": columns, "gridAutoRows": ["auto"], "gridAutoFlow": "row",
        "columnGap": 6, "rowGap": 4,
        "justifyItems": "stretch", "alignItems": "start",
    }}]
    for i in range(count):
        row, column = divmod(i, len(columns))
        nodes.append({"parent": 0, "style": block(
            minWidth=0,
            gridColumnStart={"line": column + 1}, gridColumnEnd=column + 2,
            gridRowStart=row + 1, gridRowEnd={"span": 1},
        ), "measure": {"minWidth": 16, "maxWidth": 720 + i % 241, "height": 12, "wrap": True}})
    return document(nodes, width, 800)


def grid_intrinsic(count=10_000):
    """Intrinsic columns, capped growth, spanning contributions and wrapping auto rows."""
    columns = ["min-content", "max-content", {"fitContent": 120}, {"minmax": [40, "max-content"]}, "auto"] * 2
    width = 1800
    nodes = [{"parent": None, "style": {
        "display": "grid", "boxSizing": "border-box", "width": width,
        "gridTemplateColumns": columns, "gridAutoRows": "auto", "gridAutoFlow": "row",
        "columnGap": 6, "rowGap": 4, "justifyItems": "stretch", "alignItems": "start",
    }}]
    for i in range(count):
        row, column = divmod(i, len(columns))
        span = 2 if i % 17 == 0 and column < len(columns) - 1 else 1
        nodes.append({"parent": 0, "style": block(
            gridColumnStart=column + 1, gridColumnEnd={"span": span},
            gridRowStart=row + 1, gridRowEnd=row + 2,
        ), "measure": {"minWidth": 16 + i % 17, "maxWidth": 120 + i % 181, "height": 12, "wrap": True}})
    return document(nodes, width, 800)


def grid_nested(count=1000):
    """Three grid levels with intrinsic probes and separately retained scratch buffers."""
    width = 1272
    nodes = [{"parent": None, "style": {
        "display": "grid", "boxSizing": "border-box", "width": width,
        "gridTemplateColumns": [{"minmax": [0, {"fr": 1}]}] * 10,
        "gridAutoRows": "auto", "gridAutoFlow": "row", "columnGap": 8, "rowGap": 8,
        "justifyItems": "stretch", "alignItems": "stretch",
    }}]
    for i in range(count):
        card = len(nodes)
        nodes.append({"parent": 0, "style": {
            "display": "grid", "boxSizing": "border-box",
            "gridColumnStart": i % 10 + 1, "gridRowStart": i // 10 + 1,
            "gridTemplateColumns": ["min-content", {"fitContent": 48}, {"minmax": [0, {"fr": 1}]}],
            "gridAutoRows": "auto", "gridAutoFlow": "row", "columnGap": 4,
            "justifyItems": "stretch", "alignItems": "start",
        }})
        for column, maximum in [(1, 80 + i % 21), (2, 48)]:
            nodes.append({"parent": card, "style": block(gridColumnStart=column, gridRowStart=1),
                          "measure": {"minWidth": 8, "maxWidth": maximum, "height": 8, "wrap": True}})
        nested = len(nodes)
        nodes.append({"parent": card, "style": {
            "display": "grid", "boxSizing": "border-box", "minWidth": 0,
            "gridColumnStart": 3, "gridRowStart": 1,
            "gridTemplateColumns": ["auto", {"minmax": [0, {"fr": 1}]}],
            "gridAutoRows": "auto", "gridAutoFlow": "row",
            "justifyItems": "stretch", "alignItems": "start",
        }})
        for column, maximum in [(1, 32), (2, 64 + i % 17)]:
            nodes.append({"parent": nested, "style": block(minWidth=0, gridColumnStart=column, gridRowStart=1),
                          "measure": {"minWidth": 8, "maxWidth": maximum, "height": 12, "wrap": True}})
    return document(nodes, width, 800)


def grid_auto_placement(count=10_000):
    """Matching sparse/dense grids with automatic spans that leave reusable holes."""
    columns = [48] * 10
    width = sum(columns) + 4 * (len(columns) - 1)
    nodes = [{"parent": None, "style": block(width=width)}]
    for flow, item_count in [("row", (count + 1) // 2), ("row dense", count // 2)]:
        grid = len(nodes)
        nodes.append({"parent": 0, "style": {
            "display": "grid", "boxSizing": "border-box", "width": width,
            "gridTemplateColumns": columns, "gridTemplateRows": [24],
            "gridAutoRows": [24, 32, 40], "gridAutoFlow": flow,
            "columnGap": 4, "rowGap": 2,
            "justifyContent": "start", "alignContent": "start",
            "justifyItems": "start", "alignItems": "start",
        }})
        for i in range(item_count):
            nodes.append({"parent": grid, "style": block(
                width=24, height=12,
                gridColumnStart="auto", gridColumnEnd={"span": (3, 3, 3, 2, 1)[i % 5]},
                gridRowStart="auto", gridRowEnd={"span": 2 if i % 7 == 0 else 1},
            )})
    return document(nodes, width, 800)


def grid_areas_shell(cards=500):
    """Named-area application shell with a flex navigation rail and block/flex cards."""
    nodes = [{"parent": None, "style": {
        "display": "grid", "boxSizing": "border-box", "width": 1200, "padding": 12,
        "gridTemplateAreas": [
            ["header", "header"],
            ["nav", "main"],
            ["footer", "footer"],
        ],
        "gridTemplateColumns": [200, {"minmax": [0, {"fr": 1}]}],
        "gridTemplateRows": [64, "auto", 32],
        "gridAutoFlow": "row", "columnGap": 12, "rowGap": 12,
        "justifyItems": "stretch", "alignItems": "stretch",
    }}]

    def add(parent, style, measure=None):
        node = {"parent": parent, "style": style}
        if measure is not None:
            node["measure"] = measure
        nodes.append(node)
        return len(nodes) - 1

    def area(name):
        return {"gridColumnStart": name, "gridColumnEnd": name,
                "gridRowStart": name, "gridRowEnd": name}

    header = add(0, flex(**area("header"), columnGap=12, alignItems="center"))
    add(header, block(width=120, height=32, flexShrink=0))
    add(header, block(flexGrow=1, minWidth=0),
        {"minWidth": 40, "maxWidth": 420, "height": 20, "wrap": True})
    add(header, block(width=64, height=24, flexShrink=0))
    nav = add(0, flex(**area("nav"), flexDirection="column", rowGap=4,
                      alignItems="stretch", justifyContent="flex-start", alignSelf="start"))
    for i in range(24):
        add(nav, block(padding=4, flexShrink=0),
            {"minWidth": 24, "maxWidth": 120 + i % 9 * 12, "height": 16, "wrap": True})
    main = add(0, block(**area("main"), minWidth=0))
    row = None
    for card in range(cards):
        if card % 4 == 0:
            row = add(main, flex(columnGap=8, marginBottom=8, alignItems="stretch"))
        body = add(row, block(flexGrow=1, flexBasis=0, minWidth=0, padding=8, border=1))
        title = add(body, flex(columnGap=6, alignItems="center", marginBottom=6))
        add(title, block(width=24, height=24, flexShrink=0))
        add(title, block(flexGrow=1, minWidth=0),
            {"minWidth": 24, "maxWidth": 100 + card % 41, "height": 16, "wrap": True})
        add(body, block(minWidth=0, marginBottom=6),
            {"minWidth": 32, "maxWidth": 360 + card % 181, "height": 12, "wrap": True})
        actions = add(body, flex(columnGap=4, justifyContent="flex-end"))
        for _ in range(2):
            add(actions, block(width=48, height=24, flexShrink=0))
    footer = add(0, flex(**area("footer"), justifyContent="flex-end", alignItems="center"))
    add(footer, block(width=180, height=16))
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
    "grid-fixed": grid_fixed,
    "grid-fr": grid_fr,
    "grid-intrinsic": grid_intrinsic,
    "grid-nested": grid_nested,
    "grid-auto-placement": grid_auto_placement,
    "grid-areas-shell": grid_areas_shell,
}


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "workloads")
    out.mkdir(parents=True, exist_ok=True)
    for name, build in WORKLOADS.items():
        (out / f"{name}.json").write_text(json.dumps(build(), separators=(",", ":")))
        print(out / f"{name}.json")


if __name__ == "__main__":
    main()
