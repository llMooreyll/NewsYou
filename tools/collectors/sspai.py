#!/usr/bin/env python3
"""少数派 (sspai).

The v1 articles API is the primary source: one request returns the list, each
item's summary, its author, its tags and its `post_type`. The RSS feed is kept
only as a fallback for when that API is unreachable — it carries ten items and
no `post_type`, so a fallback run cannot split 派早报 (see `_from_feed`).

派早报 (the daily brief) is a collection of separate stories, one per `<h2>`
section. It is split into one item per story at collection time, so every later
stage — classification, the digest, the page anchors — sees individual news
items rather than one bundle. The trailing 推广 sections are dropped; the
简讯 section is split into its own stories too.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from . import base
from . import feed as feedmod
from .base import FetchError

SOURCE = "sspai"
NAME = "少数派"

FEED_URL = "https://sspai.com/feed"
API_URL = "https://sspai.com/api/v1/articles?limit={limit}&offset=0"
POST_URL = "https://sspai.com/post/{id}"

LEGACY_MARKER = 'class="article-body"'

# `post_type` in the articles API. 3 is 派早报; verified against 500 articles
# (21 briefs, 479 others) with no misclassification, which is why the title is
# not used for this any more.
POST_TYPE_BRIEF = 3

# 派早报 sections that are not news. The first two are the site's own
# promotions; the variants are the ones actually observed across six issues.
PROMO_SECTION = re.compile(r"^(少数派的近期动态|你可能错过的(文章|好文章))$")
# The one-line roundup section, whose items are separate stories.
DIGEST_SECTION = re.compile(r"^(不妨一看|看看就行)的")

# 派早报 publishes on weekdays only; verified across 21 issues (Sat 0, Sun 0).
# Used to tell "the brief is not out yet" apart from "there is no brief today".
BRIEF_LATEST_HOUR = 9

def _article_id(url: str) -> int | None:
    try:
        return int(str(url).rstrip("/").rsplit("/", 1)[-1])
    except (TypeError, ValueError):
        return None

# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------

def _from_api(limit: int) -> list[dict]:
    """The articles API, one request for the whole list.

    Everything the pipeline needs about a post comes from here, which is what
    lets the per-article `info` endpoint go away: measured against it, this list
    has every `author` and a superset of `tags` (its `custom_tags` included).
    """
    data = base.fetch_json(API_URL.format(limit=max(limit, 20)))
    items: list[dict] = []
    for article in data.get("list", []):
        title = (article.get("title") or "").strip()
        if not title:
            continue
        author = article.get("author")
        items.append(base.item(
            SOURCE, NAME, title, POST_URL.format(id=article["id"]), kind="article",
            published_at=base.iso_cst(article.get("released_at") or article.get("created_at")),
            date_source="api",
            summary=base.strip_tags(article.get("summary") or "")[:1200],
            author=(author.get("nickname") if isinstance(author, dict) else None),
            tags=_tags(article),
            extra={"collector": "api_v1", "views": article.get("views_count"),
                   "post_type": article.get("post_type"),
                   "keywords": article.get("keywords") or []},
        ))
    items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    return items[:limit]

def _tags(article: dict) -> list[str]:
    """The article's tags: its `tags` plus `custom_tags`, in that order.

    The list endpoint returns fewer `tags` per article than `info` did but adds
    `custom_tags`, and the union is a superset of what `info` had.
    """
    out = [t.get("title") for t in (article.get("tags") or []) if isinstance(t, dict) and t.get("title")]
    out += [t for t in (article.get("custom_tags") or []) if isinstance(t, str) and t]
    return list(dict.fromkeys(out))

def _from_feed(limit: int, engine: str) -> list[dict]:
    """RSS fallback. No `post_type`, so 派早报 cannot be split from here."""
    return feedmod.feed_items(FEED_URL, SOURCE, NAME, engine=engine, limit=limit)

# --------------------------------------------------------------------------
# Splitting 派早报
# --------------------------------------------------------------------------

def _issue_sections(body: dict) -> list[dict]:
    """The news-bearing sections of one 派早报, in order.

    Promotions are dropped and the 简讯 roundup is expanded into its own
    stories, so what comes back is a flat list of one-story blocks.
    """
    out: list[dict] = []
    for section in body.get("sections") or []:
        title = (section.get("title") or "").strip()
        if not title or PROMO_SECTION.match(title):
            continue
        if DIGEST_SECTION.match(title):
            out.extend(_digest_blocks(section))
            continue
        if not (section.get("text") or section.get("html")):
            continue
        out.append(section)
    return out

def _digest_blocks(section: dict) -> list[dict]:
    """Split a 简讯 section into one block per top-level list item.

    The section is a `<ul>` whose items are separate stories; some of them carry
    a nested `<ul>` with the details, which is why the depth is tracked rather
    than every `<li>` being taken. `text` mirrors that structure as newline
    separated lines (verified on six issues), so both forms stay in step.

    Each item has no headline of its own, so its first sentence becomes one.
    """
    html = section.get("html") or ""
    texts = [line.strip() for line in (section.get("text") or "").split("\n") if line.strip()]
    html_items = _top_level_items(html)
    # The two representations agree on the item count; prefer the HTML because
    # it keeps the nesting that the page's own list had.
    if len(html_items) != len(texts):
        return [section] if (section.get("text") or section.get("html")) else []
    blocks = []
    for index, (item_html, item_text) in enumerate(zip(html_items, texts), 1):
        blocks.append({
            "title": _first_sentence(item_text) or f"简讯 {index}",
            "text": item_text,
            "html": item_html,
            "digest_index": index,
        })
    return blocks

def _top_level_items(html: str) -> list[str]:
    """The HTML of each `<li>` at nesting depth 1, in order."""
    starts: list[int] = []
    depth = 0
    for match in re.finditer(r"<(/?)(ul|li)\b[^>]*>", html):
        closing, tag = match.group(1) == "/", match.group(2)
        if tag == "ul":
            depth += -1 if closing else 1
        elif not closing and depth == 1:
            starts.append(match.end())
    return [html[start:starts[i + 1] if i + 1 < len(starts) else len(html)]
            for i, start in enumerate(starts)]

def _first_sentence(text: str) -> str:
    """A headline for a story that has none: its first sentence.

    Split on `。` only. `：` would cut inside a title like
    《复仇者联盟 5：毁灭之日》.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    head = text.split("。", 1)[0]
    if len(head) > 60:
        head = head[:60].rstrip() + "…"
    return head

