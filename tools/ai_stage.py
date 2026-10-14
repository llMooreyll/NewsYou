#!/usr/bin/env python3
"""Stage 2: the AI layer.

Design rule: **the model returns decisions, this module applies them.**

Why not let the model rewrite the data directly:

- auditable   — every action is a separate, inspectable record
- reversible  — raw items in data/collected/ are never touched
- idempotent  — applying the same decisions twice gives the same result
- safe       — the model cannot corrupt fields it was not asked to change
- testable   — apply/validate run without any model, so the pipeline works today

Four sub-stages:

  2a  telegram_input.json  ->  telegram_decisions.json       (drop / merge)
  2c  classify_input.json  ->  classify_decisions.json       (news categories)
  2d  classify_forum_input.json -> classify_forum_decisions.json (discussion)
  2b  digest_input.json    ->  digest_decisions.json         (rank / brief, no delete)

2c and 2d are the same call on two slices of the data, split by each item's
`nature`; `longform` items take part in none of them. 2b runs last because its
input carries the `category` the classification produced.

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
CLASSIFY_SCHEMA = "newsletter.classify-decisions/1"
CLASSIFY_INPUT_SCHEMA = "newsletter.classify-input/1"
CATEGORIES_SCHEMA = "newsletter.categories/1"

# Stages 2c (news) and 2d (discussion) are the same call on a different slice
# of the data: same prompt, same validator, same applier, told apart only by the
# `nature` they are handed. That is why this is a table and not two code paths.
# The value is the file-name stem, so the input and the decisions for a side are
# `<stem>_input.json` and `<stem>_decisions.json`.
CLASSIFY_SIDES = (("news", "classify"), ("discussion", "classify_forum"))

# Shape rules for one side's categories. The model names them freely, so these
# are what keep a day's page readable when the model returns forty buckets.
CATEGORY_LIMIT = 12          # named categories per side; the fallback is extra
CATEGORY_MIN_ITEMS = 2       # a smaller category folds into the fallback
CATEGORY_FALLBACK = "其他"
CATEGORY_NAME_LIMIT = 40
CATEGORY_SUMMARY_LIMIT = 60
CLASSIFY_SUMMARY_LIMIT = 400


def load_prompt(name: str) -> str:
    """Read a prompt from prompts/<name>.md. Missing file is not fatal."""
    path = PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def source_order(names) -> list[str]:
    """Built-in sources in panel order, then every other one as given.

    ORDER names the ten built-in sources only. A `[[feeds]]` entry in
    config.toml is a source it has never heard of, so walking ORDER alone
    dropped configured feeds at this stage: collected, then never written to
    data/processed/ and never shown.
    """
    names = list(names)
    return [n for n in ORDER if n in names] + [n for n in names if n not in ORDER]


def source_label(name: str, items: list[dict]) -> str:
    """Display name: the built-in label, else the name the collector stamped on
    the items (a configured feed's `name`), else the id itself."""
    if name in SOURCE_LABELS:
        return SOURCE_LABELS[name]
    return next((e["source_name"] for e in items if e.get("source_name")), name)


def expected_sources(cfg) -> list[str]:
    """Every source this run was supposed to collect, in panel order.

    Mirrors how collect.py builds its target list, so a source that never
    arrived can be told apart from one that was switched off: the first is worth
    reporting to the reader, the second is not. `[[feeds]]` entries are added by
    hand because configure_feeds() registers them during collection, and stage 2
    never runs it.
    """
    from collectors import registry

    names = list(cfg.get("sources.order") or [])
    for name in registry.SOURCES:
        if name not in names:
            names.append(name)
    for feed in cfg.get("feeds") or []:
        if feed.get("id") and feed["id"] not in names:
            names.append(feed["id"])
    enabled = cfg.get("sources.enabled") or []
    if enabled:
        wanted = set(enabled)
        names = [name for name in names if name in wanted]
    return names


def configured_label(name: str, cfg=None) -> str:
    """Display name for a source with no items to read it from.

    `source_label` falls back to the name a collector stamped on its items,
    which a source that produced nothing has none of. Configuration is the
    remaining authority.
    """
    if cfg is not None:
        label = (cfg.get("sources.labels") or {}).get(name)
        if label:
            return label
    return SOURCE_LABELS.get(name, name)


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

    Stage 1 stamps the result as `id` on every collected item. Preferring that
    value keeps one derivation in the pipeline: without it this function and
    the stamp could drift and every citation would point at nothing.
    """
    if entry.get("id"):
        return str(entry["id"])
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
        "nature": entry.get("nature"),
        "category": entry.get("category"),
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
    for name in source_order(results):
        items = results[name]
        status = statuses.get(name) or {}
        payload = [slim_item(e) for e in items]
        total += len(payload)
        sources[name] = {
            "source_name": source_label(name, items),
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
            "stats_note": "string",
        },
        "sources": sources,
    }

# --------------------------------------------------------------------------
# Stage 2c / 2d input
# --------------------------------------------------------------------------

def classify_groups(results: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Split the items by nature, in panel order.

    `longform` is deliberately not a group: those sources are irregularly
    updated long reads, so they keep their own panel and get no category. An
    item with no stamped nature counts as news — which is also what a data set
    collected before the field existed looks like.
    """
    groups: dict[str, list[dict]] = {side: [] for side, _ in CLASSIFY_SIDES}
    for name in source_order(results):
        for entry in results[name]:
            nature = entry.get("nature") or "news"
            if nature in groups:
                groups[nature].append(entry)
    return groups

def slim_classify_item(entry: dict) -> dict:
    """The projection for stage 2c/2d.

    No `body_text`: classification reads the headline and the excerpt, and
    leaving the article out is what makes this call roughly ten times cheaper
    than stage 2b.
    """
    extra = entry.get("extra") or {}
    return {
        "id": item_id(entry),
        "nature": entry.get("nature") or "news",
        "source": entry.get("source"),
        "source_name": entry.get("source_name"),
        "title": entry.get("title"),
        "summary": (entry.get("summary") or "")[:CLASSIFY_SUMMARY_LIMIT],
        "url": entry.get("url"),
        "channel": extra.get("channel"),
        "section_name": extra.get("section_name"),
    }

def build_classify_input(items: list[dict], day: str, nature: str) -> dict:
    return {
        "schema": CLASSIFY_INPUT_SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(),
        "day": day,
        "nature": nature,
        "item_count": len(items),
        "instructions": load_prompt("classify"),
        "response_schema": {
            "schema": CLASSIFY_SCHEMA,
            "day": day,
            "model": "string",
            "nature": nature,
            "categories": [{
                "name": "string, 中文, at most 6 characters",
                "summary": "string, one clause, at most 60 characters",
                "item_ids": ["string"],
            }],
        },
        "items": [slim_classify_item(e) for e in items],
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

    return {
        "schema": DIGEST_SCHEMA,
        "day": decisions.get("day"),
        "model": _clean_text(decisions.get("model"), 120),
        "headline": _clean_text(decisions.get("headline"), 400),
        "highlights": highlights,
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

    return {
        "schema": digest.get("schema"),
        "day": digest.get("day"),
        "model": digest.get("model"),
        "headline": digest.get("headline"),
        "stats_note": digest.get("stats_note"),
        "cards": cards,
    }

# --------------------------------------------------------------------------
# Stage 2c / 2d validation and apply
# --------------------------------------------------------------------------

def validate_classify_decisions(decisions: dict, nature: str,
                                item_index: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Return (accepted categories, problems). Never raises on bad input.

    The model proposes groupings; this decides which are usable. Two rules
    carry most of the weight. A reference has to resolve, and it has to belong
    to the side being classified: a news id inside the discussion file would
    otherwise file a news item under a community heading. And an item belongs
    to exactly one category, so the first claim wins and later ones are
    rejected instead of appearing twice under two names.
    """
    problems: list[str] = []
    if not isinstance(decisions, dict):
        return [], ["decisions file is not a JSON object"]
    if decisions.get("schema") not in (None, CLASSIFY_SCHEMA):
        problems.append(f"unexpected schema {decisions.get('schema')!r}")

    claimed: set[str] = set()
    by_name: dict[str, dict] = {}
    for index, category in enumerate(decisions.get("categories") or []):
        if not isinstance(category, dict):
            problems.append(f"category {index}: not an object")
            continue
        name = _clean_text(category.get("name"), CATEGORY_NAME_LIMIT)
        if not name:
            problems.append(f"category {index}: missing name, skipped")
            continue
        ids: list[str] = []
        for ref in category.get("item_ids") or []:
            if not isinstance(ref, str):
                problems.append(f"category {index} ({name}): id is not a string")
                continue
            entry = item_index.get(ref)
            if entry is None:
                problems.append(f"category {index} ({name}): unknown id {ref!r}")
                continue
            entry_nature = entry.get("nature") or "news"
            if entry_nature != nature:
                problems.append(f"category {index} ({name}): {ref} is "
                                f"{entry_nature}, not {nature}")
                continue
            if ref in claimed:
                problems.append(f"category {index} ({name}): {ref} already categorised")
                continue
            claimed.add(ref)
            ids.append(ref)
        if not ids:
            problems.append(f"category {index} ({name}): no usable item_ids, skipped")
            continue
        if name in by_name:
            # One label, one block: two identically titled sections would look
            # like a bug in the page rather than a mistake in the answer.
            by_name[name]["item_ids"] += ids
            problems.append(f"category {index}: {name!r} appears twice; merged")
            continue
        by_name[name] = {
            "name": name,
            "summary": _clean_text(category.get("summary"), 200)[:CATEGORY_SUMMARY_LIMIT],
            "item_ids": ids,
        }
    return list(by_name.values()), problems

def finalize_categories(categories: list[dict], items: list[dict],
                        nature: str) -> tuple[list[dict], list[str]]:
    """Enforce the shape rules and account for every item of the side.

    Named categories are ordered by size, because that is also the anchor order
    the page uses. Anything below CATEGORY_MIN_ITEMS, anything past
    CATEGORY_LIMIT, and every item the model never placed ends up in the
    fallback bucket rather than being dropped: the block then covers the whole
    side, and "the model missed it" is distinguishable from "it is filed under
    其他".
    """
    problems: list[str] = []
    ordered = sorted(categories, key=lambda c: (-len(c["item_ids"]), c["name"]))
    kept = [c for c in ordered if len(c["item_ids"]) >= CATEGORY_MIN_ITEMS]
    folded = len(ordered) - len(kept)
    if len(kept) > CATEGORY_LIMIT:
        problems.append(f"categories trimmed from {len(kept)} to {CATEGORY_LIMIT}")
        folded += len(kept) - CATEGORY_LIMIT
        kept = kept[:CATEGORY_LIMIT]

    placed = {ref for category in kept for ref in category["item_ids"]}
    leftover = [item_id(e) for e in items
                if (e.get("nature") or "news") == nature and item_id(e) not in placed]
    if folded:
        problems.append(f"{folded} category/ies folded into {CATEGORY_FALLBACK}")
    if leftover:
        kept.append({
            "name": CATEGORY_FALLBACK,
            "summary": f"其余 {len(leftover)} 条未归入以上类目",
            "item_ids": leftover,
        })
    return kept, problems

def apply_classify_decisions(categories: list[dict], item_index: dict[str, dict],
                             anchor_start: int = 0) -> list[dict]:
    """Resolve every referenced id into a render-ready ref.

    Resolved here rather than in the renderer: the page draws one panel, and
    turning an id into a title and a link would otherwise mean reading every
    processed source file back in at render time.

    Anchors are `cat-<n>` sequence numbers, not the category name. A Chinese
    label transliterated into an id would change every day the model picks a
    slightly different word, and the rail and the sections have to agree.
    """
    out: list[dict] = []
    for offset, category in enumerate(categories, 1):
        refs = []
        for ref in category["item_ids"]:
            entry = item_index.get(ref)
            if entry is None:
                continue
            extra = entry.get("extra") or {}
            refs.append({
                "id": ref,
                "source": entry.get("source"),
                "source_name": entry.get("source_name"),
                "channel": extra.get("channel"),
                "title": entry.get("title"),
                "url": entry.get("url"),
            })
        out.append({
            "anchor": f"cat-{anchor_start + offset}",
            "name": category["name"],
            "summary": category["summary"],
            "refs": refs,
        })
    return out

def has_categories(payload: dict | None) -> bool:
    """True when at least one side produced a category.

    The renderer keys the 今日汇总 panel on this, so an empty file has to read
    as "nothing was classified" and not as an empty panel.
    """
    sides = (payload or {}).get("sides") or {}
    return any((side or {}).get("categories") for side in sides.values())


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
