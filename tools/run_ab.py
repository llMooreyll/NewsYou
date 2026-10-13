#!/usr/bin/env python3
"""Run the whole pipeline twice — once per extraction engine — and compare.

This is the shadow-mode driver. It produces two independent data sets and two
self-contained HTML digests so the same day can be reviewed side by side, plus a
machine-readable diff report.

    python tools/run_ab.py                      # full run, both engines
    python tools/run_ab.py --skip-collect       # re-render from existing data
    python tools/run_ab.py --engines modern     # only one engine

Outputs (defaults):

    data/collected-legacy/  data/collected-modern/
    data/ai-legacy/         data/ai-modern/
    data/processed-legacy/  data/processed-modern/
    site/digest-legacy.html site/digest-modern.html
    reports/engine-comparison.md

The modern engine needs the optional dependencies, so it is invoked with the
project venv interpreter when one exists. The legacy engine runs on the current
interpreter.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"

def interpreter_for(engine: str) -> str:
    """modern needs the venv; legacy runs anywhere."""
    if engine == "modern":
        for candidate in (ROOT / ".venv/bin/python", ROOT / ".venv/Scripts/python.exe"):
            if candidate.exists():
                return str(candidate)
    return sys.executable

def run(cmd: list[str], *, allow_fail: bool = False) -> int:
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0 and not allow_fail:
        raise SystemExit(f"command failed ({result.returncode}): {' '.join(cmd)}")
    return result.returncode

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engines", nargs="+", default=["legacy", "modern"],
                        choices=["legacy", "modern"])
    parser.add_argument("--fulltext", default="today")
    parser.add_argument("--day", default="auto")
    parser.add_argument("--pages", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-collect", action="store_true",
                        help="reuse data/collected-<engine> instead of fetching")
    parser.add_argument("--skip-ai", action="store_true",
                        help="reuse data/ai-<engine> decision files")
    parser.add_argument("--report", type=Path, default=Path("reports/engine-comparison.md"))
    args = parser.parse_args()

    produced: dict[str, Path] = {}
    for engine in args.engines:
        python = interpreter_for(engine)
        collected = Path(f"data/collected-{engine}")
        aidir = Path(f"data/ai-{engine}")
        processed = Path(f"data/processed-{engine}")
        output = Path(f"site/digest-{engine}.html")

        print(f"\n{'=' * 60}\n  ENGINE: {engine}   ({python})\n{'=' * 60}", file=sys.stderr)

        if not args.skip_collect:
            cmd = [python, str(TOOLS / "collect.py"), "--all", "--engine", engine,
                   "--outdir", str(collected), "--aidir", str(aidir),
                   "--fulltext", args.fulltext, "--day", args.day, "--pages", str(args.pages)]
            if args.limit:
                cmd += ["--limit", str(args.limit)]
            run(cmd)

        if not args.skip_ai:
            run([python, str(TOOLS / "demo_decisions.py"),
                 "--collected", str(collected), "--ai", str(aidir)])

        run([python, str(TOOLS / "ai_apply.py"),
             "--collected", str(collected), "--ai", str(aidir), "--out", str(processed)])

        run([python, str(TOOLS / "render_digest.py"),
             "--indir", str(processed), "--fallback", str(collected),
             "--output", str(output)])

        produced[engine] = output

    if len(args.engines) == 2:
        print(f"\n{'=' * 60}\n  COMPARISON\n{'=' * 60}", file=sys.stderr)
        run([sys.executable, str(TOOLS / "compare_engines.py"),
             "--legacy", "data/collected-legacy",
             "--modern", "data/collected-modern",
             "--output", str(args.report), "--quiet"], allow_fail=True)

    print("\n--- outputs ---", file=sys.stderr)
    for engine, path in produced.items():
        print(f"  {engine:<8} {path}", file=sys.stderr)
    if len(args.engines) == 2:
        print(f"  report   {args.report}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