def _child_item(entry: dict, block: dict, index: int, total: int,
                engine: str, scope: str) -> dict:
    """One 派早报 section as a standalone item.

    The parent's id becomes this item's prefix, so the pieces of one issue sort
    together and a citation names the issue it came from. Ids are normally
    stamped later by collect.py; this needs one now, so it asks the same
    function rather than deriving a second copy of it.
    """
    import ai_stage                                  # lazy: avoids a cycle
    text = block.get("text") or ""
    html = block.get("html") or ""
    parent_id = ai_stage.item_id(entry)
    extra = dict(entry.get("extra") or {})
    extra.update({
        "column": "派早报",
        "issue_id": parent_id,
        "issue_title": entry.get("title"),
        "issue_url": entry.get("url"),
        "issue_index": index,
        "issue_total": total,
        "issue_date": entry.get("published_at"),
        "fulltext": True,
        "body_scope": scope,
        "body_chars": len(text),
        "extract_engine": engine,
    })
    if block.get("digest_index"):
        extra["is_digest"] = True
    child = base.item(
        SOURCE, NAME, block.get("title") or f"第 {index} 条", entry.get("url"),
        kind="article",
        published_at=entry.get("published_at"),
        date_source=entry.get("date_source"),
        summary=text[:1200],
        author=entry.get("author"),
        tags=list(entry.get("tags") or []),
        body_text=text or None,
        body_html=html or None,
        extra=extra,
    )
    child["id"] = f"{parent_id}:{index}"
    child["nature"] = entry.get("nature") or "news"
    return child

def _split_issue(entry: dict, body: dict, engine: str, scope: str) -> list[dict]:
    """Replace a 派早报 item with one item per story.

    Returns [] when there is nothing to split, which leaves the caller with the
    unsplit item — the honest outcome for a body that arrived without sections.
    """
    blocks = _issue_sections(body)
    if not blocks:
        return []
    return [_child_item(entry, block, i, len(blocks), engine, scope)
            for i, block in enumerate(blocks, 1)]

