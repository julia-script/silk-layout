#!/usr/bin/env python3
"""Exercise malformed requests against the built adapter's stdin/stdout boundary."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    args = parser.parse_args()
    executable = str(Path(args.cli).resolve())
    request = {"viewport": {"width": 800, "height": 600}, "nodes": [{"parent": None, "style": {}}]}
    malformed = [b"", b"{", b"{}", b"[]",
        json.dumps({**request, "unknown": True}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": 0, "style": {}}]}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {"display": "block inline"}}]}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {}}, {"parent": 2, "style": {}}]}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {}, "natural": {"width": -1}}]}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {}, "natural": {}, "measure": {"minWidth": 0, "maxWidth": 0, "height": 0}}]}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {}, "natural": {}}, {"parent": 0, "style": {}}]}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {}, "measure": {"minWidth": 0, "maxWidth": 10, "height": 0, "hasLineBoxes": False}}]}).encode(),
        json.dumps({**request, "viewportScrollOffset": {"x": 0}}).encode(),
        json.dumps({"viewport": request["viewport"], "nodes": [{"parent": None, "style": {}, "scrollOffset": {"x": 0, "y": 0, "z": 0}}]}).encode(),
        json.dumps({**request, "viewportScrollOffset": {"x": "1e999", "y": 0}}).replace('"1e999"', '1e999').encode(),
    ]
    metrics = {"horizontal": 12, "vertical": 15, "overlay": False,
        "horizontalOnTop": False, "verticalOnLeft": False}
    host = {"auto": metrics, "thin": metrics}
    for invalid in [None, [], {}, {"auto": metrics}, {**host, "extra": metrics},
            {**host, "auto": {**metrics, "extra": 0}},
            {**host, "auto": {key: value for key, value in metrics.items() if key != "vertical"}}]:
        malformed.append(json.dumps({**request, "scrollbars": invalid}).encode())
    for field in ["horizontal", "vertical"]:
        for invalid in [-1, "12", None, True]:
            malformed.append(json.dumps({**request, "scrollbars": {
                **host, "auto": {**metrics, field: invalid}}}).encode())
        malformed.append(json.dumps({**request, "scrollbars": {
            **host, "auto": {**metrics, field: "1e999"}}}).replace('"1e999"', "1e999").encode())
    for field in ["overlay", "horizontalOnTop", "verticalOnLeft"]:
        malformed.append(json.dumps({**request, "scrollbars": {
            **host, "thin": {**metrics, field: 0}}}).encode())
    for field, invalid in [("scrollbarWidth", "wide"), ("scrollbarWidth", 0),
            ("scrollbarGutter", "both-edges"), ("scrollbarGutter", "auto stable"),
            ("scrollbarGutter", True)]:
        malformed.append(json.dumps({**request, "nodes": [{
            "parent": None, "style": {field: invalid}}]}).encode())
    for field, invalid in [("fontKey", -1), ("fontKey", 0.5), ("fontKey", 1),
                           ("lineHeight", -1), ("lineHeight", "10px"),
                           ("orphans", 0), ("orphans", 0.5), ("widows", 0), ("widows", 1.5),
                           ("lineHeight", {"length": -1}), ("lineHeight", {"percent": 1}),
                           ("verticalAlign", "auto"), ("verticalAlign", {"length": 1}),
                           ("baselineSource", "middle"), ("textAlign", "auto"),
                           ("textAlignLast", "normal"), ("textWrap", "balance"),
                           ("textOrientation", "rotated"), ("textOrientation", 0),
                           ("boxDecorationBreak", "repeat")]:
        malformed.append(json.dumps({**request, "nodes": [{
            "parent": None, "style": {field: invalid}}]}).encode())
    for invalid in ["keep discard", "keep keep", "inherit", "KEEP", "", None, 1, {}]:
        malformed.append(json.dumps({**request, "nodes": [{
            "parent": None, "style": {"marginBreak": invalid}}]}).encode())
    font = {"fontSize": 10, "ascent": 8, "descent": 2, "lineGap": 0,
            "xHeight": 5, "capHeight": 7, "subscriptOffset": -2, "superscriptOffset": 3,
            "baselines": {"alphabetic": 8, "ideographic": 10, "central": 5, "mathematical": 6}}
    for invalid in [None, {}, [{}], [{**font, "fontSize": -1}],
                    [{**font, "baselines": {"alphabetic": 8}}],
                    [{**font, "unknown": 0}], [{**font, "lineGap": "infinite"}]]:
        malformed.append(json.dumps({**request, "fonts": invalid}).encode())
    for text in [None, 42, True, [], {}]:
        malformed.append(json.dumps({**request, "nodes": [request["nodes"][0],
            {"parent": 0, "text": text}]}).encode())
    malformed.append(json.dumps({**request, "nodes": [{"parent": None, "text": "root"}]}).encode())
    for extra in [{"style": {}}, {"measure": {"minWidth": 0, "maxWidth": 0, "height": 0}},
                  {"natural": {}}, {"scrollOffset": {"x": 0, "y": 0}}, {"unknown": 0}]:
        malformed.append(json.dumps({**request, "nodes": [request["nodes"][0],
            {"parent": 0, "text": "A", **extra}]}).encode())
    malformed.append(json.dumps({**request, "nodes": [request["nodes"][0],
        {"parent": 0, "text": "A"}, {"parent": 1, "style": {}}]}).encode())
    malformed.append(json.dumps({**request, "nodes": [request["nodes"][0],
        {"parent": 0, "text": "A"}, {"parent": 1, "text": "B"}]}).encode())
    for text in ["hello!", "caf\u00e9", "\u05d0", "\u4e2d", "\U0001f600"]:
        malformed.append(json.dumps({**request, "nodes": [request["nodes"][0],
            {"parent": 0, "text": text}]}).encode())
    for style in [{"writingMode": "vertical-rl"}, {"writingMode": "sideways-lr"}]:
        malformed.append(json.dumps({**request, "nodes": [{"parent": None, "style": style},
            {"parent": 0, "text": "A"}]}).encode())
    frame = {"x": 0, "y": 0, "width": 100, "height": 50}
    fragmentation = {"kind": "column", "frames": [frame]}
    for invalid in [None, [], {}, {"kind": "column"}, {"frames": [frame]},
                    {**fragmentation, "kind": "screen"}, {**fragmentation, "kind": 0},
                    {**fragmentation, "frames": []}, {**fragmentation, "frames": {}},
                    {**fragmentation, "frames": [None]}, {**fragmentation, "unknown": 0}]:
        malformed.append(json.dumps({**request, "fragmentation": invalid}).encode())
    for field in frame:
        for invalid in [None, True, "10", [], {}] + ([-1] if field in ["width", "height"] else []):
            malformed.append(json.dumps({**request, "fragmentation": {
                **fragmentation, "frames": [{**frame, field: invalid}]}}).encode())
        malformed.append(json.dumps({**request, "fragmentation": {
            **fragmentation, "frames": [{key: value for key, value in frame.items() if key != field}]}}).encode())
        malformed.append(json.dumps({**request, "fragmentation": {
            **fragmentation, "frames": [{**frame, field: "1e999"}]}}).replace('"1e999"', "1e999").encode())
    malformed.append(json.dumps({**request, "fragmentation": {
        **fragmentation, "frames": [{**frame, "unknown": 0}]}}).encode())
    for invalid in [None, [], {}, {"x": 0}, {"y": 0}, {"x": 0, "y": 0, "z": 0},
                    {"x": "0", "y": 0}, {"x": 0, "y": True}]:
        malformed.append(json.dumps({**request, "fragmentation": {
            **fragmentation, "repeatStep": invalid}}).encode())
    for field in ["x", "y"]:
        malformed.append(json.dumps({**request, "fragmentation": {
            **fragmentation, "repeatStep": {"x": 0, "y": 0, field: "1e999"}}}).replace('"1e999"', "1e999").encode())
    for field, invalid in [("writingMode", "horizontal"), ("writingMode", None),
                           ("direction", "auto"), ("direction", True),
                           ("firstPage", "recto"), ("firstPage", 0),
                           ("recto", "verso"), ("recto", None)]:
        malformed.append(json.dumps({**request, "fragmentation": {
            **fragmentation, field: invalid}}).encode())
    ancestor = {"index": 0, **frame}
    nested = {**fragmentation, "frames": [frame, {**frame, "x": 110}],
              "contexts": ["page"], "ancestors": [[ancestor], [ancestor]]}
    for patch in [
            {"contexts": None}, {"contexts": ["screen"]}, {"contexts": [1]},
            {"ancestors": None}, {"ancestors": [[ancestor]]},
            {"ancestors": [[], [ancestor]]},
            {"ancestors": [[ancestor, ancestor], [ancestor]]},
            {"ancestors": [[{**ancestor, "extra": 0}], [ancestor]]},
            {"ancestors": [[{key: value for key, value in ancestor.items() if key != "x"}], [ancestor]]},
            {"ancestors": [[{**ancestor, "index": 1}], [{**ancestor, "index": 1}]]},
            {"ancestors": [[ancestor], [{**ancestor, "index": 2}]]},
            {"ancestors": [[ancestor], [{**ancestor, "width": 99}]]},
            {"contexts": ["page", "column"], "ancestors": [
                [ancestor, ancestor], [{**ancestor, "index": 1}, ancestor]]}]:
        malformed.append(json.dumps({**request, "fragmentation": {**nested, **patch}}).encode())
    for invalid in [-1, 0.5, True, "0"]:
        malformed.append(json.dumps({**request, "fragmentation": {
            **nested, "ancestors": [[{**ancestor, "index": invalid}], [ancestor]]}}).encode())
    for missing in ["contexts", "ancestors"]:
        malformed.append(json.dumps({**request, "fragmentation": {
            key: value for key, value in nested.items() if key != missing}}).encode())
    for data in malformed:
        completed = subprocess.run([executable], input=data, capture_output=True, timeout=10)
        assert completed.returncode == 1 and completed.stdout == b"", (data, completed.returncode, completed.stdout, completed.stderr)
    completed = subprocess.run([executable], input=json.dumps(request).encode(),
                               capture_output=True, timeout=10, check=True)
    output = json.loads(completed.stdout)
    assert output["schemaVersion"] == 3 and output["texts"] == []
    assert len(output["sources"]) == 1
    source = output["sources"][0]
    assert source["kind"] == "element" and "text" not in source
    assert (source["x"], source["y"], source["width"], source["height"]) == (0, 0, 800, 0)
    assert source["firstBox"] is not None
    assert output["boxes"][source["firstBox"]]["source"] == 0
    assert output["fragments"] and source["rects"]
    assert "hasFirstBaseline" in source and "hasLastBaseline" in source and "hasBaseline" not in source
    # Exhaustion is a typed driver error, never an implicit repeating fallback.
    fragmented_request = {**request, "fragmentation": fragmentation,
                          "nodes": [{"parent": None, "style": {"height": 120}}]}
    exhausted = subprocess.run([executable], input=json.dumps(fragmented_request).encode(),
                               capture_output=True, timeout=10)
    assert exhausted.returncode == 1 and exhausted.stdout == b"", exhausted
    assert b"FragmentationError" in exhausted.stderr and b"frame 1" in exhausted.stderr, exhausted.stderr
    repeated = {**fragmented_request, "fragmentation": {
        **fragmentation, "repeatStep": {"x": 110, "y": 0}}}
    completed = subprocess.run([executable], input=json.dumps(repeated).encode(),
                               capture_output=True, timeout=10, check=True)
    fragments = json.loads(completed.stdout)["fragments"]
    assert sorted({fragment["fragmentainer"] for fragment in fragments}) == [0, 1, 2]
    for font_key in [0, 1]:
        text_request = {**request, "fonts": [font], "nodes": [
            {"parent": None, "style": {"fontKey": font_key}},
            {"parent": 0, "text": "Ab12"}]}
        completed = subprocess.run([executable], input=json.dumps(text_request).encode(),
                                   capture_output=True, timeout=10, check=True)
        output = json.loads(completed.stdout)
        assert output["schemaVersion"] == 3
        source = output["sources"][1]
        assert source["kind"] == "text" and source["text"] == "Ab12"
        fragments = [fragment for fragment in output["fragments"] if fragment["kind"] == "text"]
        assert fragments and source["rects"]
        assert sum(fragment["size"]["width"] for fragment in fragments) == (20 if font_key else 0)
        records = [output["texts"][fragment["textData"]] for fragment in fragments]
        assert min(record["sourceStart"] for record in records) == 0
        assert max(record["sourceEnd"] for record in records) == 4
        assert all(record["fontKey"] == font_key for record in records)
        assert all(fragment["source"] == 1 and output["boxes"][fragment["box"]]["kind"] == "text"
                   for fragment in fragments)
        assert all(fragment["textData"] is None for fragment in output["fragments"]
                   if fragment["kind"] != "text")
    # Resolved source levels are public output, before visual line reordering.
    # Include overflow recovery and P2/FSI original-class boundaries, not just
    # well-matched controls. Expected values follow UAX #9 X1–I2 and CSS WM §2.4.
    bidi_cases = [
        ("\u061c12", "ltr", "normal", [2, 2]),
        ("a\u061c12b", "ltr", "normal", [0, 2, 2, 0]),
        ("\u202e\u2068ab\u2069\u202c", "ltr", "normal", [2, 2]),
        ("\u202eab\u202c", "rtl", "normal", [3, 3]),
        ("\u2067" * 130 + "\u061c12" + "\u2069" * 130 + "ab",
         "ltr", "normal", [126, 126, 0, 0]),
        ("\u202b" * 130 + "12" + "\u202c" * 130 + "ab",
         "ltr", "normal", [126, 126, 0, 0]),
        ("ab", "rtl", "plaintext", [0, 0]),
        ("\u2067\u200f\u2069ab", "rtl", "plaintext", [0, 0]),
        ("ab", "rtl", "isolate-override", [3, 3]),
    ]
    for text, direction, unicode_bidi, expected_levels in bidi_cases:
        bidi_request = {**request, "fonts": [font], "nodes": [
            {"parent": None, "style": {"fontKey": 1, "direction": direction,
                                       "unicodeBidi": unicode_bidi}},
            {"parent": 0, "text": text}]}
        completed = subprocess.run([executable], input=json.dumps(bidi_request).encode(),
                                   capture_output=True, timeout=10, check=True)
        records = json.loads(completed.stdout)["texts"]
        offset = 0
        levels = []
        for character in text:
            if character.isascii():
                covering = [record for record in records
                            if record["sourceStart"] <= offset < record["sourceEnd"]]
                assert len(covering) == 1, (text, offset, covering)
                levels.append(covering[0]["bidiLevel"])
            offset += len(character.encode())
        assert levels == expected_levels, (text, direction, unicode_bidi, levels, expected_levels)
    print(f"Native bidi profile passed: {len(bidi_cases)} source-level cases")
    print(f"Native adapter smoke passed: {len(malformed)} malformed requests")


if __name__ == "__main__":
    main()
