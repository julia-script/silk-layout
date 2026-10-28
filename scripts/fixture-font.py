#!/usr/bin/env python3
"""Build original, MIT-licensed ASCII fixture fonts; JSON stdin/stdout only.

This is a build-time FontTools consumer, not a font importer. The diagnostic
outlines below were authored for this repository; no external font is read.
OpenType field semantics: https://learn.microsoft.com/typography/opentype/spec/os2
and https://learn.microsoft.com/typography/opentype/spec/head .

Only the horizontal alphabetic baseline is implemented. The other fixture
baseline values participate in provenance, but do not establish support for
nonalphabetic CSS alignment. The browser adapter must gate those modes.
"""
import base64
from decimal import Decimal
from fractions import Fraction
import hashlib
from io import BytesIO
import json
import math
import sys


# Divisible by 2, 3, 5, etc., while within OpenType's 16..16384 UPEM range.
# In particular, both the size-6 and size-10 fixture profiles are exact.
UNITS_PER_EM = 15360
ADVANCE = UNITS_PER_EM // 2
TIMESTAMP = 2082844800  # 1970-01-01 in OpenType's 1904-based epoch.
GENERATOR = "silk-original-ascii-1"
METRIC_FIELDS = (
    "fontSize", "ascent", "descent", "lineGap", "xHeight", "capHeight",
    "subscriptOffset", "superscriptOffset",
)
BASELINE_FIELDS = ("alphabetic", "ideographic", "central", "mathematical")
NONNEGATIVE_FIELDS = ("fontSize", "ascent", "descent", "xHeight", "capHeight")
LICENSE = """MIT License

Copyright (c) 2026 Julia Ortiz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

# Original five-column diagnostic silhouettes. Each row is a five-bit mask;
# lower-case glyphs use these silhouettes at x-height, not copied font outlines.
# Rendering details may snap interior edges to the integer design grid; no
# advance, baseline, or supplied font metric is rounded.
SILHOUETTES = {
    "0": (14, 17, 19, 21, 25, 17, 14),
    "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31),
    "3": (30, 1, 1, 14, 1, 1, 30),
    "4": (2, 6, 10, 18, 31, 2, 2),
    "5": (31, 16, 16, 30, 1, 1, 30),
    "6": (7, 8, 16, 30, 17, 17, 14),
    "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14),
    "9": (14, 17, 17, 15, 1, 2, 28),
    "A": (4, 10, 17, 17, 31, 17, 17),
    "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (7, 8, 16, 16, 16, 8, 7),
    "D": (28, 18, 17, 17, 17, 18, 28),
    "E": (31, 16, 16, 30, 16, 16, 31),
    "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 15),
    "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (14, 4, 4, 4, 4, 4, 14),
    "J": (7, 2, 2, 2, 18, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17),
    "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17),
    "N": (17, 25, 25, 21, 19, 19, 17),
    "O": (14, 17, 17, 17, 17, 17, 14),
    "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13),
    "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30),
    "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14),
    "V": (17, 17, 17, 17, 10, 10, 4),
    "W": (17, 17, 17, 21, 21, 27, 17),
    "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4),
    "Z": (31, 1, 2, 4, 8, 16, 31),
}


class Unsupported(ValueError):
    pass


def finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise Unsupported(f"{name} must be a finite number")
    try:
        native = float(value)
        exact = Fraction(value)
    except (OverflowError, ValueError) as error:
        raise Unsupported(f"{name} must be a finite number") from error
    if not math.isfinite(native):
        raise Unsupported(f"{name} must be finite in the native f64 model")
    if exact and native == 0:
        raise Unsupported(f"{name} underflows the native f64 model")
    return exact


def profile(record):
    if not isinstance(record, dict) or set(record) != set(METRIC_FIELDS) | {"baselines"}:
        raise Unsupported("font record must contain exactly the native font metric fields")
    baselines = record["baselines"]
    if not isinstance(baselines, dict) or set(baselines) != set(BASELINE_FIELDS):
        raise Unsupported("baselines must contain alphabetic, ideographic, central, mathematical")
    values = {name: finite_number(record[name], name) for name in METRIC_FIELDS}
    values.update({f"baselines.{name}": finite_number(baselines[name], f"baselines.{name}")
                   for name in BASELINE_FIELDS})
    for name in NONNEGATIVE_FIELDS:
        if values[name] < 0:
            raise Unsupported(f"{name} must be nonnegative")
    return values


def design_units(values):
    size = values["fontSize"]
    if size == 0:
        if any(values.values()):
            raise Unsupported("zero fontSize requires every metric and baseline to be zero")
        return None
    if values["baselines.alphabetic"] != values["ascent"]:
        raise Unsupported("alphabetic baseline must equal ascent for a real horizontal font")
    if values["ascent"] <= 0 or values["xHeight"] <= 0 or values["capHeight"] <= 0:
        raise Unsupported("nonzero fontSize requires positive ascent, xHeight, and capHeight")
    if max(values["xHeight"], values["capHeight"]) > values["ascent"]:
        raise Unsupported("xHeight and capHeight must not exceed ascent in the fixture font profile")
    if values["lineGap"] < 0:
        raise Unsupported("negative lineGap is not supported by the browser fixture font profile")
    result = {}
    for name in METRIC_FIELDS[1:]:
        scaled = values[name] * UNITS_PER_EM / size
        if scaled.denominator != 1:
            raise Unsupported(f"{name} is not exactly representable at {UNITS_PER_EM} units per em")
        number = scaled.numerator
        # hhea/OS2 metric fields are signed 16-bit; positive descent is negated
        # in hhea/sTypoDescender but retained in the unsigned Windows field.
        if not -32768 <= number <= 32767:
            raise Unsupported(f"{name} exceeds the signed 16-bit OpenType metric range")
        result[name] = number
    return result


def rectangle(pen, left, bottom, right, top):
    if bottom >= top or left >= right:
        return
    pen.moveTo((left, bottom))
    pen.lineTo((left, top))
    pen.lineTo((right, top))
    pen.lineTo((right, bottom))
    pen.closePath()


def glyph(character, units, pen_class):
    pen = pen_class(None)
    if character == " ":
        return pen.glyph(), 0
    height = units["xHeight"] if character.islower() else units["capHeight"]
    rows = SILHOUETTES[character.upper()]
    # Seven disjoint horizontal bands; positions are authored design details,
    # not measurement outputs. The top and bottom reach the supplied height.
    for row, mask in enumerate(rows):
        top = height - row * height // 7
        bottom = height - (row + 1) * height // 7
        if top == bottom:
            continue
        if row != 6:
            bottom += (top - bottom) // 3
        for column in range(5):
            if mask & (1 << (4 - column)):
                left = 640 + column * 1280
                rectangle(pen, left, bottom, left + 960, top)
    if character in "gjpqy":
        rectangle(pen, 3200, -units["descent"], 4160, 0)
    result = pen.glyph()
    # At extremely small design heights, a row can collapse entirely. Calculate
    # the bearing from the completed outline so head.flags bit 1 stays truthful.
    bearing = min(point[0] for point in result.coordinates)
    return result, bearing


def build_font(units, family, font_builder, pen_class):
    builder = font_builder(UNITS_PER_EM, isTTF=True)
    builder.font.recalcTimestamp = False
    characters = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    names = {character: ("space" if character == " " else f"uni{ord(character):04X}")
             for character in characters}
    order = [".notdef", *names.values()]
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(character): name for character, name in names.items()})
    missing = pen_class(None)
    rectangle(missing, 640, 0, 7040, units["capHeight"])
    glyphs = {".notdef": missing.glyph()}
    metrics = {".notdef": (ADVANCE, 640)}
    for character, name in names.items():
        outline, bearing = glyph(character, units, pen_class)
        glyphs[name] = outline
        metrics[name] = (ADVANCE, bearing)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=units["ascent"], descent=-units["descent"],
                                  lineGap=units["lineGap"])
    builder.setupNameTable({
        "familyName": family, "styleName": "Regular", "fullName": family + " Regular",
        "psName": family + "-Regular", "uniqueFontIdentifier": GENERATOR + ":" + family,
        "version": "Version 1.000", "copyright": "Copyright (c) 2026 Julia Ortiz",
        "description": "Original ASCII diagnostic outlines authored for silk-layout. "
                       "Half-em advances; no kerning, ligatures, or external glyph input.",
        "licenseDescription": LICENSE,
    })
    builder.setupOS2(
        version=4, xAvgCharWidth=ADVANCE, usWeightClass=400, usWidthClass=5, fsType=0,
        fsSelection=(1 << 6) | (1 << 7), achVendID="SILK",
        sTypoAscender=units["ascent"], sTypoDescender=-units["descent"],
        sTypoLineGap=units["lineGap"], usWinAscent=units["ascent"],
        usWinDescent=units["descent"], sxHeight=units["xHeight"], sCapHeight=units["capHeight"],
        ySubscriptXSize=ADVANCE, ySubscriptYSize=ADVANCE,
        ySubscriptXOffset=0, ySubscriptYOffset=units["subscriptOffset"],
        ySuperscriptXSize=ADVANCE, ySuperscriptYSize=ADVANCE,
        ySuperscriptXOffset=0, ySuperscriptYOffset=units["superscriptOffset"],
        usDefaultChar=0, usBreakChar=32, usMaxContext=1,
    )
    builder.setupPost(isFixedPitch=1, italicAngle=0)
    builder.setupMaxp()
    builder.font["head"].created = TIMESTAMP
    builder.font["head"].modified = TIMESTAMP
    builder.font["head"].flags = 3  # Baseline at zero; xMin equals left bearing.
    data = BytesIO()
    builder.save(data)
    return data.getvalue()


def asset(key, record, font_builder, pen_class):
    values = profile(record)
    units = design_units(values)
    canonical = json.dumps({"generator": GENERATOR, "unitsPerEm": UNITS_PER_EM,
                            "metrics": {name: str(value) for name, value in values.items()}},
                           sort_keys=True, separators=(",", ":")).encode("ascii")
    profile_hash = hashlib.sha256(canonical).hexdigest()
    family = "SilkFixture_" + profile_hash[:24]
    result = {"key": key, "family": family, "fontSize": float(values["fontSize"])}
    if units is None:
        # Key zero itself is implicit and never emitted. A positive-key all-zero
        # profile is also assetless; its sha256 identifies the canonical profile.
        result["sha256"] = profile_hash
    else:
        data = build_font(units, family, font_builder, pen_class)
        result["dataUrl"] = "data:font/ttf;base64," + base64.b64encode(data).decode("ascii")
        result["sha256"] = hashlib.sha256(data).hexdigest()
    return result


def reject_constant(value):
    raise ValueError(f"non-finite JSON number {value}")


def main():
    try:
        from fontTools.fontBuilder import FontBuilder
        from fontTools.pens.ttGlyphPen import TTGlyphPen
    except ImportError as error:
        print("fixture-font: FontTools is required. Install with: "
              "python3 -m venv .cache/font-tools && "
              ".cache/font-tools/bin/python -m pip install fonttools==4.67.0; "
              "then run with that Python or set SILK_FONT_PYTHON. " + str(error), file=sys.stderr)
        return 1
    try:
        request = json.load(sys.stdin, parse_float=Decimal, parse_constant=reject_constant)
        if not isinstance(request, dict) or set(request) != {"fonts"} or not isinstance(request["fonts"], list):
            raise ValueError('expected one JSON object {"fonts": [...]}')
        result = {"fonts": [], "unsupported": []}
        for key, record in enumerate(request["fonts"], 1):
            try:
                result["fonts"].append(asset(key, record, FontBuilder, TTGlyphPen))
            except Unsupported as error:
                result["unsupported"].append({"key": key, "reason": str(error)})
    except Exception as error:
        # Broken infrastructure or a programming error must fail the invocation,
        # not be disguised as an unsupported metric profile or partial result.
        print(f"fixture-font: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
