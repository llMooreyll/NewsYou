#!/usr/bin/env python3
"""Archive one day's run as a self-contained snapshot.

    python tools/archive.py                       # today, full pipeline
    python tools/archive.py --day 2026-10-13      # a specific day
    python tools/archive.py --ai-backend pi       # with the AI layer
    python tools/archive.py --force               # overwrite today's snapshot
    python tools/archive.py --skip-collect        # re-render an existing snapshot

Layout:

    archive/
      index.json            manifest of every snapshot (day, counts, engine, backend)
      2026-10-13/
        meta.json           what produced this snapshot and how it went
        collected/          raw items, never rewritten after collection
        ai/                 the inputs sent to a model and the decisions it returned
        processed/          items after the AI decisions were applied
        digest.html         the rendered single-file digest

Two properties this is built around:

- **Nothing is overwritten by accident.** Re-running the same day writes
  ``2026-10-13-v2`` instead of clobbering ``2026-10-13``; ``--force`` opts into
  replacing it.
- **The snapshot is readable on its own.** ``digest.html`` inlines its images as
  base64, so it renders without the Telegram media pool. That pool is not copied
  into the archive: it is already append-only, keyed by channel and message id.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
CST = timezone(timedelta(hours=8))

sys.path.insert(0, str(TOOLS))
import ai_model  # noqa: E402

ARCHIVE_DEFAULT = Path("archive")

def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

def interpreter_for(engine: str) -> str:
    """modern needs the project venv; legacy runs on any interpreter."""
    if engine == "modern":
        for candidate in (ROOT / ".venv/bin/python", ROOT / ".venv/Scripts/python.exe"):
            if candidate.exists():
                return str(candidate)
    return sys.executable

def run(cmd: list[str]) -> int:
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, cwd=ROOT).returncode

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
        "highlights": len(digest.get("cards") or []),
        "themes": len(digest.get("themes") or []),
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
    parser.add_argument("--archive", type=Path, default=ARCHIVE_DEFAULT)
    parser.add_argument("--day", default="auto",
                        help="telegram day: auto | today | yesterday | YYYY-MM-DD")
    parser.add_argument("--fulltext", default="today")
    parser.add_argument("--engine", default=None, choices=["legacy", "modern"],
                        help="default: the collector's own default (modern)")
    parser.add_argument("--ai-backend", default=ai_model.DEFAULT_BACKEND,
                        choices=ai_model.BACKENDS)
    parser.add_argument("--ai-model", default=ai_model.DEFAULT_MODEL)
    parser.add_argument("--ai-timeout", type=int, default=900)
    parser.add_argument("--force", action="store_true",
                        help="replace today's snapshot instead of versioning it")
    parser.add_argument("--skip-collect", action="store_true",
                        help="reuse an existing snapshot's collected/ data")
    args = parser.parse_args()

    root = args.archive
    stamp = datetime.now(CST)
    day = stamp.date().isoformat() if args.day in ("auto", "today") else args.day
    if args.day == "yesterday":
        day = (stamp.date() - timedelta(days=1)).isoformat()

    root.mkdir(parents=True, exist_ok=True)

    # Build into a staging directory and only swap it in once every step has
    # succeeded, so a failed run can never leave a half-written snapshot or
    # destroy the one it was meant to replace.
    staging: Path | None = None
    if args.skip_collect:
        snapshot = root / day
        if not (snapshot / "collected").exists():
            print(f"error: {snapshot / 'collected'} does not exist, cannot skip collection",
                  file=sys.stderr)
            return 1
        workdir = snapshot
    else:
        staging = root / f".tmp-{day}-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        workdir = staging

    collected = workdir / "collected"
    aidir = workdir / "ai"
    processed = workdir / "processed"

    engine = args.engine or "modern"
    python = interpreter_for(engine)

    try:
        if not args.skip_collect:
            cmd = [python, str(TOOLS / "collect.py"), "--all",
                   "--outdir", str(collected), "--aidir", str(aidir),
                   "--fulltext", args.fulltext, "--day", args.day]
            if args.engine:
                cmd += ["--engine", args.engine]
            if run(cmd) != 0:
                return 1

        cmd = [python, str(TOOLS / "ai_apply.py"),
               "--collected", str(collected), "--ai", str(aidir),
               "--out", str(processed),
               "--ai-backend", args.ai_backend, "--ai-timeout", str(args.ai_timeout)]
        if args.ai_model:
            cmd += ["--ai-model", args.ai_model]
        if run(cmd) != 0:
            return 1

        if run([python, str(TOOLS / "render_digest.py"),
                "--indir", str(processed), "--fallback", str(collected),
                "--output", str(workdir / "digest.html")]) != 0:
            return 1

        if staging is not None:
            final = final_name(root, day, args.force)
            if final.name != day:
                print(f"note: {root / day} exists; writing {final} instead "
                      f"(use --force to replace)", file=sys.stderr)
            install_snapshot(staging, final)
            snapshot = final
    finally:
        # On failure the staging directory is discarded, leaving any existing
        # snapshot untouched.
        if staging is not None and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    summary = summarise(snapshot)
    meta = {
        "schema": "newsletter.archive-snapshot/1",
        "archived_at": stamp.isoformat(),
        "day": summary["day"] or day,
        "snapshot": str(snapshot),
        "requested": {
            "engine": args.engine or "(collector default)",
            "engine_used": summary.get("engine"),
            "fulltext": args.fulltext,
            "ai_backend": args.ai_backend,
            "ai_model": args.ai_model,
            "telegram_day": args.day,
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
          f"{summary['highlights']} highlights", file=sys.stderr)
    print(snapshot / "digest.html")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