# --------------------------------------------------------------------------
# Brief state
# --------------------------------------------------------------------------

def brief_state(issues: list[dict], today: datetime | None = None) -> str:
    """Whether today's 派早报 exists, is not out yet, or is not due at all.

    Telling these apart matters for the page: "今日停稿" on a Saturday is right,
    but saying it at 07:00 on a Monday when the brief simply has not been
    published yet would be wrong.
    """
    now = today or datetime.now(base.CST)
    if now.weekday() >= 5:
        return "suspended"
    for issue in issues:
        stamp = str(issue.get("published_at") or "")[:10]
        if stamp == now.date().isoformat():
            return "published"
    if now.hour < BRIEF_LATEST_HOUR:
        return "pending"
    return "suspended"

# --------------------------------------------------------------------------
# collect
# --------------------------------------------------------------------------

def collect(limit: int = 20, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy",
            workers: int = base.DEFAULT_WORKERS, **_):
    try:
        items = _from_api(limit)
    except (FetchError, json.JSONDecodeError, ValueError) as exc:
        print(f"  ! sspai api failed, falling back to RSS "
              f"(派早报 will not be split): {exc}", file=sys.stderr)
        items = _from_feed(limit, engine)

    # 派早报: keep the newest issue only. A day's brief is what the digest is
    # for; older ones in the window would be re-split and re-classified daily.
    issues = [e for e in items if e.get("extra", {}).get("post_type") == POST_TYPE_BRIEF]
    rest = [e for e in items if e not in issues]
    issues.sort(key=lambda e: str(e.get("published_at") or ""), reverse=True)
    latest = issues[:1]
    items = rest + latest
    items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)

    state = brief_state(issues) if any(e.get("extra", {}).get("post_type") is not None
                                       for e in rest + latest) else "unknown"

    # Full text: 派早报 always (splitting depends on its sections), others by
    # policy. `latest` would otherwise pick a 派评 item with no body support.
    targets = {id(e) for e in latest}
    targets |= {id(e) for e in base.fulltext_targets(rest, fulltext)}

    def fetch(entry: dict) -> tuple[dict, dict | None]:
        """One body, fetched without touching the item yet.

        Fetching is the slow part and is independent per item, so it happens in
        parallel; applying the result is sequential and in order, which keeps
        the split items next to each other and the output deterministic.
        """
        scope = base.fulltext_scope_label(entry)
        try:
            return entry, _fetch_body(entry, engine)
        except (ValueError, FetchError, json.JSONDecodeError) as exc:
            print(f"  ! sspai fulltext failed {entry['url']}: {exc}", file=sys.stderr)
            return entry, None

    wanted = [e for e in items if id(e) in targets]
    bodies = dict((id(e), b) for e, b in base.parallel_map(fetch, wanted, workers))

    out: list[dict] = []
    for entry in items:
        if id(entry) not in targets:
            out.append(entry)
            continue
        scope = base.fulltext_scope_label(entry)
        body = bodies.get(id(entry))
        if not body:
            out.append(entry)
            continue
        split = _split_issue(entry, body, engine, scope) if entry in latest else []
        if split:
            out.extend(split)
            print(f"  sspai: split 派早报 {entry.get('url')} into {len(split)} items",
                  file=sys.stderr)
        else:
            base.write_body(entry, body, engine=engine, scope=scope)
            out.append(entry)

    _LAST_STATE["brief_state"] = state
    return out

_LAST_STATE: dict = {}

def last_state() -> dict:
    """Collection facts the digest needs but the items cannot carry.

    `brief_state` is about a brief that may not exist at all, so there is no
    item to read it from.
    """
    return dict(_LAST_STATE)

def _fetch_body(entry: dict, engine: str) -> dict | None:
    article_id = _article_id(entry.get("url") or "")
    if article_id is None:
        return None
    html = base.fetch(POST_URL.format(id=article_id))
    from . import extract as ex
    if engine == "modern":
        return ex.extract_article(html, entry["url"], "modern", include_images=True)
    block = base.container_at(html, LEGACY_MARKER)
    if not block:
        return None
    return {
        "text": base.strip_tags(block),
        "html": block,
        "sections": ex.split_sections(block),
        "title": None, "author": None, "published_at": None, "tags": [],
    }
