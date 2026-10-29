#!/usr/bin/env python3
"""Build the native CLI with profile-guided optimization.

Builds an instrumented CLI, trains it on the comparison workloads (fresh layouts and
incremental update cycles) and a third of the spec fixtures, merges the raw profiles
with llvm-profdata, and builds again with the profile. The result replaces the usual
release binary at build/llvm/<triple>/release/silk_layout_cli.

  SILK_CLANG=/path/to/llvm/bin/clang python3 scripts/pgo-build.py [--docs DIR]

Needs a Silk whose compiler reads SILK_PROFILE_GENERATE and SILK_PROFILE_USE (after
0.1.0-dev.28).

--docs adds the small documents (under 400 KB) of a directory to the training set, such
as the taffy README trees that `cargo run --release -- DIR` in bench/compare/taffy-readme
writes; they cover absolutely positioned and deep auto-sized trees the workloads don't.
llvm-profdata is taken from next to SILK_CLANG, else from PATH. SILK selects the silk
command (default `silk`).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench" / "compare"))
import workloads as W  # noqa: E402
import updates as U  # noqa: E402

TRIPLE = "x86_64-unknown-linux-gnu"
BINARY = ROOT / "build" / "llvm" / TRIPLE / "release" / "silk_layout_cli"
TRAINING = ["deep-random-1k", "flex-grow-1k", "flex-wrap-10k", "block-stack-10k", "nested-chain-200",
            "measured-wrap-1k", "app-cards-500"]


def build(env):
    silk = os.environ.get("SILK", "silk")
    subprocess.run([silk, "build", "--release"], cwd=ROOT / "cli", env=env, check=True)


def profdata():
    clang = os.environ.get("SILK_CLANG")
    if clang:
        beside = Path(clang).parent / "llvm-profdata"
        if beside.exists():
            return str(beside)
    found = shutil.which("llvm-profdata")
    if not found:
        sys.exit("llvm-profdata not found next to SILK_CLANG or on PATH")
    return found


def train(cli, env, docs):
    def run(document):
        subprocess.run([str(cli)], input=json.dumps(document).encode(), env=env, capture_output=True, check=False)

    for name in TRAINING:
        document = W.WORKLOADS[name]()
        run(dict(document, benchmark={"iterations": 10}))
        for scenario, cycle in U.scenarios(document).items():
            run(dict(document, updates={"iterations": 20, "cycle": cycle}))
    for path in sorted(docs.glob("*.json")) if docs else []:
        if path.stat().st_size < 400_000:
            run(dict(json.loads(path.read_text()), benchmark={"iterations": 10}))
    fixtures = sorted((ROOT / "tests" / "fixtures" / "spec").glob("*.json"))
    for fixture in fixtures[::3]:
        subprocess.run([str(cli)], stdin=fixture.open("rb"), env=env, capture_output=True, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docs", type=Path, help="directory of extra training documents")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="silk-layout-pgo-") as scratch:
        raw = Path(scratch) / "raw"
        raw.mkdir()
        build(dict(os.environ, SILK_PROFILE_GENERATE=str(raw)))
        instrumented = Path(scratch) / "silk_layout_cli"
        shutil.copy2(BINARY, instrumented)
        train(instrumented, dict(os.environ, LLVM_PROFILE_FILE=str(raw / "%p.profraw")), args.docs)
        profiles = sorted(raw.glob("*.profraw"))
        if not profiles:
            sys.exit("training wrote no profiles: is this Silk new enough for SILK_PROFILE_GENERATE?")
        merged = Path(scratch) / "silk_layout_cli.profdata"
        subprocess.run([profdata(), "merge", "-o", str(merged)] + [str(p) for p in profiles], check=True)
        build(dict(os.environ, SILK_PROFILE_USE=str(merged)))
    print(BINARY)


if __name__ == "__main__":
    main()
