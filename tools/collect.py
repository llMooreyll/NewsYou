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
import inspect
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                              # noqa: E402
import console                                             # noqa: E402
from collectors import base, registry                      # noqa: E402
from collectors import extract as ex                        # noqa: E402
from collectors import telegram as tg                       # noqa: E402
from collectors.base import CST, FULLTEXT_MODES            # noqa: E402
import ai_stage                                             # noqa: E402

# How many of a collector's own notices are reprinted under its line. Beyond
# this they are only counted: a source that emits fifty remarks has a problem
# worth one line, not fifty.
REMARK_LIMIT = 4

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

def _accepts_progress(module) -> bool:
    """Whether a collector takes the live-progress callback.

    Only the sources that can sit silent for a minute or more implement it; the
    rest are finished before a progress line would be worth reading.
    """
    try:
        return "progress" in inspect.signature(module.collect).parameters
    except (TypeError, ValueError):
        return False

def _collector_failures(report: dict) -> list[tuple[str, str]]:
    """(unit, message) for every sub-unit a collector reported as failed."""
    out: list[tuple[str, str]] = []
    for group in ("channels", "subreddits"):
        for unit, record in (report.get(group) or {}).items():
            if not record.get("ok", True):
                out.append((unit, str(record.get("error") or "failed")))
    return out

def _unit_total(report: dict) -> int:
    """How many sub-units the collector had, so "2 failed" can say "out of"
    what. Telegram calls them channels, reddit calls them subreddits."""
    for group in ("channels", "subreddits"):
        if report.get(group):
            return len(report[group])
    return 0

def day_start_of(value) -> int:
    """The hour a source's day begins, from a config value.

    Absent or 0 means midnight. A value that is not an hour of the day is a
    configuration error, and saying so beats quietly summarising the wrong
    window.
    """
    if value in (None, "", False):
        return 0
    try:
        hour = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"day start hour must be 0-23, got {value!r}") from None
    if not 0 <= hour <= 23:
        raise ValueError(f"day start hour must be 0-23, got {hour}")
    return hour

def _day_starts(cfg) -> dict[str, int]:
    """source -> the hour its day begins, including the grace offset.

    The grace exists because a source's publication time is a habit, not a
    contract: linux.do is listed at 21 and publishes at 22:00, and an edition
    half an hour late must not fall out of its own day. Only sources that
    actually have a start hour get the offset, so a midnight source stays at
    midnight rather than silently becoming 23:00 the day before.
    """
    raw = {str(k): v for k, v in (cfg.get("sources.day_start_hour") or {}).items()}
    grace = int(cfg.get("sources.day_start_grace_hours") or 0)
    out: dict[str, int] = {}
    for name, value in raw.items():
        hour = day_start_of(value)
        out[name] = (hour - grace) % 24 if hour else 0
    return out

def _snapshot_tags(cfg) -> set[str]:
    """Entries tagged 实时, as source names or `<source>:<unit>` keys.

    One list, two granularities, mirroring how `sources.units` is keyed. A whole
    source is tagged when its response is a ranking of what is current; a single
    unit is tagged when only part of a source works that way — a live feed
    channel inside an otherwise dated source, say.
    """
    return {str(name) for name in (cfg.get("sources.snapshot") or [])}

def _snapshot_units(tags: set[str], name: str) -> set[str]:
    """The unit names tagged 实时 for one source, from the shared tag list."""
    prefix = f"{name}:"
    return {tag[len(prefix):] for tag in tags if tag.startswith(prefix)}

def _collector_notes(report: dict) -> list[str]:
    """What a collector's own report has to say, in the caller's words.

    Collectors used to print these as they went, which put raw text on top of
    the live progress line. The report already carries them — telegram records
    `kept` per channel and the day it settled on — so this phrases them once,
    next to the source they belong to.
    """
    notes: list[str] = []
    for group, noun in (("channels", "频道"), ("subreddits", "板块")):
        units = report.get(group) or {}
        if not units:
            continue
        quiet = [unit for unit, record in units.items()
                 if record.get("ok") and record.get("kept") == 0]
        if quiet:
            notes.append(f"{len(quiet)}/{len(units)} 个{noun}今日无内容")
    if report.get("mode") == "auto" and report.get("day"):
        notes.append(f"自动选定日期 {report['day']}")
    return notes

