#!/usr/bin/env python3
"""Anyway.Now (anyway.fm).

The /now/ page is a list of link items; each has a title link, a description and
a relative date stored in a data-balloon attribute. Only some entries get full
text, driven by the full-text policy.
"""
from __future__ import annotations

import re
import sys
import time

from . import base
from . import extract as ex
from .base import FetchError

SOURCE = "anywayfm"
NAME = "Anyway.Now 安妮薇看看"

NOW_URL = "https://anyway.fm/now/"
LEGACY_MARKER = 'class="note-content"'

def collect(limit: int = 10, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy", **_):
    page = base.fetch(NOW_URL)
    items: list[dict] = []
    # split on the item boundary so each block is self-contained
    parts = re.split(r'(?=<div class="news-item)', page)
    for part in parts:
        if not re.search(r'<div class="news-item[^"]*" id="n\d+"', part):
            continue
        title_match = re.search(
            r'<div class="news-title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', part, re.S)
        if not title_match:
            continue
        desc_match = re.search(r'<div class="news-desc[^"]*">(.*?)</div>', part, re.S)
        author_match = re.search(r'<div class="news-author">(.*?)</div>', part, re.S)
        date_match = re.search(r"data-balloon='([^']+)'", part)
        published = base.cn_date_to_iso(date_match.group(1)) if date_match else None
        items.append(base.item(
            SOURCE, NAME, title_match.group(2),
            "https://anyway.fm/" + title_match.group(1).lstrip("/"),
            kind="link", published_at=published,
            date_source="relative_balloon" if published else "unknown",
            summary=base.strip_tags(desc_match.group(1)) if desc_match else "",
            author=base.strip_tags(author_match.group(1)) if author_match else None,
            extra={"collector": "now_page", "position": len(items) + 1},
        ))
        if len(items) >= limit:
            break

    _fetch_bodies(items, engine=engine, fulltext=fulltext)
    return items

def _fetch_bodies(items: list[dict], *, engine: str, fulltext) -> None:
    for entry in base.fulltext_targets(items, fulltext):
        scope = base.fulltext_scope_label(entry)
        try:
            detail = base.fetch(entry["url"])
        except FetchError as exc:
            print(f"  ! body failed {entry['url']}: {exc}", file=sys.stderr)
            continue
        body = ex.extract_article(detail, entry["url"], engine, legacy_marker=LEGACY_MARKER)
        if body:
            base.write_body(entry, body, engine=engine, scope=scope)
            heading = re.search(r"<h1[^>]*>(.*?)</h1>", detail, re.S)
            if heading:
                entry["extra"]["original_title"] = base.strip_tags(heading.group(1))
        time.sleep(0.4)
