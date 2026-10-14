#!/usr/bin/env python3
"""Unified collector for every newsletter source.

This file is only the **orchestrator**: it parses arguments, calls each source
module from tools/collectors/, writes per-source JSON, and builds the stage-2
input. All site-specific logic lives in tools/collectors/<source>.py.

Two interchangeable extraction engines (``--engine``):

  modern   trafilatura for article bodies, feedparser for feeds. One code path
           for every site, keeps inline links and headings. Default; needs the
           dependencies in requirements.txt and falls back to legacy (with a
           warning) when they are missing.
  legacy   standard library only — hand-rolled regex feeds and per-site body
           markers. Needs nothing installed.

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

import config                                              # noqa: E402
from collectors import base, registry                      # noqa: E402
from collectors import extract as ex                        # noqa: E402
from collectors import telegram as tg                       # noqa: E402
from collectors.base import CST, FULLTEXT_MODES            # noqa: E402
import ai_stage                                             # noqa: E402

def _source_kwargs(name: str, args) -> dict:
    """Per-source arguments. Keeps site quirks out of the main loop."""
    feed = registry.feed_config(name)
    if feed is not None:
        return {
            "url": feed["url"],
            "name": feed.get("name") or name,
            "source": feed.get("source") or name,
            "kind": feed.get("kind") or "article",
            "date_source": feed.get("date_source") or "rss",
            "fulltext": feed.get("fulltext") or args.fulltext,
        }
    if name == "telegram":
        return {"day": args.day, "pages": args.pages, "channels": args.channels,
                "workers": args.workers}
    # Every collector accepts `workers` (the ones with nothing to parallelise
    # just ignore it), so the concurrency setting is one place, not per source.
    kwargs: dict = {"workers": args.workers}
    if name in registry.FULLTEXT_SOURCES:
        kwargs["fulltext"] = args.fulltext
    if name == "linuxdo" and args.date:
        kwargs["date"] = args.date
    if name == "hackernews" and args.hn_feed:
        kwargs["feed"] = args.hn_feed
    if name == "v2ex" and args.v2ex_sort:
        kwargs["sort"] = args.v2ex_sort
    if name == "reddit":
        if args.reddit_subs:
            kwargs["subreddits"] = args.reddit_subs
        if args.reddit_sort:
            kwargs["sort"] = args.reddit_sort
        if args.reddit_delay is not None:
            kwargs["delay"] = args.reddit_delay
    return kwargs

def _nature_maps(cfg) -> tuple[dict, dict]:
    """(source -> nature, "source:unit" -> nature) from the configuration."""
    source_nature: dict[str, str] = {}
    for nature, names in (cfg.get("sources.nature") or {}).items():
        for name in names or []:
            source_nature[str(name)] = str(nature)
    unit_nature = {str(k): str(v) for k, v in (cfg.get("sources.units") or {}).items()}
    return source_nature, unit_nature

def _stamp_identity(name: str, items: list[dict], source_nature: dict,
                    unit_nature: dict) -> None:
    """Give every item its stable id and its nature, in place.

    Stamped after _postprocess() rather than before: dedupe() drops duplicates,
    and a dropped item should not have consumed an id.

    Two items of one source can still collide. dedupe() keys on title+summary
    while the id keys on the canonical URL, so the same URL under two different
    titles survives as two items with one id. The second gets a `-2` suffix and
    a warning, because a shared id would make every citation ambiguous and
    silently break the jump.
    """
    seen: dict[str, int] = {}
    units_seen: set = set()
    field = registry.UNIT_FIELD.get(name, "")
    for entry in items:
        key = ai_stage.item_id(entry)
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count > 1:
            print(f"  ! {name}: duplicate id {key}; renaming to {key}-{count}",
                  file=sys.stderr)
            key = f"{key}-{count}"
        entry["id"] = key
        unit = (entry.get("extra") or {}).get(field) if field else None
        units_seen.add(unit)
        entry["nature"] = registry.source_nature_of(
            name, unit, source_nature=source_nature, unit_nature=unit_nature)

    # A linux.do section name comes from the page, so a typo in sources.units is
    # invisible at configuration time. Saying so here is the only chance. A
    # derived unit (sspai's 派早报) is not a typo when it goes unmatched — the
    # brief simply had no split that day — so it is not reported.
    if name not in registry.DERIVED_UNITS:
        wanted = {k.split(":", 1)[1] for k in unit_nature if k.startswith(f"{name}:")}
        unmatched = sorted(wanted - units_seen)
        if unmatched:
            print(f"  ! {name}: sources.units names no item matched: {', '.join(unmatched)}",
                  file=sys.stderr)

def _postprocess(name: str, items: list[dict], args, outdir: Path) -> list[dict]:
    """Telegram-only: deterministic pre-dedupe plus image download."""
    if name != "telegram":
        return items
    items, groups = base.dedupe(items)
    outdir.mkdir(parents=True, exist_ok=True)
    if not args.no_images:
        saved = tg.download_for_items(items, base.media_root(),
                                      args.images_per_post, args.image_cap,
                                      args.workers)
        print(f"  telegram images saved: {saved}", file=sys.stderr)
    (outdir / "telegram_duplicate_groups.json").write_text(
        json.dumps({"generated_at": datetime.now(CST).isoformat(), "groups": groups},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?",
                        help="one source name, or use --all for every configured source")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=None,
                        help="override the per-source limit for this run")
    parser.add_argument("--outdir", type=Path, default=None)
    parser.add_argument("--aidir", type=Path, default=None,
                        help="where stage 2 input files are written")
    parser.add_argument("--engine", default=None, choices=ex.ENGINES,
                        help="extraction engine; default from config: collect.engine")
    parser.add_argument("--date", default=None, help="linuxdo issue date")
    parser.add_argument("--day", default=None,
                        help="telegram day: today | yesterday | auto | YYYY-MM-DD")
    parser.add_argument("--pages", type=int, default=None,
                        help="telegram pages per channel")
    parser.add_argument("--fulltext", default=None,
                        help=f"full-text policy: {' | '.join(FULLTEXT_MODES)} | <N>")
    parser.add_argument("--no-ai-input", action="store_true",
                        help="skip writing the stage 2 input files")
    parser.add_argument("--no-images", action="store_true",
                        help="do not download telegram post images")
    parser.add_argument("--images-per-post", type=int, default=None)
    parser.add_argument("--image-cap", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None,
                        help="concurrent fetches per source (default from "
                             "config: collect.workers; 1 = serial)")
    parser.add_argument("--dates", action="store_true")
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--list", action="store_true")
    config.add_arguments(parser)
    args = parser.parse_args()

    # ---- configuration: CLI > env > config.toml > built-in default ----
    cfg = config.resolve_or_exit(args, cli={
        "collect.engine": args.engine,
        "collect.fulltext": args.fulltext,
        "collect.limit": args.limit,
        "collect.images_per_post": args.images_per_post,
        "collect.image_cap": args.image_cap,
        "collect.workers": args.workers,
        "telegram.day": args.day,
        "telegram.pages": args.pages,
        "paths.collected": args.outdir,
        "paths.ai": args.aidir,
    })
    if config.emit_show_config(cfg, args):
        return 0

    # Config-defined generic feeds join the registry before anything looks a
    # source up, so a feed id is as valid as a built-in name from here on.
    registry.configure_feeds(cfg.get("feeds"))

    args.fulltext = cfg.get("collect.fulltext")
    args.day = cfg.get("telegram.day")
    args.pages = cfg.get("telegram.pages")
    args.images_per_post = cfg.get("collect.images_per_post")
    args.image_cap = cfg.get("collect.image_cap")
    args.workers = max(1, int(cfg.get("collect.workers") or 1))
    args.outdir = Path(cfg.get("paths.collected"))
    args.aidir = Path(cfg.get("paths.ai"))
    args.channels = list(cfg.get("telegram.channels") or [])
    args.source_nature, args.unit_nature = _nature_maps(cfg)
    args.hn_feed = cfg.get("hackernews.feed")
    args.v2ex_sort = cfg.get("v2ex.sort")
    args.reddit_subs = list(cfg.get("reddit.subreddits") or [])
    args.reddit_sort = cfg.get("reddit.sort")
    args.reddit_delay = cfg.get("reddit.delay")
    args.limits = dict(cfg.get("sources.limits") or {})
    args.labels = dict(cfg.get("sources.labels") or {})
    if not cfg.get("collect.images", True):
        args.no_images = True
    # collect.limit = 0 means "no global override"; the per-source limit applies.
    if args.limit is None:
        args.limit = cfg.get("collect.limit") or None

    # Panel order, and the active set: a source missing from `order` never runs.
    order = [name for name in (cfg.get("sources.order") or []) if name in registry.SOURCES]
    for name in registry.SOURCES:
        if name not in order:
            order.append(name)
    disabled = set(cfg.get("sources.disabled") or [])
    if disabled:
        unknown = disabled - set(registry.SOURCES)
        if unknown:
            print(f"config error: sources.disabled names unknown source(s): "
                  f"{', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        order = [name for name in order if name not in disabled]
    if cfg.get("sources.enabled"):
        wanted = set(cfg.get("sources.enabled"))
        unknown = wanted - set(registry.SOURCES)
        if unknown:
            print(f"config error: sources.enabled names unknown source(s): "
                  f"{', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        order = [name for name in order if name in wanted]
    args.order = order

    if args.source and args.source not in registry.SOURCES:
        parser.error(f"unknown source {args.source!r}; "
                     f"known: {', '.join(sorted(registry.SOURCES))}")

    # An explicitly requested engine is honoured or fails; the default may fall
    # back, so a bare checkout still runs. resolve_engine only falls back when
    # the request came from the default, which is why None is meaningful here.
    requested = args.engine if args.engine is not None else cfg.get("collect.engine")
    engine, warning = ex.resolve_engine(requested, allow_fallback=args.engine is None)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
        if engine == requested:        # no fallback happened: this is fatal
            return 2
    args.engine = engine

    if args.list_sources:
        for name in order:
            module = registry.get(name)
            doc = (module.__doc__ or "").strip().splitlines()[0]
            origin = "feed" if registry.feed_config(name) else "built-in"
            print(f"{name:<12} limit={registry.default_limit(name):<4} "
                  f"{origin:<8} {doc}")
        return 0

    if args.dates:
        for value in registry.get("linuxdo").available_dates():
            print(value)
        return 0

    if not args.source and not args.all:
        parser.error("choose a source, or pass --all")

    today = datetime.now(CST).date()
    targets = order if args.all else [args.source]
    results: dict[str, list[dict]] = {}
    statuses: dict[str, dict] = {}
    failures: dict[str, str] = {}

    for name in targets:
        print(f"→ {name}", file=sys.stderr)
        started = time.time()
        try:
            module = registry.get(name)
            limit = args.limit if args.limit is not None \
                else args.limits.get(name, registry.default_limit(name))
            items = module.collect(limit, engine=args.engine, **_source_kwargs(name, args))
            # The configured label wins over the collector's own display name,
            # so renaming a panel needs no code change.
            if args.labels.get(name):
                for entry in items:
                    entry["source_name"] = args.labels[name]
            for entry in items:
                base.stamp_now(entry)
            base.mark_today(items, today)
            items = _postprocess(name, items, args, args.outdir)
            _stamp_identity(name, items, args.source_nature, args.unit_nature)

            results[name] = items
            statuses[name] = base.source_status(items, today)
            # collectors may expose a structured report (telegram: per channel),
            # so partial failures are recorded instead of only printed
            if hasattr(module, "last_report"):
                statuses[name]["collector_report"] = module.last_report()
                failed = [c for c, r in (statuses[name]["collector_report"].get("channels") or {}).items()
                          if not r.get("ok")]
                if failed:
                    print(f"  partial failure: {', '.join(failed)}", file=sys.stderr)
            # Facts about a source that no item can carry. sspai reports
            # whether today's 派早报 exists at all; with no brief there is no
            # item to read it from, and the page needs to say so.
            if hasattr(module, "last_state"):
                statuses[name].update(module.last_state())
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