def _group_failures(entries: list[tuple[str, str]]) -> list[tuple[int, str, list[str]]]:
    """Fold identical failure messages together, most frequent first.

    Eighteen channels failing with the same TLS error is one problem, not
    eighteen. Repeating it buries everything else the run had to say, which is
    exactly what made a broken run unreadable.
    """
    grouped: dict[str, list[str]] = {}
    for context, message in entries:
        grouped.setdefault(message, []).append(context)
    return sorted(((len(ctx), msg, ctx) for msg, ctx in grouped.items()),
                  key=lambda row: -row[0])

def _source_detail(status: dict, count: int, notes: list[str]) -> str:
    """The right-hand side of a source's result line, in fixed columns."""
    if not count:
        fresh = "无内容"
    elif status.get("updated_today"):
        fresh = "今日"
    else:
        days = status.get("staleness_days")
        fresh = "无日期" if days is None else f"{days} 天前"
    line = f"{console.pad(f'{count} 条', 8, right=True)} {console.pad(fresh, 9)}"
    bodies = status.get("fulltext_fetched") or 0
    if bodies:
        line += f" 全文 {bodies}"
    if notes:
        line = line.rstrip() + "  " + " · ".join(notes)
    return line

def _stamp_identity(name: str, items: list[dict], source_nature: dict,
                    unit_nature: dict, known_units: set | None = None) -> list[str]:
    """Give every item its stable id and its nature, in place.

    Stamped after _postprocess() rather than before: dedupe() drops duplicates,
    and a dropped item should not have consumed an id.

    Two items of one source can still collide. dedupe() keys on title+summary
    while the id keys on the canonical URL, so the same URL under two different
    titles survives as two items with one id. The second gets a `-2` suffix,
    because a shared id would make every citation ambiguous and silently break
    the jump. Renaming is worth saying out loud, so it comes back as a note for
    the caller to print next to the source instead of mid-run.
    """
    seen: dict[str, int] = {}
    units_seen: set = set()
    dupes: list[str] = []
    field = registry.UNIT_FIELD.get(name, "")
    for entry in items:
        key = ai_stage.item_id(entry)
        count = seen.get(key, 0) + 1
        seen[key] = count
        if count > 1:
            dupes.append(key)
            key = f"{key}-{count}"
        entry["id"] = key
        unit = (entry.get("extra") or {}).get(field) if field else None
        units_seen.add(unit)
        entry["nature"] = registry.source_nature_of(
            name, unit, source_nature=source_nature, unit_nature=unit_nature)

    notes: list[str] = []
    if dupes:
        shown = ", ".join(dupes[:3]) + ("…" if len(dupes) > 3 else "")
        notes.append(f"{len(dupes)} 个重复 id（{shown} 已改名）")

    # A linux.do section name comes from the page, so a typo in sources.units is
    # invisible at configuration time. Saying so here is the only chance. A
    # derived unit (sspai's 派早报) is not a typo when it goes unmatched — the
    # brief simply had no split that day — so it is not reported. Neither is a
    # source that returned nothing, nor a unit the collector knows about and
    # simply had no posts for: both would blame the config for a quiet day.
    if name not in registry.DERIVED_UNITS and items:
        wanted = {k.split(":", 1)[1] for k in unit_nature if k.startswith(f"{name}:")}
        unmatched = sorted(wanted - units_seen - (known_units or set()))
        if unmatched:
            notes.append("sources.units 未匹配: " + ", ".join(unmatched))
    return notes

