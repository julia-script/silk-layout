"""Update-benchmark documents: ASCII-only scenes and the edits applied to them.

Each scenario is a cycle of steps. A step may replace the viewport and set a
node's whole style or text; cycles return to the initial document so every
timed compute does comparable work.
"""
import copy

WORDS = ("layout boxes flow into lines and every line of text breaks where the engine finds room "
         "for the next word while floats and flex items share the space they are given").split()


def font(size):
    return {"fontSize": size, "ascent": size * 0.9, "descent": size * 0.25, "lineGap": 0,
            "xHeight": size / 2, "capHeight": size * 0.7, "subscriptOffset": 2, "superscriptOffset": 4,
            "baselines": {"alphabetic": size * 0.9, "ideographic": size * 1.15, "central": size * 0.57, "mathematical": size * 0.6}}


FONTS = [font(15), font(13), font(26), font(40)]


def sentence(seed, count):
    return " ".join(WORDS[(seed * 7 + i) % len(WORDS)] for i in range(count))


class Doc:
    def __init__(self, viewport, root):
        self.nodes = [{"parent": None, "style": root}]
        self.viewport = viewport

    def element(self, parent, style):
        self.nodes.append({"parent": parent, "style": style})
        return len(self.nodes) - 1

    def text(self, parent, value):
        self.nodes.append({"parent": parent, "text": value})
        return len(self.nodes) - 1

    def document(self):
        return {"viewport": self.viewport, "fonts": FONTS, "nodes": self.nodes}

    def style(self, node, **changes):
        return {"node": node, "style": dict(self.nodes[node]["style"], **changes)}


def resize(width, height, steps=40):
    """A drag: every step is a new width, so no step repeats an earlier input."""
    return [{"viewport": {"width": width - step, "height": height}} for step in range(steps)]


def toggle(*steps):
    return [{"set": list(step)} if isinstance(step, (list, tuple)) else step for step in steps]


def cards(count=12):
    """The demo's pricing-card scene, scaled to twelve cards: nested flex with text."""
    doc = Doc({"width": 760, "height": 4000}, {"display": "flex", "flexWrap": "wrap", "columnGap": 16, "rowGap": 16,
                                              "padding": 20, "lineHeight": 1.5, "fontKey": 1, "boxSizing": "border-box"})
    marks = {}
    for card in range(count):
        box = doc.element(0, {"boxSizing": "border-box", "display": "flex", "flexDirection": "column", "flexGrow": 1,
                              "flexShrink": 1, "flexBasis": 200, "padding": 18, "border": 1, "rowGap": 10, "lineHeight": 1.5, "fontKey": 1})
        title = doc.element(box, {"lineHeight": 1.5, "fontKey": 2})
        doc.text(title, f"Plan {card}")
        price = doc.element(box, {"display": "flex", "alignItems": "baseline", "columnGap": 6, "lineHeight": 1.5, "fontKey": 1})
        amount = doc.element(price, {"lineHeight": 1.1, "fontKey": 4})
        doc.text(amount, f"{card * 9}")
        per = doc.element(price, {"lineHeight": 1.5, "fontKey": 2})
        doc.text(per, "per month")
        body = doc.element(box, {"lineHeight": 1.5, "fontKey": 1})
        paragraph = doc.text(body, sentence(card, 18))
        button = doc.element(box, {"marginTop": "auto", "padding": 10, "textAlign": "center", "lineHeight": 1.5, "fontKey": 2})
        doc.text(button, "Choose plan")
        if card == 5:
            marks = {"card": box, "button": button, "paragraph": paragraph}
    text = doc.nodes[marks["paragraph"]]["text"]
    return doc.document(), {
        "unchanged": toggle([]),
        "leaf-style": toggle([doc.style(marks["button"], padding=12)], [doc.style(marks["button"], padding=10)]),
        "card-style": toggle([doc.style(marks["card"], flexBasis=260)], [doc.style(marks["card"], flexBasis=200)]),
        "text-edit": toggle([{"node": marks["paragraph"], "text": text + " more"}], [{"node": marks["paragraph"], "text": text}]),
        "resize": resize(760, 4000),
    }


