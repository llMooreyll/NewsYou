#!/usr/bin/env python3
"""Unified collector for every newsletter source.

This file is only the **orchestrator**: it parses arguments, calls each source
module from tools/collectors/, writes per-source JSON, and builds the stage-2
input. All site-specific logic lives in tools/collectors/<source>.py.

Two interchangeable extraction engines (``--engine``):

  legacy   standard library only — hand-rolled regex feeds and per-site body
           markers. Default. Needs nothing installed.
  modern   trafilatura for article bodies, feedparser for feeds. One code path
           for every site, keeps inline links and headings. Needs the optional
           dependencies in requirements.txt.

Running both engines produces two independent data sets, which is how the
shadow-mode comparison works:

    python tools/collect.py --all --engine legacy --outdir data/collected-legacy
    python tools/collect.py --all --engine modern --outdir data/collected-modern

Usage
-----
    python tools/collect.py --list-sources
    python tools/collect.py --all --outdir data/collected
    python tools/collect.py sspai --limit 20
    python tools/collect.py telegram --day auto
    python tools/collect.py telegram --day 2026-10-12
    python tools/collect.py --dates
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collectors import base, registry                      # noqa: E402
from collectors import extract as ex                        # noqa: E402
from collectors import telegram as tg                       # noqa: E402
from collectors.base import CST, FULLTEXT_DEFAULT, FULLTEXT_MODES  # noqa: E402
import ai_stage                                             # noqa: E402

def _source_kwargs(name: str, args) -> dict:
    """Per-source arguments. Keeps site quirks out of the main loop."""
    if name == "telegram":
        return {"day": args.day, "pages": args.pages}
    kwargs: dict = {}
    if name in registry.FULLTEXT_SOURCES:
        kwargs["fulltext"] = args.fulltext
    if name == "linuxdo" and args.date:
        kwargs["date"] = args.date
    return kwargs

def _postprocess(name: str, items: list[dict], args, outdir: Path) -> list[dict]:
    """Telegram-only: deterministic pre-dedupe plus image download."""
    if name != "telegram":
        return items
    items, groups = base.dedupe(items)
    outdir.mkdir(parents=True, exist_ok=True)
    if not args.no_images:
        saved = tg.download_for_items(items, Path("data/telegram/media"),
                                      args.images_per_post, args.image_cap)
        print(f"  telegram images saved: {saved}", file=sys.stderr)
    (outdir / "telegram_duplicate_groups.json").write_text(
        json.dumps({"generated_at": datetime.now(CST).isoformat(), "groups": groups},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?", choices=sorted(registry.SOURCES))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--outdir", type=Path, default=Path("data/collected"))
    parser.add_argument("--aidir", type=Path, default=Path("data/ai"),
                        help="where stage 2 input files are written")
    parser.add_argument("--engine", default="legacy", choices=ex.ENGINES,
                        help="extraction engine: legacy (stdlib) or modern (trafilatura/feedparser)")
    parser.add_argument("--date", default=None, help="linuxdo issue date")
    parser.add_argument("--day", default="auto", help="telegram day: today | yesterday | auto | YYYY-MM-DD")
    parser.add_argument("--pages", type=int, default=4, help="telegram pages per channel")
    parser.add_argument("--fulltext", default=FULLTEXT_DEFAULT,
                        help=f"full-text policy: {' | '.join(FULLTEXT_MODES)} | <N>. "
                             "Default 'today' fetches full text only for items published today")
    parser.add_argument("--no-ai-input", action="store_true",
                        help="skip writing the stage 2 input files")
    parser.add_argument("--no-images", action="store_true",
                        help="do not download telegram post images (images are on by default)")
    parser.add_argument("--images-per-post", type=int, default=2)
    parser.add_argument("--image-cap", type=int, default=200)
    parser.add_argument("--dates", action="store_true")
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    ok, reason = ex.engine_available(args.engine)
    if not ok:
        print(f"error: {reason}", file=sys.stderr)
        return 2

    if args.list_sources:
        for name in registry.ORDER:
            module = registry.get(name)
            doc = (module.__doc__ or "").strip().splitlines()[0]
            print(f"{name:<12} limit={registry.default_limit(name):<4} {doc}")
        return 0

    if args.dates:
        for value in registry.get("linuxdo").available_dates():
            print(value)
        return 0

    if not args.source and not args.all:
        parser.error("choose a source, or pass --all")

    today = datetime.now(CST).date()
    targets = registry.ORDER if args.all else [args.source]
    results: dict[str, list[dict]] = {}
    statuses: dict[str, dict] = {}
    failures: dict[str, str] = {}

    for name in targets:
        print(f"→ {name}", file=sys.stderr)
        started = time.time()
        try:
            module = registry.get(name)
            limit = args.limit if args.limit is not None else registry.default_limit(name)
            items = module.collect(limit, engine=args.engine, **_source_kwargs(name, args))
            for entry in items:
                base.stamp_now(entry)
            base.mark_today(items, today)
            items = _postprocess(name, items, args, args.outdir)

            results[name] = items
            statuses[name] = base.source_status(items, today)
            fresh = ("today" if statuses[name]["updated_today"]
                     else f"{statuses[name]['staleness_days']}d ago")
            bodies = statuses[name]["fulltext_fetched"]
            print(f"  {len(items)} items in {time.time() - started:.1f}s  "
                  f"(latest: {fresh}, fulltext: {bodies})", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failures[name] = f"{type(exc).__name__}: {exc}"
            print(f"  FAILED: {failures[name]}", file=sys.stderr)

    if args.list:
        for name in targets:
            if name not in results:
                continue
            status = statuses.get(name, {})
            flag = "UPDATED" if status.get("updated_today") else f"{status.get('staleness_days')}d"
            print(f"\n=== {name} ({len(results[name])}) [{flag}] engine={args.engine} ===")
            for entry in results[name][:20]:
                when = (entry.get("published_at") or "")[:16].replace("T", " ")
                extra = entry.get("extra") or {}
                bits = [when, entry.get("date_source", "")]
                for key, suffix in (("rank", "#"), ("heat", ""), ("score", "pts")):
                    if extra.get(key):
                        bits.append(f"{suffix}{extra[key]}"[:14])
                if entry.get("body_text"):
                    bits.append(f"{len(entry['body_text'])}字")
                print(f"  {' · '.join(bits)}")
                print(f"    {entry['title'][:88]}")
                print(f"    {entry['url']}")
        if failures:
            print("\nfailures:")
            for name, err in failures.items():
                print(f"  {name}: {err}")
        return 0

    args.outdir.mkdir(parents=True, exist_ok=True)
    index = {"collected_at": datetime.now(CST).isoformat(), "day": today.isoformat(),
             "engine": args.engine, "fulltext": args.fulltext, "sources": {}}
    for name in targets:
        if name not in results:
            continue
        path = args.outdir / f"{name}.json"
        path.write_text(json.dumps({
            "source": name,
            "collected_at": datetime.now(CST).isoformat(),
            "engine": args.engine,
            "count": len(results[name]),
            "status": statuses.get(name, {}),
            "items": results[name],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index["sources"][name] = {"file": path.name, "count": len(results[name]), "ok": True,
                                  **statuses.get(name, {})}
        print(f"  wrote {path}", file=sys.stderr)

    for name, err in failures.items():
        index["sources"][name] = {"file": None, "count": 0, "ok": False, "error": err}

    (args.outdir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.no_ai_input and results:
        args.aidir.mkdir(parents=True, exist_ok=True)
        if "telegram" in results:
            tg_input = ai_stage.build_telegram_input(results["telegram"], today.isoformat())
            (args.aidir / "telegram_input.json").write_text(
                json.dumps(tg_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"  wrote {args.aidir / 'telegram_input.json'} "
                  f"({tg_input['item_count']} items)", file=sys.stderr)
        print("  stage 2b input (digest_input.json) is written by tools/ai_apply.py "
              "after the telegram merge", file=sys.stderr)

    fresh = [n for n in targets if statuses.get(n, {}).get("updated_today")]
    stale = [n for n in targets if n in statuses and not statuses[n].get("updated_today")]
    bodies = sum(statuses.get(n, {}).get("fulltext_fetched", 0) for n in targets)
    print(f"\nday {today.isoformat()} · engine {args.engine}: "
          f"{len(fresh)} source(s) updated today, {len(stale)} stale, {bodies} full text",
          file=sys.stderr)
    if stale:
        print("  stale: " + ", ".join(f"{n}({statuses[n]['staleness_days']}d)" for n in stale),
              file=sys.stderr)
    print(args.outdir / "index.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