def _postprocess(name: str, items: list[dict], args, outdir: Path) -> tuple[list[dict], list[str]]:
    """Telegram-only: deterministic pre-dedupe plus image download."""
    if name != "telegram":
        return items, []
    items, groups = base.dedupe(items)
    outdir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    if not args.no_images:
        saved = tg.download_for_items(items, base.media_root(),
                                      args.images_per_post, args.image_cap,
                                      args.workers)
        if saved:
            notes.append(f"图片 {saved}")
    (outdir / "telegram_duplicate_groups.json").write_text(
        json.dumps({"generated_at": datetime.now(CST).isoformat(), "groups": groups},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items, notes

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
    args.day_starts = _day_starts(cfg)
    args.snapshots = _snapshot_tags(cfg)
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

    report = console.reporter()
    disabled_note = f" · 禁用 {', '.join(sorted(disabled))}" if disabled else ""
    report.banner("采集", f"engine {args.engine}", f"workers {args.workers}",
                  f"{len(order)} 个源{disabled_note}")

    today = datetime.now(CST).date()
    targets = order if args.all else [args.source]
    results: dict[str, list[dict]] = {}
    statuses: dict[str, dict] = {}
    failures: dict[str, str] = {}
    breakdown: list[tuple[str, str]] = []
    # Sources run concurrently, so each one's results land in its own slot and
    # are read back in `targets` order. The output stays a function of the
    # configuration, not of which site answered first.
    collected: dict[str, dict] = {}

    def gather(index: int, name: str) -> None:
        """Fetch one source and record everything the caller needs to report it.

        Runs in a worker thread, so it touches only its own locals plus the
        `collected` slot reserved for it. Notes and remarks are returned to the
        main thread rather than printed here: the live region belongs to one
        thread, and a note written from a worker would interleave with it.
        """
        step = console.Step(report, name, index, len(targets), name)
        try:
            module = registry.get(name)
            limit = args.limit if args.limit is not None \
                else args.limits.get(name, registry.default_limit(name))
            kwargs = _source_kwargs(name, args)
            if _accepts_progress(module):
                kwargs["progress"] = step.progress
            # A collector's own stderr is buffered, not shown: a raw write lands
            # on top of the live line and is erased by the next redraw.
            with console.capture() as stray:
                items = module.collect(limit, engine=args.engine, **kwargs)
            remarks = console.captured_lines(stray)

            # The configured label wins over the collector's own display name,
            # so renaming a panel needs no code change.
            if args.labels.get(name):
                for entry in items:
                    entry["source_name"] = args.labels[name]
            for entry in items:
                base.stamp_now(entry)
            # The window is per source: linux.do's day starts the previous
            # evening, so "today" is not one date for the whole run, and a
            # source tagged 实时 has no window at all.
            start, now = base.day_window(args.day_starts.get(name, 0),
                                         snapshot=name in args.snapshots)
            base.mark_today(items, start, now)
            # A unit tagged 实时 skips the window on its own, for the case where
            # one channel inside a dated source is a live feed.
            live = _snapshot_units(args.snapshots, name)
            if live and start is not None:
                field = registry.UNIT_FIELD.get(name, "")
                for entry in items:
                    if field and (entry.get("extra") or {}).get(field) in live:
                        entry["is_today"] = True
            items, notes = _postprocess(name, items, args, args.outdir)

            status = base.source_status(items, start, now)
            # A collector may expose a structured report (telegram: per channel,
            # reddit: per subreddit), so a partial failure is recorded rather
            # than only mentioned in passing.
            collector_report = module.last_report() if hasattr(module, "last_report") else {}
            if collector_report:
                status["collector_report"] = collector_report
            # Facts about a source that no item can carry. sspai reports
            # whether today's 派早报 exists at all; with no brief there is no
            # item to read it from, and the page needs to say so.
            if hasattr(module, "last_state"):
                status.update(module.last_state())

            # Every unit the collector knows about, content or not: a
            # sources.units name is only a typo if the collector has never heard
            # of it.
            known_units: set = set()
            for group in ("channels", "subreddits"):
                known_units |= set(collector_report.get(group) or {})
            notes += _stamp_identity(name, items, args.source_nature, args.unit_nature,
                                     known_units)
            notes += _collector_notes(collector_report)

            broken = _collector_failures(collector_report)
            if broken:
                units = _unit_total(collector_report)
                notes.append(f"{len(broken)}/{units} 个单元失败" if units
                             else f"{len(broken)} 个单元失败")
            if len(remarks) > REMARK_LIMIT:
                notes.append(f"{len(remarks)} 条提示")

            # Nothing came back and the collector said why: a failed source even
            # though collect() itself returned cleanly.
            state = "fail" if (not items and broken) else ("warn" if broken else "ok")
            detail = _source_detail(status, len(items), notes)
            step.done(state, detail)
            collected[name] = {"items": items, "status": status, "remarks": remarks,
                               "broken": broken, "state": state, "detail": detail}
        except Exception as exc:  # noqa: BLE001
            failure = f"{type(exc).__name__}: {exc}"
            step.done("fail", failure)
            collected[name] = {"error": failure}

    # Every source at once. Each talks to its own hosts, and the per-source
    # concurrency inside a collector is unchanged, so the request rate any one
    # site sees is exactly what it was when this loop was serial; what grows is
    # the number of sites in flight. Wall clock becomes the slowest source
    # rather than their sum, and Telegram — four pages across eighteen channels
    # — is almost always that source.
    base.parallel_map(lambda pair: gather(*pair), list(enumerate(targets, 1)),
                      max(1, len(targets)))
    for name in targets:
        outcome = collected.get(name) or {"error": "source produced no result"}
        if "error" in outcome:
            # Already reported on its own line by `gather`; this only records it.
            failures[name] = outcome["error"]
            breakdown.append((name, outcome["error"]))
            continue
        if outcome["broken"]:
            breakdown.extend((f"{name}/{unit}", message)
                             for unit, message in outcome["broken"])
        if outcome["state"] == "fail":
            failures[name] = f"{len(outcome['broken'])} 个单元失败"
        results[name] = outcome["items"]
        statuses[name] = outcome["status"]
        for line in outcome["remarks"][:REMARK_LIMIT]:
            report.note("  " + report.style(line, "dim"))
        extra = len(outcome["remarks"]) - REMARK_LIMIT
        if extra > 0:
            report.note(report.style(f"  …还有 {extra} 条提示", "dim"))

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

    for name, err in failures.items():
        index["sources"][name] = {"file": None, "count": 0, "ok": False, "error": err}

    (args.outdir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    stage2_inputs: list[Path] = []
    if not args.no_ai_input and results:
        args.aidir.mkdir(parents=True, exist_ok=True)
        if "telegram" in results:
            tg_input = ai_stage.build_telegram_input(results["telegram"], today.isoformat())
            (args.aidir / "telegram_input.json").write_text(
                json.dumps(tg_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stage2_inputs.append(args.aidir / "telegram_input.json")

    # ---- what the run amounted to ----
    total = sum(len(results.get(name, [])) for name in targets)
    bodies = sum(statuses.get(name, {}).get("fulltext_fetched", 0) for name in targets)
    report.write()
    report.write("  " + report.style("采集完成", "bold") + "  " +
                 f"{total} 条 · {len(results)}/{len(targets)} 源成功 · "
                 f"{bodies} 篇全文 · {report.elapsed()}")
    if breakdown:
        report.write()
        report.write("  " + report.style("失败详情", "bold"))
        for count, message, contexts in _group_failures(breakdown):
            where = contexts[0] if count == 1 else f"{count} 个单元"
            label = f"    {console.pad(where, 22)} "
            indent = " " * console.display_width(label)
            for line in console.wrap(message, report.width, label, indent):
                report.write(line)
    silent = [name for name in targets if not results.get(name) and name not in failures]
    if silent:
        label = "  今日无数据："
        for line in console.wrap("，".join(silent), report.width, label,
                                 " " * console.display_width(label)):
            report.write(line)
    report.write()
    report.write("  " + report.style("输出", "dim") + "  " +
                 " · ".join([f"{args.outdir}/（{len(results)} 个源）"] +
                            [str(path) for path in stage2_inputs]))
    report.close()
    print(args.outdir / "index.json")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
