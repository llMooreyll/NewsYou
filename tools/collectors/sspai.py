#!/usr/bin/env python3
"""少数派 (sspai).

Feed first, then the v1 articles API as a fallback so the list is never short.
Full text is fetched only for 派早报 items; other posts stay title + link, which
matches the original behaviour and keeps the request count low.
"""
from __future__ import annotations

import json
import sys
import time

from . import base
from . import feed as feedmod
from .base import FetchError

SOURCE = "sspai"
NAME = "少数派"

FEED_URL = "https://sspai.com/feed"
API_URL = "https://sspai.com/api/v1/articles?limit=50&offset=0"
INFO_URL = "https://sspai.com/api/v1/article/info/get?id={id}"
POST_URL = "https://sspai.com/post/{id}"

LEGACY_MARKER = 'class="article-body"'

def _fulltext_eligible(entry: dict) -> bool:
    return "派早报" in entry["title"]

def collect(limit: int = 20, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy", **_):
    items = feedmod.feed_items(FEED_URL, SOURCE, NAME, engine=engine, limit=limit)

    if len(items) < limit:
        seen = {base.canonical_key(i["url"]) for i in items}
        try:
            data = base.fetch_json(API_URL)
            for article in data.get("list", []):
                title = article.get("title") or ""
                if not title:
                    continue
                url = POST_URL.format(id=article["id"])
                if base.canonical_key(url) in seen:
                    continue
                seen.add(base.canonical_key(url))
                items.append(base.item(
                    SOURCE, NAME, title, url, kind="article",
                    published_at=base.iso_cst(article.get("released_at") or article.get("created_at")),
                    date_source="api", summary=base.strip_tags(article.get("summary") or "")[:900],
                    extra={"collector": "api_v1", "views": article.get("views_count")},
                ))
                if len(items) >= limit:
                    break
        except (FetchError, json.JSONDecodeError) as exc:
            print(f"  ! sspai api failed: {exc}", file=sys.stderr)

    items.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)
    items = items[:limit]

    # The policy applies to the eligible subset only, otherwise `latest` would
    # pick a 派评 item that has no full-text support here.
    eligible = [e for e in items if _fulltext_eligible(e)]
    targets = {id(e) for e in base.fulltext_targets(eligible, fulltext)}

    for entry in items:
        if not _fulltext_eligible(entry) or id(entry) not in targets:
            continue
        scope = base.fulltext_scope_label(entry)
        try:
            body = _fetch_body(entry, engine)
        except (ValueError, FetchError, json.JSONDecodeError) as exc:
            print(f"  ! sspai fulltext failed {entry['url']}: {exc}", file=sys.stderr)
            continue
        if body:
            base.write_body(entry, body, engine=engine, scope=scope)
        time.sleep(0.5)

    return items

def _fetch_body(entry: dict, engine: str) -> dict | None:
    article_id = int(entry["url"].rstrip("/").rsplit("/", 1)[-1])

    # metadata (author / tags / released_at) comes from the API in both engines
    meta: dict = {}
    try:
        meta = (base.fetch_json(INFO_URL.format(id=article_id)).get("data") or {})
    except (FetchError, json.JSONDecodeError):
        meta = {}

    html = base.fetch(POST_URL.format(id=article_id))
    body = None
    if engine == "modern":
        from . import extract as ex
        body = ex.extract_article(html, entry["url"], "modern")
    else:
        from . import extract as ex
        block = base.container_at(html, LEGACY_MARKER)
        if block:
            body = {
                "text": base.strip_tags(block),
                "html": block,
                "sections": ex.split_sections(block),
                "title": None, "author": None, "published_at": None, "tags": [],
            }
    if not body:
        return None

    author = meta.get("author")
    body["author"] = (author.get("nickname") if isinstance(author, dict) else None) or body.get("author")
    body["tags"] = [t.get("title") for t in (meta.get("tags") or []) if isinstance(t, dict)] or body.get("tags") or []
    body["published_at"] = base.iso_cst(meta.get("released_time") or meta.get("released_at")) or body.get("published_at")
    return body
