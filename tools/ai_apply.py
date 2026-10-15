#!/usr/bin/env python3
"""Stage 2 runner: validate AI decisions and apply them deterministically.

Reads the raw collection, applies whatever decision files exist, writes the
processed data the renderer consumes, and emits the stage 2b input.

    # after tools/collect.py has run
    python tools/ai_apply.py                       # apply whatever exists
    python tools/ai_apply.py --report              # show what was applied
    python tools/ai_apply.py --dry-run             # validate, write nothing

Flow:

    data/collected/*.json                      raw, never modified
        + data/ai/telegram_decisions.json      optional, stage 2a
        -> data/processed/*.json               merged telegram + passthrough
        + data/ai/classify_decisions.json      optional, stage 2c (news)
        + data/ai/classify_forum_decisions.json optional, stage 2d (discussion)
        -> data/processed/categories.json      the 分类 / 讨论 blocks
        -> data/ai/digest_input.json           stage 2b input (always written)
        + data/ai/digest_decisions.json        optional, stage 2b
        -> data/processed/digest.json          the briefing panel

The per-source files are written after 2c/2d, not after 2a: an item carries the
`category` it was filed under, and writing it before the classification ran
would ship a file without one.

Nothing here calls a model. Every decision is checked before it is applied, and
anything invalid is reported and skipped rather than guessed at.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_model  # noqa: E402
import ai_stage  # noqa: E402
import config  # noqa: E402
import console  # noqa: E402

SKIP_FILES = {"index.json", "telegram_duplicate_groups.json"}

# The two classification sides differ only by which half of the data they see;
# these are for the progress line.
SIDE_LABEL = {"news": "新闻", "discussion": "讨论"}


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"  ! {path} is not valid JSON: {exc}", file=sys.stderr)
        return None


def load_collected(indir: Path, day: str | None = None) -> tuple[dict[str, list[dict]], dict, dict]:
    """Load the sources that belong to the run for `day`.

    data/collected/ outlives a run, and collect.py writes a file only for a
    source that succeeded. A source that fails today, or one switched off in
    `sources.enabled`, therefore still has its file from an earlier day sitting
    here, status and all. Loading by glob alone put that file in today's digest
    under a "今日更新" badge. A file written before `day` is skipped, and says so.

    Before, not merely different: a run that crosses midnight stamps its files
    one day after the index day, and those are this run's own.

    Returns (results, statuses, skipped), where `skipped` maps a source name to
    why it was left out.
    """
    results: dict[str, list[dict]] = {}
    statuses: dict[str, dict] = {}
    skipped: dict[str, dict] = {}
    index = load_json(indir / "index.json") or {}
    for path in sorted(indir.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        written = str(payload.get("collected_at") or "")[:10]
        if day and written and written < day:
            print(f"  ! {path.name} was collected on {written}, before {day}; "
                  f"skipped (did the source fail or get disabled today?)", file=sys.stderr)
            skipped[path.stem] = {"reason": "stale", "detail": f"上次采集 {written}"}
            continue
        results[path.stem] = payload.get("items") or []
        statuses[path.stem] = payload.get("status") or index.get("sources", {}).get(path.stem, {})
    return results, statuses, skipped


def missing_sources(collected: Path, cfg, processed: dict, skipped: dict) -> dict[str, dict]:
    """Sources this run was meant to collect but has no fresh data for.

    A failure used to show up only as a tab that quietly disappeared, because
    the renderer draws whatever is in data/processed/ and nothing else. Recording
    the absence lets the digest keep the tab and say what happened, and keeps the
    header count honest — 7/10 sources updated, not 7/7.

    A source that was switched off is not reported: it is not in the configured
    set, so its silence is intended rather than a fault.
    """
    index = load_json(collected / "index.json") or {}
    failed = {name: info for name, info in (index.get("sources") or {}).items()
              if isinstance(info, dict) and info.get("ok") is False}
    absent: dict[str, dict] = {}
    for name in ai_stage.expected_sources(cfg):
        if name in processed:
            continue
        entry = {"label": ai_stage.configured_label(name, cfg)}
        if name in failed:
            entry["reason"] = "failed"
            entry["detail"] = str(failed[name].get("error") or "")
        elif name in skipped:
            entry["reason"] = skipped[name]["reason"]
            entry["detail"] = skipped[name].get("detail", "")
        else:
            entry["reason"] = "missing"
        absent[name] = entry
    return absent


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def decisions_are_stale(path: Path, day: str) -> bool:
    """True when an existing decisions file was made for a different day.

    Reuse is keyed on the file existing, which is only sound within one day:
    yesterday's decisions reference yesterday's item ids. For Telegram those
    ids vanish (message ids increase) so the stale file is inert, but for
    anything keyed by URL — Hacker News, blogs, feeds — an item that stays on
    the list keeps its id, so yesterday's judgement gets applied to today's
    data with no error at all. Comparing the recorded day closes that gap.
    """
    if not path.exists():
        return False
    payload = load_json(path)
    if not payload:
        return False
    return payload.get("day") != day


def load_decisions(path: Path, day: str):
    """A decisions file, but only one that was made for `day`.

    Whether to *call* the model and whether to *apply* what is on disk are two
    questions. force_for() answers the first. Nothing answered the second: with
    the backend off, or when today's call failed, the file a previous day left
    behind was read back and applied. Telegram ids do not survive a day, so
    that half was inert, but URL-keyed ids do, and the digest panel came out
    carrying yesterday's headline over today's items with no error anywhere.
    """
    payload = load_json(path)
    if isinstance(payload, dict) and payload.get("day") != day:
        print(f"  ! {path.name} is for {payload.get('day') or 'an unrecorded day'}, "
              f"not {day}; not applied", file=sys.stderr)
        return None
    return payload


def remove_leftovers(outdir: Path, written: set[str]) -> list[str]:
    """Delete processed files an earlier run wrote and this one did not.

    The renderer shows every source file in this directory and the archive
    copies it whole, so a file left from another day is as good as current: a
    source that dropped out today would keep its old panel, and a day without
    decisions would keep the last briefing. Only this stage's own output is
    removed — a source file names itself in `source`, the panels carry
    `summary` or `sides` — so pointing --out at a shared directory cannot cost
    anything else.
    """
    removed: list[str] = []
    for path in sorted(outdir.glob("*.json")):
        if path.name in written:
            continue
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        is_panel = path.name == "digest.json" and "summary" in payload
        is_categories = path.name == "categories.json" and "sides" in payload
        is_source = payload.get("source") == path.stem and "items" in payload
        if is_panel or is_categories or is_source:
            path.unlink()
            removed.append(path.name)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collected", type=Path, default=None)
    parser.add_argument("--ai", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="validate but write nothing")
    parser.add_argument("--report", action="store_true", help="print the applied changes")
    parser.add_argument("--ai-backend", default=None,
                        choices=ai_model.BACKENDS,
                        help="model backend. Default from config: ai.backend")
    parser.add_argument("--ai-model", default=None,
                        help="model passed to the backend. Default from config: "
                             "ai.model. Pass '' to let the backend choose")
    parser.add_argument("--ai-timeout", type=int, default=None,
                        help="seconds before a backend call is abandoned")
    parser.add_argument("--ai-force", action="store_true",
                        help="regenerate decisions even if the file already exists")
    config.add_arguments(parser)
    args = parser.parse_args()

    # CLI > env > config.toml > built-in default, for every value below.
    cfg = config.resolve_or_exit(args, cli={
        "paths.collected": args.collected,
        "paths.ai": args.ai,
        "paths.processed": args.out,
        "ai.backend": args.ai_backend,
        "ai.model": args.ai_model,
        "ai.timeout": args.ai_timeout,
        "ai.reuse": False if args.ai_force else None,
    })
    if config.emit_show_config(cfg, args):
        return 0

    args.collected = Path(cfg.get("paths.collected"))
    args.ai = Path(cfg.get("paths.ai"))
    args.out = Path(cfg.get("paths.processed"))
    args.ai_timeout = cfg.get("ai.timeout")
    args.ai_model = cfg.get("ai.model") or None
    args.ai_extra_args = list(cfg.get("ai.args") or [])
    ai_reuse = cfg.get("ai.reuse", False)
    ai_backend, ai_warning = ai_model.resolve_backend(cfg.get("ai.backend"))
    if ai_warning:
        print(f"warning: {ai_warning}", file=sys.stderr)

    day = (load_json(args.collected / "index.json") or {}).get("day") \
        or datetime.now().astimezone().date().isoformat()

    results, statuses, skipped = load_collected(args.collected, day)
    if not results:
        print(f"no collected data in {args.collected}; run tools/collect.py first", file=sys.stderr)
        return 1

    def force_for(path: Path) -> bool:
        """Regenerate unless the caller asked to reuse a file from this same day.

        `reuse = false` (the default) means always call the model. When reuse is
        opted into, a file from another day is still regenerated rather than
        applied to data it was never about.
        """
        if args.ai_force or not ai_reuse:
            return True
        if decisions_are_stale(path, day):
            print(f"  ai: {path.name} is for another day; regenerating",
                  file=sys.stderr)
            return True
        return False

    processed: dict[str, list[dict]] = {k: list(v) for k, v in results.items()}
    report: dict = {"day": day, "generated_at": datetime.now().astimezone().isoformat()}

    output = console.reporter()
    output.banner("AI 处理", f"backend {ai_backend}",
                  f"model {args.ai_model or '（后端默认）'}",
                  f"day {day}", f"{len(processed)} 个源")

    # Numbered up front so the counter answers "how many left", not just "which
    # one is this". A step is only counted when it will actually run.
    steps_total = (1 if "telegram" in processed else 0) + len(ai_stage.CLASSIFY_SIDES) + 1
    step_no = 0

    def next_step(name: str) -> console.Step:
        nonlocal step_no
        step_no += 1
        return console.Step(output, name, step_no, steps_total, name, name_width=13)

    # ---------------- stage 2a: telegram ----------------
    # If a model backend is selected, ask it for decisions before reading them.
    # Existing decisions are reused unless --ai-force, so re-running the pipeline
    # does not spend another model call.
    tg_step = next_step("telegram 合并") if "telegram" in processed else None
    if ai_backend != "none":
        report["ai_telegram"] = ai_model.generate(
            ai_backend, args.ai / "telegram_input.json",
            args.ai / "telegram_decisions.json", model=args.ai_model,
            timeout=args.ai_timeout, force=force_for(args.ai / "telegram_decisions.json"),
            dry_run=args.dry_run, extra_args=args.ai_extra_args, day=day,
            verbose=False, progress=tg_step.progress if tg_step else None)

    tg_decisions_path = args.ai / "telegram_decisions.json"
    tg_decisions = load_decisions(tg_decisions_path, day)
    if tg_decisions and "telegram" in processed:
        known = {ai_stage.item_id(e) for e in processed["telegram"]}
        actions, problems = ai_stage.validate_telegram_decisions(tg_decisions, known)
        merged_items, tg_report = ai_stage.apply_telegram_decisions(processed["telegram"], actions)
        processed["telegram"] = merged_items
        report["telegram"] = {
            "decisions_file": str(tg_decisions_path),
            "model": tg_decisions.get("model"),
            "accepted_actions": len(actions),
            "rejected_actions": len(problems),
            "problems": problems,
            **tg_report,
        }
        if tg_step:
            tg_step.done("warn" if problems else "ok",
                         f"{tg_report['items_before']} → {tg_report['items_after']} 条 · "
                         f"去广告 {tg_report['removed_ads']} · "
                         f"丢弃 {tg_report['dropped']} · "
                         f"合并 {tg_report['merged_groups']} 组"
                         + (f" · {len(problems)} 拒绝" if problems else ""))
        for ad in tg_report.get("ad_details", [])[:10]:
            output.note(f"  · 广告 [{ad['category']}] {ad['id']}: {ad.get('reason') or ''}")
        for problem in problems[:10]:
            output.warn(f"telegram 合并: {problem}")
        statuses["telegram"] = ai_stage_status(processed["telegram"], statuses.get("telegram", {}))
    else:
        report["telegram"] = {"decisions_file": None, "applied": False,
                              "note": "no telegram_decisions.json; raw messages kept"}
        if tg_step:
            tg_step.done("warn", "无决策文件 · 保留原始消息")
        elif tg_decisions:
            output.warn("有 telegram 决策文件，但本次没有采集到 telegram 数据")

    output.write()

    # ---------------- stage 2c / 2d: classification ----------------
    # The two sides are the same prompt and the same validator over different
    # slices of the data, and each is one blocking CLI call. They go out
    # together: generate() is a subprocess.run, which releases the GIL, so this
    # is real concurrency and the wall clock is max(), not sum().
    index_by_id, aliases = ai_stage.build_item_index(processed)
    groups = ai_stage.classify_groups(processed)
    classify_paths = {
        side: (args.ai / f"{stem}_input.json", args.ai / f"{stem}_decisions.json")
        for side, stem in ai_stage.CLASSIFY_SIDES
    }

    classify_steps = {side: next_step(f"分类·{SIDE_LABEL.get(side, side)}")
                      for side in classify_paths}
    for side, (input_path, _) in classify_paths.items():
        payload = ai_stage.build_classify_input(groups[side], day, side)
        if not args.dry_run:
            write_json(input_path, payload)
        classify_steps[side].progress(f"{payload['item_count']} 条 · {input_path.name}")

    if ai_backend != "none":
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                side: pool.submit(
                    ai_model.generate, ai_backend, input_path, decisions_path,
                    model=args.ai_model, timeout=args.ai_timeout,
                    force=force_for(decisions_path), dry_run=args.dry_run,
                    extra_args=args.ai_extra_args, day=day, verbose=False,
                    progress=classify_steps[side].progress)
                for side, (input_path, decisions_path) in classify_paths.items()
            }
            report["classify_calls"] = {}
            for side, future in futures.items():
                try:
                    report["classify_calls"][side] = future.result()
                except Exception as exc:  # noqa: BLE001 - one side must not sink the other
                    report["classify_calls"][side] = {
                        "backend": ai_backend,
                        "error": f"{type(exc).__name__}: {exc}",
                    }

    categories_payload: dict = {
        "schema": ai_stage.CATEGORIES_SCHEMA,
        "day": day,
        "generated_at": report["generated_at"],
        "sides": {},
    }
    report["classify"] = {}
    for entry in (e for items in processed.values() for e in items):
        entry["category"] = None

    anchor = 0
    for side, (_, decisions_path) in classify_paths.items():
        items = groups[side]
        decisions = load_decisions(decisions_path, day)
        side_report: dict = {
            "decisions_file": str(decisions_path) if decisions else None,
            "item_count": len(items),
            "classified": 0,
            "categories": 0,
            "rejected": 0,
            "problems": [],
        }
        categories: list[dict] = []
        if decisions:
            accepted, problems = ai_stage.validate_classify_decisions(
                decisions, side, index_by_id)
            accepted, shape_problems = ai_stage.finalize_categories(
                accepted, items, side)
            problems += shape_problems
            categories = ai_stage.apply_classify_decisions(
                accepted, index_by_id, anchor_start=anchor)
            anchor += len(categories)
            for category in accepted:
                for ref in category["item_ids"]:
                    target = index_by_id.get(ref)
                    if target is not None:
                        target["category"] = category["name"]
            side_report.update({
                "model": decisions.get("model"),
                "categories": len(categories),
                "classified": sum(len(c["refs"]) for c in categories),
                "rejected": len(problems),
                "problems": problems,
            })
            for problem in problems[:10]:
                output.warn(f"分类·{SIDE_LABEL.get(side, side)}: {problem}")

        categories_payload["sides"][side] = {
            "nature": side,
            "item_count": len(items),
            "classified": side_report["classified"],
            "categories": categories,
        }
        report["classify"][side] = side_report
        # One outcome per side, whatever happened. With verbose=False on both
        # calls, a failed side would otherwise leave nothing but its own
        # absence — which is exactly what a side with no decisions looks like.
        # The step says which of the two it was.
        step = classify_steps[side]
        error = ((report.get("classify_calls") or {}).get(side) or {}).get("error")
        if error:
            step.done("fail", f"{len(items)} 条 · 无决策（{error}）")
        elif not decisions:
            step.done("warn", f"{len(items)} 条 · 无决策文件")
        else:
            step.done("warn" if side_report["rejected"] else "ok",
                      f"{side_report['classified']}/{len(items)} 条 → "
                      f"{side_report['categories']} 类"
                      + (f" · {side_report['rejected']} 拒绝"
                         if side_report["rejected"] else ""))

    output.write()

    # ---------------- write processed sources ----------------
    # After 2c/2d, so every item carries the `category` it was filed under.
    written: set[str] = set()
    if not args.dry_run:
        for name in ai_stage.source_order(processed):
            write_json(args.out / f"{name}.json", {
                "source": name,
                "source_name": ai_stage.source_label(name, processed[name]),
                "day": day,
                "generated_at": report["generated_at"],
                "count": len(processed[name]),
                "status": statuses.get(name, {}),
                "items": processed[name],
            })
            written.add(f"{name}.json")
        # A separate file from digest.json on purpose: a classification that
        # fails must not cost the briefing, and vice versa.
        if ai_stage.has_categories(categories_payload):
            write_json(args.out / "categories.json", categories_payload)
            written.add("categories.json")

    # ---------------- stage 2b input ----------------
    digest_input = ai_stage.build_digest_input(processed, statuses, day)
    if not args.dry_run:
        write_json(args.ai / "digest_input.json", digest_input)
    report["digest_input"] = {"file": str(args.ai / "digest_input.json"),
                              "item_count": digest_input["item_count"]}
    digest_step = next_step("生成汇总")
    digest_step.progress(f"{digest_input['item_count']} 条 · "
                         f"{len(digest_input['sources'])} 个源")

    # ---------------- stage 2b: digest ----------------
    # The input now exists, so a selected backend can be asked for decisions.
    if ai_backend != "none":
        report["ai_digest"] = ai_model.generate(
            ai_backend, args.ai / "digest_input.json",
            args.ai / "digest_decisions.json", model=args.ai_model,
            timeout=args.ai_timeout, force=force_for(args.ai / "digest_decisions.json"),
            dry_run=args.dry_run, extra_args=args.ai_extra_args, day=day,
            verbose=False, progress=digest_step.progress)

    digest_decisions_path = args.ai / "digest_decisions.json"
    digest_decisions = load_decisions(digest_decisions_path, day)
    if digest_decisions:
        digest, problems = ai_stage.validate_digest_decisions(digest_decisions, set(index_by_id), aliases)
        panel = ai_stage.apply_digest_decisions(digest, index_by_id)
        panel["generated_at"] = report["generated_at"]
        panel["day"] = panel.get("day") or day
        if not args.dry_run:
            write_json(args.out / "digest.json", panel)
            written.add("digest.json")
        report["digest"] = {
            "decisions_file": str(digest_decisions_path),
            "model": digest.get("model"),
            "keywords": len(panel["keywords"]),
            # Audit, not validation: it says how many of the items the summary
            # claims to draw on actually exist, not whether it used them well.
            "audited": len(panel["sources"]),
            "audit_requested": len(digest.get("source_ids") or []),
            "rejected": len(problems),
            "problems": problems,
        }
        digest_step.done("warn" if problems else "ok",
                         f"{len(panel['keywords'])} 关键词 · "
                         f"{len(panel['sources'])}/{len(digest.get('source_ids') or [])} 引用"
                         + (f" · {len(problems)} 拒绝" if problems else ""))
        for problem in problems[:10]:
            output.warn(f"汇总: {problem}")
    else:
        report["digest"] = {"decisions_file": None, "applied": False,
                            "note": "no digest_decisions.json; no briefing panel"}
        digest_step.done("warn", "无决策文件 · 不生成汇总面板")

    # ---------------- summary ----------------
    absent = missing_sources(args.collected, cfg, processed, skipped)
    if absent:
        output.write()
        label = "  今日无数据的源："
        text = "，".join(f"{name}（{info['reason']}）" for name, info in absent.items())
        for line in console.wrap(text, output.width, label,
                                 " " * console.display_width(label)):
            output.write(line)
    if not args.dry_run:
        write_json(args.out / "index.json", {
            "day": day,
            "generated_at": report["generated_at"],
            "sources": {
                name: {"count": len(items), "status": statuses.get(name, {})}
                for name, items in processed.items()
            },
            # Sources the digest should still show a tab for, with the reason
            # there is nothing behind it.
            "absent": absent,
            "stage2": {
                "ai_backend": ai_backend,
                "telegram": {k: v for k, v in report["telegram"].items() if k != "drop_details"},
                "classify": report["classify"],
                "digest": report.get("digest"),
            },
        })
        written.add("index.json")
        for name in remove_leftovers(args.out, written):
            output.note(f"清理上一次运行遗留的 {name}")
    else:
        output.note("dry run：未写入任何文件")

    # ---------------- what the stage amounted to ----------------
    classified = sum(v.get("classified", 0) for v in (report.get("classify") or {}).values())
    # Every decision this stage refused, from all three places that can refuse
    # one. A rejected decision is not an error — the validators are strict on
    # purpose — but it is the number worth watching.
    rejected = (sum(v.get("rejected", 0) for v in (report.get("classify") or {}).values())
                + (report.get("digest") or {}).get("rejected", 0)
                + (report.get("telegram") or {}).get("rejected_actions", 0))
    output.write()
    output.write("  " + output.style("处理完成", "bold") + "  " +
                 f"{digest_input['item_count']} 条输入 · 分类 {classified} 条 · "
                 f"{rejected} 项决策被拒 · {output.elapsed()}")
    output.write("  " + output.style("输出", "dim") + "  " +
                 (f"{args.out}/（{len(written)} 个文件）· "
                  f"{args.ai}/digest_input.json" if written
                  else "无（dry run）"))
    output.close()

    if args.report:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def ai_stage_status(items: list[dict], previous: dict) -> dict:
    """Recompute freshness after a merge, since merged items change timestamps."""
    from datetime import datetime as _dt, timezone, timedelta
    cst = timezone(timedelta(hours=8))
    today = _dt.now(cst).date()
    stamps = []
    for entry in items:
        raw = entry.get("published_at")
        if not raw:
            continue
        try:
            stamps.append(_dt.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(cst))
        except ValueError:
            continue
    latest = max(stamps) if stamps else None
    return {
        **previous,
        "day": today.isoformat(),
        "updated_today": any(s.date() == today for s in stamps),
        "latest_published_at": latest.isoformat() if latest else previous.get("latest_published_at"),
        "staleness_days": (today - latest.date()).days if latest else previous.get("staleness_days"),
        "count": len(items),
        "fulltext_fetched": sum(1 for e in items if e.get("body_text")),
    }


if __name__ == "__main__":
    raise SystemExit(main())