def article(sections=8):
    """Block flow: headings and long paragraphs, one float."""
    doc = Doc({"width": 680, "height": 4000}, {"padding": 24, "lineHeight": 1.6, "fontKey": 1})
    paragraphs = []
    for section in range(sections):
        heading = doc.element(0, {"lineHeight": 1.15, "marginBottom": 12, "fontKey": 3})
        doc.text(heading, f"Section {section}")
        if section == 0:
            doc.element(0, {"float": "left", "width": 140, "height": 110, "marginRight": 16, "border": 1})
        for index in range(4):
            block = doc.element(0, {"marginBottom": 12, "lineHeight": 1.6, "fontKey": 1})
            paragraphs.append((block, doc.text(block, sentence(section * 4 + index, 60))))
    target_block, target_text = paragraphs[13]
    text = doc.nodes[target_text]["text"]
    return doc.document(), {
        "unchanged": toggle([]),
        "leaf-style": toggle([doc.style(target_block, marginBottom=20)], [doc.style(target_block, marginBottom=12)]),
        "text-edit": toggle([{"node": target_text, "text": text + " more"}], [{"node": target_text, "text": text}]),
        # Inherited by the paragraph's anonymous text style.
        "font": toggle([doc.style(target_block, fontKey=2, lineHeight=1.3)], [doc.style(target_block, fontKey=1, lineHeight=1.6)]),
        # Changes which boxes are generated.
        "hide": toggle([doc.style(target_block, display="none")], [doc.style(target_block)]),
        "resize": resize(680, 4000),
    }


def rows(count=200):
    """A long list of flex rows: label, flexible spacer, value."""
    doc = Doc({"width": 600, "height": 4000}, {"padding": 8, "lineHeight": 1.4, "fontKey": 1})
    values = []
    for row in range(count):
        line = doc.element(0, {"display": "flex", "alignItems": "center", "columnGap": 8, "padding": 4, "lineHeight": 1.4, "fontKey": 1})
        label = doc.element(line, {"flexGrow": 1, "lineHeight": 1.4, "fontKey": 1})
        doc.text(label, sentence(row, 4))
        value = doc.element(line, {"lineHeight": 1.4, "fontKey": 2})
        values.append(doc.text(value, f"{row * 13}"))
    target = values[count // 2]
    return doc.document(), {
        "unchanged": toggle([]),
        "text-edit": toggle([{"node": target, "text": "99999"}], [{"node": target, "text": doc.nodes[target]["text"]}]),
        "resize": resize(600, 4000),
    }


def grid(count=200):
    """Nested grid cards: kept text, intrinsic rows, placement and repeat-count changes."""
    columns = [{"repeat": {"count": "auto-fit", "tracks": [{"minmax": [160, {"fr": 1}]}]}}]
    narrower = [{"repeat": {"count": "auto-fit", "tracks": [{"minmax": [120, {"fr": 1}]}]}}]
    doc = Doc({"width": 720, "height": 4000},
              {"display": "grid", "gridTemplateColumns": columns, "gridAutoFlow": "row dense",
               "columnGap": 12, "rowGap": 12, "padding": 20, "boxSizing": "border-box",
               "lineHeight": 1.4, "fontKey": 1})
    cells = []
    texts = []
    for index in range(count):
        cell = doc.element(0, {"display": "grid", "gridTemplateColumns": [{"fr": 1}],
                               "rowGap": 4, "padding": 8, "border": 1, "boxSizing": "border-box",
                               "lineHeight": 1.4, "fontKey": 1})
        heading = doc.element(cell, {"lineHeight": 1.4, "fontKey": 2})
        doc.text(heading, f"Cell {index}")
        body = doc.element(cell, {"lineHeight": 1.4, "fontKey": 1})
        cells.append(cell)
        texts.append(doc.text(body, sentence(index, 8)))
    target, text_node = cells[count // 2], texts[count // 2]
    text = doc.nodes[text_node]["text"]
    return doc.document(), {
        "unchanged": toggle([]),
        "text-edit": toggle([{"node": text_node, "text": "x" + text[1:]}],
                            [{"node": text_node, "text": text}]),
        "text-grow": toggle([{"node": text_node, "text": text + " " + sentence(count, 40)}],
                            [{"node": text_node, "text": text}]),
        "item-order": toggle([doc.style(target, order=-1)], [doc.style(target)]),
        "placement": toggle([doc.style(target, gridColumnStart=2, gridColumnEnd={"span": 2},
                                       gridRowStart=1, gridRowEnd={"span": 1})], [doc.style(target)]),
        "flow": toggle([doc.style(0, gridAutoFlow="column dense")], [doc.style(0)]),
        "template": toggle([doc.style(0, gridTemplateColumns=narrower)], [doc.style(0)]),
        "hide": toggle([doc.style(target, display="none")], [doc.style(target)]),
        "resize": resize(720, 4000),
    }


def all(small=False):
    """`small` scenes keep every scenario but lay out quickly in debug builds."""
    if small:
        return {"cards": cards(6), "article": article(4), "rows": rows(20), "grid": grid(12)}
    return {"cards": cards(), "article": article(), "rows": rows(), "grid": grid()}


if __name__ == "__main__":
    for name, (document, scenarios) in all().items():
        print(name, len(document["nodes"]), "nodes", list(scenarios))
