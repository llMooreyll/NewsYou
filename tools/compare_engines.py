#!/usr/bin/env python3
"""Compare two collections produced by different extraction engines.

Shadow mode: run the collector twice (--engine legacy / --engine modern), then
point this at the two output directories. It reports, per source, where the two
engines agree and where they differ — so the legacy path is only retired once
the differences have been reviewed, not assumed away.

    python tools/collect.py --all --engine legacy --outdir data/collected-legacy
    python tools/collect.py --all --engine modern --outdir data/collected-modern
    python tools/compare_engines.py --legacy data/collected-legacy \
                                    --modern data/collected-modern

Exit code is 0 when the two agree on every source, 1 when there are differences,
so it can gate a CI step or a shell `&&` chain.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collectors import base                              # noqa: E402
from collectors import registry                          # noqa: E402

def load_dir(path: Path) -> tuple[dict[str, dict], dict]:
    index = {}
    index_file = path / "index.json"
    if index_file.exists():
        index = json.loads(index_file.read_text(encoding="utf-8"))
    sources: dict[str, dict] = {}
    for name in registry.ORDER:
        file = path / f"{name}.json"
        if file.exists():
            sources[name] = json.loads(file.read_text(encoding="utf-8"))
    return sources, index

def key_of(entry: dict) -> str:
    return base.canonical_key(entry.get("url") or "") or entry.get("content_hash") or entry.get("title", "")

def _is_capture_noise(le: dict, me: dict) -> bool:
    """Both dates are just the capture time — not a real disagreement.

    Hot-search boards (weibo) have no publication time at all, so both engines
    stamp the fetch time and the two runs differ by seconds.
    """
    return (le.get("date_source") == "collection_time"
            and me.get("date_source") == "collection_time")

def compare_source(name: str, legacy: dict | None, modern: dict | None) -> dict:
    litems = (legacy or {}).get("items") or []
    mitems = (modern or {}).get("items") or []
    lmap = {key_of(e): e for e in litems}
    mmap = {key_of(e): e for e in mitems}

    only_l = [lmap[k] for k in lmap.keys() - mmap.keys()]
    only_m = [mmap[k] for k in mmap.keys() - lmap.keys()]

    # Hot-search boards (微博/知乎) are live lists: their contents and order change
    # between two runs minutes apart, so a differing row is not an engine
    # difference. Detect it so it is reported separately from real disagreements.
    live_list = bool(litems or mitems) and all(
        e.get("kind") == "hotsearch" for e in (litems + mitems)[:1])

    date_diffs = []
    date_noise = 0
    body_diffs = []
    for key in lmap.keys() & mmap.keys():
        le, me = lmap[key], mmap[key]
        ld, md = (le.get("published_at") or "")[:16], (me.get("published_at") or "")[:16]
        if ld != md:
            if _is_capture_noise(le, me):
                date_noise += 1
            else:
                date_diffs.append((le, me))
        lt, mt = le.get("body_text") or "", me.get("body_text") or ""
        if bool(lt) != bool(mt):
            body_diffs.append((le, me, "one-sided"))
        elif lt and mt and abs(len(lt) - len(mt)) / max(len(lt), len(mt)) > 0.25:
            body_diffs.append((le, me, "size"))

    return {
        "name": name,
        "live_list": live_list,
        "legacy_count": len(litems),
        "modern_count": len(mitems),
        "shared": len(lmap.keys() & mmap.keys()),
        "only_legacy": only_l,
        "only_modern": only_m,
        "date_diffs": date_diffs,
        "date_noise": date_noise,
        "body_diffs": body_diffs,
        "legacy_fulltext": sum(1 for e in litems if e.get("body_text")),
        "modern_fulltext": sum(1 for e in mitems if e.get("body_text")),
        "legacy_links": sum((e.get("body_html") or "").count("<a href") for e in litems),
        "modern_links": sum((e.get("body_html") or "").count("<a href") for e in mitems),
    }

def render(results: list[dict], legacy_index: dict, modern_index: dict) -> tuple[str, bool]:
    out: list[str] = []
    out.append("# Extraction engine comparison — legacy vs modern\n")
    out.append(f"- legacy: engine `{legacy_index.get('engine', '?')}`, "
               f"day {legacy_index.get('day', '?')}, fulltext `{legacy_index.get('fulltext', '?')}`")
    out.append(f"- modern: engine `{modern_index.get('engine', '?')}`, "
               f"day {modern_index.get('day', '?')}, fulltext `{modern_index.get('fulltext', '?')}`\n")

    out.append("| source | legacy | modern | shared | only-legacy | only-modern | date diffs | body diffs |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        out.append(f"| {r['name']} | {r['legacy_count']} | {r['modern_count']} | {r['shared']} | "
                   f"{len(r['only_legacy'])} | {len(r['only_modern'])} | "
                   f"{len(r['date_diffs'])} | {len(r['body_diffs'])} |")

    noise = sum(r.get("date_noise", 0) for r in results)
    if noise:
        out.append(f"\n_{noise} further date differences were ignored: both engines recorded the "
                   f"capture time (sources with no publication time, e.g. 微博热搜)._")

    out.append("\n## Full text and links\n")
    out.append("| source | fulltext legacy | fulltext modern | links legacy | links modern |")
    out.append("|---|---:|---:|---:|---:|")
    for r in results:
        out.append(f"| {r['name']} | {r['legacy_fulltext']} | {r['modern_fulltext']} | "
                   f"{r['legacy_links']} | {r['modern_links']} |")

    for r in results:
        if not (r["only_legacy"] or r["only_modern"] or r["date_diffs"] or r["body_diffs"]):
            continue
        out.append(f"\n## {r['name']}\n")
        if r.get("live_list"):
            out.append("_Live list (hot-search board): entries change between runs, so rows "
                       "below are expected drift rather than an engine difference._\n")
        for entry in r["only_legacy"][:10]:
            out.append(f"- **only legacy**: {entry['title'][:70]}  \n  `{entry['url']}`")
        for entry in r["only_modern"][:10]:
            out.append(f"- **only modern**: {entry['title'][:70]}  \n  `{entry['url']}`")
        for le, me in r["date_diffs"][:10]:
            out.append(f"- **date differs**: {le['title'][:60]}")
            out.append(f"  - legacy `{le.get('published_at')}` ({le.get('date_source')})")
            out.append(f"  - modern `{me.get('published_at')}` ({me.get('date_source')})")
        for le, me, kind in r["body_diffs"][:10]:
            lt, mt = len(le.get("body_text") or ""), len(me.get("body_text") or "")
            out.append(f"- **body {kind}**: {le['title'][:60]}")
            out.append(f"  - legacy {lt} chars, modern {mt} chars")

    has_diff = any((r["only_legacy"] or r["only_modern"] or r["date_diffs"] or r["body_diffs"])
                   and not r.get("live_list")
                   for r in results)
    out.append("\n## Verdict\n")
    if has_diff:
        out.append("Differences exist — review the sections above before switching engines.")
    else:
        out.append("No differences detected outside live lists. The modern engine is a drop-in "
                   "replacement for the sources that ran.")
    return "\n".join(out) + "\n", has_diff

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy", type=Path, default=Path("data/collected-legacy"))
    parser.add_argument("--modern", type=Path, default=Path("data/collected-modern"))
    parser.add_argument("--output", type=Path, default=None,
                        help="write the report here instead of stdout")
    parser.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = parser.parse_args()

    legacy, lindex = load_dir(args.legacy)
    modern, mindex = load_dir(args.modern)
    if not legacy and not modern:
        print(f"nothing to compare: {args.legacy} and {args.modern} are both empty", file=sys.stderr)
        return 2

    names = [n for n in registry.ORDER if n in legacy or n in modern]
    results = [compare_source(n, legacy.get(n), modern.get(n)) for n in names]
    report, has_diff = render(results, lindex, mindex)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(args.output)
    if not args.quiet:
        print(report)
    else:
        same = sum(1 for r in results if not (r["only_legacy"] or r["only_modern"]
                                              or r["date_diffs"] or r["body_diffs"]))
        print(f"{same}/{len(results)} sources identical; "
              f"{len(results) - same} differ", file=sys.stderr)
    return 1 if has_diff else 0

if __name__ == "__main__":
    raise SystemExit(main())
