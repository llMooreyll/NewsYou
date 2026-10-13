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
        -> data/ai/digest_input.json           stage 2b input (always written)
        + data/ai/digest_decisions.json        optional, stage 2b
        -> data/processed/digest.json          the briefing panel

Nothing here calls a model. Every decision is checked before it is applied, and
anything invalid is reported and skipped rather than guessed at.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_model  # noqa: E402
import ai_stage  # noqa: E402
import config  # noqa: E402
from ai_stage import ORDER  # noqa: E402

SKIP_FILES = {"index.json", "telegram_duplicate_groups.json"}


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"  ! {path} is not valid JSON: {exc}", file=sys.stderr)
        return None


def load_collected(indir: Path) -> tuple[dict[str, list[dict]], dict]:
    results: dict[str, list[dict]] = {}
    statuses: dict[str, dict] = {}
    index = load_json(indir / "index.json") or {}
    for path in sorted(indir.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        results[path.stem] = payload.get("items") or []
        statuses[path.stem] = payload.get("status") or index.get("sources", {}).get(path.stem, {})
    return results, statuses


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

    results, statuses = load_collected(args.collected)
    if not results:
        print(f"no collected data in {args.collected}; run tools/collect.py first", file=sys.stderr)
        return 1

    day = (load_json(args.collected / "index.json") or {}).get("day") \
        or datetime.now().astimezone().date().isoformat()

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

    # ---------------- stage 2a: telegram ----------------
    # If a model backend is selected, ask it for decisions before reading them.
    # Existing decisions are reused unless --ai-force, so re-running the pipeline
    # does not spend another model call.
    if ai_backend != "none":
        report["ai_telegram"] = ai_model.generate(
            ai_backend, args.ai / "telegram_input.json",
            args.ai / "telegram_decisions.json", model=args.ai_model,
            timeout=args.ai_timeout, force=force_for(args.ai / "telegram_decisions.json"),
            dry_run=args.dry_run, extra_args=args.ai_extra_args)

    tg_decisions_path = args.ai / "telegram_decisions.json"
    tg_decisions = load_json(tg_decisions_path)
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
        print(f"telegram: {tg_report['removed_ads']} ads removed, "
              f"{tg_report['dropped']} dropped, "
              f"{tg_report['merged_groups']} merged groups "
              f"({tg_report['items_before']} -> {tg_report['items_after']} items), "
              f"{len(problems)} rejected", file=sys.stderr)
        for ad in tg_report.get("ad_details", [])[:10]:
            print(f"  - ad [{ad['category']}] {ad['id']}: {ad.get('reason') or ''}",
                  file=sys.stderr)
        for problem in problems[:10]:
            print(f"  ! {problem}", file=sys.stderr)
        statuses["telegram"] = ai_stage_status(processed["telegram"], statuses.get("telegram", {}))
    else:
        report["telegram"] = {"decisions_file": None, "applied": False,
                              "note": "no telegram_decisions.json; raw messages kept"}
        print("telegram: no decisions file, keeping raw messages", file=sys.stderr)

    # ---------------- write processed sources ----------------
    if not args.dry_run:
        for name in ORDER:
            if name not in processed:
                continue
            write_json(args.out / f"{name}.json", {
                "source": name,
                "source_name": ai_stage.SOURCE_LABELS.get(name, name),
                "day": day,
                "generated_at": report["generated_at"],
                "count": len(processed[name]),
                "status": statuses.get(name, {}),
                "items": processed[name],
            })

    # ---------------- stage 2b input ----------------
    digest_input = ai_stage.build_digest_input(processed, statuses, day)
    if not args.dry_run:
        write_json(args.ai / "digest_input.json", digest_input)
    report["digest_input"] = {"file": str(args.ai / "digest_input.json"),
                              "item_count": digest_input["item_count"]}
    print(f"digest input: {digest_input['item_count']} items across "
          f"{len(digest_input['sources'])} sources", file=sys.stderr)

    # ---------------- stage 2b: digest ----------------
    # The input now exists, so a selected backend can be asked for decisions.
    if ai_backend != "none":
        report["ai_digest"] = ai_model.generate(
            ai_backend, args.ai / "digest_input.json",
            args.ai / "digest_decisions.json", model=args.ai_model,
            timeout=args.ai_timeout, force=force_for(args.ai / "digest_decisions.json"),
            dry_run=args.dry_run, extra_args=args.ai_extra_args)

    digest_decisions_path = args.ai / "digest_decisions.json"
    digest_decisions = load_json(digest_decisions_path)
    index_by_id, aliases = ai_stage.build_item_index(processed)
    if digest_decisions:
        digest, problems = ai_stage.validate_digest_decisions(digest_decisions, set(index_by_id), aliases)
        panel = ai_stage.apply_digest_decisions(digest, index_by_id)
        panel["generated_at"] = report["generated_at"]
        panel["day"] = panel.get("day") or day
        if not args.dry_run:
            write_json(args.out / "digest.json", panel)
        report["digest"] = {
            "decisions_file": str(digest_decisions_path),
            "model": digest.get("model"),
            "highlights": len(panel["cards"]),
            "themes": len(panel["themes"]),
            "resolved_aliases": sum(
                1 for card in panel["cards"] for ref in card["refs"]
                if ref["id"] in aliases
            ),
            "rejected": len(problems),
            "problems": problems,
        }
        print(f"digest: {len(panel['cards'])} highlights, {len(panel['themes'])} themes, "
              f"{len(problems)} rejected", file=sys.stderr)
        for problem in problems[:10]:
            print(f"  ! {problem}", file=sys.stderr)
    else:
        report["digest"] = {"decisions_file": None, "applied": False,
                            "note": "no digest_decisions.json; no briefing panel"}
        print("digest: no decisions file, no briefing panel", file=sys.stderr)

    # ---------------- summary ----------------
    if not args.dry_run:
        write_json(args.out / "index.json", {
            "day": day,
            "generated_at": report["generated_at"],
            "sources": {
                name: {"count": len(items), "status": statuses.get(name, {})}
                for name, items in processed.items()
            },
            "stage2": {
                "ai_backend": ai_backend,
                "telegram": {k: v for k, v in report["telegram"].items() if k != "drop_details"},
                "digest": report.get("digest"),
            },
        })
        print(f"wrote {args.out}/", file=sys.stderr)
    else:
        print("dry run: nothing written", file=sys.stderr)

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
