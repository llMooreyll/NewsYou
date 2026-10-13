#!/usr/bin/env python3
"""Anyway.Now (anyway.fm).

The /now/ page is a list of *recommended links*, not articles. Each entry has a
title link, an editor's description (``news-desc``) and a relative date in a
data-balloon attribute.

No full text is fetched, deliberately:

- ``/links/<id>`` is a 301 redirector that lands on a third-party site, so any
  "full text" would be someone else's article, not Anyway's.
- The only original content Anyway publishes here is the description, which is
  already stored as ``summary``.

So this source is title + description + link, and the full-text policy does not
apply to it.
"""
from __future__ import annotations

import re

from . import base

SOURCE = "anywayfm"
NAME = "Anyway.Now 安妮薇看看"

NOW_URL = "https://anyway.fm/now/"

def collect(limit: int = 10, **_):
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
    return items
