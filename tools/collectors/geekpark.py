#!/usr/bin/env python3
"""极客公园 (geekpark). RSS feed + article body."""
from __future__ import annotations

from . import base
from . import feed as feedmod

SOURCE = "geekpark"
NAME = "极客公园"

FEED_URL = "https://www.geekpark.net/rss"
LEGACY_MARKER = 'class="article-content"'

def _link_fix(url: str) -> str:
    return url.strip().replace("http://", "https://")

def collect(limit: int = 15, fulltext=base.FULLTEXT_DEFAULT, engine: str = "legacy",
            workers: int = base.DEFAULT_WORKERS, start=base.WINDOW_UNSET, **_):
    items = feedmod.feed_items(FEED_URL, SOURCE, NAME, engine=engine,
                               link_fix=_link_fix, limit=limit)
    feedmod.fetch_bodies(items, engine=engine, fulltext=fulltext, start=start,
                         legacy_marker=LEGACY_MARKER, include_images=True,
                         workers=workers)
    return items
