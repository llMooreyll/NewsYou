#!/usr/bin/env python3
"""Stage 2: the AI layer.

Design rule: **the model returns decisions, this module applies them.**

Why not let the model rewrite the data directly:

- auditable   — every action is a separate, inspectable record
- reversible  — raw items in data/collected/ are never touched
- idempotent  — applying the same decisions twice gives the same result
- safe       — the model cannot corrupt fields it was not asked to change
- testable   — apply/validate run without any model, so the pipeline works today

Two sub-stages:

  2a  telegram_input.json  ->  telegram_decisions.json   (drop / merge)
  2b  digest_input.json    ->  digest_decisions.json     (rank / brief, no delete)

Pure functions only. File IO lives in tools/ai_apply.py and tools/collect.py.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Source order and labels have one source of truth: tools/collectors/registry.py.
# Re-exported here so the AI layer and the collector can never drift apart.
from collectors.registry import ORDER, SOURCE_LABELS  # noqa: E402,F401

TOPICS = ["technology", "ai", "business", "policy", "society", "security",
          "internet", "other"]

# Commercial categories a message can be removed under (op "spam").
SPAM_CATEGORIES = ("ad", "promo", "giveaway", "crosspromo", "affiliate",
                   "crypto", "other")

TELEGRAM_SCHEMA = "newsletter.telegram-decisions/1"
DIGEST_SCHEMA = "newsletter.digest-decisions/1"
TELEGRAM_INPUT_SCHEMA = "newsletter.telegram-input/1"
DIGEST_INPUT_SCHEMA = "newsletter.digest-input/1"


def load_prompt(name: str) -> str:
    """Read a prompt from prompts/<name>.md. Missing file is not fatal."""
    path = PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def _url_key(url: str) -> str:
    """canonical_key without importing the collectors (keeps this module pure)."""
    from urllib.parse import urlparse, parse_qsl, urlencode
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    drop = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "spm", "from", "share_token", "ref"}
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in drop]
    return f"{host}{(parsed.path or '').rstrip('/')}" + (("?" + urlencode(pairs)) if pairs else "")


def item_id(entry: dict) -> str:
    """Stable reference used by the model and by the applier.

    Telegram keeps its channel/message identity so a human can find the post.
    Everything else is keyed by its canonical URL: a title hash collided when
    two items shared a title and changed whenever a title was edited.
    """
    extra = entry.get("extra") or {}
    if entry.get("source") == "telegram" and extra.get("message_id") is not None:
        return f"telegram:{extra.get('channel')}:{extra.get('message_id')}"
    key = _url_key(entry.get("url") or "")
    if not key:
        return f"{entry.get('source')}:{entry.get('content_hash')}"
    return f"{entry.get('source')}:{hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]}"


def legacy_item_id(entry: dict) -> str:
    """The pre-URL id (source:content_hash), accepted as an alias so decision
    files written before the change still resolve."""
    return f"{entry.get('source')}:{entry.get('content_hash')}"


def slim_item(entry: dict) -> dict:
    """The projection sent to the model: enough to judge, nothing more."""
    extra = entry.get("extra") or {}
    return {
        "id": item_id(entry),
        "source": entry.get("source"),
        "source_name": entry.get("source_name"),
        "title": entry.get("title"),
        "summary": (entry.get("summary") or "")[:4000],
        "body_text": entry.get("body_text"),
        "published_at": entry.get("published_at"),
        "url": entry.get("url"),
        "content_hash": entry.get("content_hash"),
        "is_today": bool(entry.get("is_today")),
        "channel": extra.get("channel"),
        "section_name": extra.get("section_name"),
    }


# --------------------------------------------------------------------------
# Stage 2a input
# --------------------------------------------------------------------------

def build_telegram_input(items: list[dict], day: str) -> dict:
    return {
        "schema": TELEGRAM_INPUT_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(),
        "day": day,
        "item_count": len(items),
        "instructions": load_prompt("telegram_merge"),
        "response_schema": {
            "schema": TELEGRAM_SCHEMA,
            "day": day,
            "model": "string",
            "actions": [
                {"op": "spam", "id": "string", "category": "ad|promo|giveaway|crosspromo|affiliate|crypto|other",
                 "reason": "string"},
                {"op": "drop", "id": "string", "canonical_id": "string", "reason": "string"},
                {"op": "merge", "primary_id": "string", "merge_ids": ["string"],
                 "title": "string", "summary": "string", "topics": ["string"],
                 "confidence": "high|medium|low"},
            ],
        },
        "items": [slim_item(e) for e in items],
    }


# --------------------------------------------------------------------------
# Stage 2b input
# --------------------------------------------------------------------------

def build_digest_input(results: dict[str, list[dict]], statuses: dict[str, dict], day: str) -> dict:
    sources: dict[str, dict] = {}
    total = 0
    for name in ORDER:
        if name not in results:
            continue
        items = results[name]
        status = statuses.get(name) or {}
        payload = [slim_item(e) for e in items]
        total += len(payload)
        sources[name] = {
            "source_name": SOURCE_LABELS.get(name, name),
            "updated_today": status.get("updated_today"),
            "staleness_days": status.get("staleness_days"),
            "latest_published_at": status.get("latest_published_at"),
            "count": len(payload),
            "items": payload,
        }
    return {
        "schema": DIGEST_INPUT_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(),
        "day": day,
        "item_count": total,
        "instructions": load_prompt("digest_highlights"),
        "response_schema": {
            "schema": DIGEST_SCHEMA,
            "day": day,
            "model": "string",
            "headline": "string",
            "highlights": [{
                "item_ids": ["string"], "rank": "number", "title": "string",
                "summary": "string", "topics": ["string"], "importance": "number 0-100",
                "why": "string",
            }],
            "themes": [{"name": "string", "summary": "string", "item_ids": ["string"]}],
            "stats_note": "string",
        },
        "sources": sources,
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

class DecisionError(ValueError):
    pass


def _clean_text(value, limit: int = 2000, *, keep_paragraphs: bool = False) -> str:
    """Collapse whitespace. With keep_paragraphs, paragraph breaks (blank
    lines) survive, so multi-paragraph summaries do not render as one block."""
    raw = str(value or "")
    if not keep_paragraphs:
        return re.sub(r"\s+", " ", raw).strip()[:limit]
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", raw.replace("\r", ""))]
    return "\n\n".join(p for p in paras if p)[:limit]


def _is_known(ref, known_ids: set[str]) -> bool:
    """Membership that survives a reference of the wrong type.

    A model sometimes returns a list where one id belongs. A list is unhashable,
    so a bare `in` against the id set raises instead of rejecting the action.
    """
    return isinstance(ref, str) and ref in known_ids


def validate_telegram_decisions(decisions: dict, known_ids: set[str]) -> tuple[list[dict], list[str]]:
    """Return (accepted actions, problems). Never raises on bad input."""
    problems: list[str] = []
    accepted: list[dict] = []

    if not isinstance(decisions, dict):
        return [], ["decisions file is not a JSON object"]
    if decisions.get("schema") not in (None, TELEGRAM_SCHEMA):
        problems.append(f"unexpected schema {decisions.get('schema')!r}")

    seen_primary: set[str] = set()
    seen_member: set[str] = set()
    dropped: set[str] = set()
    removed: set[str] = set()

    for index, action in enumerate(decisions.get("actions") or []):
        if not isinstance(action, dict):
            problems.append(f"action {index}: not an object")
            continue
        op = str(action.get("op") or "").lower()

        if op == "spam":
            target = action.get("id")
            if not _is_known(target, known_ids):
                problems.append(f"action {index}: unknown id {target!r}")
                continue
            if target in seen_member:
                problems.append(f"action {index}: {target} already merged elsewhere")
                continue
            if target in seen_primary:
                problems.append(f"action {index}: cannot remove a merge primary {target}")
                continue
            category = str(action.get("category") or "other").lower()
            if category not in SPAM_CATEGORIES:
                problems.append(f"action {index}: unknown spam category {category!r}")
                category = "other"
            removed.add(target)
            accepted.append({
                "op": "spam", "id": target, "category": category,
                "reason": _clean_text(action.get("reason"), 200),
            })

        elif op == "drop":
            target = action.get("id")
            if not _is_known(target, known_ids):
                problems.append(f"action {index}: unknown id {target!r}")
                continue
            if target in seen_member:
                problems.append(f"action {index}: {target} already merged elsewhere")
                continue
            if target in seen_primary:
                problems.append(f"action {index}: cannot drop a merge primary {target}")
                continue
            canonical = action.get("canonical_id")
            if canonical is not None and not _is_known(canonical, known_ids):
                problems.append(f"action {index}: unknown canonical_id {canonical!r}")
                canonical = None
            dropped.add(target)
            accepted.append({
                "op": "drop", "id": target, "canonical_id": canonical,
                "reason": _clean_text(action.get("reason"), 200),
            })

        elif op == "merge":
            primary = action.get("primary_id")
            members = [m for m in (action.get("merge_ids") or []) if isinstance(m, str)]
            if not _is_known(primary, known_ids):
                problems.append(f"action {index}: unknown primary_id {primary!r}")
                continue
            unknown = [m for m in members if m not in known_ids]
            if unknown:
                problems.append(f"action {index}: unknown merge_ids {unknown}")
                members = [m for m in members if m in known_ids]
            blocked = [m for m in members if m in removed or m in dropped]
            if blocked:
                problems.append(f"action {index}: merge_ids already removed or dropped {blocked}")
                members = [m for m in members if m not in removed and m not in dropped]
            if not members:
                problems.append(f"action {index}: merge has no valid merge_ids")
                continue
            clash = [m for m in members if m in seen_member] + \
                    ([primary] if primary in seen_member else [])
            if clash:
                problems.append(f"action {index}: {clash} already used in another merge")
                continue
            if primary in dropped or primary in removed:
                problems.append(f"action {index}: primary {primary} was already removed")
                continue
            members = [m for m in members if m != primary]
            if not members:
                problems.append(f"action {index}: merge_ids equal primary only")
                continue
            title = _clean_text(action.get("title"), 300)
            summary = _clean_text(action.get("summary"), 3000, keep_paragraphs=True)
            if not title or not summary:
                problems.append(f"action {index}: merge needs both title and summary")
                continue
            topics = [t for t in (action.get("topics") or []) if t in TOPICS]
            seen_primary.add(primary)
            seen_member.update(members)
            accepted.append({
                "op": "merge", "primary_id": primary, "merge_ids": members,
                "title": title, "summary": summary, "topics": topics,
                "confidence": str(action.get("confidence") or "medium").lower(),
            })

        elif op in ("keep", "noop"):
            continue
        else:
            problems.append(f"action {index}: unknown op {op!r}")

    return accepted, problems


def validate_digest_decisions(decisions: dict, known_ids: set[str],
                              aliases: dict[str, str] | None = None) -> tuple[dict, list[str]]:
    """Return (accepted digest, problems).

    `aliases` maps ids that were absorbed by a merge onto their merge primary,
    so a reference to a merged-away message resolves instead of being dropped.
    """
    aliases = aliases or {}
    problems: list[str] = []
    if not isinstance(decisions, dict):
        return {}, ["decisions file is not a JSON object"]
    if decisions.get("schema") not in (None, DIGEST_SCHEMA):
        problems.append(f"unexpected schema {decisions.get('schema')!r}")

    def resolve(ref: str) -> str | None:
        if ref in known_ids:
            return ref
        if ref in aliases:
            return aliases[ref]
        return None

    highlights: list[dict] = []
    for index, item in enumerate(decisions.get("highlights") or []):
        if not isinstance(item, dict):
            problems.append(f"highlight {index}: not an object")
            continue
        raw_ids = [i for i in (item.get("item_ids") or []) if isinstance(i, str)]
        valid: list[str] = []
        for ref in raw_ids:
            target = resolve(ref)
            if target is None:
                problems.append(f"highlight {index}: unknown id {ref!r}")
                continue
            if target not in valid:
                valid.append(target)
        if not valid:
            problems.append(f"highlight {index}: no valid item_ids, skipped")
            continue
        title = _clean_text(item.get("title"), 300)
        summary = _clean_text(item.get("summary"), 2000, keep_paragraphs=True)
        if not title:
            problems.append(f"highlight {index}: missing title, skipped")
            continue
        try:
            importance = int(item.get("importance") or 0)
        except (TypeError, ValueError):
            importance = 0
        highlights.append({
            "item_ids": valid,
            "rank": index + 1,
            "title": title,
            "summary": summary,
            "topics": [t for t in (item.get("topics") or []) if t in TOPICS],
            "importance": max(0, min(100, importance)),
            "why": _clean_text(item.get("why"), 200),
        })

    highlights.sort(key=lambda h: h["importance"], reverse=True)
    for position, item in enumerate(highlights, 1):
        item["rank"] = position
    if len(highlights) > 10:
        problems.append(f"highlights trimmed from {len(highlights)} to 10")
        highlights = highlights[:10]

    themes: list[dict] = []
    for index, theme in enumerate(decisions.get("themes") or []):
        if not isinstance(theme, dict):
            continue
        name = _clean_text(theme.get("name"), 120)
        if not name:
            continue
        ids: list[str] = []
        for ref in (theme.get("item_ids") or []):
            if not isinstance(ref, str):
                continue
            target = resolve(ref)
            if target and target not in ids:
                ids.append(target)
        themes.append({
            "name": name,
            "summary": _clean_text(theme.get("summary"), 600),
            "item_ids": ids,
        })
    if len(themes) > 5:
        themes = themes[:5]

    return {
        "schema": DIGEST_SCHEMA,
        "day": decisions.get("day"),
        "model": _clean_text(decisions.get("model"), 120),
        "headline": _clean_text(decisions.get("headline"), 400),
        "highlights": highlights,
        "themes": themes,
        "stats_note": _clean_text(decisions.get("stats_note"), 300),
    }, problems


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def _union(lists) -> list:
    out: list = []
    for values in lists:
        for value in values or []:
            if value and value not in out:
                out.append(value)
    return out


def apply_telegram_decisions(items: list[dict], actions: list[dict]) -> tuple[list[dict], dict]:
    """Apply drop/merge. Raw items are never mutated.

    A merged item keeps the primary's id (so later references stay stable) and
    gains `ai_merged`, `merged_from`, and `originals` — the untouched source
    messages, kept so the reader can verify the merge.
    """
    by_id = {item_id(e): e for e in items}
    dropped: dict[str, dict] = {}
    removed: dict[str, dict] = {}
    merges: dict[str, dict] = {}
    consumed: set[str] = set()

    for action in actions:
        if action["op"] == "spam":
            removed[action["id"]] = action
            consumed.add(action["id"])
        elif action["op"] == "drop":
            dropped[action["id"]] = action
            consumed.add(action["id"])
        elif action["op"] == "merge":
            merges[action["primary_id"]] = action
            consumed.update(action["merge_ids"])

    output: list[dict] = []
    for entry in items:
        key = item_id(entry)

        if key in removed or key in dropped:
            continue

        if key in merges:
            action = merges[key]
            members = [key] + [m for m in action["merge_ids"] if m in by_id]
            originals = []
            for member_id in members:
                source = by_id.get(member_id)
                if not source:
                    continue
                extra = source.get("extra") or {}
                originals.append({
                    "id": member_id,
                    "channel": extra.get("channel"),
                    "source_name": source.get("source_name"),
                    "title": source.get("title"),
                    "text": extra.get("text") or source.get("summary") or "",
                    "text_html": extra.get("text_html") or "",
                    "published_at": source.get("published_at"),
                    "url": source.get("url"),
                    "views": extra.get("views"),
                    "media": extra.get("media") or [],
                    "photos": extra.get("photos") or [],
                    "local_images": extra.get("local_images") or [],
                    "reactions": extra.get("reactions") or [],
                })
            merged = dict(entry)
            merged["title"] = action["title"]
            merged["summary"] = action["summary"]
            merged["ai_merged"] = True
            merged["merged_from"] = members
            merged["originals"] = originals
            extra = dict(entry.get("extra") or {})
            extra["ai_merged"] = True
            extra["merged_from"] = members
            extra["merge_confidence"] = action.get("confidence")
            extra["ai_topics"] = action.get("topics") or []
            # every member's media, primary first: a merge must not lose the
            # images that only a secondary message carried
            extra["local_images"] = _union(o["local_images"] for o in originals)
            extra["photos"] = _union(o["photos"] for o in originals)
            extra["media"] = _union(o["media"] for o in originals)
            extra["merged_sources"] = [{"channel": o["channel"], "url": o["url"]} for o in originals]
            merged["extra"] = extra
            if action.get("topics"):
                merged["tags"] = action["topics"]
            # keep the earliest timestamp so the merged item sorts by its first report
            stamps = [by_id[m].get("published_at") for m in members if by_id.get(m)]
            stamps = [s for s in stamps if s]
            if stamps:
                merged["published_at"] = min(stamps)
            output.append(merged)
            continue

        if key in consumed:
            # merged away as a secondary: dropped from the main list, preserved
            # inside the primary's `originals`
            continue

        output.append(entry)

    report = {
        "dropped": len(dropped),
        "removed_ads": len(removed),
        "removed_ad_ids": list(removed),
        "merged_groups": len(merges),
        "merged_members": sum(len(m["merge_ids"]) for m in merges.values()),
        "items_before": len(items),
        "items_after": len(output),
        "drop_details": list(dropped.values()),
        "ad_details": list(removed.values()),
    }
    return output, report


def apply_digest_decisions(digest: dict, item_index: dict[str, dict]) -> dict:
    """Resolve every referenced id into a render-ready card. No deletion."""
    cards = []
    for highlight in digest.get("highlights") or []:
        refs = []
        for ref in highlight["item_ids"]:
            entry = item_index.get(ref)
            if not entry:
                continue
            extra = entry.get("extra") or {}
            refs.append({
                "id": ref,
                "source": entry.get("source"),
                "source_name": entry.get("source_name"),
                "channel": extra.get("channel"),
                "title": entry.get("title"),
                "url": entry.get("url"),
                "published_at": entry.get("published_at"),
                "ai_merged": bool(entry.get("ai_merged")),
            })
        cards.append({**highlight, "refs": refs})

    themes = []
    for theme in digest.get("themes") or []:
        themes.append({
            "name": theme["name"],
            "summary": theme["summary"],
            "refs": [
                {
                    "id": ref,
                    "source": (item_index.get(ref) or {}).get("source"),
                    "title": (item_index.get(ref) or {}).get("title"),
                    "url": (item_index.get(ref) or {}).get("url"),
                }
                for ref in theme["item_ids"] if ref in item_index
            ],
        })

    return {
        "schema": digest.get("schema"),
        "day": digest.get("day"),
        "model": digest.get("model"),
        "headline": digest.get("headline"),
        "stats_note": digest.get("stats_note"),
        "cards": cards,
        "themes": themes,
    }


def build_item_index(results: dict[str, list[dict]]) -> tuple[dict[str, dict], dict[str, str]]:
    """Return (id -> item, absorbed id -> merge primary id).

    Items absorbed by a merge are no longer top-level, but stage 2b may still
    reference them. The alias map lets those references resolve to the primary
    instead of being silently dropped.
    """
    index: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    for items in results.values():
        for entry in items:
            key = item_id(entry)
            index[key] = entry
            old = legacy_item_id(entry)
            if old != key:
                aliases.setdefault(old, key)
            for absorbed in entry.get("merged_from") or []:
                if absorbed != key:
                    aliases[absorbed] = key
    return index, aliases
