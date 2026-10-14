#!/usr/bin/env python3
"""Archive the current run as a self-contained snapshot.

    python tools/archive.py                  # snapshot today's outputs
    python tools/archive.py --day 2026-10-13 # name it explicitly
    python tools/archive.py --force          # replace today's snapshot

This is a **copy**, not a re-run. It archives whatever the pipeline has already
produced in the canonical locations:

    data/collected/    raw items, exactly as fetched
    data/ai/           what was sent to the model and what it decided
    data/processed/    items after those decisions were applied
    site/digest.html   the rendered single-file digest

Nothing is moved or deleted. The root digest keeps existing after archiving, so
there is always a current report at a fixed path, and the next run is free to
overwrite it. `make daily` is collect + ai + render + this.

Layout:

    archive/
      index.json            manifest of every snapshot (day, counts, engine, backend)
      2026-10-13/
        meta.json           what produced this snapshot and how it went
        collected/  ai/  processed/
        digest.html         a copy of the rendered digest

Two properties this is built around:

- **Nothing is overwritten by accident.** Re-archiving the same day writes
  ``2026-10-13-v2`` instead of clobbering ``2026-10-13``; ``--force`` opts into
  replacing it. The snapshot is assembled in a staging directory and swapped in
  only once complete, so a failed run cannot destroy the snapshot it was
  replacing.
- **The snapshot is readable on its own.** ``digest.html`` inlines its images as
  base64, so it renders without the Telegram media pool. That pool is not copied
  in: it is already append-only, keyed by channel and message id.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CST = timezone(timedelta(hours=8))

sys.path.insert(0, str(TOOLS))
import config  # noqa: E402

def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

def final_name(root: Path, day: str, force: bool) -> Path:
    """Choose the snapshot name without touching the filesystem.

    Nothing is deleted here. A previous snapshot must survive until its
    replacement has been built, otherwise a failed run destroys the archive.
    """
    base = root / day
    if force or not base.exists():
        return base
    for version in range(2, 100):
        candidate = root / f"{day}-v{version}"
        if not candidate.exists():
            return candidate
    raise SystemExit(f"too many snapshots for {day}")

def install_snapshot(staging: Path, final: Path) -> None:
    """Move a finished staging directory into place without a window of loss.

    An existing snapshot is first renamed aside, never deleted up front. If
    installing the new one fails, the old one is renamed back; it is removed
    only after the new snapshot is in place.
    """
    backup = None
    if final.exists():
        backup = final.with_name(f".old-{final.name}-{os.getpid()}")
        final.rename(backup)
    try:
        staging.rename(final)
    except BaseException:
        if backup is not None and not final.exists():
            backup.rename(final)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)

def summarise(snapshot: Path) -> dict:
    """Collect the facts worth recording in the manifest."""
    index = load_json(snapshot / "collected/index.json") or {}
    processed = load_json(snapshot / "processed/index.json") or {}
    digest = load_json(snapshot / "processed/digest.json") or {}
    sources = {}
    for name, info in (index.get("sources") or {}).items():
        sources[name] = {
            "count": info.get("count"),
            "updated_today": info.get("updated_today"),
            "staleness_days": info.get("staleness_days"),
            "fulltext_fetched": info.get("fulltext_fetched"),
        }
    stage2 = processed.get("stage2") or {}
    digest_html = snapshot / "digest.html"
    return {
        "day": index.get("day"),
        "engine": index.get("engine"),
        "fulltext": index.get("fulltext"),
        "collected_at": index.get("collected_at"),
        "ai_backend": stage2.get("ai_backend", "none"),
        "item_count": sum(v["count"] or 0 for v in sources.values()),
        "sources": sources,
        "telegram": {
            "removed_ads": (stage2.get("telegram") or {}).get("removed_ads"),
            "merged_groups": (stage2.get("telegram") or {}).get("merged_groups"),
            "items_after": (stage2.get("telegram") or {}).get("items_after"),
        },
        "keywords": len(digest.get("keywords") or []),
        # From the stage 2 report in index.json, not from the processed files:
        # the categories live in processed/categories.json, and index.json is
        # the one file every snapshot is guaranteed to carry.
        "categories": sum((side or {}).get("categories") or 0
                          for side in (stage2.get("classify") or {}).values()),
        "digest_bytes": digest_html.stat().st_size if digest_html.exists() else None,
    }

def update_manifest(root: Path, entry: dict) -> None:
    """Keep archive/index.json as a newest-first list of snapshots."""
    path = root / "index.json"
    manifest = load_json(path) or {"schema": "newsletter.archive/1", "snapshots": []}
    snapshots = [s for s in manifest.get("snapshots", [])
                 if s.get("path") != entry["path"]]
    snapshots.append(entry)
    snapshots.sort(key=lambda s: (s.get("day") or "", s.get("path") or ""), reverse=True)
    manifest["snapshots"] = snapshots
    manifest["updated_at"] = datetime.now(CST).isoformat()
    manifest["count"] = len(snapshots)
    write_json(path, manifest)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, default=None)
    parser.add_argument("--day", default=None,
                        help="snapshot name (default: the collected day)")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing snapshot for the same day")
    config.add_arguments(parser)
    args = parser.parse_args()

    cfg = config.resolve_or_exit(args, cli={"archive.root": args.archive})
    if config.emit_show_config(cfg, args):
        return 0

    root = Path(cfg.get("archive.root"))
    collected = Path(cfg.get("paths.collected"))
    aidir = Path(cfg.get("paths.ai"))
    processed = Path(cfg.get("paths.processed"))
    digest_html = Path(cfg.get("output.digest"))

    # Refuse to archive a run that is not there, rather than writing an empty
    # snapshot that looks successful.
    missing = [str(p) for p in (collected, processed, digest_html) if not p.exists()]
    if missing:
        print("nothing to archive; missing:\n  " + "\n  ".join(missing)
              + "\nrun `make all` first (or use `make daily`)", file=sys.stderr)
        return 1

    index = load_json(collected / "index.json") or {}
    stamp = datetime.now(CST)
    if args.day:
        day = args.day
    else:
        day = index.get("day") or stamp.date().isoformat()

    root.mkdir(parents=True, exist_ok=True)

    # Assemble in a staging directory, then swap in, so a failure part-way
    # through leaves the existing snapshot intact.
    staging = root / f".tmp-{day}-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        for name, source in (("collected", collected), ("ai", aidir),
                             ("processed", processed)):
            if source.exists():
                shutil.copytree(source, staging / name)
            else:
                (staging / name).mkdir()
        # A copy, not a move: the root digest stays put as the current report.
        shutil.copy2(digest_html, staging / "digest.html")

        final = final_name(root, day, args.force)
        if final.name != day:
            print(f"note: {root / day} exists; writing {final} instead "
                  f"(use --force to replace)", file=sys.stderr)
        install_snapshot(staging, final)
        snapshot = final
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    summary = summarise(snapshot)
    meta = {
        "schema": "newsletter.archive-snapshot/1",
        "archived_at": stamp.isoformat(),
        "day": summary["day"] or day,
        "snapshot": str(snapshot),
        "source": {
            "collected": str(collected),
            "ai": str(aidir),
            "processed": str(processed),
            "digest": str(digest_html),
        },
        "summary": summary,
    }
    write_json(snapshot / "meta.json", meta)
    update_manifest(root, {
        "path": snapshot.name,
        "day": meta["day"],
        "archived_at": meta["archived_at"],
        **summary,
    })

    size = sum(f.stat().st_size for f in snapshot.rglob("*") if f.is_file())
    print(f"\narchived {snapshot}  ({size / 1024 / 1024:.1f} MiB)", file=sys.stderr)
    print(f"  day {meta['day']} · engine {summary.get('engine')} · "
          f"ai {summary.get('ai_backend')} · {summary['item_count']} items · "
          f"{summary['keywords']} keywords", file=sys.stderr)
    print(f"  root digest kept at {digest_html}", file=sys.stderr)
    print(snapshot / "digest.html")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
