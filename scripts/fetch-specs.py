#!/usr/bin/env python3
"""Fetch the normative layout reference set without vendoring it into the library.

The lock records original URLs and SHA-256 content hashes. --update is explicit;
normal runs verify the pinned bytes, so an upstream draft edit cannot silently
change the implementation's reference baseline.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "specs.lock.json"
CACHE = ROOT / ".cache" / "specs"
URLS = {
    **{f"css21-{chapter}": f"https://www.w3.org/TR/2011/REC-CSS2-20110607/{chapter}.html"
       for chapter in ["conform", "box", "visuren", "visudet", "visufx", "page", "text", "tables"]},
    "css21-errata": "https://www.w3.org/Style/css2-updates/REC-CSS2-20110607-errata.html",
    "html-rendering": "https://html.spec.whatwg.org/multipage/rendering.html",
    "svg2-coordinates": "https://www.w3.org/TR/SVG2/coords.html",
    **{f"unicode-uax{number}": f"https://www.unicode.org/reports/tr{number}/tr{number}-{revision}.html"
       for number, revision in [(9, 52), (11, 46), (14, 57), (24, 41), (29, 49), (44, 38), (50, 35)]},
    **{f"css22-{chapter}": f"https://www.w3.org/TR/CSS22/{chapter}.html"
       for chapter in ["conform", "box", "visuren", "visudet", "visufx", "page", "text", "tables"]},
    **{name: f"https://drafts.csswg.org/{name}/" for name in [
        "css-flexbox-1", "css-grid-2", "css-display-3", "css-display-4", "css-box-3",
        "css-sizing-3", "css-sizing-4", "css-align-3", "css-writing-modes-4",
        "css-overflow-3", "css-overflow-4", "css-position-3", "css-break-3", "css-break-4",
        "css-values-3", "css-values-4", "css-logical-1", "css-inline-3",
        "css-text-3", "css-text-4", "css-contain-2", "css-multicol-1",
        "css-page-3", "css-images-3", "css-fonts-4", "css-gaps-1",
        "css-cascade-4", "css-backgrounds-3", "css-shapes-1",
        "css-position-4", "css-pseudo-4",
        "cssom-view", "css-scrollbars-1",
        "css-transforms-1", "css-will-change-1", "css-2025"]},
}


def fetch(item):
    name, url = item
    request = urllib.request.Request(url, headers={"User-Agent": "silk-layout-spec-fetch/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        body = response.read()
        resolved = response.url
    digest = hashlib.sha256(body).hexdigest()
    return name, {"url": url, "resolved": resolved, "sha256": digest}, body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="explicitly refresh the standards baseline")
    parser.add_argument("--only", action="append", choices=sorted(URLS),
                        help="fetch only this reference; repeat for more (with --update, preserve other lock records)")
    args = parser.parse_args()
    pinned = json.loads(LOCK.read_text()) if LOCK.exists() else None
    if pinned is None and not args.update:
        parser.error("no lockfile; use --update to establish the reference baseline")
    if args.update and args.only and pinned is None:
        parser.error("a selective update requires an existing lockfile")
    CACHE.mkdir(parents=True, exist_ok=True)
    urls = URLS if args.update else {key: value["url"] for key, value in pinned["specs"].items()}
    if args.only:
        missing = set(args.only) - urls.keys()
        if missing:
            parser.error(f"references not pinned: {', '.join(sorted(missing))}; use --update")
        urls = {name: urls[name] for name in args.only}
    pending = {}
    records = dict(pinned["specs"]) if args.update and args.only else {}
    for name, url in urls.items():
        cached = CACHE / f"{name}.html"
        if not args.update and cached.exists() and hashlib.sha256(cached.read_bytes()).hexdigest() == pinned["specs"][name]["sha256"]:
            records[name] = pinned["specs"][name]
        else:
            pending[name] = url
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for name, record, body in pool.map(fetch, pending.items()):
            if not args.update and record["sha256"] != pinned["specs"][name]["sha256"]:
                raise SystemExit(f"{name}: upstream bytes changed; inspect before --update")
            (CACHE / f"{name}.html").write_bytes(body)
            records[name] = record
            print(f"{name}: {len(body)} bytes {record['sha256']}")
    if args.update:
        LOCK.write_text(json.dumps({"format": 1, "specs": dict(sorted(records.items()))}, indent=2) + "\n")
    action = "Updated" if args.update else "Verified"
    print(f"{action} {len(urls)} specifications in {CACHE}; {len(records)} lock records retained")


if __name__ == "__main__":
    main()
